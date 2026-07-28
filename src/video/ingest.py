from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MediaInfo:
    path: str
    duration: float
    width: int
    height: int
    fps: float
    has_video: bool
    has_audio: bool
    video_codec: str | None = None
    audio_codec: str | None = None


def _rate(value: str | None) -> float:
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    return float(value)


def probe(path: str | Path) -> MediaInfo:
    media_path = Path(path)
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(media_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    payload = json.loads(result.stdout)
    streams = payload.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)
    duration = float(payload.get("format", {}).get("duration") or 0.0)
    return MediaInfo(
        path=str(media_path),
        duration=duration,
        width=int(video.get("width", 0)) if video else 0,
        height=int(video.get("height", 0)) if video else 0,
        fps=_rate(video.get("r_frame_rate")) if video else 0.0,
        has_video=video is not None,
        has_audio=audio is not None,
        video_codec=video.get("codec_name") if video else None,
        audio_codec=audio.get("codec_name") if audio else None,
    )


def validate_upload_size(file_size: int | None, max_megabytes: int) -> None:
    if file_size is None:
        return
    maximum = max_megabytes * 1024 * 1024
    if file_size > maximum:
        raise ValueError(
            f"Recording is larger than the {max_megabytes} MB cloud Bot API limit. "
            "Send it as a Telegram video so Telegram can compress it."
        )


def validate_source(
    path: str | Path,
    *,
    max_pixels: int = 3840 * 2160,
    max_fps: float = 120.0,
) -> MediaInfo:
    info = probe(path)
    if not info.has_video:
        raise ValueError("Recording has no video stream")
    if not info.has_audio:
        raise ValueError("Recording has no audio stream")
    if info.duration <= 0:
        raise ValueError("Recording has no playable duration")
    if info.width * info.height > max_pixels:
        raise ValueError("Recording resolution is larger than the supported 4K-class input limit")
    if info.fps <= 0 or info.fps > max_fps:
        raise ValueError(f"Recording frame rate must be between 1 and {max_fps:g} fps")
    return info


def stage_for_render(source: str | Path, destination: str | Path, fps: int = 30) -> MediaInfo:
    """Re-encode for frame seeking while preserving the complete timeline and audio."""
    source_info = validate_source(source)
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-vf",
            "scale=1080:608:force_original_aspect_ratio=decrease,pad=1080:608:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
            "-r",
            str(fps),
            "-fps_mode",
            "cfr",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-preset",
            "medium",
            "-g",
            str(fps),
            "-keyint_min",
            str(fps),
            "-sc_threshold",
            "0",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
        capture_output=True,
        timeout=600,
    )
    staged = validate_source(output)
    if abs(staged.duration - source_info.duration) > max(1 / fps, 0.05):
        raise ValueError("Staged recording duration does not match the uploaded recording")
    return staged
