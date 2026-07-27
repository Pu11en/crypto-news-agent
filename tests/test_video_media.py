from __future__ import annotations

import subprocess
from pathlib import Path

from video import ingest


def _make_fixture(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x303030:s=640x360:r=30:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_probe_and_stage_preserve_complete_recording(tmp_path):
    source = tmp_path / "source.mp4"
    staged = tmp_path / "public" / "input-video.mp4"
    _make_fixture(source)

    before = ingest.probe(source)
    after = ingest.stage_for_render(source, staged, fps=30)

    assert before.has_video is True
    assert before.has_audio is True
    assert after.has_video is True
    assert after.has_audio is True
    assert (after.width, after.height) == (1080, 608)
    assert abs(after.duration - before.duration) <= 1 / 30
    assert staged.exists()


def test_probe_rejects_media_without_audio(tmp_path):
    source = tmp_path / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x180:r=30:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )

    try:
        ingest.validate_source(source)
    except ValueError as exc:
        assert "audio" in str(exc).lower()
    else:
        raise AssertionError("silent source should be rejected")
