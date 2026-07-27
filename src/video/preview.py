from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def capture(public_dir: str | Path, storyboard: dict, review_dir: str | Path) -> list[Path]:
    public = Path(public_dir)
    review = Path(review_dir)
    review.mkdir(parents=True, exist_ok=True)
    outputs = []
    for scene in storyboard.get("scenes", []):
        hold = float(scene.get("fullyRevealedAtSec", scene["startSec"] + 1.0))
        subprocess.run(
            ["npx", "--yes", "hyperframes@latest", "snapshot", "public", "--at", f"{hold:.3f}"],
            cwd=public.parent,
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
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