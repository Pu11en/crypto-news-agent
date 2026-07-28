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
_HEAVY_JOB_SLOT = asyncio.Semaphore(1)


def register(app: Application, settings, llm, user_filter) -> None:
    media_filter = filters.VIDEO | filters.Document.VIDEO
    app.add_handler(
        MessageHandler(media_filter & user_filter, lambda u, c: _upload(u, c, settings))
    )
    app.add_handler(
        CallbackQueryHandler(lambda u, c: _callback(u, c, settings, llm), pattern=r"^video:")
    )


def script_keyboard(script_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Approve script", callback_data=f"video:approve-script:{script_id}"
                ),
                InlineKeyboardButton(
                    "✏️ Keep revising", callback_data=f"video:keep-revising:{script_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "❌ Cancel", callback_data=f"video:cancel-script:{script_id}"
                )
            ],
        ]
    )


def _caption_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Captions on", callback_data=f"video:captions-on:{job_id}"),
                InlineKeyboardButton("Captions off", callback_data=f"video:captions-off:{job_id}"),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"video:cancel:{job_id}")],
        ]
    )


def _review_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Approve visuals", callback_data=f"video:approve-visuals:{job_id}"),
                InlineKeyboardButton("✏️ Revise visuals", callback_data=f"video:revise-visuals:{job_id}"),
            ],
            [InlineKeyboardButton("❌ Cancel job", callback_data=f"video:cancel:{job_id}")],
        ]
    )


def _failure_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔄 Retry", callback_data=f"video:retry:{job_id}"), InlineKeyboardButton("❌ Cancel", callback_data=f"video:cancel:{job_id}")]]
    )


def _done_keyboard(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📰 Make another video", callback_data=f"video:new:{job_id}"), InlineKeyboardButton("🔄 Render again", callback_data=f"video:render-again:{job_id}")]]
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
    try:
        ingest.validate_upload_size(
            getattr(media, "file_size", None), settings.video_max_upload_mb
        )
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ Upload rejected\n\n{exc}")
        return

    await asyncio.to_thread(pipeline.prune_artifacts, settings)
    work = pipeline.job_dir(settings, active.id)
    work.mkdir(parents=True, exist_ok=True)
    destination = work / "source-upload.mp4"
    status = await update.message.reply_text("📥 Downloading the OBS recording...")
    try:
        telegram_file = await context.bot.get_file(
            media.file_id,
            read_timeout=60,
            write_timeout=60,
            connect_timeout=30,
            pool_timeout=30,
        )
        await telegram_file.download_to_drive(
            destination,
            read_timeout=180,
            write_timeout=180,
            connect_timeout=30,
            pool_timeout=30,
        )
        ingest.validate_upload_size(destination.stat().st_size, settings.video_max_upload_mb)
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
            reply_markup=_caption_keyboard(active.id),
        )
    except Exception as exc:
        current = jobs.get(active.id)
        if current is not None and current.state == jobs.CANCELLED:
            await status.edit_text("❌ Upload cancelled. The job will not continue.")
            return
        jobs.fail(active.id, "download", str(exc))
        await status.edit_text(
            f"⚠️ Upload validation failed\n\n{exc}", reply_markup=_failure_keyboard(active.id)
        )


async def _callback(update: Update, context: ContextTypes.DEFAULT_TYPE, settings, llm) -> None:
    if not _authorized(update, settings):
        return
    query = update.callback_query
    await query.answer()
    callback_parts = (query.data or "").split(":")
    action = callback_parts[1] if len(callback_parts) > 1 else ""
    payload = callback_parts[2] if len(callback_parts) > 2 else None
    user_id = update.effective_user.id

    if action == "approve-script":
        current_session = sess.load(user_id)
        if current_session.state != sess.SCRIPT_DRAFT:
            await query.message.reply_text("There is no draft script waiting for approval.")
            return
        try:
            expected_script_id = int(payload or "")
        except ValueError:
            await query.message.reply_text(
                "That Approve button belongs to an older script version. Use the button under the newest draft."
            )
            return
        try:
            script, _job = news.approve_script(
                user_id,
                update.effective_chat.id,
                expected_script_id=expected_script_id,
                stale_hours=settings.video_artifact_retention_hours,
            )
        except ValueError as exc:
            await query.message.reply_text(f"Could not approve the script: {exc}")
            return
        await query.message.reply_text(
            "✅ Script approved and locked.\n\n"
            f"{script.body}\n\n"
            "Record yourself reading it in OBS as a 16:9 landscape MP4, then upload it here."
        )
        return
    if action == "keep-revising":
        try:
            if int(payload or "") != news.current_script_id(user_id):
                raise ValueError
        except ValueError:
            await query.message.reply_text("That button belongs to an older script draft.")
            return
        await query.message.reply_text("Send the script change you want in normal language.")
        return
    if action == "cancel-script":
        try:
            if int(payload or "") != news.current_script_id(user_id):
                raise ValueError
        except ValueError:
            await query.message.reply_text("That button belongs to an older script draft.")
            return
        sess.reset(user_id)
        await query.message.reply_text("❌ Script draft cancelled. Send /news to start again.")
        return

    bound_job = jobs.get(payload or "")
    if (
        bound_job is None
        or bound_job.user_id != user_id
        or bound_job.chat_id != update.effective_chat.id
    ):
        await query.message.reply_text("That control belongs to a different or expired video job.")
        return

    if action == "new":
        sess.reset(user_id)
        await query.message.reply_text("Send /news when you are ready to choose the next story.")
        return
    if action == "render-again":
        if bound_job.state != jobs.COMPLETED:
            await query.message.reply_text("That completed video is not available to render again.")
            return
        jobs.transition(bound_job.id, jobs.STORYBOARD_REVIEW, allowed_from={jobs.COMPLETED})
        context.application.create_task(_render_task(context, bound_job.id, settings))
        return

    active = bound_job
    if active.state in {jobs.COMPLETED, jobs.CANCELLED}:
        await query.message.reply_text("That video job is no longer active.")
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
        try:
            retried = jobs.retry(active.id)
        except ValueError as exc:
            await query.message.reply_text(f"That job cannot be retried: {exc}")
            return
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
        immediate_states = {
            jobs.AWAITING_VIDEO,
            jobs.AWAITING_CAPTIONS,
            jobs.STORYBOARD_REVIEW,
            jobs.FAILED,
        }
        if current and current.state in immediate_states:
            jobs.mark_cancelled(active.id)
            sess.reset(user_id)
            await query.message.reply_text("❌ Video job cancelled. Send /news to start again.")
        else:
            await query.message.reply_text(
                "⏹ Cancel requested. The current media stage will stop at its next safe checkpoint."
            )


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
            _status(context, job_id, text, InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"video:cancel:{job_id}")]])),
            loop,
        )
    return report


async def _prepare_task(context, job_id: str, settings, llm) -> None:
    loop = asyncio.get_running_loop()
    try:
        await _status(
            context,
            job_id,
            "🟣 Video job queued\n\nWaiting for the single render worker slot.",
        )
        async with _HEAVY_JOB_SLOT:
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
            await _status(context, job_id, f"⚠️ Video build stopped\n\n{exc}", _failure_keyboard(job_id))


async def _revise_task(context, job_id: str, settings, llm, feedback: str) -> None:
    loop = asyncio.get_running_loop()
    try:
        await _status(
            context,
            job_id,
            "🟣 Storyboard revision queued\n\nWaiting for the single render worker slot.",
        )
        async with _HEAVY_JOB_SLOT:
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
        await _status(context, job_id, f"⚠️ Storyboard revision stopped\n\n{exc}", _failure_keyboard(job_id))


async def _send_storyboard(context, job_id: str, result: dict) -> None:
    row = jobs.get(job_id)
    if row is None:
        raise RuntimeError("Video job disappeared before storyboard delivery")
    if row.cancel_requested:
        jobs.mark_cancelled(job_id)
        raise RuntimeError("Video job was cancelled before storyboard delivery")
    paths = [Path(path) for path in result["previewPaths"]]
    for offset in range(0, len(paths), 10):
        current = jobs.get(job_id)
        if current is None or current.cancel_requested:
            if current is not None:
                jobs.mark_cancelled(job_id)
            raise RuntimeError("Video job was cancelled during storyboard delivery")
        handles = [path.open("rb") for path in paths[offset : offset + 10]]
        try:
            media = [
                InputMediaPhoto(handle, caption=f"Scene {offset + index + 1}")
                for index, handle in enumerate(handles)
            ]
            await context.bot.send_media_group(
                row.chat_id,
                media,
                read_timeout=60,
                write_timeout=180,
                connect_timeout=30,
                pool_timeout=30,
            )
        except Exception as exc:
            jobs.fail(job_id, "preview", str(exc))
            raise
        finally:
            for handle in handles:
                handle.close()
    storyboard = result["storyboard"]
    source_count = result.get("sourceCount", "linked")
    claim_count = result.get("claimCount", len(storyboard.get("scenes", [])))
    unsupported_count = result.get("unsupportedCount", 0)
    await _status(
        context,
        job_id,
        "🖼️ Storyboard ready\n\n"
        f"Revision: {storyboard.get('revision', row.revision)}\n"
        f"Scenes: {len(storyboard.get('scenes', []))}\n"
        f"Scraped source posts checked: {source_count}\n"
        f"Spoken scenes supported: {claim_count}\n"
        f"Unsupported claims or chart values: {unsupported_count}\n"
        "Graphics density target: 80 to 90%\n\n"
        "Review the screenshots before final rendering.",
        _review_keyboard(job_id),
    )


async def _render_task(context, job_id: str, settings) -> None:
    loop = asyncio.get_running_loop()
    try:
        await _status(
            context,
            job_id,
            "🟣 Final render queued\n\nWaiting for the single render worker slot.",
        )
        async with _HEAVY_JOB_SLOT:
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
            f"Audio sync: {qa['audioSync']}\n"
            f"Blank frames: {qa['blankFrames']}\n"
            f"Text overflow: {qa['textOverflow']}\n"
            f"Telegram encoding: {qa['telegramEncoding']}\n\n"
            "Uploading the native video...",
        )
        await _send_final_video(context, row, result["outputPath"])
    except Exception as exc:
        current = jobs.get(job_id)
        if current is not None and current.state == jobs.CANCELLED:
            await _status(context, job_id, "❌ Video job cancelled. Send /news to start again.")
            return
        await _status(context, job_id, f"⚠️ Render stopped\n\n{exc}", _failure_keyboard(job_id))


async def _send_final_video(context, row, output_path: str) -> None:
    current = jobs.get(row.id)
    if current is None:
        raise RuntimeError("Video job disappeared before delivery")
    if current.cancel_requested:
        jobs.mark_cancelled(row.id)
        raise RuntimeError("Video job was cancelled before delivery")
    info = ingest.probe(output_path)
    sent_message = None
    try:
        with Path(output_path).open("rb") as handle:
            sent_message = await context.bot.send_video(
                chat_id=row.chat_id,
                video=handle,
                duration=round(info.duration),
                width=1080,
                height=1920,
                supports_streaming=True,
                caption="✅ Your crypto news video is ready.",
                reply_markup=_done_keyboard(row.id),
                read_timeout=60,
                write_timeout=300,
                connect_timeout=30,
                pool_timeout=30,
            )
    except Exception as exc:
        current = jobs.get(row.id)
        if current is not None and current.state != jobs.CANCELLED:
            jobs.fail(row.id, "delivery", str(exc))
        raise
    current = jobs.get(row.id)
    if current is not None and current.cancel_requested:
        message_id = getattr(sent_message, "message_id", None)
        if message_id is not None:
            try:
                await context.bot.delete_message(row.chat_id, message_id)
            except Exception as exc:
                log.warning("could not delete cancelled delivered video %s: %s", row.id, exc)
        jobs.mark_cancelled(row.id)
        raise RuntimeError("Video job was cancelled during delivery")
    jobs.mark_completed(row.id, output_path)
    sess.reset(row.user_id)


async def _deliver_existing(context, job_id: str) -> None:
    row = jobs.get(job_id)
    if row is None or not row.output_path or not Path(row.output_path).exists():
        if row is not None:
            jobs.fail(job_id, "delivery", "The rendered file is missing")
        await _status(context, job_id, "The rendered file is missing.", _failure_keyboard(job_id))
        return
    await _send_final_video(context, row, row.output_path)
