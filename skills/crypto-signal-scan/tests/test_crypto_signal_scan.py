from __future__ import annotations

import asyncio
import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
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
        self.assertTrue(all(r["enabled"] for r in rows))

    def test_cookie_parser_keeps_only_required_values(self):
        value = scan.parse_cookie_header("other=nope; auth_token=abc; ct0=xyz; extra=drop")
        self.assertEqual("auth_token=abc; ct0=xyz", value)
        with self.assertRaises(RuntimeError):
            scan.parse_cookie_header("ct0=xyz")

    def test_auth_add_accepts_named_cookie_values_without_double_prefix(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            scan.getpass, "getpass", side_effect=["auth_token=abc", "ct0=xyz"]
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


if __name__ == "__main__":
    unittest.main()
