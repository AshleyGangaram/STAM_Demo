"""Shared fixtures: every DB-touching test runs against a throwaway SQLite file."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import db


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """
    Point every get_session() call at a fresh database for the duration of a test.

    services.db resolves its path at call time, so patching get_engine is enough —
    no module reloading required.
    """
    path = str(tmp_path / "test_stam.db")
    engine = db.init_db(path)
    monkeypatch.setattr(db, "get_engine", lambda db_path=None: engine)
    yield engine
    engine.dispose()
