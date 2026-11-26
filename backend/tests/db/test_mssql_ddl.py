"""SQL Server portability: compile every table/index with the mssql dialect and enforce TEAM.md rules 4-6, 8, 10.
No SQL Server instance is needed (and none exists in CI here) — this is compile-level verification."""
from __future__ import annotations

import io
import re

import pytest
from sqlalchemy import Boolean, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects import mssql, postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db.models import metadata
from app.db.types import JSONType

MSSQL = mssql.dialect()
TABLES = list(metadata.sorted_tables)
NON_TENANT = {"workspaces", "idempotency_keys"}
FORBIDDEN_TSQL = re.compile(r"\b(NTEXT|TEXT|JSONB|BOOLEAN|TIMESTAMP WITH TIME ZONE|SERIAL|now\(\)|CASCADE)\b", re.I)


def _ddl(dialect) -> str:  # noqa: ANN001
    out = []
    for t in TABLES:
        out.append(str(CreateTable(t).compile(dialect=dialect)))
        out.extend(str(CreateIndex(i).compile(dialect=dialect)) for i in t.indexes)
    return "\n".join(out)


def test_expected_tables_exist():
    required = {
        "workspaces", "members", "api_keys", "workspace_settings", "connections", "secrets", "oauth_states",
        "providers", "provider_models", "workers", "mcp_servers", "mcp_tools", "knowledge_collections",
        "knowledge_documents", "skills", "skill_versions", "policies", "pipelines", "pipeline_drafts",
        "pipeline_versions", "templates", "documents", "runs", "run_steps", "run_events", "run_results",
        "review_items", "webhooks", "webhook_deliveries", "audit_events", "idempotency_keys",
    }
    assert required <= set(metadata.tables)


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_table_compiles_for_mssql(table):  # noqa: ANN001
    sql = str(CreateTable(table).compile(dialect=MSSQL))
    for idx in table.indexes:
        sql += "\n" + str(CreateIndex(idx).compile(dialect=MSSQL))
    assert not FORBIDDEN_TSQL.search(sql), sql
    # JSON columns are NVARCHAR(max); timestamps DATETIMEOFFSET; booleans BIT
    for col in table.columns:
        if isinstance(col.type, JSONType):
            assert re.search(rf"\b{re.escape(col.name)}\]? NVARCHAR\(max\)", sql), col
        if isinstance(col.type, Boolean):
            assert re.search(rf"\b{re.escape(col.name)}\]? BIT\b", sql), col


def test_full_ddl_compiles_for_both_dialects():
    assert "DATETIMEOFFSET" in _ddl(MSSQL)
    pg = _ddl(postgresql.dialect())
    assert "JSONB" in pg and "TIMESTAMP WITH TIME ZONE" in pg


@pytest.mark.parametrize("table", TABLES, ids=lambda t: t.name)
def test_portability_rules(table):  # noqa: ANN001
    indexed = set()
    for idx in table.indexes:
        indexed |= {c.name for c in idx.columns}
    for con in table.constraints:
        if isinstance(con, (UniqueConstraint, ForeignKeyConstraint)) or con is table.primary_key:
            indexed |= {c.name for c in con.columns}
        # rule 5: no cascades
        if isinstance(con, ForeignKeyConstraint):
            assert con.ondelete is None and con.onupdate is None, (table.name, con.name)
        # rule 6: no unique constraints over nullable columns
        if isinstance(con, UniqueConstraint):
            assert all(not c.nullable for c in con.columns), (table.name, con.name)
    for col in table.columns:
        if col.name in indexed and isinstance(col.type, String):
            assert col.type.length and col.type.length <= 4000, f"{table.name}.{col.name} needs a length (rule 4)"
        if col.unique or any(i.unique for i in table.indexes if col.name in i.columns):
            assert not col.nullable, f"{table.name}.{col.name}: unique on nullable column (rule 6)"
    # rule 10: tenant scope
    if table.name not in NON_TENANT:
        ws = table.columns.get("workspace_id")
        assert ws is not None and not ws.nullable, table.name
        assert any(i.columns.keys()[0] == "workspace_id" for i in table.indexes) or "workspace_id" in \
            table.primary_key.columns.keys(), f"{table.name}.workspace_id must be indexed"


def test_constraint_names_are_short():
    names = [str(c.name) for t in TABLES for c in t.constraints] + [str(i.name) for t in TABLES for i in t.indexes]
    assert all(n and n != "None" for n in names)
    too_long = [n for n in names if len(n) > 63]  # PG limit (MSSQL allows 128)
    assert not too_long, too_long


def test_index_key_size_under_mssql_limit():
    """Nonclustered index keys must be <= 1700 bytes on SQL Server (NVARCHAR = 2 bytes/char)."""
    for t in TABLES:
        groups = [list(i.columns) for i in t.indexes] + [list(c.columns) for c in t.constraints
                                                          if isinstance(c, UniqueConstraint)]
        for cols in groups:
            size = 0
            for c in cols:
                if isinstance(c.type, String):
                    size += (c.type.length or 10**6) * (2 if c.type.__class__.__name__.startswith("Unicode") else 1)
                else:
                    size += 16
            assert size <= 1700, (t.name, [c.name for c in cols], size)


def test_offline_mssql_migration_renders_tsql(tmp_path, monkeypatch):  # noqa: ANN001
    from alembic import command
    from alembic.config import Config

    from tests.db.test_migrations import alembic_config

    buf = io.StringIO()
    cfg: Config = alembic_config(stdout=buf)
    cfg.set_main_option("sqlalchemy.url", "mssql+pyodbc://u:p@h/db?driver=ODBC+Driver+18+for+SQL+Server")
    cfg.output_buffer = buf
    command.upgrade(cfg, "head", sql=True)
    sql = buf.getvalue()
    assert sql.count("CREATE TABLE") == len(TABLES) + 1  # + alembic_version
    assert "DATETIMEOFFSET" in sql and "NVARCHAR(max)" in sql and "SYSDATETIMEOFFSET()" in sql and "BIT" in sql
    assert "\nGO\n" in sql
    assert not FORBIDDEN_TSQL.search(sql)
