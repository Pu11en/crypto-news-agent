from __future__ import annotations

from config import load
from video.hyperframes_cli import HYPERFRAMES_VERSION, command


def test_hyperframes_uses_pinned_npx_fallback(monkeypatch):
    monkeypatch.delenv("HYPERFRAMES_BIN", raising=False)

    assert command("lint", "public") == [
        "npx",
        "--yes",
        f"hyperframes@{HYPERFRAMES_VERSION}",
        "lint",
        "public",
    ]


def test_hyperframes_uses_baked_production_binary(monkeypatch):
    monkeypatch.setenv("HYPERFRAMES_BIN", "/usr/local/bin/hyperframes")

    assert command("render", "public") == [
        "/usr/local/bin/hyperframes",
        "render",
        "public",
    ]


def test_production_video_work_dir_defaults_to_persistent_volume(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "test")
    monkeypatch.setenv("XQUIK_API_KEY", "test")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test")
    monkeypatch.delenv("VIDEO_WORK_DIR", raising=False)

    assert load().video_work_dir == "/data/video-jobs"
