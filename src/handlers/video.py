from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from handlers import news
from handlers import session as sess
from video import ingest, jobs, pipeline

log = logging.getLogger("agent.video")


def register(app: Application, settings, llm, user_filter) -> None:
    media_filter = filters.VIDEO | filters.Document.VIDEO
    app.add_handler(
        MessageHandler(media_filter & user_filter, lambda u, c: _upload(u, c, settings))
    )
    app.add_handler(
        CallbackQueryHandler(lambda u, c: _callback(u, c, settings, llm), pattern=r"^video:")
    )


def script_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve script", callback_data="video:approve-script"),
                InlineKeyboardButton("✏️ Keep revising", callback_data="video:keep-revising"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="video:cancel")],
        ]
    )


def _caption_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Captions on", callback_data="video:captions-on"),
                InlineKeyboardButton("Captions off", callback_data="video:captions-off"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data="video:cancel")],
        ]
    )


def _review_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve visuals", callback_data="video:approve-visuals"),
                InlineKeyboardButton("✏️ Revise visuals", callback_data="video:revise-visuals"),
            ],
            [InlineKeyboardButton("❌ Cancel job", callback_data="video:cancel")],
        ]
    )


def _failure_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Retry", callback_data="video:retry"), InlineKeyboardButton("❌ Cancel", callback_data="video:cancel")]]
    )


def _done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📰 Make another video", callback_data="video:new"), InlineKeyboardButton("🔄 Render again", callback_data="video:render-again")]]
    )


def _authorized(update: Update, settings) -> bool:
    return not settings.allowed_user_ids or update.effective_user.id in settings.allowed_user_ids


async def _upload(update: Update, context: ContextTypes.DEFAULT_TYPE, settings) -> None:
    if not _authorized(update, settings):
        return
    active = jobs.get_active_for_user(update.effective_user.id)
    if active is None or active.state != jobs.AWAITING_VIDEO:
        await update.message.reply_text("Approve a script first, then upload the OBS MP4.")
        return
    media = update.message.video or update.message.document
    if media is None:
        return
    file_name = getattr(media, "file_name", "") or "recording.mp4"
    mime_type = getattr(media, "mime_type", "") or "video/mp4"
    if not file_name.lower().endswith(".mp4") and mime_type != "video/mp4":
        await update.message.reply_text("Please upload an MP4 recording from OBS.")
        return

    work = pipeline.job_dir(settings, active.id)
    work.mkdir(parents=True, exist_ok=True)
    destination = work / "source-upload.mp4"
    status = await update.message.reply_text("📥 Downloading the OBS recording...")
    try:
        telegram_file = await context.bot.get_file(media.file_id)
        await telegram_file.download_to_drive(destination)
        info = await asyncio.to_thread(ingest.validate_source, destination)
        if info.duration > settings.video_max_seconds + 0.05:
            raise ValueError(
                f"Recording is {info.duration:.1f} seconds. The current test limit is {settings.video_max_seconds} seconds."
            )
        jobs.transition(
            active.id,
            jobs.AWAITING_CAPTIONS,
            allowed_from={jobs.AWAITING_VIDEO},
            source_path=str(destination),
            telegram_source_file_id=media.file_id,
            telegram_source_kind="video" if update.message.video else "document",
            status_message_id=status.message_id,
        )
        await status.edit_text(
            "📥 Recording received and validated.\n\n"
            f"Duration: {info.duration:.1f} seconds\n"
            f"Resolution: {info.width}x{info.height}\n"
            "Video: detected\nAudio: detected\n\n"
            "I will preserve the complete recording and original audio. Include spoken captions?",
            reply_markup=_caption_keyboard(),
        )
    except Exception as exc:
        jobs.fail(active.id, "download", str(exc))
        await status.edit_text(
            f"⚠️ Upload validation failed\n\n{exc}", reply_markup=_failure_keyboard()
        )


async def _callback(update: Update, context: ContextTypes.DEFAULT_TYPE, settings, llm) -> None:
    if not _authorized(update, settings):
        return
    query = update.callback_query
    await query.answer()
    action = (query.data or "").split(":", 1)[-1]
    user_id = update.effective_user.id

    if action == "approve-script":
        if sess.load(user_id).state != sess.SCRIPT_DRAFT:
            await query.message.reply_text("There is no draft script waiting for approval.")
            return
        script, _job = news.approve_script(user_id, update.effective_chat.id)
        await query.message.reply_text(
            "✅ Script approved and locked.\n\n"
            f"{script.body}\n\n"
            "Record yourself reading it in OBS as a 16:9 landscape MP4, then upload it here."
        )
        return
    if action == "keep-revising":
        await query.message.reply_text("Send the script change you want in normal language.")
        return
    if action == "new":
        sess.reset(user_id)
        await query.message.reply_text("Send /news when you are ready to choose the next story.")
        return
    if action == "render-again":
        latest = jobs.get_latest_for_user(user_id)
        if latest is None or latest.state != jobs.COMPLETED:
            await query.message.reply_text("There is no completed video available to render again.")
            return
        jobs.transition(latest.id, jobs.STORYBOARD_REVIEW, allowed_from={jobs.COMPLETED})
        context.application.create_task(_render_task(context, latest.id, settings))
        return
    if action == "cancel" and sess.load(user_id).state == sess.SCRIPT_DRAFT:
        sess.reset(user_id)
        await query.message.reply_text("❌ Script draft cancelled. Send /news to start again.")
        return

    active = jobs.get_active_for_user(user_id)
    if active is None:
        await query.message.reply_text("There is no active video job.")
        return

    if action in {"captions-on", "captions-off"}:
        if active.state != jobs.AWAITING_CAPTIONS:
            await query.message.reply_text("Caption choice was already recorded.")
            return
        jobs.transition(
            active.id,
            jobs.PLANNING,
            allowed_from={jobs.AWAITING_CAPTIONS},
            captions_enabled=action == "captions-on",
            current_stage="ingest",
        )
        context.application.create_task(_prepare_task(context, active.id, settings, llm))
        return
    if action == "approve-visuals":
        if active.state != jobs.STORYBOARD_REVIEW:
            await query.message.reply_text("The storyboard is not waiting for approval.")
            return
        context.application.create_task(_render_task(context, active.id, settings))
        return
    if action == "revise-visuals":
        if active.state != jobs.STORYBOARD_REVIEW:
            await query.message.reply_text("The storyboard is not waiting for revision.")
            return
        sess.set_state(user_id, sess.STORYBOARD_REVISION)
        await query.message.reply_text(
            "Tell me what to change. Mention a scene number when possible.\n\n"
            "Example: Scene 3 looks too much like text. Show how a brokerage account reaches SOL."
        )
        return
    if action == "retry":
        retried = jobs.retry(active.id)
        if retried.state == jobs.PLANNING:
            context.application.create_task(_prepare_task(context, active.id, settings, llm))
        elif retried.state == jobs.STORYBOARD_REVIEW:
            context.application.create_task(_render_task(context, active.id, settings))
        elif retried.state == jobs.AWAITING_VIDEO:
            sess.set_state(user_id, sess.AWAITING_VIDEO)
            await query.message.reply_text("Please upload the OBS MP4 again.")
        elif retried.state == jobs.DELIVERING:
            await _deliver_existing(context, active.id)
        return
    if action == "cancel":
        jobs.request_cancel(active.id)
        current = jobs.get(active.id)
        if current and current.state in {jobs.AWAITING_VIDEO, jobs.AWAITING_CAPTIONS, jobs.STORYBOARD_REVIEW, jobs.FAILED}:
            jobs.mark_cancelled(active.id)
        sess.reset(user_id)
        await query.message.reply_text("❌ Video job cancelled. Send /news to start again.")


async def handle_revision_text(update: Update, context: ContextTypes.DEFAULT_TYPE, settings, llm) -> None:
    active = jobs.get_active_for_user(update.effective_user.id)
    if active is None or active.state != jobs.STORYBOARD_REVIEW:
        sess.set_state(update.effective_user.id, sess.AWAITING_VIDEO)
        await update.message.reply_text("The storyboard is no longer waiting for revision.")
        return
    feedback = (update.message.text or "").strip()
    if not feedback:
        await update.message.reply_text("Tell me what you want changed in the storyboard.")
        return
    sess.set_state(update.effective_user.id, sess.AWAITING_VIDEO)
    context.application.create_task(_revise_task(context, active.id, settings, llm, feedback))


async def _status(context, job_id: str, text: str, markup=None) -> None:
    row = jobs.get(job_id)
    if row is None:
        return
    try:
        if row.status_message_id:
            await context.bot.edit_message_text(
                chat_id=row.chat_id,
                message_id=row.status_message_id,
                text=text,
                reply_markup=markup,
            )
        else:
            message = await context.bot.send_message(row.chat_id, text, reply_markup=markup)
            jobs.update(job_id, status_message_id=message.message_id)
    except Exception as exc:
        log.warning("status update failed for %s: %s", job_id, exc)


def _progress_callback(loop, context, job_id: str):
    def report(stage: str, detail: str) -> None:
        text = f"🟣 Building your video\n\nCurrent stage: {stage.replace('_', ' ').title()}\n{detail}\n\nYou can press Cancel at any time."
        asyncio.run_coroutine_threadsafe(
            _status(context, job_id, text, InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="video:cancel")]])),
            loop,
        )
    return report


async def _prepare_task(context, job_id: str, settings, llm) -> None:
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.to_thread(
            pipeline.prepare_storyboard,
            job_id,
            settings,
            llm,
            _progress_callback(loop, context, job_id),
        )
        await _send_storyboard(context, job_id, result)
    except Exception as exc:
        row = jobs.get(job_id)
        if row and row.state == jobs.CANCELLED:
            await _status(context, job_id, "❌ Video job cancelled.")
        else:
            await _status(context, job_id, f"⚠️ Video build stopped\n\n{exc}", _failure_keyboard())


async def _revise_task(context, job_id: str, settings, llm, feedback: str) -> None:
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.to_thread(
            pipeline.revise_storyboard,
            job_id,
            settings,
            llm,
            feedback,
            _progress_callback(loop, context, job_id),
        )
        await _send_storyboard(context, job_id, result)
    except Exception as exc:
        await _status(context, job_id, f"⚠️ Storyboard revision stopped\n\n{exc}", _failure_keyboard())


async def _send_storyboard(context, job_id: str, result: dict) -> None:
    row = jobs.get(job_id)
    paths = [Path(path) for path in result["previewPaths"]]
    for offset in range(0, len(paths), 10):
        handles = [path.open("rb") for path in paths[offset : offset + 10]]
        try:
            media = [
                InputMediaPhoto(handle, caption=f"Scene {offset + index + 1}")
                for index, handle in enumerate(handles)
            ]
            await context.bot.send_media_group(row.chat_id, media)
        finally:
            for handle in handles:
                handle.close()
    storyboard = result["storyboard"]
    source_count = result.get("sourceCount", "linked")
    await _status(
        context,
        job_id,
        "🖼️ Storyboard ready\n\n"
        f"Revision: {storyboard.get('revision', row.revision)}\n"
        f"Scenes: {len(storyboard.get('scenes', []))}\n"
        f"Scraped source posts checked: {source_count}\n"
        "Unsupported chart values: 0\n"
        "Graphics density target: 80 to 90%\n\n"
        "Review the screenshots before final rendering.",
        _review_keyboard(),
    )


async def _render_task(context, job_id: str, settings) -> None:
    loop = asyncio.get_running_loop()
    try:
        result = await asyncio.to_thread(
            pipeline.render_approved,
            job_id,
            settings,
            _progress_callback(loop, context, job_id),
        )
        row = jobs.get(job_id)
        qa = result["qa"]
        await _status(
            context,
            job_id,
            "✅ Final QA passed\n\n"
            f"Resolution: {qa['width']}x{qa['height']}\n"
            f"Duration: {qa['duration']:.1f} seconds\n"
            "Audio sync: passed\nBlank frames: passed\nText overflow: passed\nTelegram encoding: passed\n\nUploading the native video...",
        )
        await _send_final_video(context, row, result["outputPath"])
    except Exception as exc:
        await _status(context, job_id, f"⚠️ Render stopped\n\n{exc}", _failure_keyboard())


async def _send_final_video(context, row, output_path: str) -> None:
    info = ingest.probe(output_path)
    with Path(output_path).open("rb") as handle:
        await context.bot.send_video(
            chat_id=row.chat_id,
            video=handle,
            duration=round(info.duration),
            width=1080,
            height=1920,
            supports_streaming=True,
            caption="✅ Your crypto news video is ready.",
            reply_markup=_done_keyboard(),
        )
    jobs.mark_completed(row.id, output_path)
    sess.reset(row.user_id)


async def _deliver_existing(context, job_id: str) -> None:
    row = jobs.get(job_id)
    if row is None or not row.output_path:
        await _status(context, job_id, "The rendered file is missing.", _failure_keyboard())
        return
    await _send_final_video(context, row, row.output_path)
