from __future__ import annotations

import asyncio
from types import SimpleNamespace

import db
from handlers import news, session as sess
from video import jobs


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.reply_kwargs = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        self.reply_kwargs.append(kwargs)


class FakeQuery:
    def __init__(self, data: str, message: FakeMessage):
        self.data = data
        self.message = message
        self.answered = False
        self.answer_text = None

    async def answer(self, text=None):
        self.answered = True
        self.answer_text = text


class ExplodingMessage:
    def __init__(self, fail_on: int = 1):
        self.calls = 0
        self.fail_on = fail_on
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("user blocked the bot")
        self.replies.append(text)


def test_news_requires_confirmation_before_first_scrape(
    isolated_db, monkeypatch
):
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=990),
        message=message,
    )
    scrape_calls = []

    def unexpected_scrape(*args):
        scrape_calls.append(args)
        raise AssertionError("/news must wait for explicit confirmation")

    monkeypatch.setattr(news, "_scrape_and_curate", unexpected_scrape)

    asyncio.run(
        news._news(update, None, SimpleNamespace(scrape_hours=24), object())
    )

    assert scrape_calls == []
    assert "No saved scrape exists. Start the first scrape?" in message.replies[0]
    assert sess.load(990).state == sess.IDLE
    menu = message.reply_kwargs[0]["reply_markup"]
    assert len(menu.inline_keyboard) == 1
    assert menu.inline_keyboard[0][0].text == "🆕 Start first scrape"
    assert menu.inline_keyboard[0][0].callback_data == "news-choice:fresh"


def test_second_news_request_is_rejected_while_scrape_runs(isolated_db):
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=991),
        message=FakeMessage(),
    )
    settings = SimpleNamespace(scrape_hours=24)
    news._NEWS_IN_PROGRESS = True
    try:
        asyncio.run(news._news(update, None, settings, object()))
    finally:
        news._NEWS_IN_PROGRESS = False

    assert update.message.replies == [
        "Another news scrape is already running. Try again after it finishes."
    ]


def test_news_resumes_preupload_job_without_scraping(isolated_db, monkeypatch):
    factory = db.session()
    with factory() as s:
        script = db.Script(
            session_id="992",
            version=1,
            body="Approved talking notes",
            story_ids=[],
            is_final=True,
        )
        s.add(script)
        s.commit()
        script_id = script.id
    active = jobs.create_for_script(992, 9920, script_id)
    sess.set_state(992, sess.AWAITING_VIDEO)
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=992),
        message=message,
    )

    monkeypatch.setattr(
        news,
        "_scrape_and_curate",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("resume must not start a scrape")
        ),
    )

    asyncio.run(
        news._news(update, None, SimpleNamespace(scrape_hours=24), object())
    )

    assert "Latest scrape information" in message.replies[0]
    assert "video waiting for your recording" in message.replies[0]
    menu = message.reply_kwargs[0]["reply_markup"]
    assert menu.inline_keyboard[0][0].callback_data == "news-choice:resume"
    assert menu.inline_keyboard[1][0].callback_data == "news-choice:fresh"

    query = FakeQuery("news-choice:resume", message)
    callback_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=992),
        callback_query=query,
    )
    asyncio.run(
        news._news_choice_callback(
            callback_update, None, SimpleNamespace(scrape_hours=24), object()
        )
    )

    assert query.answered is True
    assert jobs.get(active.id).state == jobs.AWAITING_VIDEO
    assert sess.load(992).state == sess.AWAITING_VIDEO
    assert any("no new scrape was started" in reply for reply in message.replies)
    assert any("Approved talking notes" in reply for reply in message.replies)


def test_continue_command_reopens_current_stories_without_scraping(
    isolated_db, monkeypatch
):
    factory = db.session()
    with factory() as s:
        s.add(
            db.Run(
                id="continue-scrape",
                user_id=996,
                tweets_fetched=2,
                accounts_hit=2,
                errors=[],
            )
        )
        s.add(
            db.Story(
                run_id="continue-scrape",
                rank=1,
                headline="Continue this story",
                summary="The preserved story summary.",
                tweet_ids=[],
                score=0.8,
            )
        )
        s.commit()
    sess.set_run(996, "continue-scrape", [1])
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=996),
        message=message,
    )
    monkeypatch.setattr(
        news,
        "_scrape_and_curate",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("/continue must never scrape")
        ),
    )

    asyncio.run(news._continue_news(update, None))

    assert any("Saved scrape opened" in reply for reply in message.replies)
    assert any("Continue this story" in reply for reply in message.replies)
    assert sess.load(996).state == sess.AWAITING_PICK


def test_news_shows_latest_scrape_info_then_reuses_it(isolated_db, monkeypatch):
    factory = db.session()
    with factory() as s:
        s.add(
            db.Run(
                id="one-scrape",
                user_id=994,
                tweets_fetched=1,
                accounts_hit=1,
                errors=[],
            )
        )
        s.add(
            db.Story(
                run_id="one-scrape",
                rank=1,
                headline="Reuse this story",
                summary="The existing curated story.",
                tweet_ids=[],
                score=0.9,
            )
        )
        s.commit()
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=994),
        message=message,
    )
    monkeypatch.setattr(
        news,
        "_scrape_and_curate",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("saved scrape must be reused")
        ),
    )

    asyncio.run(news._news(update, None, SimpleNamespace(scrape_hours=24), object()))

    assert "Date:" in message.replies[0]
    assert "Posts collected: 1" in message.replies[0]
    assert "Accounts reached: 1" in message.replies[0]
    assert "Curated stories: 1" in message.replies[0]
    assert sess.load(994).run_id is None

    query = FakeQuery("news-choice:resume", message)
    callback_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=994),
        callback_query=query,
    )
    asyncio.run(
        news._news_choice_callback(
            callback_update, None, SimpleNamespace(scrape_hours=24), object()
        )
    )

    assert sess.load(994).run_id == "one-scrape"
    assert sess.load(994).state == sess.AWAITING_PICK
    assert any("no credits were spent" in reply for reply in message.replies)
    assert any("Saved scrape opened" in reply for reply in message.replies)

def test_failed_status_reply_does_not_wedge_global_scrape_lock(isolated_db):
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=993),
        message=ExplodingMessage(fail_on=1),
    )
    news._NEWS_IN_PROGRESS = False

    asyncio.run(
        news._start_fresh_news(
            update.message,
            update.effective_user.id,
            SimpleNamespace(scrape_hours=24),
            object(),
        )
    )

    assert news._NEWS_IN_PROGRESS is False
    assert sess.load(993).state != sess.SCRAPING

    # The lock must be usable again immediately by any user.
    recovered = SimpleNamespace(
        effective_user=SimpleNamespace(id=993),
        message=FakeMessage(),
    )

    def fake_scrape(settings, llm, user_id):
        return {
            "stories": [],
            "tweets_fetched": 0,
            "accounts_hit": 0,
            "run_id": "recovered-run",
        }

    original = news._scrape_and_curate
    news._scrape_and_curate = fake_scrape
    try:
        asyncio.run(
            news._start_fresh_news(
                recovered.message,
                recovered.effective_user.id,
                SimpleNamespace(scrape_hours=24),
                object(),
            )
        )
    finally:
        news._scrape_and_curate = original

    assert any("Starting a NEW scrape" in reply for reply in recovered.message.replies)


def test_news_does_not_repeat_empty_saved_scrape(isolated_db, monkeypatch):
    factory = db.session()
    with factory() as s:
        s.add(
            db.Run(
                id="empty-scrape",
                user_id=995,
                tweets_fetched=0,
                accounts_hit=0,
                errors=[],
            )
        )
        s.commit()
    message = FakeMessage()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=995),
        message=message,
    )
    monkeypatch.setattr(
        news,
        "_scrape_and_curate",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("empty scrape must not repeat automatically")
        ),
    )

    asyncio.run(news._news(update, None, SimpleNamespace(scrape_hours=24), object()))

    assert "Curated stories: 0" in message.replies[0]
    query = FakeQuery("news-choice:resume", message)
    callback_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=995),
        callback_query=query,
    )
    asyncio.run(
        news._news_choice_callback(
            callback_update, None, SimpleNamespace(scrape_hours=24), object()
        )
    )

    assert any("found no curated stories" in reply for reply in message.replies)
    assert any("/freshnews" in reply for reply in message.replies)
