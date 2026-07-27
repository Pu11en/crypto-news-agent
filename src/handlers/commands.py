"""Flow-control commands: /done and /cancel.

Both exit the active script session. /done marks the current draft as the
final version and saves it; /cancel discards it. Either way the user goes
back to idle / general chat.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from handlers import session as sess
from handlers import news


def register(app: Application, user_filter) -> None:
    app.add_handler(CommandHandler("done", _done, filters=user_filter))
    app.add_handler(CommandHandler("cancel", _cancel, filters=user_filter))


async def _done(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_sess = sess.load(user_id)
    if user_sess.state != sess.SCRIPT_DRAFT:
        await update.message.reply_text(
            "Nothing to save — you're not in a script session. Use /news to start one."
        )
        return
    body = news.finalize_script(user_id)
    if body:
        await update.message.reply_text(
            f"✅ Saved as final.\n\n{body}"
        )
    else:
        await update.message.reply_text("No draft to save.")


async def _cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_sess = sess.load(user_id)
    if user_sess.state == sess.IDLE:
        await update.message.reply_text("Nothing to cancel.")
        return
    sess.reset(user_id)
    await update.message.reply_text(
        "🗑 Cancelled. You're back to general chat — /news to start again."
    )
