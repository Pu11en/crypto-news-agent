from __future__ import annotations

import json
import subprocess
from pathlib import Path


def extract_audio(video_path: str | Path, audio_path: str | Path) -> Path:
    destination = Path(audio_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000",
            "-c:a", "pcm_s16le", str(destination),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return destination


def run(audio_path: str | Path, work_dir: str | Path, model: str = "small.en") -> list[dict]:
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "npx", "--yes", "hyperframes@latest", "transcribe", str(audio_path),
            "-d", str(work), "--json", "--model", model,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=900,
    )
    transcript_path = work / "transcript.json"
    payload = json.loads(transcript_path.read_text(encoding="utf-8"))
    words = payload.get("words", []) if isinstance(payload, dict) else payload
    normalized = []
    for item in words:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", item.get("word", ""))).strip()
        if not text:
            continue
        normalized.append(
            {
                "text": text,
                "start": float(item.get("start", 0.0)),
                "end": float(item.get("end", item.get("start", 0.0))),
            }
        )
    if not normalized:
        raise RuntimeError("No spoken words were detected in the recording")
    transcript_path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    return normalized