from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from video.hyperframes_cli import command as hyperframes
from video.ingest import probe


def _lint_has_zero_errors(output: str) -> bool:
    return re.search(r"\b0 error(?:s|\(s\))?\b", output) is not None


def _upper_luma_ranges(output_path: str | Path) -> list[int]:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(output_path),
            "-vf", "crop=1080:1312:0:0,fps=1,signalstats,metadata=print:file=-",
            "-an", "-f", "null", "-",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    mins = [int(value) for value in re.findall(r"YMIN=(\d+)", result.stdout)]
    maxs = [int(value) for value in re.findall(r"YMAX=(\d+)", result.stdout)]
    return [high - low for low, high in zip(mins, maxs)]


def _stream_timing(output_path: str | Path) -> dict[str, dict[str, float]]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,start_time,duration",
            "-of", "json", str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    timing: dict[str, dict[str, float]] = {}
    for stream in json.loads(result.stdout).get("streams", []):
        try:
            timing[str(stream["codec_type"])] = {
                "start": float(stream.get("start_time") or 0.0),
                "duration": float(stream["duration"]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return timing


def lint(public_dir: str | Path) -> str:
    public = Path(public_dir)
    result = subprocess.run(
        hyperframes("lint", "public"),
        cwd=public.parent,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0 or not _lint_has_zero_errors(output):
        raise RuntimeError(f"HyperFrames lint failed:\n{output[-4000:]}")
    return output


def render(public_dir: str | Path, output_path: str | Path) -> Path:
    public = Path(public_dir)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = destination.with_name(destination.stem + "-raw.mp4")
    lint(public)
    subprocess.run(
        hyperframes(
            "render",
            "public",
            "--skill=talking-head-recut",
            "-o",
            str(raw),
            "--fps",
            "30",
            "--workers",
            "1",
            "--low-memory-mode",
        ),
        cwd=public.parent,
        check=True,
        timeout=3600,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(raw), "-map", "0:v:0", "-map", "0:a:0",
            "-c", "copy", "-movflags", "+faststart", str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    )
    raw.unlink(missing_ok=True)
    return destination


def validate_output(
    output_path: str | Path,
    expected_duration: float,
    overflow_report_path: str | Path | None = None,
) -> dict:
    info = probe(output_path)
    errors = []
    if (info.width, info.height) != (1080, 1920):
        errors.append(f"expected 1080x1920, got {info.width}x{info.height}")
    if info.video_codec != "h264":
        errors.append(f"expected h264 video, got {info.video_codec}")
    if info.audio_codec != "aac":
        errors.append(f"expected aac audio, got {info.audio_codec}")
    if abs(info.duration - expected_duration) > 0.12:
        errors.append(f"duration changed from {expected_duration:.3f}s to {info.duration:.3f}s")
    if not info.has_audio or not info.has_video:
        errors.append("final file is missing audio or video")
    if Path(output_path).stat().st_size > 50 * 1024 * 1024:
        errors.append("final video exceeds Telegram's 50 MB sendVideo limit")
    luma_ranges = _upper_luma_ranges(output_path)
    if not luma_ranges:
        errors.append("could not sample the upper graphics region")
    elif any(value < 12 for value in luma_ranges):
        errors.append("blank upper graphics frame detected")
    stream_timing = _stream_timing(output_path)
    if {"audio", "video"}.issubset(stream_timing):
        audio = stream_timing["audio"]
        video = stream_timing["video"]
        if abs(audio["start"] - video["start"]) > 0.12:
            errors.append("audio/video stream start times differ by more than 0.12 seconds")
        audio_end = audio["start"] + audio["duration"]
        video_end = video["start"] + video["duration"]
        if abs(audio_end - video_end) > 0.12:
            errors.append("audio/video stream end times differ by more than 0.12 seconds")
    text_overflow_status = "not checked"
    if overflow_report_path is not None:
        report_path = Path(overflow_report_path)
        if not report_path.exists():
            errors.append("browser text-overflow report is missing")
        else:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            overflow_count = int(report.get("count", -1))
            if overflow_count != 0:
                errors.append(f"browser detected {overflow_count} text-overflow issue(s)")
            else:
                text_overflow_status = "passed via browser DOM measurement"
    if errors:
        raise RuntimeError("Final video QA failed: " + "; ".join(errors))
    return {
        "width": info.width,
        "height": info.height,
        "duration": info.duration,
        "videoCodec": info.video_codec,
        "audioCodec": info.audio_codec,
        "audioSync": "passed via stream start/end comparison",
        "blankFrames": f"passed across {len(luma_ranges)} sampled upper-region frames",
        "textOverflow": text_overflow_status,
        "telegramEncoding": "passed",
    }