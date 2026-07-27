from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import db  # noqa: E402


@pytest.fixture
def isolated_db(tmp_path):
    if db._engine is not None:
        db._engine.dispose()
    db._engine = None
    db._SessionLocal = None
    db.init_engine(str(tmp_path / "test.db"))
    yield tmp_path
    if db._engine is not None:
        db._engine.dispose()
    db._engine = None
    db._SessionLocal = None
