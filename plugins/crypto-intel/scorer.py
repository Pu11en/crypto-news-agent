"""Batched LLM scorer — DeepSeek V4 Pro via b.ai.

CRITICAL: reasoning_mode MUST be disabled. The DeepSeek V4 model
defaults to thinking ON, which returns `reasoning_content` on the wire.
The Hermes tool-routing layer echoes assistant messages back on each
turn, but it does not echo `reasoning_content`, so on the second turn
the API returns HTTP 400 "reasoning_content must be passed back".

We score 15-25 tweets per prompt for cost efficiency. The model returns
a JSON array of {id, importance, category, risk_level, summary}.
On invalid JSON we re-prompt once; on second failure we mark each tweet
in the batch with risk_level="unknown" and importance=0.5 as a safe
default.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Iterable

import requests

log = logging.getLogger("crypto-intel.scorer")

BAI_BASE = "https://api.b.ai/v1"
SCORING_MODEL = "deepseek-v4-pro"
BATCH_SIZE = 20

CATEGORIES = (
    "news",
    "whale",
    "hack",
    "scam",
    "defi",
    "regulation",
    "macro",
    "meme",
    "trading",
    "infrastructure",
    "ai_crypto",
    "general",
)
RISK_LEVELS = ("critical", "high", "medium", "low", "unknown")

SYSTEM_PROMPT = """You are a crypto-tweet triage analyst. You will receive
a JSON array of tweets (each with id, username, text, created_at). Score
each one for trading/intel relevance. Reply with a JSON array — one
object per tweet — with exactly these fields:
- id (string, must match input)
- importance (float 0.0-1.0)
- category (one of: news, whale, hack, scam, defi, regulation, macro,
  meme, trading, infrastructure, ai_crypto, general)
- risk_level (one of: critical, high, medium, low, unknown)
- summary (one short sentence, <= 200 chars)

Rules:
- Hack/exploit/rug-pull: risk_level=critical, importance >= 0.85
- Whale transfer > $50M: risk_level=high, importance 0.7-0.9
- Pure retweet / "GM" / meme-only: importance < 0.2
- Empty/super-short or off-topic: importance < 0.1
- Return ONLY the JSON array, no prose, no markdown fences.

The order of returned objects must match the order of input tweets."""


@dataclass
class Score:
    id: str
    importance: float
    category: str
    risk_level: str
    summary: str


class BaiScorer:
    def __init__(
        self,
        api_key: str,
        base_url: str = BAI_BASE,
        model: str = SCORING_MODEL,
        batch_size: int = BATCH_SIZE,
        request_timeout: float = 60.0,
    ):
        if not api_key:
            raise ValueError("BAI_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.batch_size = batch_size
        self.timeout = request_timeout

    def score_batch(self, tweets: list[dict]) -> list[Score]:
        if not tweets:
            return []
        batches = list(_chunks(tweets, self.batch_size))
        all_scores: list[Score] = []
        for batch in batches:
            all_scores.extend(self._score_one_batch(batch))
        return all_scores

    def _score_one_batch(self, batch: list[dict]) -> list[Score]:
        user_payload = [
            {
                "id": str(t.get("id", "")),
                "username": t.get("username", ""),
                "text": (t.get("text") or "")[:1200],
                "created_at": str(t.get("created_at") or ""),
            }
            for t in batch
        ]
        expected_ids = {item["id"] for item in user_payload}

        for attempt in (1, 2):
            raw = self._call_llm(user_payload)
            parsed = _extract_json_array(raw)
            if parsed is not None:
                scores = _normalize_scores(parsed, expected_ids)
                if scores is not None and {s.id for s in scores} >= expected_ids:
                    return scores
            log.warning("scorer attempt=%s invalid json, retrying", attempt)
            time.sleep(0.5 * attempt)

        log.warning("scorer falling back to neutral for %d tweets", len(batch))
        return [
            Score(
                id=item["id"],
                importance=0.5,
                category="general",
                risk_level="unknown",
                summary="(scoring failed)",
            )
            for item in user_payload
        ]

    def _call_llm(self, user_payload: list[dict]) -> str:
        url = f"{self.base_url}/chat/completions"
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "Tweets:\n" + json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
            "stream": False,
        }
        # CRITICAL: disable DeepSeek V4 thinking mode at request level.
        body["extra_body"] = {"thinking": {"type": "disabled"}}
        body["reasoning_effort"] = "low"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        backoff = 1.0
        for attempt in range(3):
            try:
                resp = requests.post(url, json=body, headers=headers, timeout=self.timeout)
            except requests.RequestException as e:
                log.warning("bai network error attempt=%s err=%s", attempt, e)
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", backoff))
                time.sleep(wait)
                backoff *= 2
                continue
            if resp.status_code in (500, 502, 503, 504):
                time.sleep(backoff)
                backoff *= 2
                continue
            if resp.status_code != 200:
                raise RuntimeError(
                    f"bai {resp.status_code}: {resp.text[:300]}"
                )
            data = resp.json()
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as e:
                raise RuntimeError(f"bai unexpected response shape: {data}") from e
        raise RuntimeError("bai: gave up after retries")


def _chunks(items: list, n: int) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i : i + n]


_JSON_FENCE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)


def _extract_json_array(text: str) -> list | None:
    if not text:
        return None
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except (ValueError, TypeError):
        pass
    m = _JSON_FENCE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except ValueError:
            return None
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except ValueError:
            return None
    return None


def _normalize_scores(parsed: list, expected_ids: set[str]) -> list[Score] | None:
    try:
        out: list[Score] = []
        seen = set()
        for row in parsed:
            if not isinstance(row, dict):
                continue
            tid = str(row.get("id") or "").strip()
            if not tid or tid not in expected_ids or tid in seen:
                continue
            seen.add(tid)
            imp = row.get("importance", 0.0)
            try:
                importance = max(0.0, min(1.0, float(imp)))
            except (TypeError, ValueError):
                importance = 0.0
            cat = str(row.get("category") or "general").strip().lower()
            if cat not in CATEGORIES:
                cat = "general"
            risk = str(row.get("risk_level") or "unknown").strip().lower()
            if risk not in RISK_LEVELS:
                risk = "unknown"
            summary = (str(row.get("summary") or "").strip())[:200]
            out.append(
                Score(
                    id=tid,
                    importance=importance,
                    category=cat,
                    risk_level=risk,
                    summary=summary,
                )
            )
        if not out:
            return None
        return out
    except Exception as e:  # noqa: BLE001
        log.warning("normalize failed: %s", e)
        return None
