from __future__ import annotations

import subprocess
import json

import pytest

from video import render
from video.ingest import MediaInfo


def test_lint_accepts_both_zero_error_wordings():
    assert render._lint_has_zero_errors("0 error(s), 2 warnings")
    assert render._lint_has_zero_errors("0 errors, 0 warnings")


def test_final_qa_uses_browser_overflow_report(tmp_path, monkeypatch):
    output = tmp_path / "final.mp4"
    output.write_bytes(b"mp4")
    monkeypatch.setattr(
        render,
        "probe",
        lambda _: MediaInfo(str(output), 2.0, 1080, 1920, 30.0, True, True, "h264", "aac"),
    )
    monkeypatch.setattr(render, "_upper_luma_ranges", lambda _: [200, 210])
    monkeypatch.setattr(
        render,
        "_stream_timing",
        lambda _: {
            "audio": {"start": 0.0, "duration": 2.0},
            "video": {"start": 0.0, "duration": 2.0},
        },
    )
    report = tmp_path / "overflow.json"
    report.write_text(json.dumps({"count": 0, "issues": []}), encoding="utf-8")

    unchecked = render.validate_output(output, 2.0)
    checked = render.validate_output(output, 2.0, report)

    assert unchecked["textOverflow"] == "not checked"
    assert checked["textOverflow"] == "passed via browser DOM measurement"

    report.write_text(json.dumps({"count": 1, "issues": [{"className": "title"}]}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="text-overflow"):
        render.validate_output(output, 2.0, report)


def test_final_qa_rejects_blank_upper_graphics_region(tmp_path):
    output = tmp_path / "blank.mp4"
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "color=c=black:s=1080x1920:r=30:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-shortest", str(output),
        ],
        check=True,
    )

    with pytest.raises(RuntimeError, match="blank upper graphics"):
        render.validate_output(output, expected_duration=1.0)


def test_final_qa_rejects_audio_start_offset(tmp_path):
    output = tmp_path / "offset.mp4"
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=2",
            "-itsoffset", "0.5",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(output),
        ],
        check=True,
    )

    with pytest.raises(RuntimeError, match="stream start times"):
        render.validate_output(output, expected_duration=2.5)