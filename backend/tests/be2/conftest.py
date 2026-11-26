"""BE2 fixtures: never let a background run outlive its test (the next test's DB reset would race it)."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _drain_run_engine():
    yield
    from app.services import run_engine

    q = run_engine.get_queue()
    if hasattr(q, "wait_idle"):
        q.wait_idle(30.0)
