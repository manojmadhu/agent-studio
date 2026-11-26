"""Runs, steps, events (SSE), results, review items."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, Unicode
from sqlalchemy.orm import Mapped, mapped_column, synonym

from app.db.base import ENUM_LEN, ID_LEN, NAME_LEN, Base, CreatedMixin, IdMixin, TimestampMixin, WorkspaceMixin
from app.db.types import JSONType, UTCDateTime


class Run(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    """`pipeline_version` NULL means the draft was run (API renders "draft"). `graph` is the executed snapshot."""

    __tablename__ = "runs"
    __id_prefix__ = "run"
    __table_args__ = (
        Index(None, "workspace_id", "created_at"),
        Index(None, "workspace_id", "status"),
        Index(None, "pipeline_id", "created_at"),
    )

    pipeline_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("pipelines.id"), nullable=False)
    pipeline_version: Mapped[int | None] = mapped_column(Integer)
    document_id: Mapped[str | None] = mapped_column(String(ID_LEN), ForeignKey("documents.id"), index=True)
    status: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="queued")
    test: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deliver: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    trigger: Mapped[str | None] = mapped_column(String(ENUM_LEN))  # test|ingest|email|retry|step_test
    graph: Mapped[dict | None] = mapped_column(JSONType())
    input: Mapped[dict | None] = mapped_column(JSONType())  # intake metadata, step-test input
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    field_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[dict | None] = mapped_column(JSONType())  # Problem
    retry_of: Mapped[str | None] = mapped_column(String(ID_LEN))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))


class RunStep(IdMixin, WorkspaceMixin, CreatedMixin, Base):
    __tablename__ = "run_steps"
    __id_prefix__ = "rst"
    __table_args__ = (UniqueConstraint("run_id", "node_id", "attempt"),)

    run_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("runs.id"), nullable=False, index=True)
    node_id: Mapped[str] = mapped_column(String(64), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # execution order
    status: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="pending")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    output_port: Mapped[str | None] = mapped_column(String(64))
    output: Mapped[dict | None] = mapped_column(JSONType())
    usage: Mapped[dict | None] = mapped_column(JSONType())
    error: Mapped[dict | None] = mapped_column(JSONType())


class RunEvent(IdMixin, WorkspaceMixin, CreatedMixin, Base):
    """Append-only SSE log. `seq` (1..n per run) is the SSE `id:`; resume with Last-Event-ID → seq > n.
    `payload` is the full RunEvent JSON as sent on the wire. `at` = created_at."""

    __tablename__ = "run_events"
    __id_prefix__ = "evt"
    __table_args__ = (UniqueConstraint("run_id", "seq"),)

    run_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("runs.id"), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    node_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONType(), nullable=False)

    at = synonym("created_at")


class RunResult(WorkspaceMixin, TimestampMixin, Base):
    """RunResult document (1:1 with runs). `corrections` = reviewer edits applied on decision."""

    __tablename__ = "run_results"

    run_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("runs.id"), primary_key=True)
    classification: Mapped[dict | None] = mapped_column(JSONType())
    fields: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)  # ExtractedField[]
    checks: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)  # Check[]
    path: Mapped[dict | None] = mapped_column(JSONType())
    overlays: Mapped[dict | None] = mapped_column(JSONType())  # {blocks, tables, fields, redactions}
    corrections: Mapped[dict | None] = mapped_column(JSONType())


class ReviewItem(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    __tablename__ = "review_items"
    __id_prefix__ = "rv"
    __table_args__ = (
        Index(None, "workspace_id", "created_at"),
        Index(None, "workspace_id", "status"),
        Index(None, "workspace_id", "queue"),
    )

    run_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("runs.id"), nullable=False, index=True)
    pipeline_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("pipelines.id"), nullable=False, index=True)
    document_id: Mapped[str | None] = mapped_column(String(ID_LEN), ForeignKey("documents.id"))
    node_id: Mapped[str | None] = mapped_column(String(64))  # review.human node that paused the run
    reason: Mapped[str] = mapped_column(Unicode(1000), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(ENUM_LEN))
    queue: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    assignee: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))       # display name (contract)
    assignee_id: Mapped[str | None] = mapped_column(String(ID_LEN))      # usr_… when claimed by a member
    sla_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="open")
    decision: Mapped[str | None] = mapped_column(String(ENUM_LEN))       # approve|reject|return_to_queue
    corrections: Mapped[dict | None] = mapped_column(JSONType())
    comment: Mapped[str | None] = mapped_column(Unicode(4000))
    decided_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
