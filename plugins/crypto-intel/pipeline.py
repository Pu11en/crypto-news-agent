"""Pipeline orchestrator: prefilter -> fetch -> batch-score -> upsert.

run_pipeline() spawns a daemon thread so the Telegram handler returns
immediately. Status is written to scrape_runs so get_stats can report.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from .db import Account, ScrapeRun, Tweet, get_session, seed_accounts
from .fetcher import RawTweet, XquikClient
from .scorer import BaiScorer, Score

log = logging.getLogger("crypto-intel.pipeline")

DEFAULT_FRESHNESS_HOURS = 48
DEFAULT_MAX_TWEETS_PER_ACCOUNT = 20
DEFAULT_PER_RUN_BUDGET = 1500
PRE_FILTER_MIN_LEN = 20
PRE_FILTER_MAX_DUP_RUNS = 1


def _cheap_prefilter(tweet: RawTweet) -> bool:
    """Drop retweets, super-short, pure-mention noise."""
    text = (tweet.text or "").strip()
    if not text:
        return False
    lower = text.lower()
    if lower.startswith("rt @") or lower.startswith("rt "):
        return False
    if len(text) < PRE_FILTER_MIN_LEN:
        return False
    if text.startswith("@") and " " not in text[:60]:
        return False
    return True


def run_pipeline(
    xquik_api_key: str,
    bai_api_key: str,
    progress_cb: Callable[[dict], None] | None = None,
    freshness_hours: int = DEFAULT_FRESHNESS_HOURS,
    max_tweets_per_account: int = DEFAULT_MAX_TWEETS_PER_ACCOUNT,
    per_run_budget: int = DEFAULT_PER_RUN_BUDGET,
    run_id: int | None = None,
) -> int:
    """Run synchronously in the calling thread. Returns the scrape_runs.id."""
    seed_accounts()
    if run_id is None:
        session = get_session()
        run = ScrapeRun(status="running", account_count=0, tweet_count=0, scored_count=0, error_count=0)
        session.add(run)
        session.commit()
        run_id = run.id
        session.close()
    else:
        session = get_session()
        run = session.query(ScrapeRun).filter(ScrapeRun.id == run_id).first()
        if run is None:
            run = ScrapeRun(id=run_id, status="running", account_count=0, tweet_count=0, scored_count=0, error_count=0)
            session.add(run)
            session.commit()
        session.close()

    def _emit(payload: dict) -> None:
        if progress_cb:
            try:
                progress_cb(payload)
            except Exception as e:  # noqa: BLE001
                log.warning("progress_cb failed: %s", e)

    try:
        session = get_session()
        accounts = session.query(Account).filter(Account.is_active.is_(True)).all()
        session.close()
        run.account_count = len(accounts)

        fetcher = XquikClient(
            api_key=xquik_api_key,
            freshness_hours=freshness_hours,
            max_tweets_per_account=max_tweets_per_account,
        )
        scorer = BaiScorer(api_key=bai_api_key)

        all_raw: list[RawTweet] = []
        seen_ids: set[str] = set()
        budget_left = per_run_budget
        for acct in accounts:
            if budget_left <= 0:
                break
            try:
                rows = fetcher.fetch_recent_tweets(acct.username, hours=freshness_hours)
            except Exception as e:  # noqa: BLE001
                run.error_count += 1
                log.warning("fetch failed for %s: %s", acct.username, e)
                _emit({"stage": "fetch", "account": acct.username, "error": str(e)})
                continue
            kept = 0
            for t in rows:
                if t.id in seen_ids:
                    continue
                if not _cheap_prefilter(t):
                    continue
                seen_ids.add(t.id)
                all_raw.append(t)
                kept += 1
                budget_left -= 1
                if budget_left <= 0:
                    break
            _emit(
                {
                    "stage": "fetch",
                    "account": acct.username,
                    "fetched": len(rows),
                    "kept": kept,
                }
            )
            time.sleep(0.1)

        run.tweet_count = len(all_raw)
        if not all_raw:
            run.status = "done"
            run.finished_at = datetime.now(timezone.utc)
            _persist_run(run)
            _emit({"stage": "done", "run_id": run_id, "tweets": 0})
            return run_id

        upserted_ids = _persist_raw_tweets(run_id, all_raw)
        _emit({"stage": "score_start", "run_id": run_id, "count": len(upserted_ids)})

        scored_total = 0
        batches = [upserted_ids[i : i + scorer.batch_size] for i in range(0, len(upserted_ids), scorer.batch_size)]
        for batch_ids in batches:
            tweets_for_batch = _load_tweets_for_scoring(run_id, batch_ids)
            try:
                scores: list[Score] = scorer.score_batch(tweets_for_batch)
            except Exception as e:  # noqa: BLE001
                run.error_count += 1
                log.warning("scoring batch failed: %s", e)
                _emit({"stage": "score", "error": str(e)})
                continue
            _persist_scores(scores)
            scored_total += len(scores)
            _emit({"stage": "score_batch", "scored": len(scores), "total": scored_total})

        run.scored_count = scored_total
        run.status = "done" if run.error_count == 0 else "done_with_errors"
        run.finished_at = datetime.now(timezone.utc)
        _persist_run(run)
        _emit(
            {
                "stage": "done",
                "run_id": run_id,
                "tweets": run.tweet_count,
                "scored": run.scored_count,
                "errors": run.error_count,
            }
        )
        return run_id
    except Exception as e:  # noqa: BLE001
        log.exception("pipeline run %s failed", run_id)
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.error_count += 1
        _persist_run(run)
        _emit({"stage": "failed", "run_id": run_id, "error": str(e)})
        return run_id


def start_pipeline_background(
    xquik_api_key: str,
    bai_api_key: str,
    progress_cb: Callable[[dict], None] | None = None,
    **kwargs,
) -> int:
    """Spawn a daemon thread; returns the run id immediately."""
    pre_run_session = get_session()
    pre_run = ScrapeRun(status="running")
    pre_run_session.add(pre_run)
    pre_run_session.commit()
    run_id = pre_run.id
    pre_run_session.close()

    def _runner():
        run_pipeline(
            xquik_api_key=xquik_api_key,
            bai_api_key=bai_api_key,
            progress_cb=progress_cb,
            run_id=run_id,
            **kwargs,
        )

    t = threading.Thread(target=_runner, name=f"crypto-intel-run-{run_id}", daemon=True)
    t.start()
    return run_id


def _persist_run(run: ScrapeRun) -> None:
    s = get_session()
    try:
        s.merge(run)
        s.commit()
    except Exception:
        s.rollback()
    finally:
        s.close()


def _persist_raw_tweets(run_id: int, rows: list[RawTweet]) -> list[str]:
    s = get_session()
    ids: list[str] = []
    try:
        existing = {tid for (tid,) in s.query(Tweet.id).filter(Tweet.id.in_([r.id for r in rows])).all()}
        for r in rows:
            if r.id in existing:
                continue
            s.add(
                Tweet(
                    id=r.id,
                    scrape_run_id=run_id,
                    username=r.username,
                    text=r.text,
                    created_at=r.created_at,
                    url=r.url,
                    scored=False,
                    fetched_at=datetime.now(timezone.utc),
                )
            )
            ids.append(r.id)
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return ids


def _load_tweets_for_scoring(run_id: int, ids: list[str]) -> list[dict]:
    s = get_session()
    try:
        rows = s.query(Tweet).filter(Tweet.id.in_(ids)).all()
        out = [
            {
                "id": t.id,
                "username": t.username,
                "text": t.text,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in rows
        ]
        return out
    finally:
        s.close()


def _persist_scores(scores: list[Score]) -> None:
    if not scores:
        return
    s = get_session()
    try:
        ids = [sc.id for sc in scores]
        existing = {t.id: t for t in s.query(Tweet).filter(Tweet.id.in_(ids)).all()}
        for sc in scores:
            t = existing.get(sc.id)
            if t is None:
                continue
            t.importance = sc.importance
            t.category = sc.category
            t.risk_level = sc.risk_level
            t.summary = sc.summary
            t.scored = True
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
