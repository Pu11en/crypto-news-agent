"""SQLite database layer.

Single-file SQLite with WAL for the small single-process bot. The schema
stores everything the agent produces: raw scraped tweets, the curated
stories it derives from them, the scripts it writes, and the live
per-user session state that drives the /news flow's state machine.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Boolean,
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

log = logging.getLogger("agent.db")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Tweet(Base):
    __tablename__ = "tweets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tweet_id: Mapped[str] = mapped_column(String, index=True)
    username: Mapped[str] = mapped_column(String, index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    url: Mapped[str | None] = mapped_column(String)
    tier: Mapped[str] = mapped_column(String, default="medium")
    tags: Mapped[str] = mapped_column(String, default="")  # pipe-separated
    run_id: Mapped[str] = mapped_column(String, index=True)


class Story(Base):
    """A newsworthy item the LLM curated from raw tweets."""

    __tablename__ = "stories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    rank: Mapped[int] = mapped_column(Integer)  # 1..N as returned by the LLM
    headline: Mapped[str] = mapped_column(String)
    summary: Mapped[str] = mapped_column(Text)
    tweet_ids: Mapped[list[str]] = mapped_column(JSON, default=list)  # source tweet_ids
    score: Mapped[float] = mapped_column(default=0.0)  # 0..1 newsworthiness
    # Curation vetting (default-deny at persist): display_ok stamps whether a
    # story clears the deterministic guards and may reach the PDF/chat; cut_reason
    # records why a denied story was dropped (ungrounded / low_score / over_cap).
    display_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    cut_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class Script(Base):
    """A generated video script. Versions accumulate within a session."""

    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text)
    story_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    is_final: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class Session(Base):
    """Per-user conversation state : drives the /news state machine."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # = user_id (single-user)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    state: Mapped[str] = mapped_column(String, default="idle")
    # JSON blobs keep the schema flexible without migrations for v1.
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    story_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    current_script: Mapped[str | None] = mapped_column(Text, nullable=True)
    script_version: Mapped[int] = mapped_column(Integer, default=0)
    history: Mapped[list[dict]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )


class Run(Base):
    """A single /news scrape run : for stats and debugging."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tweets_fetched: Mapped[int] = mapped_column(Integer, default=0)
    accounts_hit: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[str]] = mapped_column(JSON, default=list)


class VideoJob(Base):
    """Persistent state for one Telegram talking-head video job."""

    __tablename__ = "video_jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    chat_id: Mapped[int] = mapped_column(Integer, index=True)
    script_id: Mapped[int] = mapped_column(Integer, index=True)
    state: Mapped[str] = mapped_column(String, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    telegram_source_file_id: Mapped[str | None] = mapped_column(String, nullable=True)
    telegram_source_kind: Mapped[str | None] = mapped_column(String, nullable=True)
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_bundle_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    storyboard_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    composition_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    preview_paths: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    captions_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    failed_stage: Mapped[str | None] = mapped_column(String, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


_engine = None
_SessionLocal: sessionmaker | None = None


def init_engine(db_path: str):
    """Initialise the engine + create tables. Idempotent."""
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine

    # Ensure parent dir exists (matters for /data on a fresh volume).
    parent = os.path.dirname(db_path)
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)

    url = f"sqlite:///{db_path}"
    _engine = create_engine(
        url,
        # Single async bot process, but background scrape threads hit the DB
        # too : disable the same-thread check and let SQLite serialize.
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(_engine)

    # create_all() does not alter an existing SQLite table. Keep this migration
    # idempotent so older bot databases gain scrape ownership without exposing
    # historical unowned runs to arbitrary users.
    with _engine.begin() as conn:
        from sqlalchemy import text

        run_columns = {
            str(row[1]) for row in conn.execute(text("PRAGMA table_info(runs)"))
        }
        if "user_id" not in run_columns:
            conn.execute(text("ALTER TABLE runs ADD COLUMN user_id INTEGER"))
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_runs_user_id ON runs (user_id)")
        )

        # Curation vetting columns (default-deny at persist). Existing stories
        # are treated as displayable: SQLite ADD COLUMN with a server-side default
        # of 1 backfills every historical row, so old PDFs re-render unchanged.
        story_columns = {
            str(row[1]) for row in conn.execute(text("PRAGMA table_info(stories)"))
        }
        if "display_ok" not in story_columns:
            conn.execute(text("ALTER TABLE stories ADD COLUMN display_ok BOOLEAN DEFAULT 1"))
        if "cut_reason" not in story_columns:
            conn.execute(text("ALTER TABLE stories ADD COLUMN cut_reason TEXT"))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_stories_run_display "
                "ON stories (run_id, display_ok)"
            )
        )

    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)

    # Enable WAL for better concurrent-read performance.
    with _engine.connect() as conn:
        from sqlalchemy import text

        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.commit()

    log.info("DB ready at %s (WAL mode)", db_path)
    return _engine


def session() -> sessionmaker:
    """Return the session factory. init_engine() must have run first."""
    if _SessionLocal is None:
        raise RuntimeError("DB not initialised : call init_engine() first")
    return _SessionLocal


def get_or_create_session(user_id: int) -> Session:
    """Fetch the single per-user session row, creating it if absent."""
    db = session()()
    sid = str(user_id)
    existing = db.get(Session, sid)
    if existing is not None:
        db.close()
        return existing
    new = Session(id=sid, user_id=user_id, state="idle")
    db.add(new)
    db.commit()
    db.refresh(new)
    db.close()
    return new
