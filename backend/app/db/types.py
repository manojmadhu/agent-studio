"""Portable column types. The ONLY module allowed to import dialect-specific SQLAlchemy types.

JSONType
    PostgreSQL → JSONB · SQL Server → NVARCHAR(MAX) holding serialised JSON · others (SQLite) → JSON.
    Python ``None`` is stored as SQL NULL on every dialect (never the JSON literal ``null``), dicts, lists,
    strings, numbers and booleans round-trip unchanged.

    Mutation tracking: JSON columns are *not* wrapped in Mutable* types, so in-place changes such as
    ``pipeline.graph["nodes"].append(n)`` are NOT detected by the ORM. Always reassign the whole value
    (``draft.graph = new_graph``) — routes receive whole documents from the API anyway — or call
    ``sqlalchemy.orm.attributes.flag_modified(obj, "graph")`` after an in-place edit. Never filter inside JSON
    in SQL (portability rule 2); load and filter in Python.

UTCDateTime
    PostgreSQL → TIMESTAMP WITH TIME ZONE · SQL Server → DATETIMEOFFSET · others → DATETIME.
    Writes: aware datetimes are converted to UTC; naive datetimes are *assumed* to already be UTC.
    Reads: always an aware datetime in UTC (drivers that return naive values are treated as UTC).

LongText
    Unbounded Unicode text: TEXT on PostgreSQL, NVARCHAR(MAX) on SQL Server.

UtcNow
    SQL expression for "current UTC time" usable as a ``server_default``: ``now()`` on PostgreSQL,
    ``SYSDATETIMEOFFSET()`` on SQL Server, ``CURRENT_TIMESTAMP`` elsewhere.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, DateTime, UnicodeText
from sqlalchemy.dialects import mssql, postgresql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.functions import FunctionElement
from sqlalchemy.types import TypeDecorator


def _json_default(o: Any) -> Any:
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, (set, frozenset, tuple)):
        return list(o)
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serialisable")


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)


class JSONType(TypeDecorator):
    """Whole-document JSON column (see module docstring)."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.JSONB(none_as_null=True))
        if dialect.name == "mssql":
            return dialect.type_descriptor(mssql.NVARCHAR(None))  # NVARCHAR(max)
        return dialect.type_descriptor(JSON(none_as_null=True))

    def process_bind_param(self, value: Any, dialect) -> Any:  # noqa: ANN001
        if value is None:
            return None
        if dialect.name == "mssql":
            return dumps(value)
        # Normalise non-JSON-native values (datetimes, tuples) the same way on every dialect.
        return json.loads(dumps(value))

    def process_result_value(self, value: Any, dialect) -> Any:  # noqa: ANN001
        if value is None:
            return None
        if dialect.name == "mssql" and isinstance(value, (str, bytes, bytearray)):
            return json.loads(value)  # NVARCHAR(MAX) always comes back as text
        return value  # JSONB / JSON impls already deserialised

    def coerce_compared_value(self, op, value):  # noqa: ANN001, ANN201
        return self.impl_instance.coerce_compared_value(op, value)


class UTCDateTime(TypeDecorator):
    """Timezone-aware UTC timestamp (see module docstring)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        if dialect.name == "mssql":
            return dialect.type_descriptor(mssql.DATETIMEOFFSET())
        if dialect.name == "postgresql":
            return dialect.type_descriptor(postgresql.TIMESTAMP(timezone=True))
        return dialect.type_descriptor(DateTime(timezone=True))

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"UTCDateTime expects datetime, got {type(value).__name__}")
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)  # SQLite stores naive text; we read it back as UTC
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class LongText(TypeDecorator):
    """Unbounded Unicode text: TEXT on PostgreSQL, NVARCHAR(MAX) on SQL Server (never the deprecated NTEXT,
    which SQLAlchemy would emit for UnicodeText when the server version is unknown, e.g. offline migrations)."""

    impl = UnicodeText
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        # Return the types directly: dialect.type_descriptor() would adapt UnicodeText to the driver's
        # String impl, which the DDL compiler renders as VARCHAR on psycopg.
        if dialect.name == "mssql":
            return mssql.NVARCHAR(None)
        return UnicodeText()


class UtcNow(FunctionElement):
    """Portable server-side "now in UTC" for server_default=UtcNow()."""

    type = UTCDateTime()
    inherit_cache = True
    name = "utcnow"


@compiles(UtcNow)
def _utcnow_default(element, compiler, **kw):  # noqa: ANN001, ANN201
    return "CURRENT_TIMESTAMP"


@compiles(UtcNow, "postgresql")
def _utcnow_pg(element, compiler, **kw):  # noqa: ANN001, ANN201
    return "now()"


@compiles(UtcNow, "mssql")
def _utcnow_mssql(element, compiler, **kw):  # noqa: ANN001, ANN201
    return "SYSDATETIMEOFFSET()"


__all__ = ["JSONType", "UTCDateTime", "LongText", "UtcNow", "dumps"]
