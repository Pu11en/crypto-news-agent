from __future__ import annotations

import os
import time
from types import SimpleNamespace

from video import pipeline


def test_prune_artifacts_deletes_stale_inactive_jobs_but_keeps_active(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    active = root / "aaaaaaaaaaaaaaaa"
    stale = root / "bbbbbbbbbbbbbbbb"
    recent = root / "cccccccccccccccc"
    for folder in (active, stale, recent):
        folder.mkdir(parents=True)
        (folder / "large-upload.mp4").write_bytes(b"x")
    old = time.time() - (80 * 3600)
    os.utime(active, (old, old))
    os.utime(stale, (old, old))
    monkeypatch.setattr(pipeline.jobs, "cancel_stale", lambda _: [])
    monkeypatch.setattr(pipeline.jobs, "active_id_prefixes", lambda: {"aaaaaaaaaaaaaaaa"})
    settings = SimpleNamespace(
        video_work_dir=str(root),
        video_artifact_retention_hours=72,
    )

    removed = pipeline.prune_artifacts(settings)

    assert stale.name in removed
    assert active.exists()
    assert recent.exists()


def test_prune_never_deletes_non_job_directories(tmp_path, monkeypatch):
    root = tmp_path / "jobs"
    foreign = root / "not-a-job-directory"
    foreign.mkdir(parents=True)
    (foreign / "important.txt").write_bytes(b"keep")
    old = time.time() - (800 * 3600)
    os.utime(foreign, (old, old))
    monkeypatch.setattr(pipeline.jobs, "cancel_stale", lambda _: [])
    monkeypatch.setattr(pipeline.jobs, "active_id_prefixes", lambda: set())
    settings = SimpleNamespace(
        video_work_dir=str(root),
        video_artifact_retention_hours=72,
    )

    removed = pipeline.prune_artifacts(settings)

    assert removed == []
    assert foreign.exists()
