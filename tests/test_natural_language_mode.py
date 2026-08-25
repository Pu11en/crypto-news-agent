from __future__ import annotations

import asyncio
from types import SimpleNamespace

from handlers import chat, news
import bot


class FakeMessage:
    def __init__(self, text: str):
        self.text = text
        self.replies: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class FakeLLM:
    def chat(self, *_args):
        raise AssertionError("recognized agent actions must not fall through to chat")


def make_update(user_id: int, text: str):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=FakeMessage(text),
    )


def scraper_settings():
    return SimpleNamespace(bot_mode="scraper", scrape_hours=24)


def test_fresh_scrape_is_requested_and_confirmed_in_natural_language(monkeypatch):
    started = []

    async def fake_start(message, user_id, settings, llm):
        started.append((user_id, settings.bot_mode, llm))
        await message.reply_text("started")

    monkeypatch.setattr(news, "_start_fresh_news", fake_start)
    context = SimpleNamespace(user_data={})
    llm = FakeLLM()

    request = make_update(41, "Please get me fresh crypto news")
    asyncio.run(chat._route(request, context, scraper_settings(), llm))

    assert started == []
    assert "Xquik credits" in request.message.replies[0]
    assert "natural language" in request.message.replies[0]

    confirmation = make_update(41, "Yes, go ahead and start it")
    asyncio.run(chat._route(confirmation, context, scraper_settings(), llm))

    assert started == [(41, "scraper", llm)]
    assert confirmation.message.replies == ["started"]


def test_natural_language_help_contains_no_slash_commands():
    update = make_update(42, "What can you do?")
    asyncio.run(
        chat._route(update, SimpleNamespace(user_data={}), scraper_settings(), FakeLLM())
    )

    help_text = update.message.replies[0]
    assert "/news" not in help_text
    assert "/scrapes" not in help_text
    assert "fresh crypto news" in help_text.lower()


def test_natural_language_credit_request_routes_to_xquik_balance(monkeypatch):
    called = []

    async def fake_credits(update, context, settings):
        called.append(settings.bot_mode)
        await update.message.reply_text("credit balance")

    monkeypatch.setattr(chat.credits, "_credits", fake_credits)
    update = make_update(43, "How many Xquik credits do I have left?")
    asyncio.run(
        chat._route(update, SimpleNamespace(user_data={}), scraper_settings(), FakeLLM())
    )

    assert called == ["scraper"]
    assert update.message.replies == ["credit balance"]


def test_scraper_mode_registers_only_the_natural_language_router(monkeypatch):
    registered = []

    monkeypatch.setattr(
        bot.news, "register", lambda *args: registered.append("news-commands")
    )
    monkeypatch.setattr(
        bot.chat, "register", lambda *args: registered.append("natural-language")
    )
    monkeypatch.setattr(
        bot.credits, "register", lambda *args: registered.append("credit-commands")
    )

    bot._register_handlers(
        object(), scraper_settings(), object(), object()
    )

    assert registered == ["natural-language"]
