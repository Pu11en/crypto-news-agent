from __future__ import annotations

import os

HYPERFRAMES_VERSION = "0.7.76"


def command(*args: str) -> list[str]:
    """Return the pinned HyperFrames invocation for this environment."""
    installed = os.environ.get("HYPERFRAMES_BIN", "").strip()
    if installed:
        return [installed, *args]
    return ["npx", "--yes", f"hyperframes@{HYPERFRAMES_VERSION}", *args]
