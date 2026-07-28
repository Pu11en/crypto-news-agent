from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import db
from handlers import session as sess
from handlers import video
from video import ingest, jobs


class FakeBot:
    def __init__(self):
        self.media_groups = []
        self.messages = []
        self.videos = []
        self.edits = []
        self.deleted = []

    async def send_media_group(self, chat_id, media, **kwargs):
        self.media_groups.append((chat_id, media))

    async def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append((chat_id, text, reply_markup))
        return SimpleNamespace(message_id=700)

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)

    async def send_video(self, **kwargs):
        self.videos.append(kwargs)
        return SimpleNamespace(message_id=901)

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


def test_script_keyboard_is_bound_to_persisted_script_id():
    markup = video.script_keyboard(3)
    assert markup.inline_keyboard[0][0].callback_data == "video:approve-script:3"


def test_all_video_controls_are_bound_to_job_id():
    job_id = "job-123"
    assert video._caption_keyboard(job_id).inline_keyboard[0][0].callback_data.endswith(job_id)
    assert video._review_keyboard(job_id).inline_keyboard[0][0].callback_data.endswith(job_id)
    assert video._failure_keyboard(job_id).inline_keyboard[0][0].callback_data.endswith(job_id)
    assert video._done_keyboard(job_id).inline_keyboard[0][1].callback_data.endswith(job_id)


def _seed_job(user_id: int = 800) -> db.VideoJob:
    factory = db.session()
    with factory() as s:
        story = db.Story(
            run_id="telegram-test",
            rank=1,
            headline="ETF opens access",
            summary="A sourced story.",
            tweet_ids=["tweet-1"],
            score=0.9,
        )
        s.add(story)
        s.flush()
        script = db.Script(
            session_id=str(user_id),
            version=1,
            body="The ETF opened access.",
            story_ids=[story.id],
            is_final=True,
        )
        s.add(script)
        s.commit()
        script_id = script.id
    return jobs.create_for_script(user_id, user_id, script_id)


def test_storyboard_is_sent_as_native_telegram_media_group(isolated_db, tmp_path):
    job = _seed_job()
    jobs.transition(job.id, jobs.STORYBOARD_REVIEW, allowed_from={jobs.AWAITING_VIDEO})
    preview_paths = []
    for index in range(2):
        path = tmp_path / f"scene-{index + 1}.png"
        path.write_bytes(b"fake-png-data")
        preview_paths.append(str(path))
    fake = FakeBot()
    context = SimpleNamespace(bot=fake)
    result = {
        "previewPaths": preview_paths,
        "sourceCount": 1,
        "storyboard": {"revision": 1, "scenes": [{}, {}]},
    }

    asyncio.run(video._send_storyboard(context, job.id, result))

    assert len(fake.media_groups) == 1
    assert len(fake.media_groups[0][1]) == 2
    assert fake.messages
    assert "Storyboard ready" in fake.messages[0][1]
    assert jobs.get(job.id).status_message_id == 700


def test_storyboard_upload_failure_is_retryable(isolated_db, tmp_path):
    class FailingPreviewBot(FakeBot):
        async def send_media_group(self, chat_id, media, **kwargs):
            raise TimeoutError("preview upload timed out")

    job = _seed_job(803)
    jobs.transition(job.id, jobs.STORYBOARD_REVIEW, allowed_from={jobs.AWAITING_VIDEO})
    preview = tmp_path / "scene-01.png"
    preview.write_bytes(b"png")
    result = {
        "previewPaths": [str(preview)],
        "storyboard": {"revision": 1, "scenes": [{"id": "scene-01"}]},
        "sourceCount": 1,
        "claimCount": 1,
        "unsupportedCount": 0,
    }

    with pytest.raises(TimeoutError):
        asyncio.run(
            video._send_storyboard(
                SimpleNamespace(bot=FailingPreviewBot()), job.id, result
            )
        )

    failed = jobs.get(job.id)
    assert failed.state == jobs.FAILED
    assert failed.failed_stage == "preview"
    assert jobs.retry(job.id).state == jobs.PLANNING


def test_final_video_uses_native_send_video_and_completes_job(isolated_db, tmp_path, monkeypatch):
    job = _seed_job(801)
    jobs.transition(job.id, jobs.DELIVERING, allowed_from={jobs.AWAITING_VIDEO})
    sess.set_state(801, sess.AWAITING_VIDEO)
    output = tmp_path / "final.mp4"
    output.write_bytes(b"fake-mp4-data")
    monkeypatch.setattr(
        ingest,
        "probe",
        lambda _path: ingest.MediaInfo(
            path=str(output),
            duration=12.2,
            width=1080,
            height=1920,
            fps=30.0,
            has_video=True,
            has_audio=True,
            video_codec="h264",
            audio_codec="aac",
        ),
    )
    fake = FakeBot()
    context = SimpleNamespace(bot=fake)

    asyncio.run(video._send_final_video(context, jobs.get(job.id), str(output)))

    assert len(fake.videos) == 1
    sent = fake.videos[0]
    assert sent["supports_streaming"] is True
    assert sent["width"] == 1080 and sent["height"] == 1920
    assert jobs.get(job.id).state == jobs.COMPLETED
    assert sess.load(job.user_id).state == sess.IDLE


def test_delivery_timeout_becomes_retryable_failure(isolated_db, tmp_path, monkeypatch):
    class FailingBot(FakeBot):
        async def send_video(self, **kwargs):
            raise TimeoutError("telegram upload timed out")

    row = _seed_job(802)
    jobs.transition(row.id, jobs.DELIVERING, allowed_from={jobs.AWAITING_VIDEO})
    output = tmp_path / "final.mp4"
    output.write_bytes(b"mp4")
    monkeypatch.setattr(
        video.ingest,
        "probe",
        lambda _: ingest.MediaInfo(str(output), 12.0, 1080, 1920, 30.0, True, True, "h264", "aac"),
    )
    context = SimpleNamespace(bot=FailingBot())

    with pytest.raises(TimeoutError):
        asyncio.run(video._send_final_video(context, jobs.get(row.id), str(output)))

    failed = jobs.get(row.id)
    assert failed.state == jobs.FAILED
    assert failed.failed_stage == "delivery"
    assert jobs.retry(row.id).state == jobs.DELIVERING


def test_cancel_requested_before_delivery_prevents_send(isolated_db, tmp_path, monkeypatch):
    row = _seed_job(804)
    jobs.transition(row.id, jobs.DELIVERING, allowed_from={jobs.AWAITING_VIDEO})
    jobs.request_cancel(row.id)
    output = tmp_path / "final.mp4"
    output.write_bytes(b"mp4")
    monkeypatch.setattr(
        video.ingest,
        "probe",
        lambda _: ingest.MediaInfo(str(output), 12.0, 1080, 1920, 30.0, True, True, "h264", "aac"),
    )
    bot = FakeBot()

    with pytest.raises(RuntimeError, match="cancelled before delivery"):
        asyncio.run(
            video._send_final_video(SimpleNamespace(bot=bot), jobs.get(row.id), str(output))
        )

    assert bot.videos == []
    assert jobs.get(row.id).state == jobs.CANCELLED


def test_cancel_during_delivery_deletes_sent_message(isolated_db, tmp_path, monkeypatch):
    row = _seed_job(805)
    jobs.transition(row.id, jobs.DELIVERING, allowed_from={jobs.AWAITING_VIDEO})
    output = tmp_path / "final.mp4"
    output.write_bytes(b"mp4")
    monkeypatch.setattr(
        video.ingest,
        "probe",
        lambda _: ingest.MediaInfo(str(output), 12.0, 1080, 1920, 30.0, True, True, "h264", "aac"),
    )

    class CancelDuringSendBot(FakeBot):
        async def send_video(self, **kwargs):
            result = await super().send_video(**kwargs)
            jobs.request_cancel(row.id)
            return result

    bot = CancelDuringSendBot()
    with pytest.raises(RuntimeError, match="cancelled during delivery"):
        asyncio.run(
            video._send_final_video(SimpleNamespace(bot=bot), jobs.get(row.id), str(output))
        )

    assert bot.deleted == [(row.chat_id, 901)]
    assert jobs.get(row.id).state == jobs.CANCELLED


def test_cancel_during_upload_validation_does_not_resurrect_job(
    isolated_db, tmp_path, monkeypatch
):
    row = _seed_job(806)
    settings = SimpleNamespace(
        video_max_upload_mb=20,
        video_max_seconds=90,
        video_work_dir=str(tmp_path),
        allowed_user_ids=(),
    )

    class StatusMessage:
        def __init__(self):
            self.edits = []
            self.message_id = 555

        async def edit_text(self, text, reply_markup=None):
            self.edits.append((text, reply_markup))

    status = StatusMessage()

    class UploadMessage:
        video = SimpleNamespace(
            file_id="file-806",
            file_name="obs.mp4",
            mime_type="video/mp4",
            file_size=1024,
        )
        document = None

        async def reply_text(self, text, **kwargs):
            return status

    class FakeTelegramFile:
        async def download_to_drive(self, destination, **kwargs):
            from pathlib import Path as P

            P(destination).write_bytes(b"mp4-bytes")

    class UploadBot(FakeBot):
        async def get_file(self, file_id, **kwargs):
            return FakeTelegramFile()

    def validate_then_cancel(path):
        jobs.request_cancel(row.id)
        jobs.mark_cancelled(row.id)
        return ingest.MediaInfo(str(path), 5.0, 1920, 1080, 30.0, True, True, "h264", "aac")

    monkeypatch.setattr(video.ingest, "validate_source", validate_then_cancel)
    monkeypatch.setattr(video.pipeline, "prune_artifacts", lambda settings: [])

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=806),
        effective_chat=SimpleNamespace(id=806),
        message=UploadMessage(),
    )
    context = SimpleNamespace(bot=UploadBot())

    asyncio.run(video._upload(update, context, settings))

    assert jobs.get(row.id).state == jobs.CANCELLED
    assert jobs.get_active_for_user(806) is None
    assert status.edits
    assert "Upload validation failed" not in status.edits[-1][0]
