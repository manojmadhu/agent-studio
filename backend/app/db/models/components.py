"""Connections & secrets, providers, workers, MCP servers, knowledge, skills, policies."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, ForeignKey, Index, Integer, String, Unicode
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import (
    ENUM_LEN, ID_LEN, NAME_LEN, URL_LEN, Base, CreatedMixin, IdMixin, TimestampMixin, WorkspaceMixin,
)
from app.db.types import JSONType, LongText, UTCDateTime


class Secret(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    """Local SecretStore backing table. `ciphertext` is produced by BE1's secret_store (Fernet); never plaintext.
    Other stores (Key Vault, AWS SM, Vault) do not use this table; rows reference them via `secret_ref`."""

    __tablename__ = "secrets"
    __id_prefix__ = "sec"

    purpose: Mapped[str | None] = mapped_column(String(64))  # connection|webhook|ingest|oauth_token|...
    ciphertext: Mapped[str] = mapped_column(LongText, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    key_id: Mapped[str | None] = mapped_column(String(64))  # master-key id, for key rotation


class Connection(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    __tablename__ = "connections"
    __id_prefix__ = "con"
    __table_args__ = (Index(None, "workspace_id", "created_at"), Index(None, "workspace_id", "service"))

    name: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    service: Mapped[str] = mapped_column(String(64), nullable=False)      # ServiceId
    auth_type: Mapped[str] = mapped_column(String(64), nullable=False)    # AuthType
    settings: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)  # non-secret settings only
    secret_ref: Mapped[str | None] = mapped_column(String(255))  # e.g. "local:sec_…" / "kv://…"
    secret_hint: Mapped[str | None] = mapped_column(String(32))  # "••••9fQe"
    status: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="unverified", index=True)
    status_message: Mapped[str | None] = mapped_column(Unicode(1000))
    last_tested_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    # usage_count is derived (scan of pipelines/providers/workers/...) — not stored.


class OAuthState(IdMixin, WorkspaceMixin, CreatedMixin, Base):
    """Pending OAuth2 authorization-code flows (/connections/{id}/oauth/authorize → /oauth/callback)."""

    __tablename__ = "oauth_states"
    __id_prefix__ = "oas"

    state_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # sha256(state)
    connection_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("connections.id"), nullable=False, index=True)
    code_verifier: Mapped[str | None] = mapped_column(String(255))  # PKCE, short-lived
    redirect_uri: Mapped[str | None] = mapped_column(String(URL_LEN))
    return_to: Mapped[str | None] = mapped_column(String(URL_LEN))
    created_by: Mapped[str | None] = mapped_column(String(ID_LEN))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Provider(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    __tablename__ = "providers"
    __id_prefix__ = "prv"
    __table_args__ = (Index(None, "workspace_id", "created_at"),)

    kind: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False)
    name: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    base_url: Mapped[str | None] = mapped_column(String(URL_LEN))
    connection_id: Mapped[str | None] = mapped_column(String(ID_LEN), ForeignKey("connections.id"), index=True)
    data_region: Mapped[str | None] = mapped_column(String(64))
    default_model: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    limits: Mapped[dict | None] = mapped_column(JSONType())
    # models → provider_models; usage_count derived.


class ProviderModel(WorkspaceMixin, Base):
    """Provider.models[] (ModelInfo). Composite PK; `position` keeps the API order. Replace the set as a whole."""

    __tablename__ = "provider_models"

    provider_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("providers.id"), primary_key=True)
    model_id: Mapped[str] = mapped_column(Unicode(NAME_LEN), primary_key=True)  # ModelInfo.id
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    display_name: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    context_window: Mapped[int | None] = mapped_column(Integer)
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    embedding: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Worker(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    __tablename__ = "workers"
    __id_prefix__ = "wrk"
    __table_args__ = (Index(None, "workspace_id", "created_at"),)

    name: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    kind: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False)
    base_url: Mapped[str] = mapped_column(String(URL_LEN), nullable=False)
    adapter: Mapped[str] = mapped_column(String(64), nullable=False)
    invoke_path: Mapped[str | None] = mapped_column(Unicode(500))
    health_path: Mapped[str | None] = mapped_column(Unicode(500))
    connection_id: Mapped[str | None] = mapped_column(String(ID_LEN), ForeignKey("connections.id"), index=True)
    accepts: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    capabilities: Mapped[list] = mapped_column(JSONType(), nullable=False, default=list)
    timeout_s: Mapped[int | None] = mapped_column(Integer)
    max_concurrency: Mapped[int | None] = mapped_column(Integer)
    data_region: Mapped[str | None] = mapped_column(String(64))
    health: Mapped[dict | None] = mapped_column(JSONType())  # WorkerHealth {status, latency_ms, checked_at, message}


class McpServer(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    __tablename__ = "mcp_servers"
    __id_prefix__ = "mcp"
    __table_args__ = (Index(None, "workspace_id", "created_at"),)

    name: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    transport: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False)  # streamable_http|sse|stdio...
    url: Mapped[str] = mapped_column(String(URL_LEN), nullable=False)
    connection_id: Mapped[str | None] = mapped_column(String(ID_LEN), ForeignKey("connections.id"), index=True)
    headers: Mapped[dict | None] = mapped_column(JSONType())  # non-secret headers only
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="unknown")
    status_message: Mapped[str | None] = mapped_column(Unicode(1000))
    last_discovered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # tools → mcp_tools; usage_count derived.


class McpTool(WorkspaceMixin, Base):
    __tablename__ = "mcp_tools"

    server_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("mcp_servers.id"), primary_key=True)
    name: Mapped[str] = mapped_column(Unicode(NAME_LEN), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str | None] = mapped_column(LongText)
    input_schema: Mapped[dict | None] = mapped_column(JSONType())
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class KnowledgeCollection(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_collections"
    __id_prefix__ = "kc"
    __table_args__ = (Index(None, "workspace_id", "created_at"),)

    name: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    # BE1: nullable so DELETE /connections/{id}?force=true can unlink collections (status → failed). Migration 0002.
    connection_id: Mapped[str | None] = mapped_column(String(ID_LEN), ForeignKey("connections.id"), nullable=True,
                                                      index=True)
    embedding_provider_id: Mapped[str | None] = mapped_column(String(ID_LEN), ForeignKey("providers.id"))
    embedding_model: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    chunk_size: Mapped[int | None] = mapped_column(Integer)
    document_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="ready")  # ready|indexing|failed


class KnowledgeDocument(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    """A file added to a collection (POST /knowledge-collections/{id}/documents)."""

    __tablename__ = "knowledge_documents"
    __id_prefix__ = "kd"

    collection_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("knowledge_collections.id"), nullable=False,
                                               index=True)
    document_id: Mapped[str | None] = mapped_column(String(ID_LEN), ForeignKey("documents.id"), index=True)
    filename: Mapped[str] = mapped_column(Unicode(500), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False, default="indexing")
    error: Mapped[str | None] = mapped_column(LongText)


class Skill(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    """Current skill head; `version` = latest published version number. History in skill_versions."""

    __tablename__ = "skills"
    __id_prefix__ = "skl"
    __table_args__ = (Index(None, "workspace_id", "created_at"),)

    name: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    description: Mapped[str | None] = mapped_column(LongText)
    instructions: Mapped[str] = mapped_column(LongText, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))


class SkillVersion(WorkspaceMixin, CreatedMixin, Base):
    """Immutable Skill.versions[] entry."""

    __tablename__ = "skill_versions"

    skill_id: Mapped[str] = mapped_column(String(ID_LEN), ForeignKey("skills.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))
    description: Mapped[str | None] = mapped_column(LongText)
    instructions: Mapped[str] = mapped_column(LongText, nullable=False, default="")
    release_note: Mapped[str | None] = mapped_column(Unicode(1000))
    published_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    published_by: Mapped[str | None] = mapped_column(Unicode(NAME_LEN))


class Policy(IdMixin, WorkspaceMixin, TimestampMixin, Base):
    __tablename__ = "policies"
    __id_prefix__ = "pol"
    __table_args__ = (Index(None, "workspace_id", "created_at"),)

    name: Mapped[str] = mapped_column(Unicode(NAME_LEN), nullable=False)
    description: Mapped[str | None] = mapped_column(LongText)
    kind: Mapped[str] = mapped_column(String(ENUM_LEN), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    scope: Mapped[dict] = mapped_column(JSONType(), nullable=False, default=dict)
    config: Mapped[dict | None] = mapped_column(JSONType())
