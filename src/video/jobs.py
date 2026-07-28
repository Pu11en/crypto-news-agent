from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import db

AWAITING_VIDEO = "awaiting_video"
AWAITING_CAPTIONS = "awaiting_captions"
PLANNING = "planning"
STORYBOARD_REVIEW = "storyboard_review"
RENDERING = "rendering"
DELIVERING = "delivering"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
TERMINAL_STATES = {COMPLETED, FAILED, CANCELLED}
INACTIVE_STATES = {COMPLETED, CANCELLED}
MAX_ACTIVE_JOBS = 5
ACTIVE_JOB_TTL_HOURS = 72
_CREATE_LOCK = threading.Lock()

_RETRY_STATE = {
    "download": AWAITING_VIDEO,
    "ingest": AWAITING_CAPTIONS,
    "transcription": PLANNING,
    "source_grounding": PLANNING,
    "fact_checking": PLANNING,
    "storyboarding": PLANNING,
    "preview": PLANNING,
    "rendering": STORYBOARD_REVIEW,
    "qa": STORYBOARD_REVIEW,
    "delivery": DELIVERING,
}


def get(job_id: str) -> db.VideoJob | None:
    factory = db.session()
    with factory() as s:
        return s.get(db.VideoJob, job_id)


def create_for_script(
    user_id: int,
    chat_id: int,
    script_id: int,
    *,
    stale_hours: int = ACTIVE_JOB_TTL_HOURS,
) -> db.VideoJob:
    with _CREATE_LOCK:
        factory = db.session()
        with factory() as s:
            existing = (
                s.query(db.VideoJob)
                .filter(db.VideoJob.user_id == user_id, db.VideoJob.script_id == script_id)
                .filter(~db.VideoJob.state.in_(TERMINAL_STATES))
                .first()
            )
            if existing is not None:
                return existing
            cutoff = datetime.now(timezone.utc) - timedelta(hours=stale_hours)
            stale_rows = (
                s.query(db.VideoJob)
                .filter(~db.VideoJob.state.in_(INACTIVE_STATES))
                .filter(db.VideoJob.updated_at < cutoff)
                .all()
            )
            for stale in stale_rows:
                stale.state = CANCELLED
                stale.cancel_requested = True
                stale.completed_at = datetime.now(timezone.utc)
            active_count = (
                s.query(db.VideoJob)
                .filter(~db.VideoJob.state.in_(INACTIVE_STATES))
                .count()
            )
            if active_count >= MAX_ACTIVE_JOBS:
                raise ValueError(
                    "The video worker is at capacity. Finish or cancel an active job before starting another."
                )
            row = db.VideoJob(
                id=uuid.uuid4().hex[:16],
                user_id=user_id,
                chat_id=chat_id,
                script_id=script_id,
                state=AWAITING_VIDEO,
            )
            s.add(row)
            s.commit()
            s.refresh(row)
            return row


def get_active_for_user(user_id: int) -> db.VideoJob | None:
    factory = db.session()
    with factory() as s:
        return (
            s.query(db.VideoJob)
            .filter(db.VideoJob.user_id == user_id)
            .filter(~db.VideoJob.state.in_(INACTIVE_STATES))
            .order_by(db.VideoJob.created_at.desc())
            .first()
        )


def get_latest_for_user(user_id: int) -> db.VideoJob | None:
    factory = db.session()
    with factory() as s:
        return (
            s.query(db.VideoJob)
            .filter(db.VideoJob.user_id == user_id)
            .order_by(db.VideoJob.created_at.desc())
            .first()
        )


def active_id_prefixes() -> set[str]:
    factory = db.session()
    with factory() as s:
        rows = (
            s.query(db.VideoJob.id)
            .filter(~db.VideoJob.state.in_(INACTIVE_STATES))
            .all()
        )
        return {str(row[0]) for row in rows}


def cancel_stale(hours: int) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    factory = db.session()
    with factory() as s:
        rows = (
            s.query(db.VideoJob)
            .filter(~db.VideoJob.state.in_(INACTIVE_STATES))
            .filter(db.VideoJob.updated_at < cutoff)
            .all()
        )
        ids = [row.id for row in rows]
        for row in rows:
            row.state = CANCELLED
            row.cancel_requested = True
            row.completed_at = datetime.now(timezone.utc)
        s.commit()
        return ids


def update(job_id: str, **values) -> db.VideoJob:
    factory = db.session()
    with factory() as s:
        row = s.get(db.VideoJob, job_id)
        if row is None:
            raise ValueError(f"Video job {job_id} not found")
        for key, value in values.items():
            if not hasattr(row, key):
                raise ValueError(f"Unknown video job field: {key}")
            setattr(row, key, value)
        s.commit()
        s.refresh(row)
        return row


def transition(job_id: str, state: str, *, allowed_from: set[str] | None = None, **values) -> db.VideoJob:
    factory = db.session()
    with factory() as s:
        row = s.get(db.VideoJob, job_id)
        if row is None:
            raise ValueError(f"Video job {job_id} not found")
        if allowed_from is not None and row.state not in allowed_from:
            raise ValueError(f"Cannot transition video job from {row.state} to {state}")
        row.state = state
        for key, value in values.items():
            if not hasattr(row, key):
                raise ValueError(f"Unknown video job field: {key}")
            setattr(row, key, value)
        s.commit()
        s.refresh(row)
        return row


def fail(job_id: str, stage: str, message: str) -> db.VideoJob:
    current = get(job_id)
    if current is None:
        raise ValueError(f"Video job {job_id} not found")
    if current.state in TERMINAL_STATES:
        return current
    return transition(
        job_id,
        FAILED,
        failed_stage=stage,
        current_stage=stage,
        error_message=message[:4000],
    )


def retry(job_id: str) -> db.VideoJob:
    row = get(job_id)
    if row is None:
        raise ValueError(f"Video job {job_id} not found")
    if row.state != FAILED:
        raise ValueError("Only failed video jobs can be retried")
    state = _RETRY_STATE.get(row.failed_stage or "", PLANNING)
    return transition(
        job_id,
        state,
        allowed_from={FAILED},
        error_message=None,
        cancel_requested=False,
        current_stage=None,
    )


def request_cancel(job_id: str) -> db.VideoJob:
    return update(job_id, cancel_requested=True)


def mark_cancelled(job_id: str) -> db.VideoJob:
    return transition(
        job_id,
        CANCELLED,
        cancel_requested=True,
        completed_at=datetime.now(timezone.utc),
    )


def mark_completed(job_id: str, output_path: str) -> db.VideoJob:
    return transition(
        job_id,
        COMPLETED,
        output_path=output_path,
        completed_at=datetime.now(timezone.utc),
        current_stage="completed",
    )
