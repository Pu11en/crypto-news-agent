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

from sqlalchemy import func
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import db
import prompts
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
SCRIPT_BANNER = "📝 *Script draft* (v%d)\n\n%s\n\n_Talk to me to refine it, or_ /done _to save,_ /cancel _to discard._"


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
    app.add_handler(CommandHandler("start", _start, filters=user_filter))
    app.add_handler(CommandHandler("help", _help, filters=user_filter))
    app.add_handler(CommandHandler("scrapes", _scrapes, filters=user_filter))
    app.add_handler(CallbackQueryHandler(_scrape_callback, pattern=r"^scrape:"))


async def _start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Crypto news agent ready.\n\n"
        "/news : scrape Twitter and build a video script\n"
        "/scrapes : browse your saved scrapes\n"
        "/done : save the current script\n"
        "/cancel : discard and exit\n"
        "/credits : check Xquik credit balance\n"
        "/account : full Xquik account summary\n"
        "/help : this message\n\n"
        "Or just talk to me normally."
    )


async def _help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await _start(update, _)


async def _scrapes(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply_saved_scrapes(update.message, update.effective_user.id)


async def _news(
    update: Update,
    _: ContextTypes.DEFAULT_TYPE,
    settings: Settings,
    llm: LLMClient,
) -> None:
    global _NEWS_IN_PROGRESS
    user_id = update.effective_user.id
    if _NEWS_IN_PROGRESS:
        await update.message.reply_text(
            "Another news scrape is already running. Try again after it finishes."
        )
        return

    # Starting a new scrape is an explicit direction change. A job that has not
    # received an upload can be cancelled immediately; later-stage work remains
    # independent and can finish or be cancelled from its own controls.
    active_video = jobs.get_active_for_user(user_id)
    if active_video is not None and active_video.state == jobs.AWAITING_VIDEO:
        jobs.mark_cancelled(active_video.id)
    sess.reset(user_id)

    _NEWS_IN_PROGRESS = True
    sess.set_state(user_id, sess.SCRAPING)

    # Run the blocking scrape + curate in a thread; await from the event loop.
    try:
        await update.message.reply_text(
            f"🔍 Scraping the last {settings.scrape_hours}h across your account list…"
        )
        result = await asyncio.to_thread(_scrape_and_curate, settings, llm, user_id)
    except XquikError as e:
        sess.reset(user_id)
        await update.message.reply_text(f"Scraper error: {e}")
        return
    except LLMError as e:
        sess.reset(user_id)
        await update.message.reply_text(f"AI error during curation: {e}")
        return
    except Exception:
        log.exception("unexpected error during news scrape")
        sess.reset(user_id)
        await update.message.reply_text("Unexpected scraper error. Please try again later.")
        return
    finally:
        _NEWS_IN_PROGRESS = False

    if not result["stories"]:
        await update.message.reply_text(
            f"No newsworthy stories found in the last {settings.scrape_hours}h "
            f"(scraped {result['tweets_fetched']} tweets from "
            f"{result['accounts_hit']} accounts, ~{result['tweets_fetched']} credits). "
            "Try again later."
        )
        sess.reset(user_id)
        return

    # Persist stories + pin them to the session.
    story_ids = _persist_stories(user_id, result["run_id"], result["stories"])
    sess.set_run(user_id, result["run_id"], story_ids)

    await update.message.reply_text(
        _format_top_stories(result["stories"], result["tweets_fetched"], result["accounts_hit"]),
        parse_mode="Markdown",
    )


def _scrape_and_curate(
    settings: Settings, llm: LLMClient, user_id: int
) -> dict:
    """Blocking: fetch tweets, store them, ask the LLM to curate. Returns run meta."""
    run_id = uuid.uuid4().hex[:12]
    client = XquikClient(api_key=settings.xquik_api_key)
    accounts = load_accounts()

    log.info("run %s: scraping %d accounts", run_id, len(accounts))

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
            f"~{tweets_fetched} credits used · /credits to check balance)_"
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
            .outerjoin(db.Story, db.Story.run_id == db.Run.id)
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


def open_saved_scrape(user_id: int, reference: int | str) -> dict:
    """Restore a user-owned scrape as the active story-picking context."""
    run = _resolve_saved_run(user_id, reference)
    factory = db.session()
    with factory() as s:
        stories = (
            s.query(db.Story)
            .filter(db.Story.run_id == run.id)
            .order_by(db.Story.rank, db.Story.id)
            .all()
        )
        story_data = [
            {
                "id": story.id,
                "rank": story.rank,
                "headline": story.headline,
                "summary": story.summary,
                "tweet_ids": list(story.tweet_ids or []),
                "score": story.score,
            }
            for story in stories
        ]

    if not story_data:
        raise ValueError("Saved scrape has no curated stories to reopen")

    active_video = jobs.get_active_for_user(user_id)
    if active_video is not None and active_video.state == jobs.AWAITING_VIDEO:
        jobs.mark_cancelled(active_video.id)

    sess.set_run(user_id, run.id, [story["id"] for story in story_data])
    return {
        "run_id": run.id,
        "started_at": run.started_at,
        "tweets_fetched": run.tweets_fetched,
        "accounts_hit": run.accounts_hit,
        "stories": story_data,
    }


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


def _saved_scrapes_keyboard(saved: list[dict]) -> InlineKeyboardMarkup:
    rows = []
    for index, item in enumerate(saved, start=1):
        run_id = str(item["run_id"])
        rows.append(
            [
                InlineKeyboardButton(
                    f"Open {index}",
                    callback_data=_scrape_callback_data("open", run_id),
                ),
                InlineKeyboardButton(
                    f"Raw posts {index}",
                    callback_data=_scrape_callback_data("raw", run_id, 0),
                ),
            ]
        )
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


def _opened_scrape_keyboard(run_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Raw posts",
                    callback_data=_scrape_callback_data("raw", run_id, 0),
                ),
                InlineKeyboardButton(
                    "All scrapes", callback_data=_scrape_callback_data("list")
                ),
            ]
        ]
    )


def _format_saved_scrapes(saved: list[dict]) -> str:
    lines = ["Your saved scrapes, newest first:", ""]
    for index, item in enumerate(saved, start=1):
        started = item["started_at"]
        when = started.strftime("%Y-%m-%d %H:%M UTC") if started else "unknown time"
        lines.append(
            f"{index}. {when} | {item['tweets_fetched']} posts | "
            f"{item['story_count']} stories"
        )
    lines.append("")
    lines.append("Open one to pick from its curated stories, or view every raw post.")
    return "\n".join(lines)


def _format_opened_scrape(opened: dict) -> str:
    lines = ["Saved scrape opened. Choose stories for a new script:", ""]
    for story in opened["stories"]:
        lines.append(f"{story['rank']}. {story['headline']}")
        lines.append(str(story["summary"]))
        lines.append("")
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


async def _reply_saved_scrapes(message, user_id: int) -> None:
    saved = list_saved_scrapes(user_id)
    if not saved:
        await message.reply_text("You do not have any saved scrapes yet. Send /news to make one.")
        return
    await message.reply_text(
        _format_saved_scrapes(saved), reply_markup=_saved_scrapes_keyboard(saved)
    )


async def _reply_opened_scrape(message, user_id: int, reference: int | str) -> None:
    opened = open_saved_scrape(user_id, reference)
    await message.reply_text(
        _format_opened_scrape(opened),
        reply_markup=_opened_scrape_keyboard(str(opened["run_id"])),
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
) -> None:
    """Handle saved-scrape language before any session-state routing."""
    action, reference = intent
    user_id = update.effective_user.id
    try:
        if action == "list":
            await _reply_saved_scrapes(update.message, user_id)
        elif action == "open" and reference is not None:
            await _reply_opened_scrape(update.message, user_id, reference)
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
) -> None:
    query = update.callback_query
    if query is None or query.message is None:
        return
    await query.answer()
    parts = (query.data or "").split(":")
    user_id = update.effective_user.id
    try:
        if parts == ["scrape", "list"]:
            await _reply_saved_scrapes(query.message, user_id)
        elif len(parts) == 3 and parts[:2] == ["scrape", "open"]:
            await _reply_opened_scrape(query.message, user_id, parts[2])
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
            .filter(db.Story.run_id == run_id)
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
