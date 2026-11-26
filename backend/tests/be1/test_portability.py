"""Portability spot-checks for BE1 code: no dialect imports / raw SQL, statements compile for SQL Server."""
from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.dialects import mssql, postgresql

ROOT = Path(__file__).resolve().parents[2] / "app"
BE1_FILES = [
    "core/security.py", "core/middleware.py", "cli.py", "services/audit.py", "services/secret_store.py",
    "services/llm.py", "services/mcp_client.py", "services/webhooks.py", "services/oauth.py",
    *[f"services/adapters/{p.name}" for p in (ROOT / "services" / "adapters").glob("*.py")],
    *[f"routers/{n}.py" for n in ("_be1", "identity", "catalog", "connections", "providers", "workers", "mcp_servers",
                                  "knowledge", "skills", "policies", "ops")],
]
FORBIDDEN = [r"sqlalchemy\.dialects", r"\bpostgresql\.", r"\bmssql\.", r"\bJSONB\b", r"ILIKE|\.ilike\(",
             r"\.op\(['\"]->", r"ON CONFLICT", r"on_conflict", r"RETURNING", r"::jsonb", r"\.astext"]


def test_no_dialect_specific_code():
    offenders = []
    for rel in BE1_FILES:
        src = (ROOT / rel).read_text()
        for pat in FORBIDDEN:
            if re.search(pat, src):
                offenders.append((rel, pat))
        # raw SQL only allowed in the external-database connection probe ("SELECT 1" against the user's DB)
        if "text(" in src and rel != "services/adapters/connection_test.py":
            if re.search(r"\btext\(\s*['\"]", src):
                offenders.append((rel, "raw text()"))
    assert offenders == []


def test_queries_compile_on_both_dialects():
    from app.db.models import ApiKey, AuditEvent, Connection, IdempotencyKey, Member, Webhook

    stmts = [
        select(ApiKey).where(or_(ApiKey.key_hash == "a", ApiKey.previous_key_hash == "a")),
        select(Member).where(Member.workspace_id == "ws", func.lower(Member.email) == "x"),
        select(Connection).where(Connection.workspace_id == "ws", Connection.service == "s")
        .order_by(Connection.created_at.desc(), Connection.id.desc()).limit(51),
        select(AuditEvent).where(AuditEvent.workspace_id == "ws", AuditEvent.resource_type == "policy"),
        select(Webhook).where(Webhook.workspace_id == "ws", Webhook.enabled.is_(True)),
        select(IdempotencyKey).where(IdempotencyKey.scope_hash == "h", IdempotencyKey.key == "k"),
        select(func.count()).select_from(Member).where(Member.role == "admin"),
    ]
    for s in stmts:
        for d in (mssql.dialect(), postgresql.dialect()):
            sql = str(s.compile(dialect=d))
            assert "->" not in sql and "ILIKE" not in sql
        assert "TOP" in str(stmts[2].compile(dialect=mssql.dialect())) or "FETCH" in str(
            stmts[2].compile(dialect=mssql.dialect()))
