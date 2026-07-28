from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from typing import Callable

from video import compose, director, grounding, ingest, jobs, preview, quality, render, sources, transcribe

Progress = Callable[[str, str], None]


def _notify(callback: Progress | None, stage: str, detail: str) -> None:
    if callback:
        callback(stage, detail)


def _write_json(path: Path, payload) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _check_cancel(job_id: str) -> None:
    row = jobs.get(job_id)
    if row is None:
        raise RuntimeError("Video job disappeared")
    if row.cancel_requested:
        jobs.mark_cancelled(job_id)
        raise RuntimeError("Video job was cancelled")


def job_dir(settings, job_id: str) -> Path:
    return Path(settings.video_work_dir).expanduser().resolve() / job_id


def prune_artifacts(settings, *, max_inactive_jobs: int = 10) -> list[str]:
    """Delete stale inactive job directories while protecting resumable jobs."""
    root = Path(settings.video_work_dir).expanduser().resolve()
    if not root.exists():
        return []
    jobs.cancel_stale(settings.video_artifact_retention_hours)
    active = jobs.active_id_prefixes()
    cutoff = time.time() - (settings.video_artifact_retention_hours * 3600)
    retained: list[tuple[float, Path]] = []
    removed: list[str] = []
    for child in root.iterdir():
        if not child.is_dir() or not re.fullmatch(r"[0-9a-f]{16}", child.name):
            continue
        if child.name in active:
            continue
        modified = child.stat().st_mtime
        if modified < cutoff:
            shutil.rmtree(child)
            removed.append(child.name)
        else:
            retained.append((modified, child))
    retained.sort(reverse=True)
    for _, child in retained[max_inactive_jobs:]:
        shutil.rmtree(child)
        removed.append(child.name)
    return removed


def prepare_storyboard(job_id: str, settings, llm, progress: Progress | None = None) -> dict:
    row = jobs.get(job_id)
    if row is None or not row.source_path:
        raise RuntimeError("The uploaded recording is missing")
    work = job_dir(settings, job_id)
    public = work / "public"
    public.mkdir(parents=True, exist_ok=True)
    try:
        _check_cancel(job_id)
        _notify(progress, "ingest", "Validating and staging the complete OBS recording")
        jobs.update(job_id, current_stage="ingest")
        source_info = ingest.validate_source(row.source_path)
        if source_info.duration > settings.video_max_seconds + 0.05:
            raise ValueError(
                f"Recording is {source_info.duration:.1f}s. The current limit is {settings.video_max_seconds}s."
            )
        staged = ingest.stage_for_render(row.source_path, public / "input-video.mp4")
        metadata = {
            "duration": staged.duration,
            "width": source_info.width,
            "height": source_info.height,
            "fps": source_info.fps,
            "hasAudio": source_info.has_audio,
            "hasVideo": source_info.has_video,
        }
        metadata_path = _write_json(work / "media.json", metadata)
        jobs.update(job_id, metadata_path=str(metadata_path))
        _check_cancel(job_id)

        _notify(progress, "transcription", "Transcribing the actual delivery with word timings")
        jobs.update(job_id, current_stage="transcription")
        audio = transcribe.extract_audio(public / "input-video.mp4", work / "audio.wav")
        words = transcribe.run(audio, work, settings.whisper_model)
        transcript_path = work / "transcript.json"
        jobs.update(job_id, transcript_path=str(transcript_path))
        _check_cancel(job_id)

        _notify(progress, "source_grounding", "Loading the exact scraped source package")
        jobs.update(job_id, current_stage="source_grounding")
        bundle = sources.build_source_bundle(row.script_id)
        source_path = _write_json(work / "source-bundle.json", bundle)
        jobs.update(job_id, source_bundle_path=str(source_path))
        _check_cancel(job_id)

        _notify(progress, "storyboarding", "Checking claims and building the editorial storyboard")
        jobs.update(job_id, current_stage="storyboarding")
        storyboard = director.build_storyboard(
            llm,
            bundle,
            words,
            duration=staged.duration,
            captions_enabled=bool(row.captions_enabled),
        )
        errors = quality.validate_storyboard(storyboard, bundle)
        if errors:
            class DeterministicFallback:
                def structured(self, *args, **kwargs):
                    raise RuntimeError("use deterministic fallback")

            storyboard = director.build_storyboard(
                DeterministicFallback(),
                bundle,
                words,
                duration=staged.duration,
                captions_enabled=bool(row.captions_enabled),
            )
            errors = quality.validate_storyboard(storyboard, bundle)
        if errors:
            raise RuntimeError("Storyboard grounding failed: " + "; ".join(errors[:8]))
        _notify(
            progress,
            "fact_checking",
            "Auditing every spoken scene against exact scraped tweet quotes",
        )
        jobs.update(job_id, current_stage="fact_checking")
        claim_audit = grounding.audit_storyboard(llm, storyboard, bundle)
        storyboard["claimAudit"] = claim_audit["claims"]
        _write_json(work / "claim-audit.json", claim_audit)
        storyboard_path = _write_json(work / "storyboard.json", storyboard)
        compose.write_composition(public, storyboard, words)
        jobs.update(
            job_id,
            storyboard_path=str(storyboard_path),
            composition_path=str(public / "index.html"),
        )
        _check_cancel(job_id)

        _notify(progress, "preview", "Creating fully revealed storyboard screenshots")
        jobs.update(job_id, current_stage="preview")
        preview_paths = preview.capture(public, storyboard, work / "review")
        _check_cancel(job_id)
        jobs.transition(
            job_id,
            jobs.STORYBOARD_REVIEW,
            preview_paths=[str(path) for path in preview_paths],
            current_stage="storyboard_review",
            revision=1,
        )
        return {
            "jobId": job_id,
            "storyboard": storyboard,
            "previewPaths": [str(path) for path in preview_paths],
            "sourceCount": len(bundle.get("tweets", [])),
            "claimCount": len(claim_audit.get("claims", [])),
            "unsupportedCount": sum(
                1
                for claim in claim_audit.get("claims", [])
                if claim.get("status") != "supported"
            ),
            "sceneCount": len(storyboard.get("scenes", [])),
            "duration": staged.duration,
        }
    except Exception as exc:
        current = jobs.get(job_id)
        if current and current.state != jobs.CANCELLED:
            jobs.fail(job_id, current.current_stage or "storyboarding", str(exc))
        raise


def revise_storyboard(job_id: str, settings, llm, feedback: str, progress: Progress | None = None) -> dict:
    row = jobs.get(job_id)
    if row is None or not row.transcript_path or not row.source_bundle_path or not row.metadata_path:
        raise RuntimeError("The storyboard source artifacts are incomplete")
    work = job_dir(settings, job_id)
    try:
        _check_cancel(job_id)
        jobs.transition(job_id, jobs.PLANNING, allowed_from={jobs.STORYBOARD_REVIEW}, current_stage="storyboarding")
        _notify(progress, "storyboarding", "Applying the requested visual revision")
        words = json.loads(Path(row.transcript_path).read_text(encoding="utf-8"))
        bundle = json.loads(Path(row.source_bundle_path).read_text(encoding="utf-8"))
        metadata = json.loads(Path(row.metadata_path).read_text(encoding="utf-8"))
        storyboard = director.build_storyboard(
            llm,
            bundle,
            words,
            duration=float(metadata["duration"]),
            captions_enabled=bool(row.captions_enabled),
            revision_feedback=feedback,
        )
        storyboard["revision"] = int(row.revision or 1) + 1
        errors = quality.validate_storyboard(storyboard, bundle)
        if errors:
            raise RuntimeError("Revised storyboard failed validation: " + "; ".join(errors[:8]))
        _notify(
            progress,
            "fact_checking",
            "Rechecking every revised scene against exact scraped tweet quotes",
        )
        jobs.update(job_id, current_stage="fact_checking")
        claim_audit = grounding.audit_storyboard(llm, storyboard, bundle)
        storyboard["claimAudit"] = claim_audit["claims"]
        _write_json(work / "claim-audit.json", claim_audit)
        storyboard_path = _write_json(work / "storyboard.json", storyboard)
        compose.write_composition(work / "public", storyboard, words)
        paths = preview.capture(work / "public", storyboard, work / "review")
        _check_cancel(job_id)
        jobs.transition(
            job_id,
            jobs.STORYBOARD_REVIEW,
            preview_paths=[str(path) for path in paths],
            storyboard_path=str(storyboard_path),
            current_stage="storyboard_review",
            revision=storyboard["revision"],
        )
        return {
            "storyboard": storyboard,
            "previewPaths": [str(path) for path in paths],
            "sourceCount": len(bundle.get("tweets", [])),
            "claimCount": len(claim_audit.get("claims", [])),
            "unsupportedCount": sum(
                1
                for claim in claim_audit.get("claims", [])
                if claim.get("status") != "supported"
            ),
        }
    except Exception as exc:
        current = jobs.get(job_id)
        if current and current.state != jobs.CANCELLED:
            jobs.fail(job_id, "storyboarding", str(exc))
        raise


def render_approved(job_id: str, settings, progress: Progress | None = None) -> dict:
    row = jobs.get(job_id)
    if row is None or not row.metadata_path:
        raise RuntimeError("Video job metadata is missing")
    work = job_dir(settings, job_id)
    try:
        _check_cancel(job_id)
        jobs.transition(job_id, jobs.RENDERING, allowed_from={jobs.STORYBOARD_REVIEW}, current_stage="rendering")
        _notify(progress, "rendering", "Rendering the final 1080x1920 video")
        metadata = json.loads(Path(row.metadata_path).read_text(encoding="utf-8"))
        output = render.render(work / "public", work / "final.mp4")
        _check_cancel(job_id)
        _notify(progress, "qa", "Running deterministic and Telegram encoding QA")
        jobs.update(job_id, current_stage="qa")
        qa = render.validate_output(
            output,
            float(metadata["duration"]),
            overflow_report_path=work / "review" / "overflow-report.json",
        )
        _write_json(work / "qa.json", qa)
        _check_cancel(job_id)
        jobs.transition(job_id, jobs.DELIVERING, output_path=str(output), current_stage="delivery")
        return {"outputPath": str(output), "qa": qa}
    except Exception as exc:
        current = jobs.get(job_id)
        if current and current.state != jobs.CANCELLED:
            jobs.fail(job_id, current.current_stage or "rendering", str(exc))
        raise
