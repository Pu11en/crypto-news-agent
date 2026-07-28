from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from video import preview, render  # noqa: E402

WORK = Path("/tmp/jordancrypto-video-smoke")

subprocess.run(
    [sys.executable, str(ROOT / "tests" / "build_smoke_fixture.py")],
    check=True,
)
storyboard = json.loads((WORK / "storyboard.json").read_text(encoding="utf-8"))
previews = preview.capture(WORK / "public", storyboard, WORK / "review")
output = render.render(WORK / "public", WORK / "smoke.mp4")
qa = render.validate_output(
    output,
    expected_duration=4.0,
    overflow_report_path=WORK / "review" / "overflow-report.json",
)
print(
    json.dumps(
        {
            "ok": True,
            "previewCount": len(previews),
            "previewBytes": [path.stat().st_size for path in previews],
            "outputPath": str(output),
            "outputBytes": output.stat().st_size,
            "qa": qa,
        },
        sort_keys=True,
    )
)
