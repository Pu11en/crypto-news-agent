from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "crypto_signal_scan.py"
spec = importlib.util.spec_from_file_location("crypto_signal_scan", SCRIPT)
scan = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = scan
assert spec.loader
spec.loader.exec_module(scan)


class FakeProvider:
    def __init__(self, posts):
        self.posts = posts

    async def user_posts(self, username, limit):
        for post in self.posts.get(username, [])[:limit]:
            yield post


class PartialFailureProvider(FakeProvider):
    def __init__(self, posts, failures):
        super().__init__(posts)
        self.failures = set(failures)

    async def user_posts(self, username, limit):
        if username in self.failures:
            raise RuntimeError("simulated provider failure")
        for post in self.posts.get(username, [])[:limit]:
            yield post


class ConcurrencyProvider(FakeProvider):
    def __init__(self, posts):
        super().__init__(posts)
        self.active = 0
        self.max_active = 0

    async def user_posts(self, username, limit):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            for post in self.posts.get(username, [])[:limit]:
                yield post
        finally:
            self.active -= 1


def fake_post(post_id="100", username="lookonchain", *, reply=False, quote=False, repost=False):
    nested = lambda suffix: SimpleNamespace(id=int(f"{post_id}{suffix}"))
    return SimpleNamespace(
        id=int(post_id),
        url=f"https://x.com/{username}/status/{post_id}",
        date=datetime.now(timezone.utc),
        user=SimpleNamespace(id=42, username=username),
        rawContent="Public source post",
        inReplyToTweetId=77 if reply else None,
        quotedTweet=nested(1) if quote else None,
        retweetedTweet=nested(2) if repost else None,
        links=[SimpleNamespace(url="https://example.com/source")],
        media=SimpleNamespace(photos=[], videos=[], animated=[]),
    )


class CryptoSignalScanTests(unittest.TestCase):
    def test_bundled_registry_has_77_unique_accounts(self):
        rows = scan.load_accounts(scan.DEFAULT_ACCOUNTS)
        self.assertEqual(77, len(rows))
        self.assertEqual(77, len({r["username"].lower() for r in rows}))
        self.assertEqual(76, sum(r["enabled"] for r in rows))
        self.assertFalse(next(r for r in rows if r["username"] == "metrika_co")["enabled"])

    def test_cookie_parser_keeps_only_required_values(self):
        value = scan.parse_cookie_header("other=nope; auth_token=abc; ct0=xyz; extra=drop")
        self.assertEqual("auth_token=abc; ct0=xyz", value)
        with self.assertRaises(RuntimeError):
            scan.parse_cookie_header("ct0=xyz")

    def test_browser_cookie_table_parser_keeps_only_required_values(self):
        table = (
            "__cf_bm\tignored-value\t.x.com\t/\n"
            "auth_token\ttest-token\t.x.com\t/\n"
            "ct0\ttest-csrf\t.x.com\t/\n"
            "twid\tignored-user\t.x.com\t/\n"
        )
        self.assertEqual(
            "auth_token=test-token; ct0=test-csrf",
            scan.parse_browser_cookie_table(table),
        )

    def test_clipboard_auth_clears_clipboard_and_never_prompts(self):
        table = "auth_token\ttest-token\t.x.com\t/\nct0\ttest-csrf\t.x.com\t/\n"
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            scan, "clipboard_text", return_value=table
        ), patch.object(scan, "clear_clipboard") as cleared, patch.object(
            scan.getpass, "getpass"
        ) as prompt:
            scan.init_command(SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=True))
            self.assertEqual(
                0,
                scan.auth_add_command(SimpleNamespace(home=tmp, label="main", clipboard=True)),
            )
            cleared.assert_called_once()
            prompt.assert_not_called()

    def test_auth_add_accepts_named_cookie_values_without_double_prefix(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            scan.getpass, "getpass", side_effect=["auth_token=abc", "ct0=xyz"]
        ), patch.object(scan.sys.stdin, "isatty", return_value=True), patch.object(
            scan.sys.stderr, "isatty", return_value=True
        ):
            scan.init_command(SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=True))
            result = scan.auth_add_command(SimpleNamespace(home=tmp, label="main"))
            self.assertEqual(0, result)
            from twscrape import API
            async def stored():
                return await API(str(scan.app_paths(tmp).session_db)).pool.get_all()
            rows = asyncio.run(stored())
            self.assertEqual("abc", rows[0].cookies["auth_token"])
            self.assertEqual("xyz", rows[0].cookies["ct0"])

    def test_auth_add_replaces_prior_session_instead_of_pooling(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = scan.app_paths(tmp)
            scan.secure_runtime(paths)
            asyncio.run(scan.auth_add_async(paths, "first", "auth_token=one; ct0=one"))
            asyncio.run(scan.auth_add_async(paths, "second", "auth_token=two; ct0=two"))
            stats = asyncio.run(scan.auth_stats(paths))
            self.assertEqual(1, stats["accounts"])
            self.assertEqual(1, stats["active_accounts"])

    def test_init_can_add_risk_acknowledgement_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=False)
            scan.init_command(first)
            self.assertFalse(scan.load_config(scan.app_paths(tmp))["acknowledge_x_terms_risk"])
            second = SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=True)
            scan.init_command(second)
            self.assertTrue(scan.load_config(scan.app_paths(tmp))["acknowledge_x_terms_risk"])

    def test_account_registry_add_disable_and_validate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = scan.app_paths(tmp)
            args = SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=True)
            self.assertEqual(0, scan.init_command(args))
            with paths.accounts.open(newline="", encoding="utf-8") as f:
                self.assertEqual(77, len(list(csv.DictReader(f))))
            add = SimpleNamespace(
                home=tmp, accounts_action="add", username="new_source",
                tier="high", tags="news,bitcoin",
            )
            self.assertEqual(0, scan.accounts_command(add))
            disable = SimpleNamespace(home=tmp, accounts_action="disable", username="new_source")
            self.assertEqual(0, scan.accounts_command(disable))
            rows = scan.load_accounts(paths.accounts)
            added = next(r for r in rows if r["username"] == "new_source")
            self.assertFalse(added["enabled"])
            self.assertEqual("news|bitcoin", added["tags"])

    def test_twscrape_provider_enforces_exact_limit(self):
        class FakeApi:
            async def user_by_login(self, username):
                return SimpleNamespace(id=42)

            async def user_tweets(self, user_id, limit):
                for index in range(12):
                    yield index

        provider = object.__new__(scan.TwscrapeProvider)
        provider.api = FakeApi()

        async def gather():
            return [post async for post in provider.user_posts("lookonchain", 5)]

        self.assertEqual([0, 1, 2, 3, 4], asyncio.run(gather()))

    def test_collection_emits_raw_schema_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(scan.metadata, "version", return_value="0.20.1"):
            paths = scan.app_paths(tmp)
            scan.secure_runtime(paths)
            scan.atomic_json(paths.config, scan.default_config(True))
            paths.accounts.write_text(
                "username,tier,tags,enabled\nlookonchain,critical,whales,true\n",
                encoding="utf-8",
            )
            accounts = scan.load_accounts(paths.accounts, enabled_only=True)
            provider = FakeProvider({"lookonchain": [fake_post()]})
            posts_path, manifest_path, manifest = asyncio.run(
                scan.collect(provider, accounts, paths, hours=24, limit=5)
            )
            records = [json.loads(line) for line in posts_path.read_text().splitlines()]
            self.assertEqual(1, len(records))
            self.assertEqual("100", records[0]["post_id"])
            self.assertEqual("lookonchain", records[0]["username"])
            self.assertEqual("Public source post", records[0]["text"])
            self.assertTrue(manifest_path.exists())
            self.assertEqual(1, manifest["new_posts"])

            second_posts, _, second = asyncio.run(
                scan.collect(provider, accounts, paths, hours=24, limit=5)
            )
            self.assertEqual("", second_posts.read_text())
            self.assertEqual(0, second["new_posts"])
            self.assertEqual(1, second["duplicate_posts"])

    def test_collection_rejects_unallowlisted_author_at_write_boundary(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(scan.metadata, "version", return_value="0.20.1"):
            paths = scan.app_paths(tmp)
            scan.secure_runtime(paths)
            account = {"username": "lookonchain", "tier": "critical", "tags": "whales", "enabled": True}
            provider = FakeProvider({"lookonchain": [fake_post("150", username="promoted_user")]})
            posts_path, _, manifest = asyncio.run(
                scan.collect(provider, [account], paths, hours=24, limit=5)
            )
            self.assertEqual("", posts_path.read_text())
            self.assertEqual(1, manifest["rejected_unallowlisted_posts"])

    def test_default_policy_filters_replies_and_reposts_but_keeps_quotes(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(scan.metadata, "version", return_value="0.20.1"):
            paths = scan.app_paths(tmp)
            scan.secure_runtime(paths)
            account = {"username": "lookonchain", "tier": "critical", "tags": "whales", "enabled": True}
            provider = FakeProvider({"lookonchain": [
                fake_post("201", reply=True),
                fake_post("202", repost=True),
                fake_post("203", quote=True),
            ]})
            posts_path, _, manifest = asyncio.run(
                scan.collect(provider, [account], paths, hours=24, limit=5)
            )
            records = [json.loads(line) for line in posts_path.read_text().splitlines()]
            self.assertEqual(["203"], [record["post_id"] for record in records])
            self.assertEqual(2, manifest["filtered_posts"])
            self.assertEqual(2, manifest["concurrency"])

    def test_scan_process_lock_rejects_overlapping_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "scan.lock"
            with scan.scan_process_lock(lock_path):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with scan.scan_process_lock(lock_path):
                        pass

    def test_earliest_retry_time_is_order_independent(self):
        errors = [
            {"retry_after": "2026-09-01T00:48:27+00:00"},
            {"retry_after": None},
            {"retry_after": "2026-09-01T00:32:50+00:00"},
        ]
        self.assertEqual(
            "2026-09-01T00:32:50+00:00", scan.earliest_retry_after(errors)
        )

    def test_round_robin_batch_persists_across_restarts(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = scan.app_paths(tmp)
            scan.secure_runtime(paths)
            accounts = [
                {"username": "first"},
                {"username": "second"},
                {"username": "third"},
            ]
            selected, cursor = scan.next_account_batch(paths.state_db, accounts, 1)
            self.assertEqual(["first"], [item["username"] for item in selected])
            self.assertEqual(0, cursor)
            self.assertEqual(1, scan.advance_account_cursor(paths.state_db, cursor, 1))

            restarted_paths = scan.app_paths(tmp)
            selected, cursor = scan.next_account_batch(restarted_paths.state_db, accounts, 1)
            self.assertEqual(["second"], [item["username"] for item in selected])
            self.assertEqual(1, cursor)
            scan.advance_account_cursor(restarted_paths.state_db, cursor, 2)
            selected, cursor = scan.next_account_batch(restarted_paths.state_db, accounts, 1)
            self.assertEqual(["first"], [item["username"] for item in selected])
            self.assertEqual(3, cursor)

    def test_scan_command_rejects_multi_account_automatic_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            scan.init_command(SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=True))
            paths = scan.app_paths(tmp)
            config = scan.load_config(paths)
            config["accounts_per_scan"] = 2
            scan.atomic_json(paths.config, config)
            args = SimpleNamespace(
                home=tmp, account=None, all_accounts=False, hours=24, limit=1
            )
            with self.assertRaisesRegex(RuntimeError, "must be 1"):
                scan.scan_command(args)

    def test_scan_command_advances_default_round_robin_after_success(self):
        async def ready_session(paths):
            return {"accounts": 1, "active_accounts": 1}

        with tempfile.TemporaryDirectory() as tmp, patch.object(scan.metadata, "version", return_value="0.20.1"):
            scan.init_command(SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=True))
            paths = scan.app_paths(tmp)
            accounts = scan.load_accounts(paths.accounts, enabled_only=True)
            posts = {
                account["username"]: [fake_post(str(6000 + index), username=account["username"])]
                for index, account in enumerate(accounts[:2])
            }
            args = SimpleNamespace(
                home=tmp, account=None, all_accounts=False, hours=24, limit=1
            )
            with patch.object(scan, "auth_stats", side_effect=ready_session), patch.object(
                scan, "TwscrapeProvider", return_value=FakeProvider(posts)
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(0, scan.scan_command(args))
                self.assertEqual(0, scan.scan_command(args))
            selected, cursor = scan.next_account_batch(paths.state_db, accounts, 1)
            self.assertEqual(2, cursor)
            self.assertEqual(accounts[2]["username"], selected[0]["username"])

    def test_scan_command_does_not_advance_rotation_on_rate_limit(self):
        async def ready_session(paths):
            return {"accounts": 1, "active_accounts": 1}

        retry_after = datetime.now(timezone.utc) + timedelta(minutes=15)

        class RateLimitedProvider:
            async def user_posts(self, username, limit):
                if False:
                    yield None
                raise scan.ProviderRateLimited("UserTweets", retry_after)

        with tempfile.TemporaryDirectory() as tmp:
            scan.init_command(SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=True))
            paths = scan.app_paths(tmp)
            accounts = scan.load_accounts(paths.accounts, enabled_only=True)
            args = SimpleNamespace(
                home=tmp, account=None, all_accounts=False, hours=24, limit=1
            )
            with patch.object(scan, "auth_stats", side_effect=ready_session), patch.object(
                scan, "TwscrapeProvider", return_value=RateLimitedProvider()
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(3, scan.scan_command(args))
            selected, cursor = scan.next_account_batch(paths.state_db, accounts, 1)
            self.assertEqual(0, cursor)
            self.assertEqual(accounts[0]["username"], selected[0]["username"])

    def test_twenty_five_repeated_runs_remain_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(scan.metadata, "version", return_value="0.20.1"):
            paths = scan.app_paths(tmp)
            scan.secure_runtime(paths)
            account = {"username": "lookonchain", "tier": "critical", "tags": "whales", "enabled": True}
            posts = [fake_post(str(1000 + index)) for index in range(20)]
            run_dirs = set()
            for iteration in range(25):
                posts_path, _, manifest = asyncio.run(
                    scan.collect(FakeProvider({"lookonchain": posts}), [account], paths, hours=24, limit=20)
                )
                run_dirs.add(posts_path.parent)
                if iteration == 0:
                    self.assertEqual(20, manifest["new_posts"])
                    self.assertEqual(20, len(posts_path.read_text().splitlines()))
                else:
                    self.assertEqual(0, manifest["new_posts"])
                    self.assertEqual(20, manifest["duplicate_posts"])
                    self.assertEqual("", posts_path.read_text())
            self.assertEqual(25, len(run_dirs))
            with sqlite3.connect(paths.state_db) as db:
                self.assertEqual(20, db.execute("SELECT COUNT(*) FROM seen_posts").fetchone()[0])

    def test_restart_preserves_deduplication_and_health(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(scan.metadata, "version", return_value="0.20.1"):
            first_paths = scan.app_paths(tmp)
            scan.secure_runtime(first_paths)
            account = {"username": "lookonchain", "tier": "critical", "tags": "whales", "enabled": True}
            provider = FakeProvider({"lookonchain": [fake_post("3001")]})
            asyncio.run(scan.collect(provider, [account], first_paths, hours=24, limit=5))

            restarted_paths = scan.app_paths(tmp)
            posts_path, _, manifest = asyncio.run(
                scan.collect(FakeProvider({"lookonchain": [fake_post("3001")]}), [account], restarted_paths, hours=24, limit=5)
            )
            self.assertEqual(0, manifest["new_posts"])
            self.assertEqual(1, manifest["duplicate_posts"])
            self.assertEqual("", posts_path.read_text())
            with sqlite3.connect(restarted_paths.state_db) as db:
                health = db.execute(
                    "SELECT last_success_at,last_error FROM account_health WHERE username='lookonchain'"
                ).fetchone()
            self.assertIsNotNone(health[0])
            self.assertIsNone(health[1])

    def test_partial_failure_preserves_success_and_error_health(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(scan.metadata, "version", return_value="0.20.1"):
            paths = scan.app_paths(tmp)
            scan.secure_runtime(paths)
            accounts = [
                {"username": "lookonchain", "tier": "critical", "tags": "whales", "enabled": True},
                {"username": "whale_alert", "tier": "critical", "tags": "whales", "enabled": True},
            ]
            provider = PartialFailureProvider(
                {"lookonchain": [fake_post("4001")]}, failures={"whale_alert"}
            )
            posts_path, _, manifest = asyncio.run(
                scan.collect(provider, accounts, paths, hours=24, limit=5)
            )
            self.assertEqual(["lookonchain"], manifest["reached_accounts"])
            self.assertEqual("whale_alert", manifest["failed_accounts"][0]["username"])
            self.assertEqual(1, len(posts_path.read_text().splitlines()))
            with sqlite3.connect(paths.state_db) as db:
                failure = db.execute(
                    "SELECT last_error FROM account_health WHERE username='whale_alert'"
                ).fetchone()[0]
            self.assertIn("simulated provider failure", failure)

    def test_adapter_distinguishes_rate_lock_from_unusable_session(self):
        class Pool:
            def __init__(self, rows):
                self.rows = rows

            async def get_all(self):
                return self.rows

        provider = object.__new__(scan.TwscrapeProvider)
        retry_after = datetime.now(timezone.utc) + timedelta(minutes=15)
        provider.api = SimpleNamespace(
            pool=Pool([SimpleNamespace(active=True, locks={"UserTweets": retry_after})])
        )
        error = asyncio.run(provider.availability_error("UserTweets"))
        self.assertIsInstance(error, scan.ProviderRateLimited)
        self.assertEqual(retry_after, error.retry_after)

        for unusable_row in (
            SimpleNamespace(active=False, locks={}),
            SimpleNamespace(
                active=True,
                locks={"UserTweets": datetime.now(timezone.utc) - timedelta(seconds=1)},
            ),
        ):
            provider.api = SimpleNamespace(pool=Pool([unusable_row]))
            error = asyncio.run(provider.availability_error("UserTweets"))
            self.assertIsInstance(error, scan.ProviderSessionUnavailable)
            self.assertNotIsInstance(error, scan.ProviderRateLimited)
            self.assertIn("doctor --live-auth", str(error))

    def test_rate_limit_failure_reports_retry_time(self):
        retry_after = datetime.now(timezone.utc) + timedelta(minutes=15)

        class RateLimitedProvider:
            async def user_posts(self, username, limit):
                if False:
                    yield None
                raise scan.ProviderRateLimited("UserTweets", retry_after)

        with tempfile.TemporaryDirectory() as tmp:
            paths = scan.app_paths(tmp)
            scan.secure_runtime(paths)
            account = {"username": "lookonchain", "tier": "critical", "tags": "whales", "enabled": True}
            _, _, manifest = asyncio.run(
                scan.collect(RateLimitedProvider(), [account], paths, hours=24, limit=5)
            )
            error = manifest["failed_accounts"][0]
            self.assertEqual("lookonchain", error["username"])
            self.assertTrue(error["retryable"])
            self.assertEqual(scan.iso(retry_after), error["retry_after"])
            self.assertIn("UserTweets", error["error"])

    def test_concurrency_never_exceeds_configured_bound(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(scan.metadata, "version", return_value="0.20.1"):
            paths = scan.app_paths(tmp)
            scan.secure_runtime(paths)
            accounts = [
                {"username": f"source_{index}", "tier": "medium", "tags": "test", "enabled": True}
                for index in range(8)
            ]
            posts = {
                account["username"]: [fake_post(str(5000 + index), username=account["username"])]
                for index, account in enumerate(accounts)
            }
            provider = ConcurrencyProvider(posts)
            _, _, manifest = asyncio.run(
                scan.collect(provider, accounts, paths, hours=24, limit=1, concurrency=2)
            )
            self.assertEqual(2, provider.max_active)
            self.assertEqual(2, manifest["concurrency"])
            self.assertEqual(8, len(manifest["reached_accounts"]))

    def test_non_tty_auth_fails_before_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            scan.init_command(SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=True))
            with patch.object(scan.sys.stdin, "isatty", return_value=False), patch.object(
                scan.getpass, "getpass"
            ) as prompt:
                with self.assertRaisesRegex(RuntimeError, "interactive local terminal"):
                    scan.auth_add_command(SimpleNamespace(home=tmp, label="main"))
            prompt.assert_not_called()

    def test_pre_auth_doctor_is_ready_without_creating_session_db(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()) as output:
            scan.init_command(SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=True))
            paths = scan.app_paths(tmp)
            self.assertFalse(paths.session_db.exists())
            self.assertEqual(0, scan.doctor_command(SimpleNamespace(home=tmp, live_auth=False)))
            self.assertFalse(paths.session_db.exists())
            report = json.loads(output.getvalue().split("Initialized", 1)[-1].split("\n", 1)[-1])
            self.assertEqual("pre-auth", report["readiness_stage"])
            self.assertTrue(report["ready"])

    def test_configure_updates_validated_defaults(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            scan.init_command(SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=True))
            args = SimpleNamespace(
                home=tmp, lookback_hours=12, per_account_limit=50, post_types="all"
            )
            self.assertEqual(0, scan.configure_command(args))
            config = scan.load_config(scan.app_paths(tmp))
            self.assertEqual(12, config["lookback_hours"])
            self.assertEqual(50, config["per_account_limit"])
            self.assertTrue(config["include_replies"])
            self.assertTrue(config["include_reposts"])

    def test_auth_remove_physically_deletes_session_store(self):
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            paths = scan.app_paths(tmp)
            scan.secure_runtime(paths)
            asyncio.run(scan.auth_add_async(paths, "main", "auth_token=one; ct0=two"))
            self.assertTrue(paths.session_db.exists())
            self.assertEqual(0, scan.auth_remove_command(SimpleNamespace(home=tmp, yes=True)))
            self.assertFalse(paths.session_db.exists())

    def test_cycle_advances_accounts_and_publishes_latest(self):
        async def ready_session(paths):
            return {"accounts": 1, "active_accounts": 1}

        with tempfile.TemporaryDirectory() as tmp, patch.object(scan.metadata, "version", return_value="0.20.1"):
            scan.init_command(SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=True))
            paths = scan.app_paths(tmp)
            accounts = scan.load_accounts(paths.accounts, enabled_only=True)
            posts = {
                account["username"]: [fake_post(str(7000 + index), username=account["username"])]
                for index, account in enumerate(accounts[:2])
            }
            args = SimpleNamespace(
                home=tmp, hours=24, limit=1, max_runtime_seconds=60, max_accounts=2
            )
            with patch.object(scan, "auth_stats", side_effect=ready_session), patch.object(
                scan, "TwscrapeProvider", return_value=FakeProvider(posts)
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(0, scan.cycle_command(args))
            latest = json.loads((paths.output / "latest.json").read_text())
            combined = Path(latest["combined"]).read_text().splitlines()
            self.assertEqual(2, len(combined))
            _, cursor = scan.next_account_batch(paths.state_db, accounts, 1)
            self.assertEqual(2, cursor)

    def test_registry_search_queries_every_enabled_account_in_batches(self):
        class SearchProvider:
            async def search_posts(self, query, limit):
                for index, username in enumerate(("alpha", "beta", "gamma")):
                    if f"from:{username}" in query:
                        yield fake_post(str(8100 + index), username=username)

        with tempfile.TemporaryDirectory() as tmp:
            paths = scan.app_paths(tmp)
            scan.secure_runtime(paths)
            accounts = [
                {"username": name, "tier": "high", "tags": [], "enabled": True}
                for name in ("alpha", "beta", "gamma")
            ]
            posts, manifest_path, manifest = asyncio.run(scan.collect_registry_search(
                SearchProvider(), accounts, paths,
                hours=24, per_account_limit=5, batch_size=2, max_runtime_seconds=60,
                include_quotes=True, include_replies=False, include_reposts=False,
            ))
            self.assertTrue(manifest["full_registry_pass"])
            self.assertEqual(3, manifest["queried_accounts"])
            self.assertEqual(2, len(manifest["batches"]))
            self.assertEqual(3, len(posts.read_text().splitlines()))
            self.assertTrue(manifest_path.exists())
            self.assertTrue((paths.output / "latest.json").exists())
            with redirect_stdout(io.StringIO()) as shown:
                self.assertEqual(0, scan.show_command(SimpleNamespace(home=tmp, limit=2)))
            display = json.loads(shown.getvalue())
            self.assertEqual(2, len(display["posts"]))
            self.assertEqual(3, display["queried_accounts"])

            repeated, _, repeated_manifest = asyncio.run(scan.collect_registry_search(
                SearchProvider(), accounts, paths,
                hours=24, per_account_limit=5, batch_size=2, max_runtime_seconds=60,
                include_quotes=True, include_replies=False, include_reposts=False,
            ))
            self.assertEqual(3, repeated_manifest["eligible_posts"])
            self.assertEqual(0, repeated_manifest["new_posts"])
            self.assertEqual(3, repeated_manifest["duplicate_posts"])
            self.assertEqual(3, len(repeated.read_text().splitlines()))
            latest = json.loads((paths.output / "latest.json").read_text())
            self.assertEqual([], Path(latest["new"]).read_text().splitlines())
            with redirect_stdout(io.StringIO()) as all_shown:
                scan.show_command(SimpleNamespace(home=tmp, limit=100, scope="all"))
            self.assertEqual(3, len(json.loads(all_shown.getvalue())["posts"]))
            with redirect_stdout(io.StringIO()) as new_shown:
                scan.show_command(SimpleNamespace(home=tmp, limit=100, scope="new"))
            self.assertEqual(0, len(json.loads(new_shown.getvalue())["posts"]))

    def test_registry_search_reports_unqueried_accounts_on_rate_limit(self):
        retry = datetime.now(timezone.utc) + timedelta(minutes=10)

        class LimitedSearch:
            calls = 0
            async def search_posts(self, query, limit):
                self.calls += 1
                if self.calls > 1:
                    raise scan.ProviderRateLimited("SearchTimeline", retry)
                yield fake_post("8200", username="alpha")

        with tempfile.TemporaryDirectory() as tmp:
            paths = scan.app_paths(tmp)
            scan.secure_runtime(paths)
            accounts = [
                {"username": name, "tier": "high", "tags": [], "enabled": True}
                for name in ("alpha", "beta", "gamma")
            ]
            _, _, manifest = asyncio.run(scan.collect_registry_search(
                LimitedSearch(), accounts, paths,
                hours=24, per_account_limit=5, batch_size=2, max_runtime_seconds=1,
                include_quotes=True, include_replies=False, include_reposts=False,
            ))
            self.assertFalse(manifest["full_registry_pass"])
            self.assertEqual(2, manifest["queried_accounts"])
            self.assertEqual(["gamma"], manifest["unqueried_usernames"])
            self.assertEqual("retry_after_beyond_deadline", manifest["stop_reason"])

    def test_cycle_holds_cursor_when_retry_exceeds_deadline(self):
        async def ready_session(paths):
            return {"accounts": 1, "active_accounts": 1}

        retry_after = datetime.now(timezone.utc) + timedelta(minutes=15)

        class Limited:
            async def user_posts(self, username, limit):
                if False:
                    yield None
                raise scan.ProviderRateLimited("UserTweets", retry_after)

        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            scan.init_command(SimpleNamespace(home=tmp, force=False, acknowledge_x_terms_risk=True))
            paths = scan.app_paths(tmp)
            args = SimpleNamespace(
                home=tmp, hours=24, limit=1, max_runtime_seconds=1, max_accounts=1
            )
            with patch.object(scan, "auth_stats", side_effect=ready_session), patch.object(
                scan, "TwscrapeProvider", return_value=Limited()
            ):
                self.assertEqual(3, scan.cycle_command(args))
            accounts = scan.load_accounts(paths.accounts, enabled_only=True)
            _, cursor = scan.next_account_batch(paths.state_db, accounts, 1)
            self.assertEqual(0, cursor)


if __name__ == "__main__":
    unittest.main()
