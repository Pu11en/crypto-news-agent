from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pypdf import PdfReader

import db
import reporting
from handlers import news


def _seed_report_scrape(user_id: int, run_id: str) -> None:
    when = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
    factory = db.session()
    with factory() as session:
        session.add(
            db.Run(
                id=run_id,
                user_id=user_id,
                started_at=when,
                finished_at=when,
                tweets_fetched=2,
                accounts_hit=2,
                errors=[],
            )
        )
        session.add_all(
            [
                db.Tweet(
                    tweet_id=f"{run_id}-1",
                    username="alpha",
                    text="First complete source post.",
                    created_at=when,
                    url=f"https://x.com/alpha/status/{run_id}-1",
                    tier="high",
                    tags="btc",
                    run_id=run_id,
                ),
                db.Tweet(
                    tweet_id=f"{run_id}-2",
                    username="beta",
                    text="Second complete source post.",
                    created_at=when,
                    url=f"https://x.com/beta/status/{run_id}-2",
                    tier="medium",
                    tags="eth",
                    run_id=run_id,
                ),
            ]
        )
        session.add(
            db.Story(
                run_id=run_id,
                rank=1,
                headline="A saved report story",
                summary="The full evidence remains attached.",
                tweet_ids=[f"{run_id}-1"],
                score=0.9,
            )
        )
        session.commit()


def test_report_is_complete_cached_and_recovers_from_corruption(isolated_db):
    _seed_report_scrape(801, "report-run")
    db_path = isolated_db / "test.db"
    report_dir = isolated_db / "reports"

    first = reporting.ensure_scrape_report(db_path, report_dir, "report-run", 801)
    first_mtime = first.stat().st_mtime_ns
    reader = PdfReader(str(first))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)

    assert first.name == "crypto-scrape-2026-07-30-report-run.pdf"
    assert len(reader.pages) >= 3
    assert "report-run" in extracted
    assert "#2" in extracted

    second = reporting.ensure_scrape_report(db_path, report_dir, "report-run", 801)
    assert second == first
    assert second.stat().st_mtime_ns == first_mtime

    first.write_bytes(b"not a pdf")
    repaired = reporting.ensure_scrape_report(db_path, report_dir, "report-run", 801)
    assert repaired.read_bytes().startswith(b"%PDF")
    reporting.validate_report(
        repaired,
        reporting._load_report_data(db_path, "report-run", 801),
        "report-run",
    )


def test_report_generation_is_user_scoped_and_concurrency_safe(isolated_db):
    _seed_report_scrape(802, "private-report")
    db_path = isolated_db / "test.db"
    report_dir = isolated_db / "reports"

    with pytest.raises(ValueError, match="not found"):
        reporting.ensure_scrape_report(
            db_path, report_dir, "private-report", 999
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        paths = list(
            pool.map(
                lambda _: reporting.ensure_scrape_report(
                    db_path, report_dir, "private-report", 802
                ),
                range(3),
            )
        )
    assert paths[0] == paths[1] == paths[2]
    assert not list(paths[0].parent.glob("*.tmp.pdf"))


class _FakeMessage:
    def __init__(self):
        self.text_replies = []
        self.documents = []

    async def reply_text(self, text, **kwargs):
        self.text_replies.append((text, kwargs))

    async def reply_document(self, document, **kwargs):
        self.documents.append((document, kwargs))


class _FakeQuery:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeMessage()
        self.answered = False

    async def answer(self):
        self.answered = True


def _settings(isolated_db, **overrides):
    values = {
        "bot_mode": "scraper",
        "db_path": str(isolated_db / "test.db"),
        "report_dir": str(isolated_db / "reports"),
        "report_max_mb": 45,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_production_keyboards_offer_pdf_but_full_mode_does_not(isolated_db):
    _seed_report_scrape(803, "button-report")
    saved = news.list_saved_scrapes(803)

    production = news._saved_scrapes_keyboard(saved, research_only=True)
    production_values = [
        button.callback_data
        for row in production.inline_keyboard
        for button in row
    ]
    full = news._saved_scrapes_keyboard(saved, research_only=False)
    full_values = [
        button.callback_data for row in full.inline_keyboard for button in row
    ]

    assert "scrape:pdf:button-report" in production_values
    assert all(":pdf:" not in value for value in full_values)
    opened = news._opened_scrape_keyboard("button-report", research_only=True)
    assert opened.inline_keyboard[0][0].callback_data == "scrape:pdf:button-report"


def test_pdf_callback_delivers_same_owned_scrape(isolated_db):
    _seed_report_scrape(804, "callback-report")
    query = _FakeQuery("scrape:pdf:callback-report")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=804),
        callback_query=query,
    )

    asyncio.run(news._scrape_callback(update, None, _settings(isolated_db)))

    assert query.answered is True
    assert len(query.message.documents) == 1
    document, kwargs = query.message.documents[0]
    assert document.name.endswith("callback-report.pdf")
    assert kwargs["filename"] == document.name
    assert query.message.text_replies == []


def test_report_failure_or_size_limit_preserves_scrape_and_is_retryable(
    isolated_db, monkeypatch
):
    _seed_report_scrape(805, "retry-report")
    message = _FakeMessage()

    def fail_generation(*args, **kwargs):
        raise RuntimeError("generator unavailable")

    monkeypatch.setattr(reporting, "ensure_scrape_report", fail_generation)
    asyncio.run(
        news._reply_scrape_report(
            message, 805, "retry-report", _settings(isolated_db)
        )
    )

    assert message.documents == []
    assert "safely saved" in message.text_replies[-1][0]
    assert "will not start another scrape" in message.text_replies[-1][0]
    assert news.list_saved_scrapes(805)[0]["run_id"] == "retry-report"

    oversized = isolated_db / "oversized.pdf"
    oversized.write_bytes(b"x" * (1024 * 1024 + 1))
    monkeypatch.setattr(
        reporting, "ensure_scrape_report", lambda *args, **kwargs: oversized
    )
    asyncio.run(
        news._reply_scrape_report(
            message,
            805,
            "retry-report",
            _settings(isolated_db, report_max_mb=1),
        )
    )
    assert message.documents == []
    assert "safely saved" in message.text_replies[-1][0]
