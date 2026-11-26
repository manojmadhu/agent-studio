"""app.core.pagination.paginate against a real table (keyset on created_at DESC, id DESC)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core.errors import ApiError
from app.core.pagination import paginate
from app.db import models as m


@pytest.fixture()
def ws_runs(db):  # noqa: ANN001, ANN201
    db.add(m.Workspace(id="ws_page", name="Paging"))
    db.add(m.Pipeline(id="pl_page", workspace_id="ws_page", name="p"))
    db.flush()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # 3 rows share a timestamp to exercise the id tie-breaker.
    stamps = [base, base, base, base + timedelta(seconds=1), base + timedelta(seconds=2),
              base + timedelta(minutes=5), base + timedelta(hours=1)]
    for i, ts in enumerate(stamps):
        db.add(m.Run(id=f"run_p{i:02d}", workspace_id="ws_page", pipeline_id="pl_page", status="completed",
                     created_at=ts))
    db.commit()
    return db


def _all_pages(db, limit):  # noqa: ANN001, ANN202
    stmt = select(m.Run).where(m.Run.workspace_id == "ws_page")
    cursor, pages = None, []
    while True:
        page = paginate(db, stmt, m.Run, limit=limit, cursor=cursor, serialize=lambda r: r.id)
        pages.append(page["data"])
        cursor = page["next_cursor"]
        if not cursor:
            return pages


def test_keyset_pages_cover_all_rows_in_order(ws_runs):  # noqa: ANN001
    pages = _all_pages(ws_runs, 3)
    assert [len(p) for p in pages] == [3, 3, 1]
    flat = [i for p in pages for i in p]
    assert flat == ["run_p06", "run_p05", "run_p04", "run_p03", "run_p02", "run_p01", "run_p00"]


def test_exact_multiple_has_no_trailing_cursor(ws_runs):  # noqa: ANN001
    stmt = select(m.Run).where(m.Run.workspace_id == "ws_page")
    page = paginate(ws_runs, stmt, m.Run, limit=7, cursor=None, serialize=lambda r: r.id)
    assert len(page["data"]) == 7 and page["next_cursor"] is None


def test_workspace_scoping_and_synonym_column(ws_runs):  # noqa: ANN001
    stmt = select(m.Run).where(m.Run.workspace_id == "ws_default")
    ids = paginate(ws_runs, stmt, m.Run, limit=200, cursor=None, serialize=lambda r: r.id)["data"]
    assert ids and not any(i.startswith("run_p") for i in ids)
    # Audit events paginate over the `at` synonym of created_at.
    page = paginate(ws_runs, select(m.AuditEvent), m.AuditEvent, limit=5, cursor=None, serialize=lambda r: r.id)
    assert page["next_cursor"] is None


def test_bad_cursor_is_400(ws_runs):  # noqa: ANN001
    with pytest.raises(ApiError) as exc:
        paginate(ws_runs, select(m.Run), m.Run, limit=5, cursor="not-a-cursor", serialize=lambda r: r.id)
    assert exc.value.status == 400
