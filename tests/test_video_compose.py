from __future__ import annotations

from video import compose, preview


def _storyboard(captions: bool = False):
    return {
        "schemaVersion": 1,
        "composition": {
            "width": 1080,
            "height": 1920,
            "fps": 30,
            "durationSeconds": 4.0,
            "graphicsZone": {"x": 0, "y": 0, "width": 1080, "height": 1312},
            "cameraZone": {"x": 0, "y": 1312, "width": 1080, "height": 608},
            "captionsEnabled": captions,
        },
        "scenes": [
            {
                "id": "scene-01",
                "startSec": 0.0,
                "endSec": 2.0,
                "fullyRevealedAtSec": 1.1,
                "kicker": "FIRST DAY",
                "title": "$42M",
                "body": "Traditional accounts can now access SOL.",
                "visualForm": "hero-stat",
                "dataPoints": [{"label": "Inflow", "value": "$42 million"}],
                "primaryElements": ["Brokerage", "ETF", "SOL"],
                "evidenceTweetIds": ["tweet-42"],
            },
            {
                "id": "scene-02",
                "startSec": 2.0,
                "endSec": 4.0,
                "fullyRevealedAtSec": 3.1,
                "kicker": "THE PATH",
                "title": "Brokerage to SOL",
                "body": "The fund creates the access route.",
                "visualForm": "process-flow",
                "dataPoints": [],
                "primaryElements": ["Brokerage", "ETF", "SOL"],
                "evidenceTweetIds": ["tweet-42"],
            },
        ],
    }


def test_composition_locks_camera_audio_and_scene_clips(tmp_path):
    public = tmp_path / "public"
    transcript = [
        {"text": "The", "start": 0.0, "end": 0.3},
        {"text": "ETF", "start": 0.3, "end": 0.7},
        {"text": "arrived.", "start": 0.7, "end": 1.2},
    ]

    index = compose.write_composition(public, _storyboard(False), transcript)
    html = index.read_text(encoding="utf-8")

    assert 'data-width="1080"' in html
    assert 'data-height="1920"' in html
    assert 'class="video-zone"' in html
    assert "top:1312px" in html
    assert "width:1080px;height:608px" in html
    assert '<audio id="source-audio" src="input-video.mp4"' in html
    assert html.count('class="card-host clip"') == 2
    assert 'data-card-id="scene-01"' in html
    assert 'var s=".card-host[data-card-id=\\\"scene-01\\\"]"' in html
    assert 'data-card-id=\\\\\\\"scene-01' not in html
    assert "http://" not in html and "https://" not in html
    assert 'class="caption clip"' not in html
    assert (public / "vendor" / "gsap.min.js").exists()
    assert (public / "fonts" / "Inter-700-latin.woff2").exists()


def test_captions_are_optional_and_timed(tmp_path):
    public = tmp_path / "public"
    transcript = [
        {"text": "The", "start": 0.0, "end": 0.3},
        {"text": "ETF", "start": 0.3, "end": 0.7},
        {"text": "arrived.", "start": 0.7, "end": 1.2},
    ]

    index = compose.write_composition(public, _storyboard(True), transcript)
    html = index.read_text(encoding="utf-8")

    assert 'class="caption clip"' in html
    assert "The ETF arrived." in html


def test_browser_dom_overflow_measurement_detects_real_overflow(tmp_path):
    public = tmp_path / "public"
    index = compose.write_composition(public, _storyboard(False), [])
    (public / "input-video.mp4").write_bytes(b"")

    assert preview.measure_text_overflow(public)["count"] == 0

    document = index.read_text(encoding="utf-8").replace(
        "</body>",
        '<div class="title" style="width:10px;white-space:nowrap;overflow:hidden">THIS TEXT CANNOT FIT</div></body>',
    )
    index.write_text(document, encoding="utf-8")

    report = preview.measure_text_overflow(public)
    assert report["count"] >= 1
    assert any(issue.get("className") == "title" for issue in report["issues"])
