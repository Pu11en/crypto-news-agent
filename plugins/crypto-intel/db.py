"""Thread-safe SQLite layer for the crypto-intel pipeline.

Engine: WAL mode, check_same_thread=False, lazy_singleton.
One short-lived session per tool call.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    Boolean,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    username = Column(String(120), primary_key=True)
    tier = Column(String(40), nullable=False, default="medium")
    tags = Column(String(256), nullable=False, default="")
    is_active = Column(Boolean, nullable=False, default=True)
    last_fetched = Column(DateTime, nullable=True)


class Tweet(Base):
    __tablename__ = "tweets"

    id = Column(String(64), primary_key=True)
    scrape_run_id = Column(Integer, nullable=True)
    username = Column(String(120), nullable=False, index=True)
    text = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, nullable=True, index=True)
    importance = Column(Float, nullable=False, default=0.0)
    category = Column(String(80), nullable=False, default="general")
    risk_level = Column(String(40), nullable=False, default="unknown")
    summary = Column(Text, nullable=False, default="")
    url = Column(String(512), nullable=True)
    scored = Column(Boolean, nullable=False, default=False)
    fetched_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))


class ScrapeRun(Base):
    __tablename__ = "scrape_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    started_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(40), nullable=False, default="running")
    account_count = Column(Integer, nullable=False, default=0)
    tweet_count = Column(Integer, nullable=False, default=0)
    scored_count = Column(Integer, nullable=False, default=0)
    error_count = Column(Integer, nullable=False, default=0)


_db_lock = threading.Lock()
_engine = None
_session_factory = None


def _get_db_path() -> str:
    base = os.environ.get("HERMES_HOME", "/data/.hermes")
    db_dir = os.path.join(base, "..", "crypto-intel")
    Path(db_dir).mkdir(parents=True, exist_ok=True)
    return os.path.join(db_dir, "pipeline.db")


def get_engine():
    global _engine
    if _engine is not None:
        return _engine
    with _db_lock:
        if _engine is not None:
            return _engine
        url = f"sqlite:///{os.path.abspath(_get_db_path())}"
        _engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
            poolclass=create_engine.__doc__ and None,
        )

        @event.listens_for(_engine, "connect")
        def _set_wal(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

        Base.metadata.create_all(_engine)
        return _engine


def get_session() -> Session:
    global _session_factory
    engine = get_engine()
    if _session_factory is None:
        with _db_lock:
            if _session_factory is None:
                _session_factory = sessionmaker(bind=engine)
    return _session_factory()


def seed_accounts() -> int:
    accounts_path = Path(__file__).parent / "accounts.txt"
    if not accounts_path.exists():
        return 0
    session = get_session()
    count = 0
    try:
        existing = {a.username for a in session.query(Account).all()}
        with open(accounts_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(",", 2)
                if len(parts) < 3:
                    continue
                username, tier, tags = parts[0].strip(), parts[1].strip(), parts[2].strip()
                if username not in existing:
                    session.add(Account(username=username, tier=tier, tags=tags))
                    existing.add(username)
                    count += 1
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return count
