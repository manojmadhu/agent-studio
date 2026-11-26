"""Pipelines, drafts, versions, templates, documents."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, Unicode
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.db.base import ENUM_LEN, ID_LEN, NAME_LEN, Base, CreatedMixin, IdMixin, TimestampMixin, WorkspaceMixin
from app.db.types import JSONType, LongText, UTCDateTime


class Pipeline(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    """Pipeline head. Derived API fields (computed in code, not stored): draft_changes (from the draft),
    step_count / ai_component_count (from the live or draft graph). `kpi` is a cached PipelineKpi snapshot
    (seeded from the portal mock; BE2 may recompute it from runs/review_items and overwrite it)."""

    __tablename__ = "pipelines"
    __id_prefix__ = "pl"
    __table_args__ = (Index(None, "workspace_id", "created_at"), Index(None, "workspace_id", "status"))

    name: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    description: Mapped[str | None] = mapped_column(LongText)
    status: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="draft")  # active|paused|draft
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # accepting intake traffic
    owner_team: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    live_version: Mapped[int | None] = mapped_column(Integer)
    intake_type: Mapped[str | None] = mapped_column(String(64))
    kpi: Mapped[dict | None] = mapped_column(JSONType())
    template_id: Mapped[str | None] = mapped_column(String(ID_LEN))
    ingest_secret_ref: Mapped[str | None] = mapped_column(String(255))  # HMAC secret for /ingest (via SecretStore)
    created_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    updated_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class PipelineDraft(WorkspaceMixin, TimestampMixin, Base):
    """1:1 editable draft. `etag` changes on every save (If-Match). `saved_at` is a synonym of updated_at."""

    __tablename__ = "pipeline_drafts"

    pipeline_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("pipelines.id"), primary_key=True)
    graph: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=lambda: {"nodes": [], "edges": []})
    etag: Mapped[str] = mapped_column(String(64), nullable=False)
    changes: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)  # Draft.changes / draft_changes
    issues: Mapped[list | None] = mapped_column(JSONType())  # last validation result (ValidationIssue[])
    saved_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    base_version: Mapped[int | None] = mapped_column(Integer)  # version the draft was branched from

    saved_at = synonym("updated_at")


class PipelineVersion(IdMixin, WorkspaceMixin, CreatedMixin, Base):
    """Immutable published version. `live` is derived (pipelines.live_version == version)."""

    __tablename__ = "pipeline_versions"
    __id_prefix__ = "plv"
    __table_args__ = (UniqueConstraint("pipeline_id", "version"),)

    pipeline_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("pipelines.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    graph: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    release_note: Mapped[str | None] = mapped_column(Unicode(2000))
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    published_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))


class Template(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    """Pipeline templates (seeded per workspace). step_count/ai_component_count stored for cheap listing."""

    __tablename__ = "templates"
    __id_prefix__ = "tpl"

    name: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    description: Mapped[str | None] = mapped_column(LongText)
    intake_type: Mapped[str | None] = mapped_column(String(64))
    graph: Mapped[dict] = mapped_column(JSONType(), nullable=False)
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ai_component_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Document(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    """Uploaded/ingested document. Bytes live in the object store at `object_key`. `uploaded_at` = created_at."""

    __tablename__ = "documents"
    __id_prefix__ = "doc"
    __table_args__ = (Index(None, "workspace_id", "created_at"), Index(None, "workspace_id", "sha256"))

    filename: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    pages: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    sha256: Mapped[str | None] = mapped_column(String(64))
    object_key: Mapped[str | None] = mapped_column(Unicode(1000))
    source: Mapped[str | None] = mapped_column(String(ENUM_LEN))  # upload|ingest|email|sample
    pipeline_id: Mapped[str | None] = mapped_column(String(ID_LEN))  # intake pipeline (no FK: docs outlive pipelines)
    meta: Mapped[dict | None] = mapped_column("metadata", JSONType())  # ingest `metadata` form field
    redacted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # page images redacted by default
    retention_until: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    uploaded_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    uploaded_at = synonym("created_at")
