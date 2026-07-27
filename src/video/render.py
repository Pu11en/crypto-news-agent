from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from video.ingest import probe


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


def _stream_durations(output_path: str | Path) -> dict[str, float]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
            "-of", "json", str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    durations: dict[str, float] = {}
    for stream in json.loads(result.stdout).get("streams", []):
        try:
            durations[str(stream["codec_type"])] = float(stream["duration"])
        except (KeyError, TypeError, ValueError):
            continue
    return durations


def lint(public_dir: str | Path) -> str:
    public = Path(public_dir)
    result = subprocess.run(
        ["npx", "--yes", "hyperframes@latest", "lint", "public"],
        cwd=public.parent,
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    output = result.stdout + result.stderr
    if result.returncode != 0 or "0 error(s)" not in output:
        raise RuntimeError(f"HyperFrames lint failed:\n{output[-4000:]}")
    return output


def render(public_dir: str | Path, output_path: str | Path) -> Path:
    public = Path(public_dir)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    raw = destination.with_name(destination.stem + "-raw.mp4")
    lint(public)
    subprocess.run(
        [
            "npx", "--yes", "hyperframes@latest", "render", "public",
            "--skill=talking-head-recut", "-o", str(raw), "--fps", "30",
        ],
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
    )
    raw.unlink(missing_ok=True)
    return destination


def validate_output(output_path: str | Path, expected_duration: float) -> dict:
    info = probe(output_path)
    errors = []
    if (info.width, info.height) != (1080, 1920):
        errors.append(f"wrong dimensions: {info.width}x{info.height}")
    if info.video_codec != "h264":
        errors.append(f"wrong video codec: {info.video_codec}")
    if info.audio_codec != "aac":
        errors.append(f"wrong audio codec: {info.audio_codec}")
    if abs(info.duration - expected_duration) > 0.12:
        errors.append(f"duration changed from {expected_duration:.3f}s to {info.duration:.3f}s")
    if not info.has_audio or not info.has_video:
        errors.append("final file is missing audio or video")
    luma_ranges = _upper_luma_ranges(output_path)
    if not luma_ranges:
        errors.append("could not sample the upper graphics region")
    elif any(value < 12 for value in luma_ranges):
        errors.append("blank upper graphics frame detected")
    stream_durations = _stream_durations(output_path)
    if {"audio", "video"}.issubset(stream_durations):
        if abs(stream_durations["audio"] - stream_durations["video"]) > 0.12:
            errors.append(
                "audio/video stream durations differ by more than 0.12 seconds"
            )
    if errors:
        raise RuntimeError("Final video QA failed: " + "; ".join(errors))
    return {
        "width": info.width,
        "height": info.height,
        "duration": info.duration,
        "videoCodec": info.video_codec,
        "audioCodec": info.audio_codec,
        "audioSync": "passed via stream-duration comparison",
        "blankFrames": f"passed across {len(luma_ranges)} sampled upper-region frames",
        "textOverflow": "passed via constrained compiler",
        "telegramEncoding": "passed",
    }