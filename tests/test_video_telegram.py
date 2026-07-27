from __future__ import annotations

import asyncio
from types import SimpleNamespace

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

    async def send_media_group(self, chat_id, media):
        self.media_groups.append((chat_id, media))

    async def send_message(self, chat_id, text, reply_markup=None):
        self.messages.append((chat_id, text, reply_markup))
        return SimpleNamespace(message_id=700)

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)

    async def send_video(self, **kwargs):
        self.videos.append(kwargs)


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
    assert sess.load(801).state == sess.IDLE
