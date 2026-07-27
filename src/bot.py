"""Bot entrypoint.

Wires up:
  - config + DB + LLM client (singletons, built once at boot)
  - a framework-level allowlist filter so only listed Telegram user IDs can
    trigger any handler (unauthorized updates are dropped before our code runs)
  - all handlers (commands + free-text router)

Run locally:  python src/bot.py
Run in prod:  same — the Dockerfile CMD is `python src/bot.py`.
"""

from __future__ import annotations

import logging
import os
import sys

from telegram.ext import (
    Application,
    ApplicationBuilder,
    filters,
)

# When launched as `python src/bot.py`, `src` isn't on sys.path by default —
# add it so `import config`, `import db`, etc. resolve.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import db  # noqa: E402
from handlers import chat, commands, news  # noqa: E402
from llm import GLMClient  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
# Quiet the chatty libs.
for noisy in ("httpx", "telegram", "urllib3", "openai"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("agent.bot")


def main() -> None:
    log.info("Starting crypto news agent…")
    settings = config.load()

    if not settings.allowed_user_ids:
        log.warning(
            "ALLOWED_USER_IDS is empty — NO ONE will be able to use the bot."
        )

    # Initialise DB before the LLM, since handlers depend on it.
    db.init_engine(settings.db_path)

    llm = GLMClient(settings)

    app: Application = (
        ApplicationBuilder().token(settings.telegram_bot_token).build()
    )

    # Framework-level allowlist: build a user filter once and pass it to every
    # handler. Updates from non-allowlisted users never reach our code — the
    # Application drops them at the filter stage.
    if settings.allowed_user_ids:
        user_filter = filters.User(user_id=set(settings.allowed_user_ids))
    else:
        # Empty allowlist → match nobody (deny-all) rather than nobody → match all.
        user_filter = ~filters.ALL
    log.info("Allowlist: %s", settings.allowed_user_ids or "(empty!)")

    news.register(app, settings, llm, user_filter)
    commands.register(app, user_filter)
    chat.register(app, settings, llm, user_filter)

    app.run_polling()


if __name__ == "__main__":
    main()
