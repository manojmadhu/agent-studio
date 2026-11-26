"""Alembic environment. URL comes from app.config (DI_DATABASE_URL); metadata from app.db.models.

Portability: migrations must apply on PostgreSQL and SQL Server. Column types render through
app.db.types (JSONType, UTCDateTime) and server defaults through app.db.types.UtcNow, which compile per dialect.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import get_settings
from app.db.models import metadata  # noqa: F401  (registers every model)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = metadata


def _url() -> str:
    # An explicit -x url=... or sqlalchemy.url (tests) wins over the environment.
    x = context.get_x_argument(as_dictionary=True)
    return x.get("url") or config.get_main_option("sqlalchemy.url") or get_settings().database_url


def _render_item(type_, obj, autogen_context):  # noqa: ANN001, ANN201
    """Render our portable types with a short `dbt.` prefix (imported in script.py.mako)."""
    if type_ == "type" and type(obj).__module__ == "app.db.types":
        return f"dbt.{type(obj).__name__}()"
    return False


COMMON = dict(
    target_metadata=target_metadata,
    compare_type=True,
    compare_server_default=False,
    render_as_batch=False,
    render_item=_render_item,
)


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, dialect_opts={"paramstyle": "named"}, **COMMON)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:  # tests may pass an open connection
        context.configure(connection=connectable, **COMMON)
        with context.begin_transaction():
            context.run_migrations()
        return
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, **COMMON)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
