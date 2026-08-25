"""Central configuration : all settings come from environment variables.

Loaded once at startup. Raises if anything required is missing or unparsable
so misconfig fails fast at boot instead of mid-request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _required(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {name}")
    return val


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _positive_int(name: str, default: int) -> int:
    value = _int(name, default)
    if value < 1:
        raise RuntimeError(f"{name} must be at least 1")
    return value


def _user_ids(name: str) -> list[int]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return []
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


def _bot_mode() -> str:
    mode = os.environ.get("BOT_MODE", "full").strip().lower()
    if mode not in {"full", "scraper"}:
        raise RuntimeError("BOT_MODE must be 'full' or 'scraper'")
    return mode


@dataclass(frozen=True)
class Settings:
    # LLM : fallback (z.ai GLM)
    zai_api_key: str
    zai_base_url: str
    zai_model: str
    # LLM : primary (DeepSeek)
    deepseek_api_key: str
    deepseek_base_url: str
    deepseek_model: str
    # Scraper
    xquik_api_key: str
    scrape_hours: int
    max_tweets_per_account: int
    # Telegram
    telegram_bot_token: str
    allowed_user_ids: tuple[int, ...]
    # Product mode
    bot_mode: str = "full"
    # Storage
    db_path: str = "/data/agent.db"
    report_dir: str = "/data/reports"
    report_max_mb: int = 45
    video_work_dir: str = "data/video-jobs"
    video_max_seconds: int = 90
    video_max_upload_mb: int = 20
    video_artifact_retention_hours: int = 72
    whisper_model: str = "small.en"


def load() -> Settings:
    return Settings(
        zai_api_key=_required("ZAI_API_KEY"),
        zai_base_url=os.environ.get(
            "ZAI_BASE_URL", "https://api.z.ai/api/coding/paas/v4"
        ).rstrip("/"),
        zai_model=os.environ.get("ZAI_MODEL", "glm-5-turbo"),
        deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY", ""),
        deepseek_base_url=os.environ.get(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"
        ).rstrip("/"),
        deepseek_model=os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        xquik_api_key=_required("XQUIK_API_KEY"),
        scrape_hours=_int("SCRAPE_HOURS", 24),
        max_tweets_per_account=_int("MAX_TWEETS_PER_ACCOUNT", 20),
        telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
        allowed_user_ids=tuple(_user_ids("ALLOWED_USER_IDS")),
        bot_mode=_bot_mode(),
        db_path=os.environ.get("DB_PATH", "/data/agent.db"),
        report_dir=os.environ.get("REPORT_DIR", "").strip() or "/data/reports",
        report_max_mb=_positive_int("REPORT_MAX_MB", 45),
        video_work_dir=os.environ.get("VIDEO_WORK_DIR", "/data/video-jobs"),
        video_max_seconds=_int("VIDEO_MAX_SECONDS", 90),
        video_max_upload_mb=_int("VIDEO_MAX_UPLOAD_MB", 20),
        video_artifact_retention_hours=_int("VIDEO_ARTIFACT_RETENTION_HOURS", 72),
        whisper_model=os.environ.get("WHISPER_MODEL", "small.en"),
    )
