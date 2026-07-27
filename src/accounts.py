"""Load the curated Twitter account list.

Format of accounts.txt (one per line):
    username,tier,tags
where tags is a pipe-separated list. Lines starting with '#' are comments.
Blank lines are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "accounts.txt"


@dataclass(frozen=True)
class Account:
    username: str
    tier: str
    tags: tuple[str, ...]


def load_accounts(path: Path = DEFAULT_PATH) -> list[Account]:
    accounts: list[Account] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        username = parts[0].lstrip("@")
        tier = parts[1] or "medium"
        tags = tuple(t for t in (parts[2].split("|") if len(parts) > 2 else []) if t)
        accounts.append(Account(username=username, tier=tier, tags=tags))
    return accounts
