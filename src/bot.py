"""Bot entrypoint.

Wires up:
  - config + DB + LLM client (singletons, built once at boot)
  - a framework-level allowlist filter so only listed Telegram user IDs can
    trigger any handler (unauthorized updates are dropped before our code runs)
  - natural-language production router; local full mode retains legacy commands

Run locally:  python src/bot.py
Run in prod:  same : the Dockerfile CMD is `python src/bot.py`.
"""

from __future__ import annotations

import logging
import os
import sys

# When launched as `python src/bot.py`, `src` isn't on sys.path by default :
# add it so `import config`, `import db`, etc. resolve.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env from the project root (one level up from src/) before config runs.
# Safe in prod: if no .env exists (e.g. Railway), this is a no-op.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from telegram.ext import (  # noqa: E402
    Application,
    ApplicationBuilder,
    filters,
)

import config  # noqa: E402
import db  # noqa: E402
from handlers import chat, credits, news  # noqa: E402
from llm import LLMClient  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s : %(message)s",
)
# Quiet the chatty libs.
for noisy in ("httpx", "telegram", "urllib3", "openai"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("agent.bot")


async def _clear_command_menu(app: Application) -> None:
    """Remove Telegram's slash-command menu in natural-language production mode."""
    await app.bot.delete_my_commands()
    log.info("Cleared Telegram command menu; production is natural-language only")


def _register_handlers(app, settings, llm, user_filter) -> None:
    """Register the mode's public interface.

    Production scraper mode exposes only free-text language. Full local mode
    retains the legacy command and video-development interface.
    """
    if settings.bot_mode == "scraper":
        chat.register(app, settings, llm, user_filter)
        return

    news.register(app, settings, llm, user_filter)
    from handlers import commands, video

    commands.register(app, user_filter, settings)
    video.register(app, settings, llm, user_filter)
    chat.register(app, settings, llm, user_filter)
    credits.register(app, settings, user_filter)


def main() -> None:
    log.info("Starting crypto news agent…")
    settings = config.load()

    # Initialise DB before the LLM, since handlers depend on it.
    db.init_engine(settings.db_path)
    if settings.bot_mode == "full":
        from video import pipeline

        removed = pipeline.prune_artifacts(settings)
        if removed:
            log.info("Pruned %d stale video job directories", len(removed))
    else:
        log.info("BOT_MODE=scraper: script and video features are disabled")

    llm = LLMClient(settings)

    builder = ApplicationBuilder().token(settings.telegram_bot_token)
    if settings.bot_mode == "scraper":
        builder = builder.post_init(_clear_command_menu)
    app: Application = builder.build()

    # Framework-level allowlist: build a user filter once and pass it to every
    # handler. Updates from non-allowlisted users never reach our code : the
    # Application drops them at the filter stage.
    #
    # If ALLOWED_USER_IDS is empty, the bot is OPEN TO EVERYONE (any Telegram
    # user can trigger it). Set the env var to restrict access to specific IDs.
    if settings.allowed_user_ids:
        user_filter = filters.User(user_id=set(settings.allowed_user_ids))
        log.info("Allowlist: %s", settings.allowed_user_ids)
    else:
        user_filter = filters.ALL
        log.warning(
            "ALLOWED_USER_IDS is empty: bot is OPEN TO EVERYONE. "
            "Anyone who finds the bot can trigger scrapes."
        )

    _register_handlers(app, settings, llm, user_filter)

    app.run_polling()


if __name__ == "__main__":
    main()
