"""Declarative base + common column mixins.

Naming convention keeps constraint/index names short and deterministic on both dialects (PostgreSQL truncates
at 63 chars, SQL Server allows 128) so migrations generated against PostgreSQL apply unchanged on SQL Server.
`tests/db/test_mssql_ddl.py` asserts every generated name is <= 63 chars.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

from app.core.ids import new_id
from app.core.time import utcnow
from app.db.types import UtcNow, UTCDateTime

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}

ID_LEN = 40       # every *_id column (portability rule 4)
NAME_LEN = 200    # human names / labels
URL_LEN = 2048    # URLs (not indexed)
ENUM_LEN = 32     # enum-like strings (rule 8)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class IdMixin:
    """`id String(40)` primary key. Subclasses set `__id_prefix__` (e.g. "con") to get a python-side default
    `new_id(prefix)`; routes may still pass an explicit id (seeded/portal ids are kept)."""

    __id_prefix__: str = ""

    @declared_attr
    def id(cls) -> Mapped[str]:  # noqa: N805
        prefix = cls.__id_prefix__
        default = (lambda: new_id(prefix)) if prefix else None
        return mapped_column(String(ID_LEN), primary_key=True, default=default)


class WorkspaceMixin:
    """Tenant scope (portability rule 10): NOT NULL + indexed + FK to workspaces (no cascade, rule 5)."""

    @declared_attr
    def workspace_id(cls) -> Mapped[str]:  # noqa: N805
        return mapped_column(String(ID_LEN), ForeignKey("workspaces.id"), nullable=False, index=True)


class CreatedMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow,
                                                 server_default=UtcNow())


class TimestampMixin(CreatedMixin):
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, default=utcnow, onupdate=utcnow,
                                                 server_default=UtcNow())


__all__ = ["Base", "IdMixin", "WorkspaceMixin", "CreatedMixin", "TimestampMixin", "NAMING_CONVENTION",
           "ID_LEN", "NAME_LEN", "URL_LEN", "ENUM_LEN"]
