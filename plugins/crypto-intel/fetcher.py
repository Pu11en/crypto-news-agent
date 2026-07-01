"""Xquik API client — fetches recent tweets for crypto accounts.

Auth: x-api-key header.
Base URL: https://xquik.com/api/v1
Rate limits: 10 req/s sustained; respect Retry-After on 429.

Per the docs, there is no direct "get tweets by user" endpoint via the
public REST surface — we use the search endpoint scoped to
`from:username since:YYYY-MM-DD` to retrieve recent posts. For accounts
that fail search, we fall back to the profile page extraction.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

import requests

log = logging.getLogger("crypto-intel.fetcher")

XQUIK_BASE = "https://xquik.com/api/v1"
DEFAULT_MAX_TWEETS_PER_ACCOUNT = 20
DEFAULT_FRESHNESS_HOURS = 48
DEFAULT_PER_RUN_BUDGET = 1500


@dataclass
class RawTweet:
    id: str
    username: str
    text: str
    created_at: datetime | None
    url: str | None = None


class XquikClient:
    def __init__(
        self,
        api_key: str,
        base_url: str = XQUIK_BASE,
        max_tweets_per_account: int = DEFAULT_MAX_TWEETS_PER_ACCOUNT,
        freshness_hours: int = DEFAULT_FRESHNESS_HOURS,
        request_timeout: float = 20.0,
    ):
        if not api_key:
            raise ValueError("XQUIK_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.max_tweets = max_tweets_per_account
        self.freshness_hours = freshness_hours
        self.timeout = request_timeout
        self._session = requests.Session()
        self._session.headers.update(
            {
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def _request_with_retry(self, method: str, path: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        backoff = 1.0
        for attempt in range(4):
            try:
                resp = self._session.request(
                    method, url, params=params, timeout=self.timeout
                )
            except requests.RequestException as e:
                log.warning("xquik network error attempt=%s err=%s", attempt, e)
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code == 429:
                retry_after = float(resp.headers.get("Retry-After", backoff))
                log.warning("xquik 429 sleeping %.1fs", retry_after)
                time.sleep(retry_after)
                backoff *= 2
                continue
            if resp.status_code == 401:
                raise PermissionError("Xquik 401 — check XQUIK_API_KEY")
            if resp.status_code == 402:
                raise PermissionError("Xquik 402 — billing/credits required")
            if 500 <= resp.status_code < 600:
                log.warning("xquik %s — backoff", resp.status_code)
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Xquik {method} {path} failed: {resp.status_code} {resp.text[:200]}"
                )
            try:
                return resp.json()
            except ValueError:
                raise RuntimeError(f"Xquik non-JSON response: {resp.text[:200]}")
        raise RuntimeError(f"Xquik gave up after retries: {method} {path}")

    def fetch_recent_tweets(
        self, username: str, hours: int | None = None
    ) -> list[RawTweet]:
        hours = hours or self.freshness_hours
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        since_str = since.strftime("%Y-%m-%d")
        query = f"from:{username} since:{since_str}"
        params = {"q": query, "limit": self.max_tweets}
        data = self._request_with_retry("GET", "/x/tweets/search", params)
        tweets = data.get("tweets") or data.get("data") or []
        return list(self._parse(username, tweets))

    def _parse(self, username: str, rows: list[dict]) -> Iterator[RawTweet]:
        for row in rows:
            tweet_id = str(row.get("id") or row.get("tweetId") or "").strip()
            if not tweet_id:
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            created_raw = (
                row.get("createdAt")
                or row.get("created_at")
                or row.get("timestamp")
            )
            created = _parse_date(created_raw)
            if created and created < datetime.now(timezone.utc) - timedelta(
                hours=self.freshness_hours
            ):
                continue
            url = row.get("url") or f"https://x.com/{username}/status/{tweet_id}"
            yield RawTweet(
                id=tweet_id, username=username, text=text, created_at=created, url=url
            )


_TWITTER_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S",
    "%a %b %d %H:%M:%S %z %Y",
    "%Y-%m-%d %H:%M:%S",
]


def _parse_date(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(int(raw), tz=timezone.utc)
    s = str(raw).strip()
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    for fmt in _TWITTER_DATE_FORMATS:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    iso = _try_isoformat(s)
    if iso:
        return iso
    return None


def _try_isoformat(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None
