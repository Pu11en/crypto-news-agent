from __future__ import annotations

import db
from handlers import chat
from video import jobs


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
    assert chat.is_script_approval("Perfect, one more change") is False
    assert chat.is_script_approval("almost perfect but change the hook") is False


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
        script = db.Script(
            session_id="55",
            version=1,
            body="A protocol announced a move.",
            story_ids=[story.id],
            is_final=False,
        )
        s.add(script)
        s.commit()
        script_id = script.id
    sess.set_current_script(55, "A protocol announced a move.")

    final_script, job = news.approve_script(55, chat_id=5500)

    assert final_script.id == script_id
    assert final_script.is_final is True
    assert final_script.story_ids
    assert job.script_id == script_id
    assert sess.load(55).state == sess.AWAITING_VIDEO
