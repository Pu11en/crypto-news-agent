"""Free-text message router.

Every non-command message arrives here. What we do depends on the user's
session state:
    idle          → general chat with the LLM
    awaiting_pick → treat the message as a story selection (1,3,5 / auto)
    script_draft  → treat the message as refinement feedback on the script
"""

from __future__ import annotations

import asyncio
import logging
import re

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

import prompts
from config import Settings
from handlers import credits
from handlers import session as sess
from handlers import news
from llm import LLMClient, LLMError

log = logging.getLogger("agent.chat")

_APPROVAL_PHRASES = (
    "looks good",
    "look good",
    "perfect",
    "approved",
    "approve it",
    "use this",
    "this is the one",
    "ready to record",
    "exactly what i want",
    "good to go",
    "lock it",
)
_REVISION_MARKERS = (
    "almost ",
    " but ",
    "except ",
    "one more",
    "change the",
    "change this",
    "please change",
    "fix ",
    "revise ",
    "not yet",
)
_NEGATED_APPROVAL = re.compile(
    r"\b(?:do not|don't|dont|does not|doesn't|doesnt|did not|didn't|didnt|"
    r"cannot|can't|cant|will not|won't|wont|would not|wouldn't|wouldnt|"
    r"should not|shouldn't|shouldnt|could not|couldn't|couldnt|"
    r"is not|isn't|isnt|never|not)\b[\w\s']{0,25}?"
    r"\b(?:approve|approved|good|perfect|ready|exactly|lock|use|go)\b"
)

_FRESH_NEWS_INTENT = re.compile(
    r"\b(?:fresh|new)\s+(?:crypto\s+)?news\b|"
    r"\b(?:start|run|fetch|do)\b[^.?!]{0,50}\b(?:new\s+)?scrape\b|"
    r"\bscrape\b[^.?!]{0,50}\b(?:x|twitter|accounts?|posts?|news)\b"
)
_HELP_INTENT = re.compile(
    r"^(?:help|what can you do|how (?:do|should) i use (?:this|you)|show capabilities)[?.!]*$"
)
_CREDITS_INTENT = re.compile(
    r"\b(?:xquik\s+)?credit(?:s| balance)?\b|\bhow many credits\b"
)
_ACCOUNT_INTENT = re.compile(r"\b(?:show|check|view)\b.*\bxquik account\b")
_CONFIRM_INTENT = re.compile(
    r"^(?:yes|yep|yeah|confirm|confirmed|proceed|go ahead|do it|start it|"
    r"yes[, ]+go ahead(?: and start it)?|yes[, ]+start it)[.!]*$"
)
_CANCEL_INTENT = re.compile(
    r"^(?:no|nope|cancel|never mind|nevermind|don't|do not|stop)[.!]*$"
)
_PENDING_FRESH_KEY = "pending_fresh_scrape_confirmation"

_NATURAL_HELP = (
    "Talk to me naturally. You can ask me to get fresh crypto news, show your "
    "saved scrapes, open the latest research, show the raw posts from a saved "
    "scrape, check your Xquik credit balance, or answer questions about the "
    "latest saved research. I will ask for confirmation before a new scrape "
    "spends Xquik credits."
)


def is_script_approval(text: str) -> bool:
    """Recognize explicit approval while avoiding common revision phrases."""
    normalized = re.sub(r"\s+", " ", text.strip().lower().replace("’", "'"))
    if _NEGATED_APPROVAL.search(normalized):
        return False
    if any(marker in normalized for marker in _REVISION_MARKERS):
        return False
    return any(phrase in normalized for phrase in _APPROVAL_PHRASES)


def register(
    app: Application, settings: Settings, llm: LLMClient, user_filter
) -> None:
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & user_filter,
            lambda u, c: _route(u, c, settings, llm),
        )
    )


async def _handle_natural_scraper_action(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    llm: LLMClient,
    text: str,
) -> bool:
    """Route production actions expressed as ordinary language.

    Returning True means the message was handled and must not fall through to
    research chat. Confirmation state is kept in Telegram's per-user memory;
    losing it on restart is safe because a scrape then requires a new request.
    """
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    user_data = getattr(context, "user_data", None)
    if user_data is None:
        user_data = {}

    if user_data.get(_PENDING_FRESH_KEY):
        if _CONFIRM_INTENT.fullmatch(normalized):
            user_data.pop(_PENDING_FRESH_KEY, None)
            await news._start_fresh_news(
                update.message, update.effective_user.id, settings, llm
            )
            return True
        if _CANCEL_INTENT.fullmatch(normalized):
            user_data.pop(_PENDING_FRESH_KEY, None)
            await update.message.reply_text("Okay, I won't start a new scrape.")
            return True
        await update.message.reply_text(
            "Please answer naturally with yes to start the scrape or no to cancel it."
        )
        return True

    if _HELP_INTENT.fullmatch(normalized):
        await update.message.reply_text(_NATURAL_HELP)
        return True
    if _ACCOUNT_INTENT.search(normalized):
        await credits._account(update, context, settings)
        return True
    if _CREDITS_INTENT.search(normalized):
        await credits._credits(update, context, settings)
        return True
    if _FRESH_NEWS_INTENT.search(normalized):
        user_data[_PENDING_FRESH_KEY] = True
        await update.message.reply_text(
            "A new scrape will query only the configured accounts through Xquik "
            "and spend Xquik credits. Say yes in natural language to start it, or "
            "say no to cancel."
        )
        return True
    return False


async def _route(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    llm: LLMClient,
) -> None:
    user_id = update.effective_user.id
    text = update.message.text or ""
    research_only = news._scraper_only(settings)
    if research_only and await _handle_natural_scraper_action(
        update, context, settings, llm, text
    ):
        return
    scrape_intent = news.parse_scrape_intent(text)
    if scrape_intent is not None:
        await news.handle_scrape_intent(
            update, scrape_intent, research_only=research_only
        )
        return

    user_sess = sess.load(user_id)
    if research_only:
        await _handle_research_chat(update, llm, user_sess, text)
        return

    if user_sess.state == sess.AWAITING_PICK:
        await _handle_pick(update, settings, llm, user_sess, text)
    elif user_sess.state == sess.SCRIPT_DRAFT:
        if is_script_approval(text):
            await _handle_approval(update, settings)
        else:
            await _handle_refine(update, llm, user_sess, text)
    elif user_sess.state == sess.STORYBOARD_REVISION:
        from handlers import video

        await video.handle_revision_text(update, context, settings, llm)
    elif user_sess.state == sess.AWAITING_VIDEO:
        await _handle_chat(update, llm, user_sess, text)
    else:
        await _handle_chat(update, llm, user_sess, text)


# ---------------------------------------------------------------- awaiting_pick

async def _handle_pick(
    update: Update,
    settings: Settings,
    llm: LLMClient,
    user_sess,
    text: str,
) -> None:
    # How many stories are on offer? Look them up from the run.
    import db

    s = db.session()()
    try:
        available = (
            s.query(db.Story)
            .filter(
                db.Story.run_id == user_sess.run_id,
                db.Story.display_ok.is_(True),
            )
            .order_by(db.Story.rank)
            .all()
        )
    finally:
        s.close()
    num = len(available)

    chosen = news.parse_pick(text, num)
    if chosen is None:
        await update.message.reply_text(
            "Didn't catch that. Reply with story numbers (e.g. `1,3,5`) or `auto`.",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        "Building your talking notes : one moment…"
    )
    try:
        body = await asyncio.to_thread(
            news.write_initial_script, settings, llm, update.effective_user.id, chosen
        )
    except LLMError as e:
        await update.message.reply_text(f"AI error creating talking notes: {e}")
        return

    user_sess = sess.load(update.effective_user.id)
    script_id = news.current_script_id(update.effective_user.id)
    await update.message.reply_text(
        news.script_banner(body, user_sess.script_version),
        parse_mode="Markdown",
        reply_markup=__import__("handlers.video", fromlist=["script_keyboard"]).script_keyboard(
            script_id
        ),
    )


# ---------------------------------------------------------------- script_draft

async def _handle_approval(update: Update, settings: Settings) -> None:
    try:
        script, _job = news.approve_script(
            update.effective_user.id,
            update.effective_chat.id,
            stale_hours=settings.video_artifact_retention_hours,
        )
    except ValueError as exc:
        await update.message.reply_text(f"Could not lock the talking notes: {exc}")
        return
    await update.message.reply_text(
        "✅ Talking notes locked.\n\n"
        f"{script.body}\n\n"
        "Record yourself speaking naturally from these notes in OBS as a 16:9 MP4, then upload the file here."
    )


async def _handle_refine(
    update: Update, llm: LLMClient, user_sess, text: str
) -> None:
    user_id = update.effective_user.id
    current = user_sess.current_script or ""

    # Build refinement prompt and call the LLM in a thread.
    try:
        new_body = await asyncio.to_thread(
            llm.refine_script,
            prompts.REFINE_SYSTEM,
            prompts.build_refine_prompt(current, text),
        )
    except LLMError as e:
        await update.message.reply_text(f"AI error refining talking notes: {e}")
        return

    sess.set_current_script(user_id, new_body)
    # Persist this version and bind its immutable row ID to the button.
    script_id = news._save_script(user_id, new_body, user_sess.story_ids or [])

    user_sess = sess.load(user_id)
    await update.message.reply_text(
        news.script_banner(new_body, user_sess.script_version),
        parse_mode="Markdown",
        reply_markup=__import__("handlers.video", fromlist=["script_keyboard"]).script_keyboard(
            script_id
        ),
    )


# ---------------------------------------------------------------- scraper-only research chat

async def _handle_research_chat(
    update: Update, llm: LLMClient, user_sess, text: str
) -> None:
    context = await asyncio.to_thread(
        news.latest_research_context, update.effective_user.id
    )
    system = f"{prompts.RESEARCH_CHAT_SYSTEM}\n\n{context}"
    await _handle_chat(update, llm, user_sess, text, system=system)


# ---------------------------------------------------------------- idle / general chat

async def _handle_chat(
    update: Update,
    llm: LLMClient,
    user_sess,
    text: str,
    system: str = prompts.CHAT_SYSTEM,
) -> None:
    user_id = update.effective_user.id
    history = list(user_sess.history or [])

    try:
        reply = await asyncio.to_thread(
            llm.chat, system, history, text
        )
    except LLMError as e:
        await update.message.reply_text(f"AI error: {e}")
        return

    # Update rolling history for the next turn.
    sess.append_history(user_id, "user", text)
    sess.append_history(user_id, "assistant", reply)

    await update.message.reply_text(reply)
