"""Flow-control commands: /done and /cancel.

Both exit the active script session. /done marks the current draft as the
final version and saves it; /cancel discards it. Either way the user goes
back to idle / general chat.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from handlers import news
from handlers import session as sess
from video import jobs


def register(app: Application, user_filter) -> None:
    app.add_handler(CommandHandler("done", _done, filters=user_filter))
    app.add_handler(CommandHandler("cancel", _cancel, filters=user_filter))


async def _done(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_sess = sess.load(user_id)
    if user_sess.state != sess.SCRIPT_DRAFT:
        await update.message.reply_text(
            "Nothing to save : you're not in a script session. Use /news to start one."
        )
        return
    try:
        script, _job = news.approve_script(user_id, update.effective_chat.id)
    except ValueError:
        script = None
    if script:
        await update.message.reply_text(
            "✅ Script approved and locked.\n\n"
            f"{script.body}\n\n"
            "Record yourself reading it in OBS as a 16:9 MP4, then upload the file here."
        )
    else:
        await update.message.reply_text("No draft to save.")


async def _cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    active = jobs.get_active_for_user(user_id)
    if active is not None:
        jobs.request_cancel(active.id)
        current = jobs.get(active.id)
        if current and current.state in {
            jobs.AWAITING_VIDEO,
            jobs.AWAITING_CAPTIONS,
            jobs.STORYBOARD_REVIEW,
            jobs.FAILED,
        }:
            jobs.mark_cancelled(active.id)
        sess.reset(user_id)
        await update.message.reply_text(
            "❌ Video job cancelled. Send /news to start again."
        )
        return
    user_sess = sess.load(user_id)
    if user_sess.state == sess.IDLE:
        await update.message.reply_text("Nothing to cancel.")
        return
    sess.reset(user_id)
    await update.message.reply_text(
        "🗑 Cancelled. You're back to general chat : /news to start again."
    )
