from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from video.hyperframes_cli import command as hyperframes


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return


def _chrome_binary() -> str:
    system = shutil.which("chromium") or shutil.which("google-chrome")
    if system:
        return system
    cached = sorted(
        Path.home().glob(
            ".cache/puppeteer/chrome-headless-shell/*/chrome-headless-shell-*/chrome-headless-shell"
        ),
        reverse=True,
    )
    if cached:
        return str(cached[0])
    raise RuntimeError("No headless Chrome binary is available for text-overflow QA")


def measure_text_overflow(public_dir: str | Path) -> dict:
    public = Path(public_dir).resolve()
    handler = partial(_QuietHandler, directory=str(public))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        result = subprocess.run(
            [
                _chrome_binary(),
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--virtual-time-budget=3000",
                "--dump-dom",
                f"http://127.0.0.1:{port}/index.html",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if result.returncode != 0:
        raise RuntimeError(f"Text-overflow browser check failed:\n{result.stderr[-2000:]}")
    match = re.search(r'data-text-overflow-report="([^"]*)"', result.stdout)
    if not match:
        raise RuntimeError("Text-overflow browser report was missing from the rendered DOM")
    payload = urllib.parse.unquote(html.unescape(match.group(1)))
    report = json.loads(payload)
    if not isinstance(report, dict) or "count" not in report:
        raise RuntimeError("Text-overflow browser report was malformed")
    return report


def capture(public_dir: str | Path, storyboard: dict, review_dir: str | Path) -> list[Path]:
    public = Path(public_dir)
    review = Path(review_dir)
    review.mkdir(parents=True, exist_ok=True)
    overflow_report = measure_text_overflow(public)
    (review / "overflow-report.json").write_text(
        json.dumps(overflow_report, indent=2), encoding="utf-8"
    )
    if int(overflow_report.get("count", 0)) > 0:
        raise RuntimeError(
            "Rendered text overflow detected: "
            + json.dumps(overflow_report.get("issues", [])[:8], ensure_ascii=False)
        )

    outputs = []
    for scene in storyboard.get("scenes", []):
        hold = float(scene.get("fullyRevealedAtSec", scene["startSec"] + 1.0))
        output = ""
        for attempt in range(3):
            result = subprocess.run(
                hyperframes("snapshot", "public", "--at", f"{hold:.3f}"),
                cwd=public.parent,
                check=False,
                capture_output=True,
                text=True,
                timeout=300,
            )
            output = result.stdout + result.stderr
            if result.returncode == 0:
                break
            time.sleep(2 * (attempt + 1))
        else:
            raise RuntimeError(
                f"Snapshot failed for {scene['id']} after 3 attempts:\n{output[-4000:]}"
            )
        frames = sorted((public / "snapshots").glob("frame-00-*.png"))
        if not frames:
            raise RuntimeError(f"No preview image was created for {scene['id']}")
        destination = review / f"{scene['id']}.png"
        shutil.copy2(frames[0], destination)
        if destination.stat().st_size < 10_000:
            raise RuntimeError(f"Preview image is unexpectedly small for {scene['id']}")
        outputs.append(destination)
    return outputs
