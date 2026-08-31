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
import uuid
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


def load_accounts(path: Path, enabled_only: bool = False) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError("account registry missing; run init first")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            username = (row.get("username") or "").strip().lstrip("@")
            if not username:
                continue
            if not USERNAME_RE.fullmatch(username):
                raise RuntimeError(f"invalid X username syntax: {username}")
            key = username.lower()
            if key in seen:
                raise RuntimeError(f"duplicate account: {username}")
            seen.add(key)
            enabled = (row.get("enabled") or "true").lower() in {"1", "true", "yes"}
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
    save_accounts(paths.accounts, rows)
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
            """
        )
    restrict_mode(path, 0o600)


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
    if missing:
        raise RuntimeError("cookie header must contain auth_token and ct0")
    return f"auth_token={pieces['auth_token']}; ct0={pieces['ct0']}"


def auth_add_command(args: argparse.Namespace) -> int:
    paths = app_paths(args.home)
    secure_runtime(paths)
    load_config(paths)
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
    asyncio.run(auth_add_async(paths, args.label, cookie_header))
    del raw, auth_token, cookie_header, ct0
    print("X session stored locally with owner-only permissions; cookie values were not printed.")
    return 0


async def auth_stats(paths: Paths, verify_live: bool = False) -> dict[str, Any]:
    try:
        from twscrape import API
    except ImportError:
        return {"installed": False, "accounts": 0, "active_accounts": 0, "live_verified": False}
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
    print(json.dumps(checks, indent=2, sort_keys=True))
    permission_ready = os.name == "nt" or (
        checks["runtime_mode"] == "0o700" and checks["session_mode"] in {None, "0o600"}
    )
    auth_ready = checks["x_sessions"] == 1 and checks["active_x_sessions"] == 1
    if args.live_auth:
        auth_ready = auth_ready and checks["live_auth_verified"] is True
    ready = all([
        checks["initialized"], checks["registry"], checks["configured_accounts"] > 0,
        checks["twscrape_installed"], auth_ready,
        checks["terms_risk_acknowledged"], permission_ready,
    ])
    return 0 if ready else 2


class Provider(Protocol):
    async def user_posts(self, username: str, limit: int) -> AsyncIterator[Any]: ...


class TwscrapeProvider:
    def __init__(self, session_db: Path):
        try:
            from twscrape import API
        except ImportError as exc:
            raise RuntimeError("twscrape missing; install requirements.txt first") from exc
        self.api = API(str(session_db), raise_when_no_account=True, wait_timeout=30)
        if session_db.exists():
            restrict_mode(session_db, 0o600)

    async def user_posts(self, username: str, limit: int) -> AsyncIterator[Any]:
        user = await self.api.user_by_login(username)
        if user is None:
            raise RuntimeError("account not found")
        async for post in self.api.user_tweets(user.id, limit=limit):
            yield post


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
    errors: list[dict[str, str]] = []
    new_count = duplicate_count = filtered_count = rejected_count = 0
    allowed_usernames = {account["username"].lower() for account in selected}
    init_state(paths.state_db)

    effective_concurrency = max(1, min(int(concurrency), 4))
    semaphore = asyncio.Semaphore(effective_concurrency)

    async def fetch_one(account: dict[str, Any]) -> tuple[str, list[Any], str | None]:
        username = account["username"]
        try:
            posts: list[Any] = []
            async with semaphore:
                async for post in provider.user_posts(username, limit):
                    posts.append(post)
            return username, posts, None
        except Exception as exc:
            return username, [], f"{type(exc).__name__}: {str(exc)[:180]}"

    fetched = await asyncio.gather(*(fetch_one(account) for account in selected))

    with sqlite3.connect(paths.state_db) as db, posts_path.open("w", encoding="utf-8") as out:
        for username, posts, error in fetched:
            if error:
                errors.append({"username": username, "error": error})
                db.execute(
                    "INSERT INTO account_health(username,last_success_at,last_error) VALUES(?,NULL,?) "
                    "ON CONFLICT(username) DO UPDATE SET last_error=excluded.last_error",
                    (username, error),
                )
                db.commit()
                continue

            for post in posts:
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
        "new_posts": new_count,
        "duplicate_posts": duplicate_count,
        "filtered_posts": filtered_count,
        "rejected_unallowlisted_posts": rejected_count,
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


def scan_command(args: argparse.Namespace) -> int:
    paths = app_paths(args.home)
    secure_runtime(paths)
    config = load_config(paths)
    if not config.get("acknowledge_x_terms_risk"):
        raise RuntimeError("live scan disabled; rerun init with --acknowledge-x-terms-risk")
    accounts = load_accounts(paths.accounts, enabled_only=True)
    only = {u.lstrip("@").lower() for u in (args.account or [])} or None
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
            provider, accounts, paths, hours, limit, only,
            concurrency=int(config.get("concurrency", 2)),
            include_quotes=bool(config.get("include_quotes", True)),
            include_replies=bool(config.get("include_replies", False)),
            include_reposts=bool(config.get("include_reposts", False)),
        )
    )
    print(json.dumps({
        "posts": str(posts), "manifest": str(manifest_path),
        "new_posts": manifest["new_posts"],
        "reached_accounts": len(manifest["reached_accounts"]),
        "failed_accounts": len(manifest["failed_accounts"]),
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

    auth = sub.add_parser("auth-add", help="securely register one local X cookie session")
    auth.add_argument("--label", default="main")
    auth.set_defaults(func=auth_add_command)

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

    scan = sub.add_parser("scan", help="collect recent raw public posts")
    scan.add_argument("--hours", type=int, help="lookback hours; defaults to local config")
    scan.add_argument("--limit", type=int, help="maximum posts requested per account; defaults to local config")
    scan.add_argument("--account", action="append", help="restrict scan to an enabled account; repeatable")
    scan.set_defaults(func=scan_command)

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
