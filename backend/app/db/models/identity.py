"""Workspaces, members, API keys, settings, idempotency."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Unicode, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ENUM_LEN, ID_LEN, NAME_LEN, Base, CreatedMixin, IdMixin, TimestampMixin, WorkspaceMixin
from app.db.types import JSONType, LongText, UTCDateTime


class Workspace(IdMixin, TimestampMixin, Base):
    """Tenant root. `name`/`environment` are also exposed through GET/PATCH /settings and Principal.workspace."""

    __tablename__ = "workspaces"
    __id_prefix__ = "ws"

    name: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    environment: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="sandbox")  # sandbox|production
    region: Mapped[str | None] = mapped_column(String(64))
    disabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class WorkspaceSettings(TimestampMixin, Base):
    """1:1 with workspaces. WorkspaceSettings schema = workspaces.{name,environment} + this row."""

    __tablename__ = "workspace_settings"

    workspace_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("workspaces.id"), primary_key=True)
    default_region: Mapped[str | None] = mapped_column(String(64))
    retention_days: Mapped[int | None] = mapped_column(Integer)
    allowed_regions: Mapped[list | None] = mapped_column(JSONType())   # list[str]
    sso: Mapped[dict | None] = mapped_column(JSONType())               # {provider, issuer, ...}
    extra: Mapped[dict | None] = mapped_column(JSONType())             # forward-compatible settings
    updated_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))


class Member(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    __tablename__ = "members"
    __id_prefix__ = "usr"
    __table_args__ = (UniqueConstraint("workspace_id", "email"),)

    name: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    email: Mapped[str] = mapped_column(Unicode(320), nullable=False)
    role: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="viewer")  # admin|builder|reviewer|viewer
    status: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="active")  # active|invited|disabled
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # OIDC subject (iss|sub). Nullable → deliberately NOT unique (rule 6); uniqueness enforced in code.
    external_subject: Mapped[str | None] = mapped_column(String(255), index=True)
    invited_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))


class ApiKey(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    """Only sha256(secret) is stored. Rotation keeps the previous hash valid until previous_expires_at."""

    __tablename__ = "api_keys"
    __id_prefix__ = "key"

    name: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    prefix: Mapped[str] = mapped_column(String(64), nullable=False, index=True)    # e.g. dik_live_7Hf2 (shown)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # hex sha256 of full secret
    scopes: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    allowed_ips: Mapped[list | None] = mapped_column(JSONType())  # IP/CIDR allow-list (ApiKeyCreate.allowed_ips)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_used_ip: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    # Grace secret during rotation (nullable → indexed, not unique).
    previous_key_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    previous_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class IdempotencyKey(IdMixin, CreatedMixin, Base):
    """Replay store for Idempotency-Key on POST (BE1 middleware). Internal, keyed by credential scope hash."""

    __tablename__ = "idempotency_keys"
    __id_prefix__ = "idk"
    __table_args__ = (UniqueConstraint("scope_hash", "key"),)

    workspace_id: Mapped[str | None] = mapped_column(String(ID_LEN), index=True)  # informational, no FK
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256(workspace + credential)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    path: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="in_progress")  # in_progress|completed
    status_code: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[str | None] = mapped_column(LongText)
    response_headers: Mapped[dict | None] = mapped_column(JSONType())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False, index=True)

    def as_dict(self) -> dict[str, Any]:  # convenience for debugging
        return {"key": self.key, "method": self.method, "path": self.path, "status_code": self.status_code}
