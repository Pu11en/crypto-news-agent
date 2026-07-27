from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from video import compose, ingest  # noqa: E402

WORK = Path("/tmp/jordancrypto-video-smoke")
if WORK.exists():
    shutil.rmtree(WORK)
(WORK / "public").mkdir(parents=True)
source = WORK / "source.mp4"
subprocess.run(
    [
        "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x202028:s=640x360:r=30:d=4",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
    ],
    check=True,
    capture_output=True,
)
ingest.stage_for_render(source, WORK / "public" / "input-video.mp4")
storyboard = {
    "schemaVersion": 1,
    "composition": {
        "width": 1080, "height": 1920, "fps": 30, "durationSeconds": 4.0,
        "graphicsZone": {"x": 0, "y": 0, "width": 1080, "height": 1312},
        "cameraZone": {"x": 0, "y": 1312, "width": 1080, "height": 608},
        "captionsEnabled": True,
    },
    "scenes": [
        {
            "id": "scene-01", "startSec": 0.0, "endSec": 2.0, "fullyRevealedAtSec": 1.2,
            "kicker": "FIRST DAY", "title": "$42M", "body": "Traditional accounts can now access SOL.",
            "visualForm": "hero-stat", "dataPoints": [{"label": "INFLOW", "value": "$42M"}],
            "primaryElements": ["Brokerage", "ETF", "SOL"], "evidenceTweetIds": ["tweet-42"],
        },
        {
            "id": "scene-02", "startSec": 2.0, "endSec": 4.0, "fullyRevealedAtSec": 3.2,
            "kicker": "THE PATH", "title": "Brokerage to SOL", "body": "The fund creates a simple access route.",
            "visualForm": "process-flow", "dataPoints": [],
            "primaryElements": ["Brokerage", "ETF", "SOL"], "evidenceTweetIds": ["tweet-42"],
        },
    ],
}
words = [
    {"text": "The", "start": 0.0, "end": 0.3}, {"text": "ETF", "start": 0.3, "end": 0.7},
    {"text": "opened", "start": 0.7, "end": 1.1}, {"text": "access.", "start": 1.1, "end": 1.6},
]
compose.write_composition(WORK / "public", storyboard, words)
(WORK / "storyboard.json").write_text(json.dumps(storyboard, indent=2), encoding="utf-8")
print(WORK)
