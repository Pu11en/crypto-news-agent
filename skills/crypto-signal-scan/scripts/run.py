#!/usr/bin/env python3
"""Create the isolated runtime on first use, then run crypto-signal-scan."""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS = SKILL_DIR / "requirements.txt"
TARGET = SKILL_DIR / "scripts" / "crypto_signal_scan.py"
TESTS = SKILL_DIR / "tests"


def runtime_root() -> Path:
    if os.environ.get("CRYPTO_SIGNAL_HOME"):
        return Path(os.environ["CRYPTO_SIGNAL_HOME"]).expanduser() / "runtime"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "crypto-signal-scan" / "runtime"


def venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_runtime() -> Path:
    root = runtime_root()
    python = venv_python(root)
    if not python.exists():
        root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        venv.EnvBuilder(with_pip=True, clear=False).create(root)
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS)],
            check=True,
        )
    return python


def main() -> int:
    python = ensure_runtime()
    if sys.argv[1:] == ["test"]:
        command = [str(python), "-m", "unittest", "discover", "-s", str(TESTS), "-v"]
    else:
        command = [str(python), str(TARGET), *sys.argv[1:]]
    completed = subprocess.run(command)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
