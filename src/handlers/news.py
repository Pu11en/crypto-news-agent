"""The /news flow : scrape → curate → pick → script.

State machine (session.state drives routing in chat.py):
    idle → (run /news) → awaiting_pick → (user picks) → script_draft → (/done) → idle

Blocking work (scraping 100+ accounts, LLM calls) runs in threads via
asyncio.to_thread so the async Telegram event loop never stalls.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlalchemy import and_, func
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import db
import prompts
import reporting
from accounts import load_accounts
from config import Settings
from handlers import session as sess
from llm import LLMClient, LLMError
from video import jobs
from xquik import RawTweet, XquikClient, XquikError

log = logging.getLogger("agent.news")

# Cap concurrency on the scrape : Xquik tolerates ~10 req/s.
_SCRAPE_WORKERS = 8
_NEWS_IN_PROGRESS = False
SCRIPT_BANNER = "🎙️ *Talking notes* (v%d)\n\n%s\n\n_Talk to me to refine them, or_ /done _to lock,_ /cancel _to discard._"

# Default-deny vetting thresholds (see wayfinder map #2 / build #6). A story
# clears vetting only if it is grounded in a real scraped tweet, scores at least
# the newsworthiness bar the prompt already calls "important", and falls within
# the hard cap. Everything else is stamped display_ok=0 with a cut_reason.
_SCORE_FLOOR = 0.7


def _scraper_only(settings) -> bool:
    return getattr(settings, "bot_mode", "full") == "scraper"


def register(
    app: Application,
    settings: Settings,
    llm: LLMClient,
    user_filter,
) -> None:
    app.add_handler(
        CommandHandler(
            "news",
            lambda u, c: _news(u, c, settings, llm),
            filters=user_filter,
        )
    )
    app.add_handler(
        CommandHandler(
            "freshnews",
            lambda u, c: _fresh_news(u, c, settings, llm),
            filters=user_filter,
        )
    )
    app.add_handler(
        CommandHandler(
            "start", lambda u, c: _start(u, c, settings), filters=user_filter
        )
    )
    app.add_handler(
        CommandHandler(
            "help", lambda u, c: _help(u, c, settings), filters=user_filter
        )
    )
    app.add_handler(
        CommandHandler(
            "scrapes",
            lambda u, c: _scrapes(u, c, settings),
            filters=user_filter,
        )
    )
    app.add_handler(
        CommandHandler(
            "continue",
            lambda u, c: _continue_news(u, c, settings),
            filters=user_filter,
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: _scrape_callback(u, c, settings),
            pattern=r"^scrape:",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: _news_choice_callback(u, c, settings, llm),
            pattern=r"^news-choice:",
        )
    )


async def _start(
    update: Update, _: ContextTypes.DEFAULT_TYPE, settings: Settings
) -> None:
    if _scraper_only(settings):
        body = (
            "Crypto news research agent ready.\n\n"
            "/news : review the latest scrape or request a new one\n"
            "/freshnews : explicitly start a new scrape\n"
            "/scrapes : browse every saved scrape and raw post\n"
            "/continue : reopen the latest saved research\n"
            "/credits : check Xquik credit balance\n"
            "/account : full Xquik account summary\n"
            "/help : this message\n\n"
            "Automatic script generation, video uploads, captions, and rendering "
            "are disabled. You can ask me to help draft or revise a script in chat."
        )
    else:
        body = (
            "Crypto news agent ready.\n\n"
            "/news : resume the current scrape, notes, or video\n"
            "/freshnews : start a new scrape after /cancel\n"
            "/scrapes : browse your saved scrapes\n"
            "/continue : reopen your current saved work\n"
            "/done : lock the current talking notes\n"
            "/cancel : discard and exit\n"
            "/credits : check Xquik credit balance\n"
            "/account : full Xquik account summary\n"
            "/help : this message\n\n"
            "Or just talk to me normally."
        )
    await update.message.reply_text(body)


async def _help(
    update: Update, _: ContextTypes.DEFAULT_TYPE, settings: Settings
) -> None:
    await _start(update, _, settings)


async def _scrapes(
    update: Update, _: ContextTypes.DEFAULT_TYPE, settings: Settings
) -> None:
    await _reply_saved_scrapes(
        update.message,
        update.effective_user.id,
        research_only=_scraper_only(settings),
    )


async def _continue_news(
    update: Update,
    _: ContextTypes.DEFAULT_TYPE,
    settings: Settings | None = None,
) -> None:
    """Reopen existing work without ever starting a scrape."""
    user_id = update.effective_user.id
    if not await _resume_existing_work(
        update.message,
        user_id,
        research_only=_scraper_only(settings),
    ):
        await update.message.reply_text(
            "There is no saved work to continue. Ask me to get fresh crypto "
            "news if you want to start a scrape."
        )


async def _news(
    update: Update,
    _: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    llm: LLMClient,
) -> None:
    """Show the latest scrape and let the user continue it or request a new one."""
    if _NEWS_IN_PROGRESS:
        await update.message.reply_text(
            "Another news scrape is already running. Try again after it finishes."
        )
        return

    user_id = update.effective_user.id
    saved = list_saved_scrapes(user_id, limit=1)
    research_only = _scraper_only(settings)
    active_video = None if research_only else jobs.get_active_for_user(user_id)
    current = sess.load(user_id)
    await _show_news_choice(
        update.message,
        user_id,
        saved[0] if saved else None,
        active_video,
        current,
        research_only=research_only,
    )


async def _fresh_news(
    update: Update,
    _: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    llm: LLMClient,
) -> None:
    """Explicit, credit-spending command for a brand-new scrape."""
    await _start_fresh_news(
        update.message, update.effective_user.id, settings, llm
    )


def _current_work_status(active_video, current) -> str:
    if active_video is not None:
        labels = {
            jobs.AWAITING_VIDEO: "video waiting for your recording",
            jobs.AWAITING_CAPTIONS: "recording uploaded; captions choice pending",
            jobs.PLANNING: "storyboard processing",
            jobs.STORYBOARD_REVIEW: "visual review pending",
            jobs.RENDERING: "video rendering",
            jobs.DELIVERING: "finished video delivery",
            jobs.FAILED: f"video failed during {active_video.failed_stage or 'processing'}",
        }
        return labels.get(active_video.state, active_video.state.replace("_", " "))
    labels = {
        sess.AWAITING_PICK: "stories ready for selection",
        sess.SCRIPT_DRAFT: "talking-notes draft in progress",
        sess.AWAITING_VIDEO: "talking notes locked; recording pending",
        sess.STORYBOARD_REVISION: "storyboard revision in progress",
        sess.SCRAPING: "scrape in progress",
        sess.IDLE: "no active draft or video",
    }
    return labels.get(current.state, current.state.replace("_", " "))


def _news_choice_keyboard(
    has_current_work: bool, has_saved_scrape: bool
) -> InlineKeyboardMarkup:
    rows = []
    if has_current_work or has_saved_scrape:
        continue_label = (
            "♻️ Continue current" if has_current_work else "♻️ Use last scrape"
        )
        rows.append(
            [
                InlineKeyboardButton(
                    continue_label, callback_data="news-choice:resume"
                )
            ]
        )
    fresh_label = (
        "🆕 Start new scrape"
        if has_saved_scrape
        else "🆕 Start first scrape"
    )
    rows.append(
        [
            InlineKeyboardButton(
                fresh_label, callback_data="news-choice:fresh"
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


async def _show_news_choice(
    message,
    user_id: int,
    latest: dict | None,
    active_video,
    current,
    research_only: bool = False,
) -> None:
    lines = ["🗞 Latest scrape information", ""]
    if latest is None:
        lines.append("No saved scrape metadata is available.")
    else:
        started = latest["started_at"]
        when = (
            started.strftime("%b %d, %Y at %I:%M %p UTC")
            if started is not None
            else "unknown time"
        )
        lines.extend(
            [
                f"Date: {when}",
                f"Posts collected: {latest['tweets_fetched']}",
                f"Accounts reached: {latest['accounts_hit']}",
                f"Curated stories: {latest['story_count']}",
            ]
        )
    has_saved_scrape = latest is not None
    if research_only:
        has_current_work = has_saved_scrape
        status = (
            "latest saved research is ready to review"
            if has_saved_scrape
            else "no saved scrape"
        )
    else:
        has_current_work = active_video is not None or current.state != sess.IDLE
        status = _current_work_status(active_video, current)
    question = (
        "Review this saved research, or explicitly request a new scrape?"
        if research_only and has_saved_scrape
        else (
            "Continue with this work, or explicitly request a new scrape?"
            if has_current_work or has_saved_scrape
            else "No saved scrape exists. Start the first scrape?"
        )
    )
    lines.extend(["", f"Current work: {status}", "", question])
    await message.reply_text(
        "\n".join(lines),
        reply_markup=_news_choice_keyboard(
            has_current_work, has_saved_scrape
        ),
    )


async def _news_choice_callback(
    update: Update,
    _: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    llm: LLMClient,
) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    action = (query.data or "").split(":", 1)[-1]
    user_id = update.effective_user.id
    log.info("News choice received: user=%s action=%s", user_id, action)
    if action == "resume":
        await query.answer("Opening your saved work…")
    else:
        await query.answer()

    research_only = _scraper_only(settings)
    if action == "resume":
        if not await _resume_existing_work(
            query.message, user_id, research_only=research_only
        ):
            await query.message.reply_text(
                "There is no saved work to continue. Choose Start new scrape."
            )
        return

    if action != "fresh":
        await query.message.reply_text("That news choice is invalid or expired.")
        return

    if not research_only:
        active_video = jobs.get_active_for_user(user_id)
        current = sess.load(user_id)
        if active_video is not None or current.state not in {
            sess.IDLE,
            sess.AWAITING_PICK,
        }:
            await query.message.reply_text(
                "Your current notes or video are protected. Choose Continue current, "
                "or send /cancel before requesting a new scrape."
            )
            return
        if current.state == sess.AWAITING_PICK:
            sess.reset(user_id)
    await _start_fresh_news(query.message, user_id, settings, llm)


def _resume_video_keyboard(job) -> InlineKeyboardMarkup:
    if job.state == jobs.FAILED:
        rows = [
            [
                InlineKeyboardButton(
                    "🔄 Retry", callback_data=f"video:retry:{job.id}"
                ),
                InlineKeyboardButton(
                    "❌ Cancel", callback_data=f"video:cancel:{job.id}"
                ),
            ]
        ]
    elif job.state == jobs.AWAITING_CAPTIONS:
        rows = [
            [
                InlineKeyboardButton(
                    "Captions on", callback_data=f"video:captions-on:{job.id}"
                ),
                InlineKeyboardButton(
                    "Captions off", callback_data=f"video:captions-off:{job.id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel", callback_data=f"video:cancel:{job.id}"
                )
            ],
        ]
    elif job.state == jobs.STORYBOARD_REVIEW:
        rows = [
            [
                InlineKeyboardButton(
                    "✅ Approve visuals",
                    callback_data=f"video:approve-visuals:{job.id}",
                ),
                InlineKeyboardButton(
                    "✏️ Revise visuals",
                    callback_data=f"video:revise-visuals:{job.id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel", callback_data=f"video:cancel:{job.id}"
                )
            ],
        ]
    elif job.state == jobs.AWAITING_VIDEO:
        rows = []
    else:
        rows = [
            [
                InlineKeyboardButton(
                    "❌ Cancel", callback_data=f"video:cancel:{job.id}"
                )
            ]
        ]
    return InlineKeyboardMarkup(rows)


async def _reply_active_video(message, active_video) -> None:
    state_labels = {
        jobs.AWAITING_VIDEO: "waiting for your recording",
        jobs.AWAITING_CAPTIONS: "recording received; choose captions",
        jobs.PLANNING: "building the storyboard",
        jobs.STORYBOARD_REVIEW: "waiting for visual approval",
        jobs.RENDERING: "rendering",
        jobs.DELIVERING: "delivering the finished video",
        jobs.FAILED: f"failed during {active_video.failed_stage or 'processing'}",
    }
    status = state_labels.get(active_video.state, active_video.state.replace("_", " "))
    lines = [
        "♻️ Resuming your current video — no new scrape was started.",
        "",
        f"Status: {status}",
    ]

    if active_video.state == jobs.AWAITING_VIDEO:
        factory = db.session()
        with factory() as s:
            script = s.get(db.Script, active_video.script_id)
            body = script.body if script is not None else ""
        if body:
            lines.extend(["", body])
        lines.extend(["", "Upload your OBS MP4 when you are ready."])
    elif active_video.state == jobs.FAILED:
        lines.extend(
            [
                "",
                "The same scrape, talking notes, recording, and completed artifacts "
                "are still attached to this job.",
            ]
        )
    else:
        lines.extend(["", "Your existing scrape and talking notes are protected."])

    keyboard = _resume_video_keyboard(active_video)
    await message.reply_text(
        "\n".join(lines),
        reply_markup=keyboard if keyboard.inline_keyboard else None,
    )


async def _resume_existing_work(
    message, user_id: int, research_only: bool = False
) -> bool:
    if research_only:
        saved = list_saved_scrapes(user_id, limit=1)
        if not saved:
            return False
        if int(saved[0]["story_count"]) == 0:
            await message.reply_text(
                "Your latest saved scrape contains no curated stories. "
                "Ask me to show your saved scrapes so you can inspect its raw posts."
            )
            return True
        await message.reply_text(
            "♻️ Reusing your latest saved research — no credits were spent."
        )
        await _reply_opened_scrape(
            message,
            user_id,
            saved[0]["run_id"],
            research_only=True,
        )
        return True

    active_video = jobs.get_active_for_user(user_id)
    if active_video is not None:
        await _reply_active_video(message, active_video)
        return True

    user_sess = sess.load(user_id)
    if user_sess.state == sess.SCRIPT_DRAFT and user_sess.current_script:
        from handlers import video

        await message.reply_text(
            script_banner(user_sess.current_script, user_sess.script_version),
            parse_mode="Markdown",
            reply_markup=video.script_keyboard(current_script_id(user_id)),
        )
        return True

    if user_sess.state == sess.AWAITING_VIDEO and user_sess.current_script:
        await message.reply_text(
            "♻️ Resuming your locked talking notes — no new scrape was started.\n\n"
            f"{user_sess.current_script}\n\n"
            "The video job is missing. Send /cancel to safely reset this incomplete "
            "state, then reopen the same scrape with /news."
        )
        return True

    if user_sess.state == sess.AWAITING_PICK and user_sess.run_id:
        await _reply_opened_scrape(message, user_id, user_sess.run_id)
        return True

    saved = list_saved_scrapes(user_id, limit=1)
    if saved:
        if int(saved[0]["story_count"]) == 0:
            await message.reply_text(
                "♻️ Your latest saved scrape found no curated stories. No new "
                "scrape was started. Use /freshnews only when you explicitly want "
                "to spend credits on another scrape."
            )
            return True
        await message.reply_text(
            "♻️ Reusing your latest saved scrape — no credits were spent."
        )
        await _reply_opened_scrape(message, user_id, saved[0]["run_id"])
        return True
    return False


async def _start_fresh_news(
    message,
    user_id: int,
    settings: Settings,
    llm: LLMClient,
) -> None:
    global _NEWS_IN_PROGRESS
    if _NEWS_IN_PROGRESS:
        await message.reply_text(
            "Another news scrape is already running. Try again after it finishes."
        )
        return

    research_only = _scraper_only(settings)
    if not research_only:
        active_video = jobs.get_active_for_user(user_id)
        current = sess.load(user_id)
        if active_video is not None or current.state != sess.IDLE:
            await message.reply_text(
                "Your current scrape, notes, or video is protected. Send /news to resume "
                "it. If you truly want to abandon it, send /cancel first and then "
                "/freshnews."
            )
            return

    _NEWS_IN_PROGRESS = True
    if not research_only:
        sess.set_state(user_id, sess.SCRAPING)

    try:
        await message.reply_text(
            f"🔍 Starting a NEW scrape of the last {settings.scrape_hours}h…"
        )
        result = await asyncio.to_thread(_scrape_and_curate, settings, llm, user_id)
    except XquikError as e:
        if not research_only:
            sess.reset(user_id)
        await message.reply_text(f"Scraper error: {e}")
        return
    except LLMError as e:
        if not research_only:
            sess.reset(user_id)
        await message.reply_text(f"AI error during curation: {e}")
        return
    except Exception:
        log.exception("unexpected error during news scrape")
        if not research_only:
            sess.reset(user_id)
        await message.reply_text("Unexpected scraper error. Please try again later.")
        return
    finally:
        _NEWS_IN_PROGRESS = False

    displayable = [s for s in result["stories"] if s.get("display_ok", True)]
    if not displayable:
        # Nothing cleared vetting (or the model returned nothing). Behave like an
        # empty curation: the scrape's raw posts are already saved, so deliver the
        # report in scraper mode rather than wedge on a story-less reopen.
        await message.reply_text(
            f"No newsworthy stories found in the last {settings.scrape_hours}h "
            f"(scraped {result['tweets_fetched']} tweets from "
            f"{result['accounts_hit']} accounts, ~{result['tweets_fetched']} credits). "
            "Try again later."
        )
        if not research_only:
            sess.reset(user_id)
        else:
            await _reply_scrape_report(
                message, user_id, result["run_id"], settings
            )
        return

    story_ids = _persist_stories(user_id, result["run_id"], result["stories"])
    if research_only:
        await _reply_opened_scrape(
            message,
            user_id,
            result["run_id"],
            research_only=True,
        )
        await _reply_scrape_report(
            message, user_id, result["run_id"], settings
        )
        return

    sess.set_run(user_id, result["run_id"], story_ids)
    await message.reply_text(
        _format_top_stories(
            displayable, result["tweets_fetched"], result["accounts_hit"]
        ),
        parse_mode="Markdown",
    )

def _vet_stories(
    raw_stories: list[dict], run_tweets: list[RawTweet], max_stories: int
) -> list[dict]:
    """Stamp each story with display_ok / cut_reason using deterministic guards.

    Applied in order (first cut wins): grounding, score floor, hard cap. The list
    is returned in full so the audit trail is preserved in the DB; callers that
    surface stories to the user read only display_ok=1 rows.
    """
    valid_ids = {t.tweet_id for t in run_tweets}

    # Hard cap is applied to score-ordered survivors only, so a story cut by the
    # cap is the lowest-scoring qualifying one, never a high scorer.
    scored: list[dict] = []
    for story in raw_stories:
        story_ids = {str(tid) for tid in story.get("tweet_ids", [])}
        if not story_ids.intersection(valid_ids):
            story["display_ok"] = False
            story["cut_reason"] = "ungrounded"
            continue
        try:
            score = float(story.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        if score < _SCORE_FLOOR:
            story["display_ok"] = False
            story["cut_reason"] = "low_score"
            continue
        story["display_ok"] = True
        story["cut_reason"] = None
        scored.append(story)

    # Stable sort by score desc (lower rank breaks ties): higher score wins the
    # cap, so the cap always drops the lowest-scoring qualifying survivors.
    scored.sort(
        key=lambda s: (float(s.get("score", 0.0)), -int(s.get("rank", 0))),
        reverse=True,
    )
    for survivor in scored[max_stories:]:
        survivor["display_ok"] = False
        survivor["cut_reason"] = "over_cap"
    return raw_stories


def _summarise_cuts(stories: list[dict]) -> dict:
    """Return per-run vetting counts for the observability log line."""
    counts: dict[str, int] = {}
    displayed = 0
    for story in stories:
        if story.get("display_ok"):
            displayed += 1
            continue
        reason = story.get("cut_reason") or "unknown"
        counts[reason] = counts.get(reason, 0) + 1
    return {"returned": len(stories), "displayed": displayed, "cuts": counts}


def _scrape_and_curate(
    settings: Settings, llm: LLMClient, user_id: int
) -> dict:
    """Blocking: fetch tweets, store them, ask the LLM to curate. Returns run meta."""
    run_id = uuid.uuid4().hex[:12]
    accounts = load_accounts()
    client = XquikClient(
        api_key=settings.xquik_api_key,
        allowed_usernames={account.username for account in accounts},
    )

    log.info("run %s: scraping %d allowlisted accounts via Xquik", run_id, len(accounts))

    tweets: list[RawTweet] = []
    errors: list[str] = []

    def fetch_one(acct):
        try:
            return client.fetch_recent_tweets(
                acct.username, settings.scrape_hours, settings.max_tweets_per_account
            )
        except XquikError as e:
            errors.append(f"{acct.username}: {e}")
            return []
        except Exception as e:  # noqa: BLE001 : don't let one account kill the run
            errors.append(f"{acct.username}: {e}")
            return []

    with ThreadPoolExecutor(max_workers=_SCRAPE_WORKERS) as pool:
        for batch in pool.map(fetch_one, accounts):
            tweets.extend(batch)

    log.info(
        "run %s: %d tweets from %d accounts (%d errors)",
        run_id,
        len(tweets),
        len(accounts) - len(errors),
        len(errors),
    )

    # Persist tweets + run row.
    _persist_tweets(user_id, run_id, tweets, accounts, errors)

    accounts_hit = len(accounts) - len(errors)
    # Xquik bills tweet search at 1 credit per tweet returned, so the
    # credit cost of a run equals the number of tweets fetched.
    # Ref: https://docs.xquik.com/guides/billing
    run_meta = {
        "run_id": run_id,
        "tweets_fetched": len(tweets),
        "accounts_hit": accounts_hit,
        "credit_cost": len(tweets),
        "stories": [],
    }

    if not tweets:
        return run_meta

    # Curate. Cap input size to avoid blowing context : newest first, and trim
    # very long tweets.
    trimmed = sorted(
        tweets,
        key=lambda t: t.created_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    prompt = prompts.build_curate_prompt(
        trimmed, settings.scrape_hours, max_stories=5
    )
    curated = llm.curate(prompts.CURATE_SYSTEM, prompt)
    stories = curated.get("stories", []) if isinstance(curated, dict) else []

    # Enforce the curation contract in code (default-deny at persist): a model
    # that over-produces or invents filler cannot leak past these guards into
    # the report or chat. Vetted-in-full so the audit trail survives.
    stories = _vet_stories(stories, tweets, max_stories=5)
    summary = _summarise_cuts(stories)
    log.info(
        "run %s: curation vetted %d returned -> %d displayed (cuts: %s)",
        run_id,
        summary["returned"],
        summary["displayed"],
        summary["cuts"] or "none",
    )

    # Enrich each story with clickable source tweet URLs.
    url_map = {t.tweet_id: t.url for t in tweets if t.url}
    for story in stories:
        story["source_urls"] = [
            url_map[tid] for tid in story.get("tweet_ids", []) if tid in url_map
        ]

    run_meta["stories"] = stories
    return run_meta


def _persist_tweets(
    user_id: int,
    run_id: str,
    tweets: list[RawTweet],
    accounts: list,
    errors: list[str],
) -> None:
    s = db.session()()
    try:
        # Build a username → tier/tags lookup for denormalising onto tweets.
        meta = {a.username: a for a in accounts}
        for t in tweets:
            acct = meta.get(t.username)
            row = db.Tweet(
                tweet_id=t.tweet_id,
                username=t.username,
                text=t.text,
                created_at=t.created_at,
                url=t.url,
                tier=acct.tier if acct else "medium",
                tags="|".join(acct.tags) if acct else "",
                run_id=run_id,
            )
            s.add(row)
        s.add(
            db.Run(
                id=run_id,
                user_id=user_id,
                finished_at=datetime.now(timezone.utc),
                tweets_fetched=len(tweets),
                accounts_hit=len(accounts) - len(errors),
                errors=errors[:50],
            )
        )
        s.commit()
    finally:
        s.close()


def _persist_stories(user_id: int, run_id: str, raw_stories: list[dict]) -> list[int]:
    s = db.session()()
    ids: list[int] = []
    try:
        for story in raw_stories:
            row = db.Story(
                run_id=run_id,
                rank=int(story.get("rank", 0)),
                headline=str(story.get("headline", "")).strip(),
                summary=str(story.get("summary", "")).strip(),
                tweet_ids=list(story.get("tweet_ids", [])),
                score=float(story.get("score", 0.0)),
                display_ok=bool(story.get("display_ok", True)),
                cut_reason=story.get("cut_reason"),
            )
            s.add(row)
            s.flush()
            ids.append(row.id)
        s.commit()
    finally:
        s.close()
    return ids


def _format_top_stories(
    stories: list[dict], tweets_fetched: int = 0, accounts_hit: int = 0
) -> str:
    lines = [f"📊 *Top {len(stories)} stories (last 24h)*", ""]
    # Cost footer: 1 credit per tweet returned (Xquik billing).
    # https://docs.xquik.com/guides/billing
    cost_note = ""
    if tweets_fetched:
        acct_note = f" from {accounts_hit} accounts" if accounts_hit else ""
        cost_note = (
            f"\n\n_({tweets_fetched} tweets scraped{acct_note} · "
            f"~{tweets_fetched} credits used · ask for your Xquik balance)_"
        )
    for s in stories:
        rank = s.get("rank", "?")
        headline = s.get("headline", "")
        summary = s.get("summary", "")
        score = s.get("score", 0)
        source_urls = s.get("source_urls", [])
        lines.append(f"*{rank}. {headline}*")
        lines.append(f"   {summary}")
        if source_urls:
            links = " · ".join(f"[source]({u})" for u in source_urls[:3])
            lines.append(f"   🔗 {links}")
        lines.append(f"   _newsworthiness: {score}_\n")
    lines.append(
        "Reply with story numbers (e.g. `1,3,5`) or `auto` to use all of them."
    )
    return "\n".join(lines) + cost_note


# ---------------------------------------------------------- saved scrape library

_SCRAPE_LIST_PATTERNS = (
    re.compile(
        r"\b(?:show|list|view|see)\s+(?:me\s+)?(?:my\s+)?"
        r"(?:saved\s+)?scrapes?\b"
    ),
    re.compile(r"\bwhat\s+(?:saved\s+)?scrapes?\s+do\s+i\s+have\b"),
)
_SCRAPE_RAW_PATTERN = re.compile(
    r"\b(?:show|view|open|see)\s+(?:the\s+)?(?:full|raw)\s+scrape\s+#?(\d+)\b"
)
_SCRAPE_OPEN_PATTERN = re.compile(
    r"\b(?:open|view|show|see|return\s+to|go\s+back\s+to)\s+"
    r"(?:the\s+)?(?:saved\s+)?scrape\s+#?(\d+)\b"
)
_SCRAPE_LAST_PATTERN = re.compile(
    r"\b(?:open|view|show|see|return\s+to|go\s+back\s+to)\s+"
    r"(?:the\s+)?(?:last|latest|newest)(?:\s+saved)?\s+scrape\b"
)


def parse_scrape_intent(text: str) -> tuple[str, int | None] | None:
    """Recognize common saved-scrape navigation requests."""
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    raw_match = _SCRAPE_RAW_PATTERN.search(normalized)
    if raw_match:
        return ("raw", int(raw_match.group(1)))
    if _SCRAPE_LAST_PATTERN.search(normalized):
        return ("open", 1)
    open_match = _SCRAPE_OPEN_PATTERN.search(normalized)
    if open_match:
        return ("open", int(open_match.group(1)))
    if any(pattern.search(normalized) for pattern in _SCRAPE_LIST_PATTERNS):
        return ("list", None)
    return None


def list_saved_scrapes(user_id: int, limit: int = 10) -> list[dict]:
    """Return newest-first scrape summaries owned by one Telegram user."""
    bounded_limit = max(1, min(int(limit), 50))
    factory = db.session()
    with factory() as s:
        rows = (
            s.query(
                db.Run,
                func.count(func.distinct(db.Story.id)).label("story_count"),
            )
            .outerjoin(
                db.Story,
                and_(
                    db.Story.run_id == db.Run.id,
                    db.Story.display_ok.is_(True),
                ),
            )
            .filter(db.Run.user_id == user_id)
            .group_by(db.Run.id)
            .order_by(db.Run.started_at.desc(), db.Run.id.desc())
            .limit(bounded_limit)
            .all()
        )
        return [
            {
                "run_id": run.id,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "tweets_fetched": run.tweets_fetched,
                "accounts_hit": run.accounts_hit,
                "story_count": int(story_count),
            }
            for run, story_count in rows
        ]


def raw_scrape_page(
    user_id: int,
    run_id: str,
    page: int = 0,
    page_size: int = 5,
) -> dict:
    """Return one bounded page of full raw posts from a user-owned scrape."""
    page = max(0, int(page))
    page_size = max(1, min(int(page_size), 20))
    factory = db.session()
    with factory() as s:
        owned_run = (
            s.query(db.Run)
            .filter(db.Run.id == str(run_id), db.Run.user_id == user_id)
            .first()
        )
        if owned_run is None:
            raise ValueError("Saved scrape not found")
        query = (
            s.query(db.Tweet)
            .filter(db.Tweet.run_id == owned_run.id)
            .order_by(db.Tweet.created_at.desc(), db.Tweet.id.desc())
        )
        total = query.count()
        pages = max(1, math.ceil(total / page_size))
        page = min(page, pages - 1)
        posts = query.offset(page * page_size).limit(page_size).all()
        return {
            "run_id": owned_run.id,
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "total": total,
            "has_previous": page > 0,
            "has_next": page + 1 < pages,
            "posts": [
                {
                    "tweet_id": post.tweet_id,
                    "username": post.username,
                    "text": post.text,
                    "created_at": post.created_at,
                    "url": post.url,
                    "tier": post.tier,
                    "tags": post.tags,
                }
                for post in posts
            ],
        }


def _resolve_saved_run(user_id: int, reference: int | str) -> db.Run:
    factory = db.session()
    with factory() as s:
        query = s.query(db.Run).filter(db.Run.user_id == user_id)
        if isinstance(reference, int):
            if reference < 1:
                raise ValueError("Saved scrape not found")
            row = (
                query.order_by(db.Run.started_at.desc(), db.Run.id.desc())
                .offset(reference - 1)
                .first()
            )
        else:
            row = query.filter(db.Run.id == str(reference)).first()
        if row is None:
            raise ValueError("Saved scrape not found")
        s.expunge(row)
        return row


def load_saved_scrape(user_id: int, reference: int | str) -> dict:
    """Load a user-owned scrape without changing conversation or media state."""
    run = _resolve_saved_run(user_id, reference)
    factory = db.session()
    with factory() as s:
        stories = (
            s.query(db.Story)
            .filter(db.Story.run_id == run.id, db.Story.display_ok.is_(True))
            .order_by(db.Story.rank, db.Story.id)
            .all()
        )
        source_ids = [
            str(tweet_id)
            for story in stories
            for tweet_id in (story.tweet_ids or [])
        ]
        source_rows = (
            s.query(db.Tweet)
            .filter(
                db.Tweet.run_id == run.id,
                db.Tweet.tweet_id.in_(source_ids),
            )
            .all()
            if source_ids
            else []
        )
        source_map = {str(post.tweet_id): post for post in source_rows}
        story_data = []
        for story in stories:
            tweet_ids = [str(value) for value in (story.tweet_ids or [])]
            source_posts = []
            for tweet_id in tweet_ids:
                post = source_map.get(tweet_id)
                if post is None:
                    continue
                source_posts.append(
                    {
                        "tweet_id": tweet_id,
                        "username": post.username,
                        "created_at": post.created_at,
                        "text": post.text,
                        "url": post.url,
                    }
                )
            story_data.append(
                {
                    "id": story.id,
                    "rank": story.rank,
                    "headline": story.headline,
                    "summary": story.summary,
                    "tweet_ids": tweet_ids,
                    "source_posts": source_posts,
                    "score": story.score,
                }
            )

    if not story_data:
        raise ValueError("Saved scrape has no curated stories to reopen")
    return {
        "run_id": run.id,
        "started_at": run.started_at,
        "tweets_fetched": run.tweets_fetched,
        "accounts_hit": run.accounts_hit,
        "stories": story_data,
    }


def latest_research_context(user_id: int) -> str:
    """Return grounded context from the latest saved scrape for research chat."""
    saved = list_saved_scrapes(user_id, limit=1)
    if not saved or int(saved[0]["story_count"]) == 0:
        return "LATEST SAVED SCRAPE: none with curated stories."
    opened = load_saved_scrape(user_id, saved[0]["run_id"])
    lines = [
        f"LATEST SAVED SCRAPE: {opened['run_id']}",
        f"POSTS COLLECTED: {opened['tweets_fetched']}",
        "",
    ]
    for story in opened["stories"]:
        lines.append(f"STORY {story['rank']}: {story['headline']}")
        lines.append(f"SUMMARY: {story['summary']}")
        for source in story.get("source_posts", []):
            timestamp = _format_source_timestamp(source.get("created_at"))
            lines.append(
                f"SOURCE @{source.get('username', 'unknown')} | {timestamp}"
            )
            source_text = str(source.get("text") or "").strip()
            if source_text:
                lines.append(f"POST: {source_text[:700]}")
            if source.get("url"):
                lines.append(f"URL: {source['url']}")
        lines.append("")
    return "\n".join(lines)


def open_saved_scrape(user_id: int, reference: int | str) -> dict:
    """Restore a user-owned scrape as the active story-picking context."""
    opened = load_saved_scrape(user_id, reference)
    active_video = jobs.get_active_for_user(user_id)
    if active_video is not None:
        raise ValueError(
            "Your current video is protected. Send /news to resume it, or "
            "send /cancel before opening another scrape."
        )
    current = sess.load(user_id)
    if current.state in {
        sess.SCRIPT_DRAFT,
        sess.AWAITING_VIDEO,
        sess.STORYBOARD_REVISION,
    } and current.current_script:
        raise ValueError(
            "Your current talking notes are protected. Send /news to resume "
            "them, or send /cancel before opening another scrape."
        )

    sess.set_run(
        user_id,
        opened["run_id"],
        [story["id"] for story in opened["stories"]],
    )
    return opened


def _scrape_callback_data(
    action: str,
    run_id: str | None = None,
    page: int | None = None,
) -> str:
    parts = ["scrape", action]
    if run_id is not None:
        parts.append(run_id)
    if page is not None:
        parts.append(str(page))
    data = ":".join(parts)
    if len(data.encode("utf-8")) > 64:
        raise ValueError("Saved scrape ID is too long for Telegram callback data")
    return data


def _saved_scrapes_keyboard(
    saved: list[dict], research_only: bool = False
) -> InlineKeyboardMarkup:
    rows = []
    for index, item in enumerate(saved, start=1):
        run_id = str(item["run_id"])
        row = [
            InlineKeyboardButton(
                f"Open {index}",
                callback_data=_scrape_callback_data("open", run_id),
            )
        ]
        if research_only:
            row.append(
                InlineKeyboardButton(
                    f"PDF {index}",
                    callback_data=_scrape_callback_data("pdf", run_id),
                )
            )
        row.append(
            InlineKeyboardButton(
                f"Raw posts {index}",
                callback_data=_scrape_callback_data("raw", run_id, 0),
            )
        )
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _raw_scrape_keyboard(raw: dict) -> InlineKeyboardMarkup:
    run_id = str(raw["run_id"])
    page = int(raw["page"])
    navigation = []
    if raw["has_previous"]:
        navigation.append(
            InlineKeyboardButton(
                "Previous",
                callback_data=_scrape_callback_data("raw", run_id, page - 1),
            )
        )
    if raw["has_next"]:
        navigation.append(
            InlineKeyboardButton(
                "Next",
                callback_data=_scrape_callback_data("raw", run_id, page + 1),
            )
        )
    rows = [navigation] if navigation else []
    rows.append(
        [
            InlineKeyboardButton(
                "Open stories",
                callback_data=_scrape_callback_data("open", run_id),
            ),
            InlineKeyboardButton(
                "All scrapes", callback_data=_scrape_callback_data("list")
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _opened_scrape_keyboard(
    run_id: str, research_only: bool = False
) -> InlineKeyboardMarkup:
    rows = []
    if research_only:
        rows.append(
            [
                InlineKeyboardButton(
                    "Download PDF",
                    callback_data=_scrape_callback_data("pdf", run_id),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "Raw posts",
                callback_data=_scrape_callback_data("raw", run_id, 0),
            ),
            InlineKeyboardButton(
                "All scrapes", callback_data=_scrape_callback_data("list")
            ),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _format_saved_scrapes(
    saved: list[dict], research_only: bool = False
) -> str:
    lines = ["Your saved scrapes, newest first:", ""]
    for index, item in enumerate(saved, start=1):
        started = item["started_at"]
        when = started.strftime("%Y-%m-%d %H:%M UTC") if started else "unknown time"
        lines.append(
            f"{index}. {when} | {item['tweets_fetched']} posts | "
            f"{item['story_count']} stories"
        )
    lines.append("")
    if research_only:
        lines.append("Open one to review its curated stories, sources, and timestamps.")
    else:
        lines.append("Open one to pick from its curated stories, or view every raw post.")
    return "\n".join(lines)


def _format_source_timestamp(value: datetime | None) -> str:
    if value is None:
        return "time unavailable"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(
        "%b %d, %Y at %I:%M %p UTC"
    )


def _format_opened_scrape(
    opened: dict, research_only: bool = False
) -> str:
    heading = (
        "Saved scrape opened. Review the curated research:"
        if research_only
        else "Saved scrape opened. Choose stories for a new script:"
    )
    lines = [heading, ""]
    for story in opened["stories"]:
        lines.append(f"{story['rank']}. {story['headline']}")
        lines.append(str(story["summary"]))
        source_posts = story.get("source_posts", [])
        if source_posts:
            lines.append("Source posts:")
            for index, post in enumerate(source_posts, start=1):
                username = str(post.get("username") or "unknown")
                timestamp = _format_source_timestamp(post.get("created_at"))
                lines.append(
                    f"   {index}. @{username} — {timestamp}"
                )
                if post.get("url"):
                    lines.append(f"      {post['url']}")
        else:
            lines.append("Source posts: unavailable in this saved scrape")
        lines.append("")
    if research_only:
        lines.append(
            "Ask me naturally to browse saved runs or open every raw post. "
            "No script or video job was created."
        )
    else:
        lines.append("Reply with story numbers such as 1,3,5, or say auto for all.")
    return "\n".join(lines)


def _format_raw_scrape(raw: dict) -> str:
    lines = [
        f"Raw posts for scrape {raw['run_id']}",
        f"Page {raw['page'] + 1} of {raw['pages']} | {raw['total']} posts",
        "",
    ]
    if not raw["posts"]:
        lines.append("This scrape contains no raw posts.")
    for offset, post in enumerate(raw["posts"], start=1):
        absolute = raw["page"] * raw["page_size"] + offset
        lines.append(f"{absolute}. @{post['username']}")
        lines.append(str(post["text"]))
        if post["url"]:
            lines.append(str(post["url"]))
        lines.append("")
    return "\n".join(lines).rstrip()


def _telegram_chunks(text: str, limit: int = 3900) -> list[str]:
    """Split without truncating content so every raw post remains visible."""
    if len(text) <= limit:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        split_at = min(limit, len(remaining))
        if split_at < len(remaining):
            newline = remaining.rfind("\n", 0, split_at)
            if newline > limit // 2:
                split_at = newline + 1
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    return chunks


async def _reply_saved_scrapes(
    message, user_id: int, research_only: bool = False
) -> None:
    saved = list_saved_scrapes(user_id)
    if not saved:
        guidance = (
            "Ask me to get fresh crypto news to make one."
            if research_only
            else "Send /news to make one."
        )
        await message.reply_text(
            f"You do not have any saved scrapes yet. {guidance}"
        )
        return
    await message.reply_text(
        _format_saved_scrapes(saved, research_only=research_only),
        reply_markup=_saved_scrapes_keyboard(
            saved, research_only=research_only
        ),
    )


async def _reply_opened_scrape(
    message,
    user_id: int,
    reference: int | str,
    research_only: bool = False,
) -> None:
    opened = (
        load_saved_scrape(user_id, reference)
        if research_only
        else open_saved_scrape(user_id, reference)
    )
    chunks = _telegram_chunks(
        _format_opened_scrape(opened, research_only=research_only)
    )
    for index, chunk in enumerate(chunks):
        markup = (
            _opened_scrape_keyboard(
                str(opened["run_id"]), research_only=research_only
            )
            if index == len(chunks) - 1
            else None
        )
        await message.reply_text(chunk, reply_markup=markup)


async def _reply_scrape_report(
    message,
    user_id: int,
    run_id: str,
    settings: Settings,
) -> None:
    """Generate or reuse one saved report without ever starting a scrape."""
    try:
        owned_run = _resolve_saved_run(user_id, run_id)
        report_path = await asyncio.to_thread(
            reporting.ensure_scrape_report,
            settings.db_path,
            settings.report_dir,
            owned_run.id,
            user_id,
        )
        size_bytes = report_path.stat().st_size
        maximum_bytes = max(1, int(settings.report_max_mb)) * 1024 * 1024
        if size_bytes > maximum_bytes:
            raise RuntimeError(
                f"report is {size_bytes / (1024 * 1024):.1f} MB; "
                f"configured upload limit is {settings.report_max_mb} MB"
            )
        await message.reply_document(
            document=report_path,
            filename=report_path.name,
            caption=(
                "Complete saved scrape report. Curated evidence and every raw "
                "post are included; source links are clickable."
            ),
            read_timeout=60,
            write_timeout=60,
        )
    except ValueError as exc:
        await message.reply_text(str(exc))
    except Exception:
        log.exception(
            "report generation or delivery failed: user=%s run=%s",
            user_id,
            run_id,
        )
        await message.reply_text(
            "Your scrape is safely saved, but I could not prepare or send its "
            "PDF right now. Tap PDF to retry the same report. This will not "
            "start another scrape."
        )


async def _reply_raw_scrape(
    message,
    user_id: int,
    run_id: str,
    page: int = 0,
) -> None:
    raw = raw_scrape_page(user_id, run_id, page=page)
    chunks = _telegram_chunks(_format_raw_scrape(raw))
    for index, chunk in enumerate(chunks):
        markup = _raw_scrape_keyboard(raw) if index == len(chunks) - 1 else None
        await message.reply_text(chunk, reply_markup=markup)


async def handle_scrape_intent(
    update: Update,
    intent: tuple[str, int | None],
    research_only: bool = False,
) -> None:
    """Handle saved-scrape language before any session-state routing."""
    action, reference = intent
    user_id = update.effective_user.id
    try:
        if action == "list":
            await _reply_saved_scrapes(
                update.message, user_id, research_only=research_only
            )
        elif action == "open" and reference is not None:
            await _reply_opened_scrape(
                update.message,
                user_id,
                reference,
                research_only=research_only,
            )
        elif action == "raw" and reference is not None:
            run = _resolve_saved_run(user_id, reference)
            await _reply_raw_scrape(update.message, user_id, run.id)
        else:
            raise ValueError("Saved scrape request was not understood")
    except ValueError as exc:
        await update.message.reply_text(str(exc))


async def _scrape_callback(
    update: Update,
    _: ContextTypes.DEFAULT_TYPE,
    settings: Settings | None = None,
) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    await query.answer()
    parts = (query.data or "").split(":")
    user_id = update.effective_user.id
    research_only = _scraper_only(settings)
    try:
        if parts == ["scrape", "list"]:
            await _reply_saved_scrapes(
                query.message, user_id, research_only=research_only
            )
        elif len(parts) == 3 and parts[:2] == ["scrape", "open"]:
            await _reply_opened_scrape(
                query.message,
                user_id,
                parts[2],
                research_only=research_only,
            )
        elif len(parts) == 3 and parts[:2] == ["scrape", "pdf"]:
            if not research_only or settings is None:
                raise ValueError("PDF reports are available in production scraper mode")
            await _reply_scrape_report(
                query.message,
                user_id,
                parts[2],
                settings,
            )
        elif len(parts) == 4 and parts[:2] == ["scrape", "raw"]:
            await _reply_raw_scrape(
                query.message,
                user_id,
                parts[2],
                page=max(0, int(parts[3])),
            )
        else:
            raise ValueError("Saved scrape control is invalid or expired")
    except (TypeError, ValueError) as exc:
        await query.message.reply_text(str(exc))


# ---------------------------------------------------------------- pick parsing

def parse_pick(text: str, num_available: int) -> list[int] | None:
    """Parse the user's story-pick reply.

    Returns:
      - []          → "auto" (use all)
      - [1,3,5]     → those specific 1-based ranks
      - None        → unparseable / invalid
    """
    t = text.strip().lower()
    if t in ("auto", "all", "a", "*"):
        return []
    nums: list[int] = []
    for token in t.replace(";", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if not token.isdigit():
            return None
        n = int(token)
        if n < 1 or n > num_available:
            return None
        nums.append(n)
    return nums if nums else None


# ---------------------------------------------------------------- script writing (called from chat.py)

def write_initial_script(
    settings: Settings,
    llm: LLMClient,
    user_id: int,
    chosen_ranks: list[int],
) -> str:
    """Blocking: look up chosen stories, call LLM, persist + return script body."""
    s = db.session()()
    try:
        user_sess = s.get(db.Session, str(user_id))
        run_id = user_sess.run_id
        all_stories = (
            s.query(db.Story)
            .filter(db.Story.run_id == run_id, db.Story.display_ok.is_(True))
            .order_by(db.Story.rank)
            .all()
        )
        if chosen_ranks:
            wanted = set(chosen_ranks)
            selected = [st for st in all_stories if st.rank in wanted]
        else:
            selected = all_stories
    finally:
        s.close()

    if not selected:
        raise LLMError("No stories matched the selection.")

    body = llm.write_script(
        prompts.SCRIPT_SYSTEM, prompts.build_script_prompt(selected)
    )
    sess.start_script(user_id, [st.id for st in selected])
    sess.set_current_script(user_id, body)

    # Persist this draft version.
    _save_script(user_id, body, [st.id for st in selected])

    return body


def _save_script(user_id: int, body: str, story_ids: list[int]) -> int:
    user_sess = sess.load(user_id)
    s = db.session()()
    try:
        row = db.Script(
            session_id=str(user_id),
            version=user_sess.script_version,
            body=body,
            story_ids=story_ids,
            is_final=False,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id
    finally:
        s.close()


def current_script_id(user_id: int) -> int:
    user_sess = sess.load(user_id)
    factory = db.session()
    with factory() as s:
        row = (
            s.query(db.Script)
            .filter(
                db.Script.session_id == str(user_id),
                db.Script.version == user_sess.script_version,
                db.Script.body == user_sess.current_script,
                db.Script.story_ids == (user_sess.story_ids or []),
            )
            .order_by(db.Script.id.desc())
            .first()
        )
        if row is None:
            raise ValueError("The displayed draft has not been persisted")
        return row.id


def finalize_script(user_id: int) -> str | None:
    """Mark the current script final and return its body. Returns None if no draft."""
    user_sess = sess.load(user_id)
    if not user_sess.current_script:
        return None
    s = db.session()()
    try:
        # Mark the exact session-displayed version final.
        latest = (
            s.query(db.Script)
            .filter(
                db.Script.session_id == str(user_id),
                db.Script.version == user_sess.script_version,
                db.Script.body == user_sess.current_script,
                db.Script.story_ids == (user_sess.story_ids or []),
            )
            .order_by(db.Script.id.desc())
            .first()
        )
        if latest:
            latest.is_final = True
            s.commit()
    finally:
        s.close()
    body = user_sess.current_script
    sess.reset(user_id)
    return body


def approve_script(
    user_id: int,
    chat_id: int,
    expected_script_id: int | None = None,
    stale_hours: int = jobs.ACTIVE_JOB_TTL_HOURS,
) -> tuple[db.Script, db.VideoJob]:
    """Lock the latest draft and create the idempotent Telegram video job."""
    user_sess = sess.load(user_id)
    if not user_sess.current_script:
        raise ValueError("No draft script is available to approve")
    factory = db.session()
    with factory() as s:
        latest = (
            s.query(db.Script)
            .filter(
                db.Script.session_id == str(user_id),
                db.Script.version == user_sess.script_version,
                db.Script.body == user_sess.current_script,
                db.Script.story_ids == (user_sess.story_ids or []),
            )
            .order_by(db.Script.id.desc())
            .first()
        )
        if expected_script_id is not None:
            latest = s.get(db.Script, expected_script_id)
            if latest is not None and (
                latest.session_id != str(user_id)
                or latest.version != user_sess.script_version
                or latest.body != user_sess.current_script
                or latest.story_ids != (user_sess.story_ids or [])
            ):
                latest = None
        if latest is None:
            raise ValueError("The displayed script version does not match the saved draft")
        latest.is_final = True
        s.commit()
        s.refresh(latest)
        script_id = latest.id
    job = jobs.create_for_script(
        user_id=user_id,
        chat_id=chat_id,
        script_id=script_id,
        stale_hours=stale_hours,
    )
    sess.set_state(user_id, sess.AWAITING_VIDEO)
    return latest, job


def script_banner(body: str, version: int) -> str:
    return SCRIPT_BANNER % (version, body)
