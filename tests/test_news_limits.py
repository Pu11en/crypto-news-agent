from __future__ import annotations

import asyncio
from types import SimpleNamespace

import db
from handlers import news, session as sess
from video import jobs


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


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


def test_news_is_rejected_while_video_job_is_active(isolated_db):
    factory = db.session()
    with factory() as s:
        script = db.Script(
            session_id="992",
            version=1,
            body="Approved script",
            story_ids=[],
            is_final=True,
        )
        s.add(script)
        s.commit()
        script_id = script.id
    jobs.create_for_script(992, 9920, script_id)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=992),
        message=FakeMessage(),
    )

    asyncio.run(news._news(update, None, SimpleNamespace(scrape_hours=24), object()))

    assert update.message.replies == [
        "A video job is still active. Finish, retry, or cancel it before starting /news again."
    ]


def test_failed_status_reply_does_not_wedge_global_scrape_lock(isolated_db):
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=993),
        message=ExplodingMessage(fail_on=1),
    )
    news._NEWS_IN_PROGRESS = False

    asyncio.run(news._news(update, None, SimpleNamespace(scrape_hours=24), object()))

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
            news._news(recovered, None, SimpleNamespace(scrape_hours=24), object())
        )
    finally:
        news._scrape_and_curate = original

    assert any("Scraping the last" in reply for reply in recovered.message.replies)
