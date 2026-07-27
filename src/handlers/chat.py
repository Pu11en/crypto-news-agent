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
)
_NEGATING_WORDS = ("not ", "almost ", "but ", "change ", "fix ", "revise ")


def is_script_approval(text: str) -> bool:
    """Recognize explicit approval while avoiding common revision phrases."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    if any(word in normalized for word in _NEGATING_WORDS):
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


async def _route(
    update: Update,
    _: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    llm: LLMClient,
) -> None:
    user_id = update.effective_user.id
    text = update.message.text or ""
    user_sess = sess.load(user_id)

    if user_sess.state == sess.AWAITING_PICK:
        await _handle_pick(update, settings, llm, user_sess, text)
    elif user_sess.state == sess.SCRIPT_DRAFT:
        if is_script_approval(text):
            await _handle_approval(update)
        else:
            await _handle_refine(update, llm, user_sess, text)
    elif user_sess.state == sess.STORYBOARD_REVISION:
        from handlers import video

        await video.handle_revision_text(update, _, settings, llm)
    elif user_sess.state == sess.AWAITING_VIDEO:
        await update.message.reply_text(
            "This video job is active. Upload the OBS MP4 or use the buttons on the current status message."
        )
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
            .filter(db.Story.run_id == user_sess.run_id)
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
        "Writing the script : one moment…"
    )
    try:
        body = await asyncio.to_thread(
            news.write_initial_script, settings, llm, update.effective_user.id, chosen
        )
    except LLMError as e:
        await update.message.reply_text(f"AI error writing script: {e}")
        return

    user_sess = sess.load(update.effective_user.id)
    await update.message.reply_text(
        news.script_banner(body, user_sess.script_version),
        parse_mode="Markdown",
        reply_markup=__import__("handlers.video", fromlist=["script_keyboard"]).script_keyboard(),
    )


# ---------------------------------------------------------------- script_draft

async def _handle_approval(update: Update) -> None:
    try:
        script, _job = news.approve_script(
            update.effective_user.id, update.effective_chat.id
        )
    except ValueError as exc:
        await update.message.reply_text(f"Could not approve the script: {exc}")
        return
    await update.message.reply_text(
        "✅ Script approved and locked.\n\n"
        f"{script.body}\n\n"
        "Record yourself reading it in OBS as a 16:9 MP4, then upload the file here."
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
        await update.message.reply_text(f"AI error refining script: {e}")
        return

    sess.set_current_script(user_id, new_body)
    # Persist this version.
    news._save_script(user_id, new_body, user_sess.story_ids or [])

    user_sess = sess.load(user_id)
    await update.message.reply_text(
        news.script_banner(new_body, user_sess.script_version),
        parse_mode="Markdown",
        reply_markup=__import__("handlers.video", fromlist=["script_keyboard"]).script_keyboard(),
    )


# ---------------------------------------------------------------- idle / general chat

async def _handle_chat(update: Update, llm: LLMClient, user_sess, text: str) -> None:
    user_id = update.effective_user.id
    history = list(user_sess.history or [])

    try:
        reply = await asyncio.to_thread(
            llm.chat, prompts.CHAT_SYSTEM, history, text
        )
    except LLMError as e:
        await update.message.reply_text(f"AI error: {e}")
        return

    # Update rolling history for the next turn.
    sess.append_history(user_id, "user", text)
    sess.append_history(user_id, "assistant", reply)

    await update.message.reply_text(reply)
