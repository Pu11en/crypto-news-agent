from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import db
import pytest
from handlers import chat
from video import jobs
from video import pipeline


def _seed_final_script() -> int:
    factory = db.session()
    with factory() as s:
        story = db.Story(
            run_id="run-1",
            rank=1,
            headline="ETF opens access",
            summary="Traditional accounts can access SOL.",
            tweet_ids=["tweet-1"],
            score=0.9,
        )
        s.add(story)
        s.flush()
        script = db.Script(
            session_id="42",
            version=2,
            body="A spot ETF opened traditional access to SOL.",
            story_ids=[story.id],
            is_final=True,
        )
        s.add(script)
        s.commit()
        return script.id


def test_explicit_script_approval_recognizes_positive_phrases_not_negation():
    assert chat.is_script_approval("Looks good") is True
    assert chat.is_script_approval("perfect, use this") is True
    assert chat.is_script_approval("that's exactly what I want") is True
    assert chat.is_script_approval("not good yet") is False
    assert chat.is_script_approval("I don't approve it") is False
    assert chat.is_script_approval("I cannot approve it") is False
    assert chat.is_script_approval("I won't approve it") is False
    assert chat.is_script_approval("not exactly what I want") is False
    assert chat.is_script_approval("Perfect, one more change") is False
    assert chat.is_script_approval("almost perfect but change the hook") is False
    assert chat.is_script_approval("doesn't look good") is False
    assert chat.is_script_approval("this isn't perfect") is False
    assert chat.is_script_approval("never lock it") is False
    assert chat.is_script_approval("no, don't lock it") is False
    assert chat.is_script_approval("I wouldn't use this") is False
    assert chat.is_script_approval("that's not ready") is False
    assert chat.is_script_approval("didn't like it, not good to go") is False


def test_create_job_links_final_script_and_is_idempotent(isolated_db):
    script_id = _seed_final_script()

    first = jobs.create_for_script(user_id=42, chat_id=99, script_id=script_id)
    second = jobs.create_for_script(user_id=42, chat_id=99, script_id=script_id)

    assert first.id == second.id
    assert first.state == jobs.AWAITING_VIDEO
    assert first.script_id == script_id
    assert jobs.get_active_for_user(42).id == first.id


def test_job_transitions_cancel_and_retry_are_persisted(isolated_db):
    script_id = _seed_final_script()
    job = jobs.create_for_script(user_id=42, chat_id=99, script_id=script_id)

    jobs.transition(job.id, jobs.AWAITING_CAPTIONS, allowed_from={jobs.AWAITING_VIDEO})
    jobs.transition(job.id, jobs.PLANNING, allowed_from={jobs.AWAITING_CAPTIONS})
    failed = jobs.fail(job.id, "storyboarding", "model returned invalid JSON")

    assert failed.state == jobs.FAILED
    assert failed.failed_stage == "storyboarding"
    assert "invalid JSON" in failed.error_message
    assert jobs.get_active_for_user(42).id == job.id

    retried = jobs.retry(job.id)
    assert retried.state == jobs.PLANNING
    assert retried.error_message is None

    cancelled = jobs.request_cancel(job.id)
    assert cancelled.cancel_requested is True
    completed_cancel = jobs.mark_cancelled(job.id)
    assert completed_cancel.state == jobs.CANCELLED
    assert jobs.get_active_for_user(42) is None
    assert jobs.get_latest_for_user(42).id == job.id


def test_approve_script_creates_job_without_losing_source_links(isolated_db):
    from handlers import news, session as sess

    user = sess.start_script(55, [])
    factory = db.session()
    with factory() as s:
        story = db.Story(
            run_id="run-final",
            rank=1,
            headline="Migration announced",
            summary="A protocol announced a move.",
            tweet_ids=["tweet-final"],
            score=0.8,
        )
        s.add(story)
        s.flush()
        user.story_ids = [story.id]
        s.merge(user)
        wrong_script = db.Script(
            session_id="55",
            version=1,
            body="A protocol announced a move.",
            story_ids=[],
            is_final=False,
        )
        s.add(wrong_script)
        s.flush()
        wrong_script_id = wrong_script.id
        script = db.Script(
            session_id="55",
            version=1,
            body="A protocol announced a move.",
            story_ids=[story.id],
            is_final=False,
        )
        s.add(script)
        s.add(
            db.Script(
                session_id="55",
                version=2,
                body="A different unreviewed draft.",
                story_ids=[story.id],
                is_final=False,
            )
        )
        s.commit()
        script_id = script.id
    sess.set_current_script(55, "A protocol announced a move.")

    with pytest.raises(ValueError, match="displayed script"):
        news.approve_script(55, chat_id=5500, expected_script_id=wrong_script_id)

    final_script, job = news.approve_script(
        55, chat_id=5500, expected_script_id=script_id
    )

    assert final_script.id == script_id
    assert final_script.body == "A protocol announced a move."
    assert final_script.is_final is True
    assert final_script.story_ids
    assert job.script_id == script_id
    assert sess.load(55).state == sess.AWAITING_VIDEO


def test_global_active_job_capacity_is_bounded(isolated_db):
    factory = db.session()
    script_ids = []
    with factory() as s:
        for index in range(6):
            script = db.Script(
                session_id=str(100 + index),
                version=1,
                body=f"Approved script {index}",
                story_ids=[],
                is_final=True,
            )
            s.add(script)
            s.flush()
            script_ids.append(script.id)
        s.commit()

    for index in range(5):
        jobs.create_for_script(100 + index, 1000 + index, script_ids[index])

    with pytest.raises(ValueError, match="capacity"):
        jobs.create_for_script(105, 1005, script_ids[5])


def test_global_capacity_is_atomic_across_threads(isolated_db):
    factory = db.session()
    script_ids = []
    with factory() as s:
        for index in range(10):
            script = db.Script(
                session_id=str(200 + index),
                version=1,
                body=f"Concurrent script {index}",
                story_ids=[],
                is_final=True,
            )
            s.add(script)
            s.flush()
            script_ids.append(script.id)
        s.commit()

    def create(index):
        try:
            jobs.create_for_script(200 + index, 2000 + index, script_ids[index])
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        outcomes = list(pool.map(create, range(10)))

    assert sum(outcomes) == jobs.MAX_ACTIVE_JOBS
    with factory() as s:
        assert s.query(db.VideoJob).count() == jobs.MAX_ACTIVE_JOBS


def test_stale_jobs_are_expired_before_capacity_check(isolated_db):
    factory = db.session()
    script_ids = []
    with factory() as s:
        for index in range(6):
            script = db.Script(
                session_id=str(300 + index),
                version=1,
                body=f"Stale-capacity script {index}",
                story_ids=[],
                is_final=True,
            )
            s.add(script)
            s.flush()
            script_ids.append(script.id)
        s.commit()

    stale_ids = []
    for index in range(5):
        row = jobs.create_for_script(300 + index, 3000 + index, script_ids[index])
        stale_ids.append(row.id)

    with factory() as s:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=73)
        for row in s.query(db.VideoJob).filter(db.VideoJob.id.in_(stale_ids)).all():
            row.updated_at = cutoff
        s.commit()

    replacement = jobs.create_for_script(305, 3005, script_ids[5])

    assert replacement.state == jobs.AWAITING_VIDEO
    assert all(jobs.get(job_id).state == jobs.CANCELLED for job_id in stale_ids)


def test_precancelled_job_stops_before_ingestion(isolated_db, tmp_path, monkeypatch):
    script_id = _seed_final_script()
    job = jobs.create_for_script(999, 9990, script_id)
    jobs.transition(
        job.id,
        jobs.PLANNING,
        allowed_from={jobs.AWAITING_VIDEO},
        source_path=str(tmp_path / "never-read.mp4"),
    )
    jobs.request_cancel(job.id)
    monkeypatch.setattr(
        pipeline.ingest,
        "validate_source",
        lambda _: (_ for _ in ()).throw(AssertionError("ingest should not run")),
    )
    settings = SimpleNamespace(video_work_dir=str(tmp_path), video_max_seconds=90)

    with pytest.raises(RuntimeError, match="cancelled"):
        pipeline.prepare_storyboard(job.id, settings, object())

    assert jobs.get(job.id).state == jobs.CANCELLED


def test_fail_cannot_resurrect_cancelled_or_completed_jobs(isolated_db):
    script_id = _seed_final_script()
    job = jobs.create_for_script(user_id=42, chat_id=99, script_id=script_id)
    jobs.mark_cancelled(job.id)

    result = jobs.fail(job.id, "download", "Cannot transition video job from cancelled")

    assert result.state == jobs.CANCELLED
    assert jobs.get(job.id).state == jobs.CANCELLED
    assert jobs.get_active_for_user(42) is None

    second = jobs.create_for_script(user_id=77, chat_id=770, script_id=script_id)
    jobs.mark_completed(second.id, "/tmp/out.mp4")
    assert jobs.fail(second.id, "delivery", "late failure").state == jobs.COMPLETED


def test_qa_stage_failure_retries_from_storyboard_review(isolated_db):
    script_id = _seed_final_script()
    job = jobs.create_for_script(user_id=42, chat_id=99, script_id=script_id)
    jobs.transition(job.id, jobs.RENDERING, allowed_from={jobs.AWAITING_VIDEO})
    jobs.fail(job.id, "qa", "final QA failed")

    assert jobs.retry(job.id).state == jobs.STORYBOARD_REVIEW


def test_completed_job_does_not_bind_reapproved_script(isolated_db):
    script_id = _seed_final_script()
    first = jobs.create_for_script(user_id=42, chat_id=99, script_id=script_id)
    jobs.mark_completed(first.id, "/tmp/out.mp4")

    second = jobs.create_for_script(user_id=42, chat_id=99, script_id=script_id)

    assert second.id != first.id
    assert second.state == jobs.AWAITING_VIDEO
