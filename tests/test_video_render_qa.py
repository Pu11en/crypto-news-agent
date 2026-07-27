from __future__ import annotations

import subprocess

import pytest

from video import render


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