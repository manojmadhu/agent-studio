"""Webhooks, deliveries, audit trail."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Unicode
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.db.base import ENUM_LEN, ID_LEN, NAME_LEN, URL_LEN, Base, CreatedMixin, IdMixin, TimestampMixin, WorkspaceMixin
from app.db.types import JSONType, LongText, UTCDateTime


class Webhook(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    """WebhookSubscription. Signing secret lives in the SecretStore (`secret_ref`)."""

    __tablename__ = "webhooks"
    __id_prefix__ = "whk"

    url: Mapped[str] = mapped_column(String(URL_LEN), nullable=False)
    events: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pipeline_ids: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Unicode(1000))
    secret_ref: Mapped[str | None] = mapped_column(String(255))
    secret_hint: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))


class WebhookDelivery(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    __tablename__ = "webhook_deliveries"
    __id_prefix__ = "whd"
    __table_args__ = (Index(None, "status", "next_attempt_at"), Index(None, "webhook_id", "created_at"))

    webhook_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("webhooks.id"), nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    event_id: Mapped[str] = mapped_column(String(ID_LEN), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    status: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="pending")  # pending|succeeded|failed
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_excerpt: Mapped[str | None] = mapped_column(LongText)
    last_error: Mapped[str | None] = mapped_column(Unicode(2000))
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AuditEvent(IdMixin, WorkspaceMixin, CreatedMixin, Base):
    """Append-only (never UPDATE/DELETE from app code). `at` = created_at. actor = {kind, id, name}."""

    __tablename__ = "audit_events"
    __id_prefix__ = "aud"
    __table_args__ = (Index(None, "workspace_id", "created_at"), Index(None, "resource_type", "resource_id"))

    actor: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(ID_LEN), index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str | None] = mapped_column(Unicode(1000))
    ip: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSONType())

    at = synonym("created_at")
