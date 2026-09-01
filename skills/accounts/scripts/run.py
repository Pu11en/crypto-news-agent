#!/usr/bin/env python3
"""Bootstrap the shared Twitter News runtime, then run Accounts."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
REQUIREMENTS = SKILL_DIR / "requirements.txt"
CONSTRAINTS = SKILL_DIR / "constraints.txt"
TARGET = SKILL_DIR / "scripts" / "accounts.py"
TESTS = SKILL_DIR / "tests"


def runtime_root() -> Path:
    if os.environ.get("TWITTER_NEWS_HOME"):
        return Path(os.environ["TWITTER_NEWS_HOME"]).expanduser() / "runtime"
    for variable in ("CRYPTO_NEWS_DESK_HOME", "CRYPTO_SIGNAL_HOME"):
        if os.environ.get(variable):
            return Path(os.environ[variable]).expanduser() / "runtime"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    toolkit = base / "twitter-news"
    destination = toolkit / "runtime"
    if not destination.exists():
        for name in ("crypto-news-desk", "crypto-signal-scan"):
            source = base / name / "runtime"
            if source.exists():
                toolkit.mkdir(parents=True, exist_ok=True, mode=0o700)
                source.rename(destination)
                break
    return destination


def venv_python(root: Path) -> Path:
    return root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def dependency_digest() -> str:
    digest = hashlib.sha256()
    for path in (REQUIREMENTS, CONSTRAINTS):
        digest.update(path.read_bytes())
    return digest.hexdigest()


def runtime_healthy(python: Path) -> bool:
    if not python.exists():
        return False
    result = subprocess.run(
        [str(python), "-c", "from importlib.metadata import version; import twscrape; assert version('twscrape') == '0.20.1'"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def ensure_runtime() -> Path:
    root = runtime_root()
    python = venv_python(root)
    stamp = root / ".requirements.sha256"
    expected = dependency_digest()
    if not python.exists():
        root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        venv.EnvBuilder(with_pip=True, clear=False).create(root)
    stamped = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else ""
    if stamped != expected or not runtime_healthy(python):
        subprocess.run(
            [
                str(python), "-m", "pip", "install", "--disable-pip-version-check",
                "-r", str(REQUIREMENTS), "-c", str(CONSTRAINTS),
            ],
            check=True,
        )
        if not runtime_healthy(python):
            raise RuntimeError("isolated runtime dependency validation failed")
        tmp = stamp.with_suffix(".tmp")
        tmp.write_text(expected + "\n", encoding="utf-8")
        os.replace(tmp, stamp)
    return python


def main() -> int:
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        completed = subprocess.run([sys.executable, str(TARGET), *sys.argv[1:]])
        if sys.argv[1:] in (["-h"], ["--help"]):
            print("\nRunner-only command:\n  test  run the bundled isolated-runtime tests")
        return completed.returncode
    python = ensure_runtime()
    if sys.argv[1:] == ["test"]:
        command = [str(python), "-m", "unittest", "discover", "-s", str(TESTS), "-v"]
    else:
        command = [str(python), str(TARGET), *sys.argv[1:]]
    completed = subprocess.run(command)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
