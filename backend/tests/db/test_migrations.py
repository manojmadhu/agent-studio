"""Alembic upgrade → downgrade → upgrade on PostgreSQL, in a throw-away schema so the shared test tables
(and other tests) are untouched; then autogenerate must report no drift against the models."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from sqlalchemy import inspect

from app.db.models import metadata

BACKEND = Path(__file__).resolve().parents[2]
SCHEMA = "zz_migration_test"


def alembic_config(stdout=None):  # noqa: ANN001, ANN201
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"), stdout=stdout or io.StringIO())
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


@pytest.fixture()
def pg_schema_conn(engine):  # noqa: ANN001, ANN201
    if engine.dialect.name != "postgresql":
        pytest.skip("schema-isolated migration test is PostgreSQL-only")
    with engine.connect() as conn:
        conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
        conn.exec_driver_sql(f"CREATE SCHEMA {SCHEMA}")
        conn.exec_driver_sql(f"SET search_path TO {SCHEMA}")
        conn.commit()
        try:
            yield conn
        finally:
            conn.rollback()
            conn.exec_driver_sql("SET search_path TO public")
            conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
            conn.commit()


def _run(conn, fn, *args, **kw):  # noqa: ANN001, ANN002, ANN003, ANN202
    cfg = alembic_config()
    cfg.attributes["connection"] = conn
    fn(cfg, *args, **kw)
    conn.commit()


def test_upgrade_downgrade_upgrade(pg_schema_conn):  # noqa: ANN001
    from alembic import command

    _run(pg_schema_conn, command.upgrade, "head")
    tables = set(inspect(pg_schema_conn).get_table_names(schema=SCHEMA))
    assert set(metadata.tables) <= tables

    _run(pg_schema_conn, command.downgrade, "base")
    assert set(inspect(pg_schema_conn).get_table_names(schema=SCHEMA)) <= {"alembic_version"}

    _run(pg_schema_conn, command.upgrade, "head")
    assert set(metadata.tables) <= set(inspect(pg_schema_conn).get_table_names(schema=SCHEMA))

    # No drift between models and the migration (same as `alembic check`).
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    ctx = MigrationContext.configure(pg_schema_conn, opts={"compare_type": True})
    diff = compare_metadata(ctx, metadata)
    assert diff == [], diff


def test_single_head():
    from alembic.script import ScriptDirectory

    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1


def test_test_db_is_stamped_at_head(engine):  # noqa: ANN001
    """I-8: conftest's create_all is stamped, so `alembic upgrade head` / `downgrade base` work on test DBs."""
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory

    with engine.connect() as conn:
        current = set(MigrationContext.configure(conn).get_current_heads())
    assert current == set(ScriptDirectory.from_config(alembic_config()).get_heads())
