"""Per-user session state helpers.

A session row in the DB tracks where the user is in the /news flow:
    idle            : no active flow; free chat
    awaiting_pick   : top-5 stories shown, waiting for "1,3,5" / "auto" / "all"
    script_draft    - a script exists; messages refine it until /done or /cancel
    awaiting_video  - approved script is locked; waiting for an OBS upload

These helpers wrap the DB row so handlers don't touch SQLAlchemy directly.
"""

from __future__ import annotations

from db import Session, get_or_create_session, session as sessionmaker

IDLE = "idle"
AWAITING_PICK = "awaiting_pick"
SCRAPING = "scraping"
SCRIPT_DRAFT = "script_draft"
AWAITING_VIDEO = "awaiting_video"
STORYBOARD_REVISION = "storyboard_revision"


def load(user_id: int) -> Session:
    return get_or_create_session(user_id)


def _save(sess: Session) -> None:
    db = sessionmaker()()
    db.merge(sess)
    db.commit()
    db.close()


def set_state(user_id: int, state: str) -> Session:
    sess = load(user_id)
    sess.state = state
    _save(sess)
    return sess


def set_run(user_id: int, run_id: str, story_ids: list[int]) -> Session:
    """Pin a scrape run + its curated stories to the session, await user pick."""
    sess = load(user_id)
    sess.run_id = run_id
    sess.story_ids = story_ids
    sess.state = AWAITING_PICK
    # Clear any prior script context.
    sess.current_script = None
    sess.script_version = 0
    sess.history = []
    _save(sess)
    return sess


def start_script(user_id: int, chosen_story_ids: list[int]) -> Session:
    sess = load(user_id)
    sess.story_ids = chosen_story_ids
    sess.state = SCRIPT_DRAFT
    sess.current_script = None
    sess.script_version = 0
    sess.history = []
    _save(sess)
    return sess


def set_current_script(user_id: int, body: str) -> Session:
    sess = load(user_id)
    sess.current_script = body
    sess.script_version = int(sess.script_version or 0) + 1
    _save(sess)
    return sess


def append_history(user_id: int, role: str, content: str) -> None:
    sess = load(user_id)
    hist = list(sess.history or [])
    hist.append({"role": role, "content": content})
    # Keep the rolling window small : last 12 turns.
    sess.history = hist[-12:]
    _save(sess)


def reset(user_id: int) -> Session:
    sess = load(user_id)
    sess.state = IDLE
    sess.run_id = None
    sess.story_ids = []
    sess.current_script = None
    sess.script_version = 0
    sess.history = []
    _save(sess)
    return sess
