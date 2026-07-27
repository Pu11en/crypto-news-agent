"""Xquik API client — fetches recent tweets for a list of accounts.

Reconstructed from the project's git history (the original fetcher that
powered the crypto-intel pipeline). Auth is an x-api-key header; the
search endpoint is scoped with `from:USERNAME since:YYYY-MM-DD`.

Concurrency: callers may fan out across accounts; this client is
thread-safe (one fresh Session per process, no shared mutable state).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

import requests

log = logging.getLogger("agent.xquik")

XQUIK_BASE = "https://xquik.com/api/v1"
_REQUEST_TIMEOUT = 20.0
_MAX_RETRIES = 4


@dataclass
class RawTweet:
    tweet_id: str
    username: str
    text: str
    created_at: datetime | None
    url: str | None = None


# Xquik / Twitter return dates in a variety of formats — try them all.
_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%a %b %d %H:%M:%S %z %Y",
    "%Y-%m-%d %H:%M:%S",
]


def _parse_date(raw) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    s = str(raw).strip().replace("Z", "+00:00")
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class XquikError(Exception):
    pass


class XquikAuthError(XquikError):
    pass


class XquikClient:
    def __init__(self, api_key: str, base_url: str = XQUIK_BASE):
        if not api_key:
            raise ValueError("Xquik API key is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def _request(self, method: str, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        backoff = 1.0
        last_err = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._session.request(
                    method, url, params=params, timeout=_REQUEST_TIMEOUT
                )
            except requests.RequestException as e:
                last_err = e
                log.warning("xquik network error attempt=%s err=%s", attempt, e)
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", backoff))
                log.warning("xquik 429 rate-limited, sleeping %.1fs", retry_after)
                time.sleep(retry_after)
                backoff *= 2
                continue
            if resp.status_code == 401:
                raise XquikAuthError("Xquik 401 — check XQUIK_API_KEY")
            if resp.status_code == 402:
                raise XquikAuthError("Xquik 402 — billing/credits required")
            if 500 <= resp.status_code < 600:
                last_err = f"HTTP {resp.status_code}"
                log.warning("xquik %s — backing off", resp.status_code)
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code != 200:
                raise XquikError(
                    f"Xquik {method} {path} failed: {resp.status_code} {resp.text[:200]}"
                )
            try:
                return resp.json()
            except ValueError:
                raise XquikError(f"Xquik non-JSON response: {resp.text[:200]}")

        raise XquikError(f"Xquik gave up after {_MAX_RETRIES} retries: {last_err}")

    def fetch_recent_tweets(
        self, username: str, hours: int, limit: int
    ) -> list[RawTweet]:
        """Fetch tweets from one account within the last `hours` hours."""
        username = username.lstrip("@")
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = f"from:{username} since:{since.strftime('%Y-%m-%d')}"
        params = {"q": query, "limit": limit}
        data = self._request("GET", "/x/tweets/search", params)
        rows = data.get("tweets") or data.get("data") or []
        return list(self._parse(username, rows, hours))

    def get_credits(self) -> dict:
        """Fetch account credit balance + lifetime stats.

        Returns the parsed `/credits` response:
            balance (str), lifetime_purchased (str), lifetime_used (str),
            auto_topup_enabled (bool), auto_topup_threshold (str),
            auto_topup_amount_dollars (int)
        Values are strings because Xquik returns them that way (big ints).
        """
        return self._request("GET", "/credits")

    def get_account(self) -> dict:
        """Fetch the full account summary (includes creditInfo + monitorBilling).

        Heavier than get_credits() — use for the detailed /account view.
        """
        return self._request("GET", "/account")

    def _parse(
        self, username: str, rows: list[dict], hours: int
    ) -> Iterator[RawTweet]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        for row in rows:
            tweet_id = str(
                row.get("id") or row.get("tweetId") or ""
            ).strip()
            if not tweet_id:
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            created = _parse_date(
                row.get("createdAt") or row.get("created_at") or row.get("timestamp")
            )
            # Drop anything older than the freshness window (server may return more).
            if created and created < cutoff:
                continue
            url = row.get("url") or f"https://x.com/{username}/status/{tweet_id}"
            yield RawTweet(
                tweet_id=tweet_id,
                username=username,
                text=text,
                created_at=created,
                url=url,
            )
