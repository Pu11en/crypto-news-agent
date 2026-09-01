#!/usr/bin/env python3
"""Free, local, raw X post collection for the crypto-signal-scan skill."""

from __future__ import annotations

import argparse
import asyncio
import csv
import getpass
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, AsyncIterator, Protocol

SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ACCOUNTS = SKILL_DIR / "assets" / "accounts.csv"
APP_NAME = "crypto-signal-scan"
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


@dataclass(frozen=True)
class Paths:
    root: Path
    config: Path
    accounts: Path
    session_db: Path
    state_db: Path
    scan_lock: Path
    output: Path


def app_paths(home: str | None = None) -> Paths:
    if home:
        root = Path(home).expanduser()
    elif os.environ.get("CRYPTO_SIGNAL_HOME"):
        root = Path(os.environ["CRYPTO_SIGNAL_HOME"]).expanduser()
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_NAME
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME
    return Paths(
        root=root,
        config=root / "config.json",
        accounts=root / "accounts.csv",
        session_db=root / "x-session.sqlite",
        state_db=root / "state.sqlite",
        scan_lock=root / "scan.lock",
        output=root / "output",
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def restrict_mode(path: Path, unix_mode: int) -> None:
    if os.name != "nt" and path.exists():
        os.chmod(path, unix_mode)


def secure_runtime(paths: Paths) -> None:
    paths.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    restrict_mode(paths.root, 0o700)
    paths.output.mkdir(parents=True, exist_ok=True, mode=0o700)
    restrict_mode(paths.output, 0o700)
    for sensitive in (paths.session_db, paths.state_db):
        restrict_mode(sensitive, 0o600)


@contextmanager
def scan_process_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    restrict_mode(path, 0o600)
    handle.seek(0)
    if handle.read(1) == b"":
        handle.write(b"0")
        handle.flush()
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError("another crypto-signal-scan process is already running") from exc
    try:
        yield
    finally:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def default_config(acknowledged: bool) -> dict[str, Any]:
    return {
        "acknowledge_x_terms_risk": acknowledged,
        "backend": "twscrape",
        "concurrency": 2,
        "include_quotes": True,
        "include_replies": False,
        "include_reposts": False,
        "lookback_hours": 24,
        "per_account_limit": 20,
        "accounts_per_scan": 1,
    }


def init_command(args: argparse.Namespace) -> int:
    paths = app_paths(args.home)
    secure_runtime(paths)
    if not paths.accounts.exists() or args.force:
        shutil.copyfile(DEFAULT_ACCOUNTS, paths.accounts)
    if not paths.config.exists() or args.force:
        atomic_json(paths.config, default_config(args.acknowledge_x_terms_risk))
    elif args.acknowledge_x_terms_risk:
        config = load_config(paths)
        config["acknowledge_x_terms_risk"] = True
        atomic_json(paths.config, config)
    init_state(paths.state_db)
    secure_runtime(paths)
    print(f"Initialized {APP_NAME} at {paths.root}")
    if not load_config(paths).get("acknowledge_x_terms_risk"):
        print("Live scans disabled until init is rerun with --acknowledge-x-terms-risk.")
    return 0


def load_config(paths: Paths) -> dict[str, Any]:
    if not paths.config.exists():
        raise RuntimeError("not initialized; run init first")
    return json.loads(paths.config.read_text(encoding="utf-8"))


def configure_command(args: argparse.Namespace) -> int:
    paths = app_paths(args.home)
    config = load_config(paths)
    if args.lookback_hours is not None:
        if args.lookback_hours < 1:
            raise RuntimeError("--lookback-hours must be positive")
        config["lookback_hours"] = args.lookback_hours
    if args.per_account_limit is not None:
        if args.per_account_limit < 1 or args.per_account_limit > 200:
            raise RuntimeError("--per-account-limit must be between 1 and 200")
        config["per_account_limit"] = args.per_account_limit
    if args.post_types is not None:
        policies = {
            "originals": (False, False, False),
            "quotes": (True, False, False),
            "all": (True, True, True),
        }
        quotes, replies, reposts = policies[args.post_types]
        config.update({
            "include_quotes": quotes,
            "include_replies": replies,
            "include_reposts": reposts,
        })
    atomic_json(paths.config, config)
    print(json.dumps(config, indent=2, sort_keys=True))
    return 0


def load_accounts(path: Path, enabled_only: bool = False) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError("account registry missing; run init first")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {"username", "tier", "tags", "enabled"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise RuntimeError("account CSV requires username,tier,tags,enabled headers")
        for row in reader:
            username = (row.get("username") or "").strip().lstrip("@")
            if not username:
                continue
            if not USERNAME_RE.fullmatch(username):
                raise RuntimeError(f"invalid X username syntax: {username}")
            key = username.lower()
            if key in seen:
                raise RuntimeError(f"duplicate account: {username}")
            seen.add(key)
            enabled_text = (row.get("enabled") or "true").strip().lower()
            if enabled_text not in {"1", "true", "yes", "0", "false", "no"}:
                raise RuntimeError(f"invalid enabled value for {username}: {enabled_text}")
            enabled = enabled_text in {"1", "true", "yes"}
            item = {
                "username": username,
                "tier": (row.get("tier") or "medium").strip(),
                "tags": (row.get("tags") or "").strip(),
                "enabled": enabled,
            }
            if not enabled_only or enabled:
                rows.append(item)
    return rows


def save_accounts(path: Path, rows: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["username", "tier", "tags", "enabled"])
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "enabled": str(bool(row["enabled"])).lower()})
    os.replace(tmp, path)


def accounts_command(args: argparse.Namespace) -> int:
    paths = app_paths(args.home)
    rows = load_accounts(paths.accounts)
    if args.accounts_action == "list":
        for row in rows:
            mark = "on " if row["enabled"] else "off"
            print(f"{mark}\t@{row['username']}\t{row['tier']}\t{row['tags']}")
        print(f"{sum(r['enabled'] for r in rows)}/{len(rows)} enabled")
        return 0
    if args.accounts_action == "validate":
        enabled = [r for r in rows if r["enabled"]]
        tiers = sorted({r["tier"] for r in rows})
        print(f"valid=yes total={len(rows)} enabled={len(enabled)} tiers={','.join(tiers)}")
        return 0
    if args.accounts_action in {"import", "sync-bundled"}:
        source = Path(args.csv).expanduser() if args.accounts_action == "import" else DEFAULT_ACCOUNTS
        incoming = load_accounts(source)
        if args.mode == "replace":
            updated = incoming
        else:
            updated = list(rows)
            by_name = {row["username"].lower(): row for row in updated}
            for item in incoming:
                existing = by_name.get(item["username"].lower())
                if existing:
                    existing.update(item)
                else:
                    updated.append(item)
                    by_name[item["username"].lower()] = item
        enabled = sum(item["enabled"] for item in updated)
        if not updated or enabled < 1:
            raise RuntimeError("account registry must contain at least one enabled account")
        with scan_process_lock(paths.scan_lock):
            save_accounts(paths.accounts, updated)
            reset_account_cursor(paths.state_db)
        print(f"accounts {args.accounts_action}: total={len(updated)} enabled={enabled} mode={args.mode}")
        return 0

    candidate = args.username.lstrip("@")
    if not USERNAME_RE.fullmatch(candidate):
        raise RuntimeError(f"invalid X username syntax: {candidate}")
    key = candidate.lower()
    match = next((r for r in rows if r["username"].lower() == key), None)
    if args.accounts_action == "add":
        if match:
            raise RuntimeError(f"account already exists: @{args.username.lstrip('@')}")
        rows.append({
            "username": args.username.lstrip("@"),
            "tier": args.tier,
            "tags": "|".join(t.strip() for t in args.tags.split(",") if t.strip()),
            "enabled": True,
        })
    elif not match:
        raise RuntimeError(f"account not found: @{args.username.lstrip('@')}")
    elif args.accounts_action == "remove":
        rows.remove(match)
    elif args.accounts_action == "disable":
        match["enabled"] = False
    elif args.accounts_action == "enable":
        match["enabled"] = True
    if not any(row["enabled"] for row in rows):
        raise RuntimeError("account registry must contain at least one enabled account")
    with scan_process_lock(paths.scan_lock):
        save_accounts(paths.accounts, rows)
        reset_account_cursor(paths.state_db)
    print(f"accounts {args.accounts_action}: @{args.username.lstrip('@')}")
    return 0


def init_state(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_posts (
                post_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                first_seen_at TEXT NOT NULL,
                scan_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS account_health (
                username TEXT PRIMARY KEY,
                last_success_at TEXT,
                last_error TEXT
            );
            CREATE TABLE IF NOT EXISTS scan_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
    restrict_mode(path, 0o600)


def next_account_batch(
    state_db: Path, accounts: list[dict[str, Any]], count: int
) -> tuple[list[dict[str, Any]], int]:
    if not accounts:
        return [], 0
    init_state(state_db)
    with sqlite3.connect(state_db) as db:
        row = db.execute("SELECT value FROM scan_meta WHERE key='account_cursor'").fetchone()
    cursor = int(row[0]) if row else 0
    size = max(1, min(int(count), len(accounts)))
    selected = [accounts[(cursor + offset) % len(accounts)] for offset in range(size)]
    return selected, cursor


def reset_account_cursor(state_db: Path) -> None:
    init_state(state_db)
    with sqlite3.connect(state_db) as db:
        db.execute(
            "INSERT INTO scan_meta(key,value) VALUES('account_cursor','0') "
            "ON CONFLICT(key) DO UPDATE SET value='0'"
        )
        db.commit()


def advance_account_cursor(state_db: Path, cursor: int, amount: int) -> int:
    next_cursor = cursor + amount
    with sqlite3.connect(state_db) as db:
        db.execute(
            "INSERT INTO scan_meta(key,value) VALUES('account_cursor',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(next_cursor),),
        )
        db.commit()
    return next_cursor


async def auth_add_async(paths: Paths, label: str, cookie_header: str) -> None:
    try:
        from twscrape import API
    except ImportError as exc:
        raise RuntimeError("twscrape missing; install requirements.txt first") from exc
    api = API(str(paths.session_db))
    existing = await api.pool.get_all()
    if existing:
        await api.pool.delete_accounts([account.username for account in existing])
    await api.pool.add_account_cookies(label, cookie_header)
    restrict_mode(paths.session_db, 0o600)


def parse_cookie_header(raw: str) -> str:
    pieces: dict[str, str] = {}
    for part in raw.strip().split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        pieces[name.strip()] = value.strip()
    missing = {"auth_token", "ct0"} - pieces.keys()
    if missing or not pieces.get("auth_token") or not pieces.get("ct0"):
        raise RuntimeError("cookie header must contain non-empty auth_token and ct0")
    return f"auth_token={pieces['auth_token']}; ct0={pieces['ct0']}"


def auth_add_command(args: argparse.Namespace) -> int:
    paths = app_paths(args.home)
    secure_runtime(paths)
    load_config(paths)
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise RuntimeError(
            "auth-add requires an interactive local terminal; open a terminal, cd to the skill directory, and run it there"
        )
    raw = getpass.getpass("Paste x.com cookie header or auth_token value (input hidden): ")
    auth_token = None
    if "auth_token=" in raw and "ct0=" in raw:
        cookie_header = parse_cookie_header(raw)
        ct0 = None
    else:
        candidate = raw.strip()
        if candidate.startswith("auth_token="):
            auth_token = candidate.split("=", 1)[1].strip()
        elif "=" in candidate:
            raise RuntimeError("enter a complete cookie header or the raw auth_token value")
        else:
            auth_token = candidate
        ct0 = getpass.getpass("Paste x.com ct0 value (input hidden): ").strip()
        if ct0.startswith("ct0="):
            ct0 = ct0.split("=", 1)[1].strip()
        if not auth_token or not ct0:
            raise RuntimeError("both auth_token and ct0 are required")
        cookie_header = f"auth_token={auth_token}; ct0={ct0}"
    with scan_process_lock(paths.scan_lock):
        asyncio.run(auth_add_async(paths, args.label, cookie_header))
    del raw, auth_token, cookie_header, ct0
    permission_note = "owner-only Unix permissions" if os.name != "nt" else "the current Windows user profile"
    print(f"X session stored locally under {permission_note}; cookie values were not printed.")
    return 0


def auth_remove_command(args: argparse.Namespace) -> int:
    if not args.yes:
        raise RuntimeError("auth-remove requires --yes")
    paths = app_paths(args.home)
    secure_runtime(paths)

    async def remove() -> int:
        try:
            from twscrape import API
        except ImportError as exc:
            raise RuntimeError("twscrape missing; install requirements.txt first") from exc
        api = API(str(paths.session_db))
        rows = await api.pool.get_all()
        if rows:
            await api.pool.delete_accounts([account.username for account in rows])
        return len(rows)

    with scan_process_lock(paths.scan_lock):
        removed = asyncio.run(remove())
        for candidate in (
            paths.session_db,
            Path(str(paths.session_db) + "-wal"),
            Path(str(paths.session_db) + "-shm"),
        ):
            candidate.unlink(missing_ok=True)
    print(f"removed_x_sessions={removed}")
    return 0


async def auth_stats(paths: Paths, verify_live: bool = False) -> dict[str, Any]:
    try:
        from twscrape import API
    except ImportError:
        return {"installed": False, "accounts": 0, "active_accounts": 0, "live_verified": False}
    if not paths.session_db.exists():
        return {
            "installed": True,
            "accounts": 0,
            "active_accounts": 0,
            "live_verified": False if verify_live else None,
            "live_error": "exactly one active local X session is required" if verify_live else None,
        }
    api = API(str(paths.session_db), raise_when_no_account=True, wait_timeout=30)
    rows = await api.pool.get_all()
    active = [account for account in rows if account.active]
    live_verified: bool | None = None
    live_error: str | None = None
    if verify_live:
        if len(rows) != 1 or len(active) != 1:
            live_verified = False
            live_error = "exactly one active local X session is required"
        else:
            try:
                probe = await api.user_by_login("xdevelopers")
                live_verified = probe is not None
                if not live_verified:
                    live_error = "authenticated X lookup returned no result"
            except Exception as exc:
                live_verified = False
                live_error = f"{type(exc).__name__}: {str(exc)[:180]}"
    restrict_mode(paths.session_db, 0o600)
    return {
        "installed": True,
        "accounts": len(rows),
        "active_accounts": len(active),
        "live_verified": live_verified,
        "live_error": live_error,
    }


def mode(path: Path) -> str | None:
    if not path.exists():
        return None
    return oct(stat.S_IMODE(path.stat().st_mode))


def doctor_command(args: argparse.Namespace) -> int:
    paths = app_paths(args.home)
    checks: dict[str, Any] = {
        "initialized": paths.config.exists(),
        "registry": paths.accounts.exists(),
        "runtime_mode": mode(paths.root),
    }
    try:
        checks["configured_accounts"] = len(load_accounts(paths.accounts, enabled_only=True))
    except Exception:
        checks["configured_accounts"] = 0
    stats = asyncio.run(auth_stats(paths, verify_live=args.live_auth))
    checks["twscrape_installed"] = stats["installed"]
    checks["x_sessions"] = stats["accounts"]
    checks["active_x_sessions"] = stats["active_accounts"]
    checks["live_auth_verified"] = stats["live_verified"]
    if stats.get("live_error"):
        checks["live_auth_error"] = stats["live_error"]
    checks["session_mode"] = mode(paths.session_db)
    if paths.config.exists():
        checks["terms_risk_acknowledged"] = bool(load_config(paths).get("acknowledge_x_terms_risk"))
    else:
        checks["terms_risk_acknowledged"] = False
    permission_ready = os.name == "nt" or (
        checks["runtime_mode"] == "0o700" and checks["session_mode"] in {None, "0o600"}
    )
    auth_ready = checks["x_sessions"] == 1 and checks["active_x_sessions"] == 1
    if args.live_auth:
        auth_ready = auth_ready and checks["live_auth_verified"] is True
    else:
        auth_ready = True
    checks["readiness_stage"] = "live-auth" if args.live_auth else "pre-auth"
    ready = all([
        checks["initialized"], checks["registry"], checks["configured_accounts"] > 0,
        checks["twscrape_installed"], auth_ready,
        checks["terms_risk_acknowledged"], permission_ready,
    ])
    checks["ready"] = ready
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if ready else 2


class Provider(Protocol):
    async def user_posts(self, username: str, limit: int) -> AsyncIterator[Any]: ...


class ProviderSessionUnavailable(RuntimeError):
    pass


class ProviderRateLimited(RuntimeError):
    def __init__(self, queue: str, retry_after: datetime | None):
        self.queue = queue
        self.retry_after = retry_after
        detail = f" until {iso(retry_after)}" if retry_after else ""
        super().__init__(f"X endpoint {queue} is rate-limited{detail}")


class TwscrapeProvider:
    def __init__(self, session_db: Path):
        try:
            from twscrape import API, NoAccountError
        except ImportError as exc:
            raise RuntimeError("twscrape missing; install requirements.txt first") from exc
        self.api = API(str(session_db), raise_when_no_account=True, wait_timeout=30)
        self.no_account_error = NoAccountError
        if session_db.exists():
            restrict_mode(session_db, 0o600)

    async def availability_error(self, queue: str) -> RuntimeError:
        rows = await self.api.pool.get_all()
        now = utc_now()
        retry_times = [
            account.locks.get(queue)
            for account in rows
            if account.active
            and account.locks.get(queue) is not None
            and account.locks.get(queue) > now
        ]
        if retry_times:
            return ProviderRateLimited(queue, min(retry_times))
        return ProviderSessionUnavailable(
            f"no active X session is available for {queue}; run doctor --live-auth"
        )

    async def user_posts(self, username: str, limit: int) -> AsyncIterator[Any]:
        try:
            user = await self.api.user_by_login(username)
        except self.no_account_error as exc:
            raise (await self.availability_error("UserByScreenName")) from exc
        if user is None:
            raise RuntimeError("account not found")
        emitted = 0
        try:
            async for post in self.api.user_tweets(user.id, limit=limit):
                if emitted >= limit:
                    break
                yield post
                emitted += 1
        except self.no_account_error as exc:
            raise (await self.availability_error("UserTweets")) from exc


def media_urls(post: Any) -> list[str]:
    media = getattr(post, "media", None)
    if media is None:
        return []
    urls: list[str] = []
    urls.extend(getattr(p, "url", "") for p in getattr(media, "photos", []))
    urls.extend(getattr(v, "videoUrl", "") for v in getattr(media, "animated", []))
    for video in getattr(media, "videos", []):
        variants = sorted(getattr(video, "variants", []), key=lambda v: getattr(v, "bitrate", 0))
        if variants:
            urls.append(getattr(variants[-1], "url", ""))
    return [u for u in urls if u]


def normalize_post(post: Any, scan_id: str, fetched_at: datetime) -> dict[str, Any]:
    quoted = getattr(post, "quotedTweet", None)
    reposted = getattr(post, "retweetedTweet", None)
    links = [getattr(link, "url", "") for link in (getattr(post, "links", None) or [])]
    return {
        "post_id": str(post.id),
        "author_id": str(post.user.id),
        "username": post.user.username,
        "text": post.rawContent,
        "created_at": iso(post.date),
        "public_url": post.url,
        "in_reply_to_post_id": str(post.inReplyToTweetId) if post.inReplyToTweetId else None,
        "quoted_post_id": str(quoted.id) if quoted else None,
        "reposted_post_id": str(reposted.id) if reposted else None,
        "media_urls": media_urls(post),
        "external_links": [u for u in links if u],
        "fetched_at": iso(fetched_at),
        "source_adapter": f"twscrape-{metadata.version('twscrape')}",
        "scan_id": scan_id,
    }


def claim_post(db: sqlite3.Connection, record: dict[str, Any]) -> bool:
    cursor = db.execute(
        "INSERT OR IGNORE INTO seen_posts(post_id, username, first_seen_at, scan_id) VALUES (?, ?, ?, ?)",
        (record["post_id"], record["username"], record["fetched_at"], record["scan_id"]),
    )
    return cursor.rowcount == 1


async def collect(
    provider: Provider,
    accounts: list[dict[str, Any]],
    paths: Paths,
    hours: int,
    limit: int,
    only: set[str] | None = None,
    *,
    concurrency: int = 2,
    include_quotes: bool = True,
    include_replies: bool = False,
    include_reposts: bool = False,
) -> tuple[Path, Path, dict[str, Any]]:
    scan_id = str(uuid.uuid4())
    started = utc_now()
    cutoff = started - timedelta(hours=hours)
    selected = [a for a in accounts if not only or a["username"].lower() in only]
    if only and not selected:
        raise RuntimeError("none of the requested --account values are enabled in the registry")
    run_dir = paths.output / f"{started.strftime('%Y%m%dT%H%M%SZ')}-{scan_id[:8]}"
    run_dir.mkdir(parents=True, mode=0o700)
    posts_path = run_dir / "posts.jsonl"
    manifest_path = run_dir / "scan-manifest.json"
    reached: list[str] = []
    errors: list[dict[str, Any]] = []
    processing_errors: list[dict[str, str]] = []
    new_count = duplicate_count = filtered_count = rejected_count = fetched_count = 0
    allowed_usernames = {account["username"].lower() for account in selected}
    init_state(paths.state_db)

    effective_concurrency = max(1, min(int(concurrency), 4))
    semaphore = asyncio.Semaphore(effective_concurrency)

    async def fetch_one(account: dict[str, Any]) -> tuple[str, list[Any], dict[str, Any] | None]:
        username = account["username"]
        try:
            posts: list[Any] = []
            async with semaphore:
                async for post in provider.user_posts(username, limit):
                    posts.append(post)
            return username, posts, None
        except ProviderRateLimited as exc:
            return username, [], {
                "username": username,
                "error": str(exc),
                "retryable": True,
                "retry_after": iso(exc.retry_after) if exc.retry_after else None,
                "fatal": False,
            }
        except ProviderSessionUnavailable as exc:
            return username, [], {
                "username": username,
                "error": str(exc),
                "retryable": False,
                "retry_after": None,
                "fatal": True,
            }
        except Exception as exc:
            return username, [], {
                "username": username,
                "error": f"{type(exc).__name__}: {str(exc)[:180]}",
                "retryable": False,
                "retry_after": None,
                "fatal": False,
            }

    fetched = await asyncio.gather(*(fetch_one(account) for account in selected))

    with sqlite3.connect(paths.state_db) as db, posts_path.open("w", encoding="utf-8") as out:
        for username, posts, error in fetched:
            if error:
                errors.append(error)
                db.execute(
                    "INSERT INTO account_health(username,last_success_at,last_error) VALUES(?,NULL,?) "
                    "ON CONFLICT(username) DO UPDATE SET last_error=excluded.last_error",
                    (username, error["error"]),
                )
                db.commit()
                continue

            fetched_count += len(posts)
            for post in posts:
                try:
                    author = str(getattr(post.user, "username", "")).lower()
                    if author != username.lower() or author not in allowed_usernames:
                        rejected_count += 1
                        continue
                    post_date = post.date if post.date.tzinfo else post.date.replace(tzinfo=timezone.utc)
                    excluded_type = (
                        (post.inReplyToTweetId and not include_replies)
                        or (getattr(post, "retweetedTweet", None) and not include_reposts)
                        or (getattr(post, "quotedTweet", None) and not include_quotes)
                    )
                    if post_date < cutoff or excluded_type:
                        filtered_count += 1
                        continue
                    record = normalize_post(post, scan_id, utc_now())
                    if claim_post(db, record):
                        out.write(json.dumps(record, ensure_ascii=False) + "\n")
                        new_count += 1
                    else:
                        duplicate_count += 1
                except Exception as exc:
                    processing_errors.append({
                        "username": username,
                        "post_id": str(getattr(post, "id", "unknown")),
                        "error": f"{type(exc).__name__}: {str(exc)[:180]}",
                    })
            reached.append(username)
            db.execute(
                "INSERT INTO account_health(username,last_success_at,last_error) VALUES(?,?,NULL) "
                "ON CONFLICT(username) DO UPDATE SET last_success_at=excluded.last_success_at,last_error=NULL",
                (username, iso(utc_now())),
            )
            db.commit()

    manifest = {
        "scan_id": scan_id,
        "started_at": iso(started),
        "finished_at": iso(utc_now()),
        "lookback_hours": hours,
        "requested_accounts": [a["username"] for a in selected],
        "reached_accounts": reached,
        "failed_accounts": errors,
        "fetched_posts": fetched_count,
        "new_posts": new_count,
        "duplicate_posts": duplicate_count,
        "filtered_posts": filtered_count,
        "rejected_unallowlisted_posts": rejected_count,
        "post_processing_errors": processing_errors,
        "coverage_scope": "bounded best-effort timeline request; not complete X coverage",
        "bounded_by_request_limit": fetched_count >= limit * max(1, len(reached)),
        "policy": {
            "include_quotes": include_quotes,
            "include_replies": include_replies,
            "include_reposts": include_reposts,
        },
        "concurrency": effective_concurrency,
        "output": str(posts_path),
        "source_adapter": f"twscrape-{metadata.version('twscrape')}",
    }
    atomic_json(manifest_path, manifest)
    return posts_path, manifest_path, manifest


def earliest_retry_after(errors: list[dict[str, Any]]) -> str | None:
    retry_times = [error["retry_after"] for error in errors if error.get("retry_after")]
    return min(retry_times) if retry_times else None


def cycle_command(args: argparse.Namespace) -> int:
    paths = app_paths(args.home)
    secure_runtime(paths)
    with scan_process_lock(paths.scan_lock):
        return cycle_command_unlocked(args, paths)


def cycle_command_unlocked(args: argparse.Namespace, paths: Paths) -> int:
    config = load_config(paths)
    if not config.get("acknowledge_x_terms_risk"):
        raise RuntimeError("live cycle disabled; rerun init with --acknowledge-x-terms-risk")
    accounts = load_accounts(paths.accounts, enabled_only=True)
    if not accounts:
        raise RuntimeError("no enabled accounts")
    hours = args.hours if args.hours is not None else int(config.get("lookback_hours", 24))
    limit = args.limit if args.limit is not None else int(config.get("per_account_limit", 20))
    max_runtime = int(args.max_runtime_seconds)
    max_accounts = min(int(args.max_accounts or len(accounts)), len(accounts))
    if hours < 1 or limit < 1 or max_runtime < 1 or max_accounts < 1:
        raise RuntimeError("cycle arguments must be positive")
    session = asyncio.run(auth_stats(paths))
    if session["accounts"] != 1 or session["active_accounts"] != 1:
        raise RuntimeError("exactly one active local X session is required; run auth-add")

    provider = TwscrapeProvider(paths.session_db)
    cycle_id = str(uuid.uuid4())
    started = utc_now()
    deadline = time.monotonic() + max_runtime
    cycle_dir = paths.output / "cycles" / f"{started.strftime('%Y%m%dT%H%M%SZ')}-{cycle_id[:8]}"
    cycle_dir.mkdir(parents=True, mode=0o700)
    combined_path = cycle_dir / "combined.jsonl"
    manifest_path = cycle_dir / "cycle-manifest.json"
    combined_path.write_text("", encoding="utf-8")
    rounds: list[dict[str, Any]] = []
    totals = {"new_posts": 0, "duplicate_posts": 0, "filtered_posts": 0, "fetched_posts": 0}
    advanced = 0
    status = "pass_finished"
    stop_reason: str | None = None
    cursor_before = next_account_batch(paths.state_db, accounts, 1)[1]

    while advanced < max_accounts:
        if time.monotonic() >= deadline:
            status, stop_reason = "max_runtime", "runtime budget reached"
            break
        selected, cursor = next_account_batch(paths.state_db, accounts, 1)
        username = selected[0]["username"]
        posts_path, round_manifest_path, manifest = asyncio.run(
            collect(
                provider, selected, paths, hours, limit,
                concurrency=1,
                include_quotes=bool(config.get("include_quotes", True)),
                include_replies=bool(config.get("include_replies", False)),
                include_reposts=bool(config.get("include_reposts", False)),
            )
        )
        with combined_path.open("a", encoding="utf-8") as combined:
            combined.write(posts_path.read_text(encoding="utf-8"))
        for key in totals:
            totals[key] += int(manifest.get(key, 0))
        error = manifest["failed_accounts"][0] if manifest["failed_accounts"] else None
        outcome = "reached" if manifest["reached_accounts"] else "failed"
        rounds.append({
            "username": username,
            "outcome": outcome,
            "manifest": str(round_manifest_path),
            "posts": str(posts_path),
            "new_posts": manifest["new_posts"],
            "error": error,
        })
        if manifest["reached_accounts"] or (error and not error.get("retryable") and not error.get("fatal")):
            advance_account_cursor(paths.state_db, cursor, 1)
            advanced += 1
            continue
        if error and error.get("fatal"):
            status, stop_reason = "session_unavailable", error["error"]
            break
        retry_text = error.get("retry_after") if error else None
        if not retry_text:
            status, stop_reason = "retry_after_unknown", "retryable error had no retry time"
            break
        retry_at = datetime.fromisoformat(retry_text)
        wait_seconds = max(1.0, (retry_at - utc_now()).total_seconds() + 1.0)
        if time.monotonic() + wait_seconds > deadline:
            status, stop_reason = "retry_after_beyond_deadline", retry_text
            break
        time.sleep(wait_seconds)

    cursor_after = next_account_batch(paths.state_db, accounts, 1)[1]
    cycle_manifest = {
        "schema_version": 1,
        "cycle_id": cycle_id,
        "started_at": iso(started),
        "finished_at": iso(utc_now()),
        "status": status,
        "stop_reason": stop_reason,
        "lookback_hours": hours,
        "per_account_limit": limit,
        "max_runtime_seconds": max_runtime,
        "registry_snapshot_count": len(accounts),
        "accounts_targeted": max_accounts,
        "accounts_advanced": advanced,
        "cursor_before": cursor_before,
        "cursor_after": cursor_after,
        "next_account": accounts[cursor_after % len(accounts)]["username"],
        "rounds": rounds,
        "combined_output": str(combined_path),
        "coverage_scope": "best-effort bounded requests; not complete X coverage",
        **totals,
    }
    atomic_json(manifest_path, cycle_manifest)
    atomic_json(paths.output / "latest.json", {
        "cycle_id": cycle_id,
        "manifest": str(manifest_path),
        "combined": str(combined_path),
        "published_at": iso(utc_now()),
    })
    print(json.dumps({
        "status": status,
        "accounts_advanced": advanced,
        "next_account": cycle_manifest["next_account"],
        "new_posts": totals["new_posts"],
        "combined": str(combined_path),
        "manifest": str(manifest_path),
    }, indent=2))
    if status == "pass_finished":
        return 0 if any(item["outcome"] == "reached" for item in rounds) else 4
    return 2 if status == "session_unavailable" else 3


def latest_command(args: argparse.Namespace) -> int:
    paths = app_paths(args.home)
    latest = paths.output / "latest.json"
    if not latest.exists():
        raise RuntimeError("no completed or partial cycle output exists yet; run cycle")
    value = json.loads(latest.read_text(encoding="utf-8"))
    print(json.dumps(value, indent=2, sort_keys=True))
    return 0


def scan_command(args: argparse.Namespace) -> int:
    paths = app_paths(args.home)
    secure_runtime(paths)
    with scan_process_lock(paths.scan_lock):
        return scan_command_unlocked(args, paths)


def scan_command_unlocked(args: argparse.Namespace, paths: Paths) -> int:
    config = load_config(paths)
    if not config.get("acknowledge_x_terms_risk"):
        raise RuntimeError("live scan disabled; rerun init with --acknowledge-x-terms-risk")
    accounts = load_accounts(paths.accounts, enabled_only=True)
    only = {u.lstrip("@").lower() for u in (args.account or [])} or None
    if only and args.all_accounts:
        raise RuntimeError("--account and --all-accounts cannot be combined")
    rotation_cursor: int | None = None
    if only:
        selected_accounts = accounts
    elif args.all_accounts:
        selected_accounts = accounts
    else:
        batch_size = int(config.get("accounts_per_scan", 1))
        if batch_size != 1:
            raise RuntimeError("config accounts_per_scan must be 1 for single-session rotation")
        selected_accounts, rotation_cursor = next_account_batch(paths.state_db, accounts, batch_size)
    hours = args.hours if args.hours is not None else int(config.get("lookback_hours", 24))
    limit = args.limit if args.limit is not None else int(config.get("per_account_limit", 20))
    if hours < 1 or limit < 1:
        raise RuntimeError("--hours and --limit must be positive")
    session = asyncio.run(auth_stats(paths))
    if session["accounts"] != 1 or session["active_accounts"] != 1:
        raise RuntimeError("exactly one active local X session is required; run auth-add")
    provider = TwscrapeProvider(paths.session_db)
    posts, manifest_path, manifest = asyncio.run(
        collect(
            provider, selected_accounts, paths, hours, limit, only,
            concurrency=int(config.get("concurrency", 2)),
            include_quotes=bool(config.get("include_quotes", True)),
            include_replies=bool(config.get("include_replies", False)),
            include_reposts=bool(config.get("include_reposts", False)),
        )
    )
    if rotation_cursor is not None:
        nonretryable_failure = any(
            not error.get("retryable", False) and not error.get("fatal", False)
            for error in manifest["failed_accounts"]
        )
        should_advance = bool(manifest["reached_accounts"]) or nonretryable_failure
        next_cursor = rotation_cursor
        if should_advance:
            next_cursor = advance_account_cursor(
                paths.state_db, rotation_cursor, len(manifest["requested_accounts"])
            )
        manifest["account_rotation"] = {
            "mode": "round_robin",
            "cursor_before": rotation_cursor,
            "cursor_after": next_cursor,
            "registry_size": len(accounts),
            "advanced": should_advance,
        }
        atomic_json(manifest_path, manifest)
    retry_after = earliest_retry_after(manifest["failed_accounts"])
    print(json.dumps({
        "posts": str(posts), "manifest": str(manifest_path),
        "new_posts": manifest["new_posts"],
        "reached_accounts": len(manifest["reached_accounts"]),
        "failed_accounts": len(manifest["failed_accounts"]),
        "retry_after": retry_after,
    }, indent=2))
    return 0 if manifest["reached_accounts"] else 3


def health_command(args: argparse.Namespace) -> int:
    paths = app_paths(args.home)
    if not paths.state_db.exists():
        print("No scan state yet.")
        return 2
    with sqlite3.connect(paths.state_db) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute(
            "SELECT username,last_success_at,last_error FROM account_health ORDER BY username COLLATE NOCASE"
        )]
    print(json.dumps(rows, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--home", help="override local runtime directory")
    sub = p.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize local runtime state")
    init.add_argument("--force", action="store_true")
    init.add_argument("--acknowledge-x-terms-risk", action="store_true")
    init.set_defaults(func=init_command)

    configure = sub.add_parser("configure", help="show or update validated scan defaults")
    configure.add_argument("--lookback-hours", type=int)
    configure.add_argument("--per-account-limit", type=int)
    configure.add_argument("--post-types", choices=("originals", "quotes", "all"))
    configure.set_defaults(func=configure_command)

    auth = sub.add_parser("auth-add", help="securely register one local X cookie session")
    auth.add_argument("--label", default="main")
    auth.set_defaults(func=auth_add_command)

    auth_remove = sub.add_parser("auth-remove", help="delete all locally stored X sessions")
    auth_remove.add_argument("--yes", action="store_true", help="confirm local session deletion")
    auth_remove.set_defaults(func=auth_remove_command)

    doctor = sub.add_parser("doctor", help="check installation and authentication readiness")
    doctor.add_argument("--live-auth", action="store_true", help="verify cookies with one authenticated X lookup")
    doctor.set_defaults(func=doctor_command)

    acc = sub.add_parser("accounts", help="manage the monitored account registry")
    acc_sub = acc.add_subparsers(dest="accounts_action", required=True)
    for action in ("list", "validate"):
        q = acc_sub.add_parser(action)
        q.set_defaults(func=accounts_command)
    add = acc_sub.add_parser("add")
    add.add_argument("username")
    add.add_argument("--tier", default="medium")
    add.add_argument("--tags", default="")
    add.set_defaults(func=accounts_command)
    for action in ("remove", "disable", "enable"):
        q = acc_sub.add_parser(action)
        q.add_argument("username")
        q.set_defaults(func=accounts_command)
    account_import = acc_sub.add_parser("import", help="merge or replace accounts from CSV")
    account_import.add_argument("csv")
    account_import.add_argument("--mode", choices=("merge", "replace"), default="merge")
    account_import.set_defaults(func=accounts_command)
    account_sync = acc_sub.add_parser("sync-bundled", help="merge or replace from bundled registry")
    account_sync.add_argument("--mode", choices=("merge", "replace"), default="merge")
    account_sync.set_defaults(func=accounts_command)

    scan = sub.add_parser("scan", help="collect recent raw public posts")
    scan.add_argument("--hours", type=int, help="lookback hours; defaults to local config")
    scan.add_argument("--limit", type=int, help="maximum posts requested per account; defaults to local config")
    scan.add_argument("--account", action="append", help="restrict scan to an enabled account; repeatable")
    scan.add_argument(
        "--all-accounts",
        action="store_true",
        help="attempt every enabled account in one run instead of safe round-robin batching",
    )
    scan.set_defaults(func=scan_command)

    cycle = sub.add_parser("cycle", help="run a bounded resumable account cycle")
    cycle.add_argument("--hours", type=int, help="lookback hours; defaults to local config")
    cycle.add_argument("--limit", type=int, help="maximum posts requested per account")
    cycle.add_argument("--max-runtime-seconds", type=int, default=3600)
    cycle.add_argument("--max-accounts", type=int, help="maximum accounts to advance this cycle")
    cycle.set_defaults(func=cycle_command)

    latest = sub.add_parser("latest", help="show stable paths for the latest cycle output")
    latest.set_defaults(func=latest_command)

    health = sub.add_parser("health", help="show per-account scan health")
    health.set_defaults(func=health_command)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
