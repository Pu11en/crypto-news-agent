from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import db
from handlers import chat, news, session as sess
from video import jobs


def _seed_scrape(user_id: int, run_id: str, minutes_ago: int = 0) -> None:
    when = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    factory = db.session()
    with factory() as s:
        s.add(
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
        s.add_all(
            [
                db.Tweet(
                    tweet_id=f"{run_id}-1",
                    username="alpha",
                    text="Alpha reports the first source post.",
                    created_at=when,
                    url=f"https://x.com/alpha/status/{run_id}-1",
                    tier="high",
                    tags="btc",
                    run_id=run_id,
                ),
                db.Tweet(
                    tweet_id=f"{run_id}-2",
                    username="beta",
                    text="Beta reports the second source post.",
                    created_at=when,
                    url=f"https://x.com/beta/status/{run_id}-2",
                    tier="medium",
                    tags="eth",
                    run_id=run_id,
                ),
            ]
        )
        s.add(
            db.Story(
                run_id=run_id,
                rank=1,
                headline=f"Headline for {run_id}",
                summary="Saved story summary.",
                tweet_ids=[f"{run_id}-1"],
                score=0.9,
            )
        )
        s.commit()


def test_parse_saved_scrape_navigation_in_normal_language():
    assert news.parse_scrape_intent("show my saved scrapes") == ("list", None)
    assert news.parse_scrape_intent("what scrapes do I have?") == ("list", None)
    assert news.parse_scrape_intent("open the last scrape") == ("open", 1)
    assert news.parse_scrape_intent("go back to scrape 2") == ("open", 2)
    assert news.parse_scrape_intent("show full scrape 3") == ("raw", 3)
    assert news.parse_scrape_intent("make the hook shorter") is None


def test_saved_scrape_library_is_user_scoped_and_keeps_full_raw_posts(isolated_db):
    _seed_scrape(101, "user101-new", minutes_ago=0)
    _seed_scrape(101, "user101-old", minutes_ago=30)
    _seed_scrape(202, "other-user", minutes_ago=0)

    saved = news.list_saved_scrapes(101)

    assert [item["run_id"] for item in saved] == ["user101-new", "user101-old"]
    assert saved[0]["tweets_fetched"] == 2
    assert saved[0]["story_count"] == 1
    raw = news.raw_scrape_page(101, "user101-new", page=0, page_size=1)
    assert raw["total"] == 2
    assert raw["posts"][0]["username"] in {"alpha", "beta"}
    assert raw["posts"][0]["text"].endswith("source post.")


def test_new_scrape_run_is_persisted_with_requesting_user(isolated_db):
    news._persist_tweets(212, "owned-run", [], [], [])

    factory = db.session()
    with factory() as s:
        run = s.get(db.Run, "owned-run")
        assert run.user_id == 212


def test_raw_page_clamps_forged_page_and_chunks_without_data_loss(isolated_db):
    _seed_scrape(213, "bounded-run")

    raw = news.raw_scrape_page(213, "bounded-run", page=999, page_size=1)
    assert raw["page"] == 1
    assert len(raw["posts"]) == 1

    long_text = "header\n" + ("raw-post-content " * 400)
    chunks = news._telegram_chunks(long_text, limit=200)
    assert "".join(chunks) == long_text
    assert all(len(chunk) <= 200 for chunk in chunks)


def test_opening_saved_scrape_cancels_preupload_job_and_restores_story_pick(isolated_db):
    _seed_scrape(303, "saved-run")
    factory = db.session()
    with factory() as s:
        story = s.query(db.Story).filter(db.Story.run_id == "saved-run").first()
        script = db.Script(
            session_id="303",
            version=1,
            body="Old locked script",
            story_ids=[story.id],
            is_final=True,
        )
        s.add(script)
        s.commit()
        script_id = script.id
    active = jobs.create_for_script(303, 303, script_id)
    sess.set_state(303, sess.AWAITING_VIDEO)

    opened = news.open_saved_scrape(303, 1)

    assert opened["run_id"] == "saved-run"
    assert jobs.get(active.id).state == jobs.CANCELLED
    current = sess.load(303)
    assert current.state == sess.AWAITING_PICK
    assert current.run_id == "saved-run"
    assert len(current.story_ids) == 1


def test_user_cannot_open_another_users_saved_scrape(isolated_db):
    _seed_scrape(404, "private-run")

    try:
        news.open_saved_scrape(505, "private-run")
    except ValueError as exc:
        assert "not found" in str(exc).lower()
    else:
        raise AssertionError("another user's scrape was opened")


def test_legacy_runs_table_gets_idempotent_user_ownership_migration(tmp_path):
    legacy_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(legacy_path)
    connection.execute(
        """
        CREATE TABLE runs (
            id VARCHAR PRIMARY KEY,
            started_at DATETIME NOT NULL,
            finished_at DATETIME,
            tweets_fetched INTEGER NOT NULL,
            accounts_hit INTEGER NOT NULL,
            errors JSON NOT NULL
        )
        """
    )
    connection.commit()
    connection.close()

    if db._engine is not None:
        db._engine.dispose()
    db._engine = None
    db._SessionLocal = None
    try:
        engine = db.init_engine(str(legacy_path))
        db.init_engine(str(legacy_path))
        with engine.connect() as migrated:
            columns = {
                row[1] for row in migrated.exec_driver_sql("PRAGMA table_info(runs)")
            }
            indexes = {
                row[1] for row in migrated.exec_driver_sql("PRAGMA index_list(runs)")
            }
        assert "user_id" in columns
        assert "ix_runs_user_id" in indexes
    finally:
        if db._engine is not None:
            db._engine.dispose()
        db._engine = None
        db._SessionLocal = None


def test_scrape_buttons_use_immutable_run_ids_and_paginate(isolated_db):
    _seed_scrape(606, "callback-run")
    saved = news.list_saved_scrapes(606)
    list_markup = news._saved_scrapes_keyboard(saved)

    assert list_markup.inline_keyboard[0][0].callback_data == "scrape:open:callback-run"
    assert list_markup.inline_keyboard[0][1].callback_data == "scrape:raw:callback-run:0"

    raw = news.raw_scrape_page(606, "callback-run", page=0, page_size=1)
    raw_markup = news._raw_scrape_keyboard(raw)
    callback_values = [
        button.callback_data
        for row in raw_markup.inline_keyboard
        for button in row
    ]
    assert "scrape:raw:callback-run:1" in callback_values
    assert "scrape:open:callback-run" in callback_values
    assert all(len(value.encode("utf-8")) <= 64 for value in callback_values)


class _FakeMessage:
    def __init__(self, text: str = ""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))


class _FakeQuery:
    def __init__(self, data: str):
        self.data = data
        self.message = _FakeMessage()
        self.answered = False

    async def answer(self):
        self.answered = True


def test_raw_scrape_callback_returns_requested_page(isolated_db):
    _seed_scrape(607, "callback-page")
    factory = db.session()
    with factory() as s:
        for index in range(3, 7):
            s.add(
                db.Tweet(
                    tweet_id=f"callback-page-{index}",
                    username="extra",
                    text=f"Extra source post {index}.",
                    created_at=datetime.now(timezone.utc),
                    url=f"https://x.com/extra/status/callback-page-{index}",
                    tier="medium",
                    tags="btc",
                    run_id="callback-page",
                )
            )
        s.commit()
    query = _FakeQuery("scrape:raw:callback-page:1")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=607),
        callback_query=query,
    )

    asyncio.run(news._scrape_callback(update, None))

    assert query.answered is True
    assert "Page 2 of 2" in query.message.replies[0][0]
    assert "6 posts" in query.message.replies[0][0]
    assert query.message.replies[-1][1]["reply_markup"] is not None


def test_saved_scrape_navigation_preempts_awaiting_video_state(isolated_db):
    _seed_scrape(608, "route-run")
    factory = db.session()
    with factory() as s:
        story = s.query(db.Story).filter(db.Story.run_id == "route-run").first()
        script = db.Script(
            session_id="608",
            version=1,
            body="Old approved script",
            story_ids=[story.id],
            is_final=True,
        )
        s.add(script)
        s.commit()
        script_id = script.id
    active = jobs.create_for_script(608, 608, script_id)
    sess.set_state(608, sess.AWAITING_VIDEO)
    message = _FakeMessage("open the last scrape")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=608),
        message=message,
    )

    class LLMShouldNotRun:
        def chat(self, *args, **kwargs):
            raise AssertionError("normal chat ran before scrape navigation")

    asyncio.run(chat._route(update, None, SimpleNamespace(), LLMShouldNotRun()))

    assert jobs.get(active.id).state == jobs.CANCELLED
    assert sess.load(608).state == sess.AWAITING_PICK
    assert "Saved scrape opened" in message.replies[0][0]


def test_unrelated_message_is_normal_chat_while_awaiting_video(isolated_db):
    sess.set_state(609, sess.AWAITING_VIDEO)
    message = _FakeMessage("what else can you help me with?")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=609),
        message=message,
    )

    class ChatLLM:
        def chat(self, system, history, text):
            return f"Agent answer: {text}"

    asyncio.run(
        chat._route(
            update,
            None,
            SimpleNamespace(),
            ChatLLM(),
        )
    )

    assert message.replies[0][0] == "Agent answer: what else can you help me with?"
