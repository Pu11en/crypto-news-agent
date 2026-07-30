from __future__ import annotations

import asyncio
from types import SimpleNamespace

import db
import pytest
from handlers import chat, news, session as sess


class FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies = []
        self.reply_kwargs = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.reply_kwargs.append(kwargs)


def _seed_research(user_id: int, run_id: str, with_story: bool = True) -> int | None:
    factory = db.session()
    with factory() as s:
        s.add(
            db.Run(
                id=run_id,
                user_id=user_id,
                tweets_fetched=1,
                accounts_hit=1,
                errors=[],
            )
        )
        s.add(
            db.Tweet(
                tweet_id=f"{run_id}-tweet",
                username="source",
                text="Grounded source post.",
                url=f"https://x.com/source/status/{run_id}-tweet",
                tier="high",
                tags="btc",
                run_id=run_id,
            )
        )
        story_id = None
        if with_story:
            story = db.Story(
                run_id=run_id,
                rank=1,
                headline="Grounded research story",
                summary="A concise research summary.",
                tweet_ids=[f"{run_id}-tweet"],
                score=0.9,
            )
            s.add(story)
            s.flush()
            story_id = story.id
        s.commit()
        return story_id


def _protect_draft(user_id: int, run_id: str, story_id: int) -> None:
    sess.set_run(user_id, run_id, [story_id])
    sess.start_script(user_id, [story_id])
    sess.set_current_script(user_id, "Protected local draft")


def test_scraper_mode_reopens_research_without_touching_legacy_draft(isolated_db):
    story_id = _seed_research(701, "saved-research")
    _protect_draft(701, "saved-research", story_id)
    before = sess.load(701)
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=701),
        message=message,
    )

    asyncio.run(
        news._continue_news(
            update,
            None,
            SimpleNamespace(bot_mode="scraper"),
        )
    )

    after = sess.load(701)
    rendered = "\n".join(message.replies)
    assert "Reusing your latest saved research" in rendered
    assert "Review the curated research" in rendered
    assert "https://x.com/source/status/saved-research-tweet" in rendered
    assert "No script or video job was created" in rendered
    assert after.state == sess.SCRIPT_DRAFT
    assert after.current_script == "Protected local draft"
    assert after.script_version == before.script_version


def test_scraper_mode_fresh_run_preserves_draft_and_creates_no_script(
    isolated_db, monkeypatch
):
    old_story_id = _seed_research(702, "old-research")
    _protect_draft(702, "old-research", old_story_id)
    _seed_research(702, "new-research", with_story=False)

    def fake_scrape(settings, llm, user_id):
        return {
            "run_id": "new-research",
            "tweets_fetched": 1,
            "accounts_hit": 1,
            "stories": [
                {
                    "rank": 1,
                    "headline": "New research only",
                    "summary": "Freshly curated without script generation.",
                    "tweet_ids": ["new-research-tweet"],
                    "score": 0.95,
                }
            ],
        }

    monkeypatch.setattr(news, "_scrape_and_curate", fake_scrape)
    delivered_reports = []

    async def fake_report(message, user_id, run_id, settings):
        delivered_reports.append((user_id, run_id))

    monkeypatch.setattr(news, "_reply_scrape_report", fake_report)
    message = FakeMessage()
    news._NEWS_IN_PROGRESS = False
    asyncio.run(
        news._start_fresh_news(
            message,
            702,
            SimpleNamespace(bot_mode="scraper", scrape_hours=24),
            object(),
        )
    )

    current = sess.load(702)
    rendered = "\n".join(message.replies)
    assert "Starting a NEW scrape" in rendered
    assert "New research only" in rendered
    assert "No script or video job was created" in rendered
    assert current.state == sess.SCRIPT_DRAFT
    assert current.run_id == "old-research"
    assert current.current_script == "Protected local draft"
    assert delivered_reports == [(702, "new-research")]
    factory = db.session()
    with factory() as s:
        assert s.query(db.Script).filter(db.Script.session_id == "702").count() == 0


def test_scraper_mode_text_never_refines_or_generates_script(
    isolated_db, monkeypatch
):
    story_id = _seed_research(703, "chat-research")
    _protect_draft(703, "chat-research", story_id)

    async def forbidden(*args, **kwargs):
        raise AssertionError("script refinement must be disabled in scraper mode")

    monkeypatch.setattr(chat, "_handle_refine", forbidden)

    captured = {}

    class ChatLLM:
        def chat(self, system, history, text):
            captured["system"] = system
            return f"Research-agent answer: {text}"

    message = FakeMessage("help me understand the first story")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=703),
        message=message,
    )
    asyncio.run(
        chat._route(
            update,
            None,
            SimpleNamespace(bot_mode="scraper"),
            ChatLLM(),
        )
    )

    assert message.replies == [
        "Research-agent answer: help me understand the first story"
    ]
    assert "Never generate a script automatically" in captured["system"]
    assert "explicitly asks to make or revise a script" in captured["system"]
    assert "Grounded research story" in captured["system"]
    assert "Grounded source post." in captured["system"]
    assert "https://x.com/source/status/chat-research-tweet" in captured["system"]
    current = sess.load(703)
    assert current.state == sess.SCRIPT_DRAFT
    assert current.current_script == "Protected local draft"


def test_scraper_mode_help_excludes_script_video_and_caption_commands(isolated_db):
    message = FakeMessage()
    update = SimpleNamespace(message=message)

    asyncio.run(
        news._start(update, None, SimpleNamespace(bot_mode="scraper"))
    )

    help_text = message.replies[0]
    assert "/scrapes" in help_text
    assert "Automatic script generation" in help_text
    assert "help draft or revise a script in chat" in help_text
    assert "/done" not in help_text
    assert "/cancel" not in help_text


def test_bot_mode_validation(monkeypatch):
    import config

    monkeypatch.setenv("BOT_MODE", "scraper")
    assert config._bot_mode() == "scraper"
    monkeypatch.setenv("BOT_MODE", "full")
    assert config._bot_mode() == "full"
    monkeypatch.setenv("BOT_MODE", "unknown")
    with pytest.raises(RuntimeError, match="BOT_MODE"):
        config._bot_mode()
