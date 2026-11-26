"""Cursor (keyset) pagination: ?limit=1..200&cursor= → {data: [...], next_cursor}.

Portable across PostgreSQL and MSSQL: ordering is (created_at DESC, id DESC) and the cursor encodes the
last row's (created_at, id). No OFFSET scans, no DB-specific JSON operators.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any, Callable, Sequence, TypeVar

from sqlalchemy import Select, and_, or_
from sqlalchemy.orm import Session

from app.core.errors import bad_request
from app.core.time import ensure_aware

T = TypeVar("T")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, int(limit)))


def encode_cursor(created_at: datetime, rid: str) -> str:
    raw = json.dumps({"t": ensure_aware(created_at).isoformat(), "id": rid}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        pad = "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(cursor + pad).decode())
        return ensure_aware(datetime.fromisoformat(data["t"])), str(data["id"])
    except Exception as exc:  # noqa: BLE001
        raise bad_request("invalid_cursor", "The cursor is malformed or expired") from exc


def paginate(
    db: Session,
    stmt: Select,
    model: Any,
    *,
    limit: int | None,
    cursor: str | None,
    serialize: Callable[[Any], dict],
    created_col: Any = None,
    id_col: Any = None,
) -> dict:
    """Run `stmt` (already filtered by workspace etc.) with keyset pagination over (created_at, id) DESC.

    `model` must expose `created_at` and `id` columns unless created_col/id_col are given.
    Returns the OpenAPI *Page shape: {"data": [...], "next_cursor": str | None}.
    """
    created = created_col if created_col is not None else model.created_at
    ident = id_col if id_col is not None else model.id
    n = clamp_limit(limit)
    if cursor:
        t, rid = decode_cursor(cursor)
        stmt = stmt.where(or_(created < t, and_(created == t, ident < rid)))
    rows: Sequence[Any] = db.scalars(stmt.order_by(created.desc(), ident.desc()).limit(n + 1)).all()
    more = len(rows) > n
    rows = rows[:n]
    next_cursor = encode_cursor(getattr(rows[-1], created.key), getattr(rows[-1], ident.key)) if more and rows else None
    return {"data": [serialize(r) for r in rows], "next_cursor": next_cursor}


def paginate_list(items: list[T], *, limit: int | None, cursor: str | None, key: Callable[[T], str],
                  serialize: Callable[[T], dict]) -> dict:
    """In-memory pagination for small, non-table collections (e.g. catalog, tools). Cursor = last key."""
    n = clamp_limit(limit)
    start = 0
    if cursor:
        keys = [key(i) for i in items]
        if cursor not in keys:  # unknown or stale (row deleted) cursor: never silently restart at page 1
            raise bad_request("invalid_cursor", "The cursor is malformed or expired")
        start = keys.index(cursor) + 1
    chunk = items[start:start + n]
    more = start + n < len(items)
    return {"data": [serialize(i) for i in chunk], "next_cursor": key(chunk[-1]) if more and chunk else None}
