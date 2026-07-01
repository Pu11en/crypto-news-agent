"""Standalone seed script: populate the accounts table from accounts.txt.

Usage:
    python -m plugins.crypto_intel.seed_accounts
    # or from the package directory:
    python -m seed_accounts
"""

from __future__ import annotations

import sys

from .db import seed_accounts


def main() -> int:
    count = seed_accounts()
    print(f"Seeded {count} new account(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
