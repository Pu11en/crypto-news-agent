#!/usr/bin/env python3
"""Bootstrap the shared Twitter News runtime, then run Accounts."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import uuid
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
    try:
        result = subprocess.run(
            [str(python), "-I", "-c", "from importlib.metadata import version; import twscrape; assert version('twscrape') == '0.20.1'"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def build_runtime(root: Path, expected: str) -> None:
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = root.parent / f".{root.name}-build-{uuid.uuid4().hex}"
    backup = root.parent / f".{root.name}-backup-{uuid.uuid4().hex}"
    moved_existing = False
    try:
        venv.EnvBuilder(with_pip=True, clear=True).create(staging)
        python = venv_python(staging)
        subprocess.run(
            [
                str(python), "-I", "-m", "pip", "install", "--disable-pip-version-check",
                "-r", str(REQUIREMENTS), "-c", str(CONSTRAINTS),
            ],
            check=True,
            timeout=600,
        )
        if not runtime_healthy(python):
            raise RuntimeError("isolated runtime dependency validation failed")
        (staging / ".requirements.sha256").write_text(expected + "\n", encoding="utf-8")
        if root.exists():
            root.rename(backup)
            moved_existing = True
        staging.rename(root)
        if moved_existing:
            shutil.rmtree(backup, ignore_errors=True)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        if moved_existing and not root.exists() and backup.exists():
            backup.rename(root)
        raise RuntimeError(
            "could not build the isolated Twitter News runtime; check network/disk access and rerun the command"
        ) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if root.exists():
            shutil.rmtree(backup, ignore_errors=True)


def ensure_runtime() -> Path:
    root = runtime_root()
    python = venv_python(root)
    stamp = root / ".requirements.sha256"
    expected = dependency_digest()
    try:
        stamped = stamp.read_text(encoding="utf-8").strip() if stamp.exists() else ""
    except OSError:
        stamped = ""
    if stamped != expected or not runtime_healthy(python):
        build_runtime(root, expected)
        python = venv_python(root)
    return python


def main() -> int:
    if any(arg in {"-h", "--help"} for arg in sys.argv[1:]):
        completed = subprocess.run([sys.executable, str(TARGET), *sys.argv[1:]])
        if sys.argv[1:] in (["-h"], ["--help"]):
            print("\nRunner-only command:\n  test  run the bundled isolated-runtime tests")
        return completed.returncode
    try:
        python = ensure_runtime()
        if sys.argv[1:] == ["test"]:
            command = [str(python), "-I", "-m", "unittest", "discover", "-s", str(TESTS), "-v"]
        else:
            command = [str(python), "-I", str(TARGET), *sys.argv[1:]]
        completed = subprocess.run(command)
        return completed.returncode
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
