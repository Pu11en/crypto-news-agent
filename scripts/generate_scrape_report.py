"""CLI wrapper for the production scrape-report generator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reporting import generate_report  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--user-id", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    generate_report(args.db, args.run_id, args.user_id, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
