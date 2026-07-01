"""Tool JSON schemas + handler implementations for the crypto-intel plugin.

Mirrors the spotify pattern: schemas + handlers live here, register() in
__init__.py iterates a list and calls ctx.register_tool().
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone

from .db import Account, ScrapeRun, Tweet, get_session
from .pipeline import start_pipeline_background


_RUN_STATUS_LOCK = threading.Lock()
_RUN_RESULTS: dict[int, dict] = {}


def _check_keys(_ctx) -> bool:
    return bool(os.environ.get("BAI_API_KEY")) and bool(os.environ.get("XQUIK_API_KEY"))


# ----- Schemas ----------------------------------------------------------------

RUN_PIPELINE_SCHEMA = {
    "name": "crypto_run_pipeline",
    "description": (
        "Start a background pipeline run: fetch recent crypto tweets from "
        "tracked accounts and score them with DeepSeek V4 Pro. Returns "
        "immediately with a run id; check progress with crypto_get_stats."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "freshness_hours": {
                "type": "integer",
                "description": "Only fetch tweets newer than this (default 48h).",
                "default": 48,
            },
            "max_tweets_per_account": {
                "type": "integer",
                "description": "Cap per account per run (default 20).",
                "default": 20,
            },
            "per_run_budget": {
                "type": "integer",
                "description": "Hard cap on total tweets scored (default 1500).",
                "default": 1500,
            },
        },
    },
}

GET_DIGEST_SCHEMA = {
    "name": "crypto_get_digest",
    "description": (
        "Latest scored tweets grouped by category. Use min_score and "
        "limit to tune; default min_score=0.6, limit=20."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "min_score": {"type": "number", "default": 0.6},
            "limit": {"type": "integer", "default": 20},
            "category": {"type": "string", "default": ""},
        },
    },
}

GET_ALERTS_SCHEMA = {
    "name": "crypto_get_alerts",
    "description": (
        "Critical-only tweets (hacks, whale moves > $50M, scams, "
        "regulation, security incidents). Default risk_level=critical."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 10},
            "risk_level": {
                "type": "string",
                "default": "critical",
                "enum": ["critical", "high", "medium", "low"],
            },
        },
    },
}

GET_IDEAS_SCHEMA = {
    "name": "crypto_get_ideas",
    "description": (
        "Top-scored tweets as short-form hooks (trading ideas, alpha). "
        "Default min_score=0.75, limit=15."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "min_score": {"type": "number", "default": 0.75},
            "limit": {"type": "integer", "default": 15},
        },
    },
}

SEARCH_TWEETS_SCHEMA = {
    "name": "crypto_search_tweets",
    "description": (
        "Query stored scored tweets by free-text (matches text/summary), "
        "category, or username."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "q": {"type": "string", "default": ""},
            "category": {"type": "string", "default": ""},
            "username": {"type": "string", "default": ""},
            "min_score": {"type": "number", "default": 0.0},
            "limit": {"type": "integer", "default": 20},
        },
    },
}

GET_STATS_SCHEMA = {
    "name": "crypto_get_stats",
    "description": (
        "Pipeline status: last run id/status/timestamps, tweets "
        "fetched/scored, accounts tracked, run progress if a run is "
        "currently in flight."
    ),
    "parameters": {"type": "object", "properties": {}},
}


# ----- Handlers ---------------------------------------------------------------

def _handle_run_pipeline(args: dict) -> dict:
    xquik_key = os.environ.get("XQUIK_API_KEY", "")
    bai_key = os.environ.get("BAI_API_KEY", "")
    if not xquik_key or not bai_key:
        return {"ok": False, "error": "XQUIK_API_KEY / BAI_API_KEY not set"}

    run_id_holder: dict = {"id": None}

    def _on_progress(payload: dict) -> None:
        with _RUN_STATUS_LOCK:
            _RUN_RESULTS[run_id_holder["id"]] = payload

    kwargs = {
        "freshness_hours": int(args.get("freshness_hours") or 48),
        "max_tweets_per_account": int(args.get("max_tweets_per_account") or 20),
        "per_run_budget": int(args.get("per_run_budget") or 1500),
    }
    run_id = start_pipeline_background(
        xquik_api_key=xquik_key,
        bai_api_key=bai_key,
        progress_cb=_on_progress,
        **kwargs,
    )
    run_id_holder["id"] = run_id
    with _RUN_STATUS_LOCK:
        _RUN_RESULTS[run_id] = {"stage": "queued", "run_id": run_id}
    return {
        "ok": True,
        "run_id": run_id,
        "status": "queued",
        "message": "Pipeline started in background. Use crypto_get_stats to check progress.",
    }


def _handle_get_digest(args: dict) -> dict:
    min_score = float(args.get("min_score") or 0.6)
    limit = int(args.get("limit") or 20)
    category = (args.get("category") or "").strip().lower()
    s = get_session()
    try:
        q = s.query(Tweet).filter(Tweet.scored.is_(True), Tweet.importance >= min_score)
        if category:
            q = q.filter(Tweet.category == category)
        rows = q.order_by(Tweet.importance.desc(), Tweet.fetched_at.desc()).limit(limit).all()
        items = [_tweet_to_dict(t) for t in rows]
    finally:
        s.close()
    return {"ok": True, "count": len(items), "items": items}


def _handle_get_alerts(args: dict) -> dict:
    limit = int(args.get("limit") or 10)
    risk_level = (args.get("risk_level") or "critical").strip().lower()
    s = get_session()
    try:
        rows = (
            s.query(Tweet)
            .filter(
                Tweet.scored.is_(True),
                Tweet.risk_level == risk_level,
            )
            .order_by(Tweet.importance.desc(), Tweet.fetched_at.desc())
            .limit(limit)
            .all()
        )
        items = [_tweet_to_dict(t) for t in rows]
    finally:
        s.close()
    return {"ok": True, "count": len(items), "risk_level": risk_level, "items": items}


def _handle_get_ideas(args: dict) -> dict:
    min_score = float(args.get("min_score") or 0.75)
    limit = int(args.get("limit") or 15)
    s = get_session()
    try:
        rows = (
            s.query(Tweet)
            .filter(
                Tweet.scored.is_(True),
                Tweet.importance >= min_score,
                Tweet.category.in_(["trading", "whale", "defi", "meme", "ai_crypto"]),
            )
            .order_by(Tweet.importance.desc(), Tweet.fetched_at.desc())
            .limit(limit)
            .all()
        )
        items = [_tweet_to_dict(t, short=True) for t in rows]
    finally:
        s.close()
    return {"ok": True, "count": len(items), "items": items}


def _handle_search_tweets(args: dict) -> dict:
    q = (args.get("q") or "").strip()
    category = (args.get("category") or "").strip().lower()
    username = (args.get("username") or "").strip().lower()
    min_score = float(args.get("min_score") or 0.0)
    limit = int(args.get("limit") or 20)
    s = get_session()
    try:
        query = s.query(Tweet).filter(Tweet.scored.is_(True), Tweet.importance >= min_score)
        if q:
            like = f"%{q}%"
            query = query.filter((Tweet.text.ilike(like)) | (Tweet.summary.ilike(like)))
        if category:
            query = query.filter(Tweet.category == category)
        if username:
            query = query.filter(Tweet.username == username)
        rows = query.order_by(Tweet.importance.desc(), Tweet.fetched_at.desc()).limit(limit).all()
        items = [_tweet_to_dict(t) for t in rows]
    finally:
        s.close()
    return {"ok": True, "count": len(items), "items": items}


def _handle_get_stats(_args: dict) -> dict:
    s = get_session()
    try:
        account_count = s.query(Account).count()
        active_account_count = s.query(Account).filter(Account.is_active.is_(True)).count()
        total_tweets = s.query(Tweet).count()
        scored_tweets = s.query(Tweet).filter(Tweet.scored.is_(True)).count()
        last_run = s.query(ScrapeRun).order_by(ScrapeRun.id.desc()).first()
        running = s.query(ScrapeRun).filter(ScrapeRun.status == "running").order_by(ScrapeRun.id.desc()).first()
    finally:
        s.close()
    out = {
        "ok": True,
        "accounts": {"total": account_count, "active": active_account_count},
        "tweets": {"total": total_tweets, "scored": scored_tweets},
        "last_run": _run_to_dict(last_run) if last_run else None,
    }
    if running:
        with _RUN_STATUS_LOCK:
            progress = _RUN_RESULTS.get(running.id, {"stage": "running"})
        out["running"] = {"run_id": running.id, **progress}
    return out


# ----- Helpers ----------------------------------------------------------------

def _tweet_to_dict(t: Tweet, short: bool = False) -> dict:
    base = {
        "id": t.id,
        "username": t.username,
        "category": t.category,
        "risk_level": t.risk_level,
        "importance": round(float(t.importance or 0.0), 3),
        "summary": t.summary,
        "url": t.url,
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "fetched_at": t.fetched_at.isoformat() if t.fetched_at else None,
    }
    if not short:
        base["text"] = t.text
    return base


def _run_to_dict(r: ScrapeRun) -> dict:
    return {
        "id": r.id,
        "status": r.status,
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "account_count": r.account_count,
        "tweet_count": r.tweet_count,
        "scored_count": r.scored_count,
        "error_count": r.error_count,
    }
