"""Portable schema reset (PostgreSQL + SQL Server): drop EVERY table in the database (reflected, so leftovers
from failed runs and `alembic_version` go too), then rebuild via Alembic so the DB is always upgradeable.

    python -m app.db.reset               # DI_DATABASE_URL → drop all, alembic upgrade head
    python -m app.db.reset --create-all  # drop all, metadata.create_all + alembic stamp head (what tests do)

Destroys data. Refuses to run when DI_ENV=production.
"""
from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import Connection, Engine, MetaData

from app.config import get_settings

BACKEND = Path(__file__).resolve().parents[2]


def alembic_config():  # noqa: ANN201
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return cfg


def drop_everything(engine: Engine) -> None:
    """Drop all tables in the default schema, FK-ordered (reflection, no raw SQL)."""
    md = MetaData()
    md.reflect(bind=engine)
    md.drop_all(bind=engine)


def _alembic(conn: Connection, fn, *args) -> None:  # noqa: ANN001
    cfg = alembic_config()
    cfg.attributes["connection"] = conn
    fn(cfg, *args)


def rebuild(engine: Engine, *, create_all: bool = False) -> None:
    """drop_everything, then either `alembic upgrade head` or `create_all` + `alembic stamp head`.
    Both leave a DB whose alembic_version matches its tables, so later upgrades/downgrades work."""
    from alembic import command

    from app.db.models import metadata

    drop_everything(engine)
    with engine.connect() as conn:
        if create_all:
            metadata.create_all(conn)
            _alembic(conn, command.stamp, "head")
        else:
            _alembic(conn, command.upgrade, "head")
        conn.commit()


if __name__ == "__main__":  # pragma: no cover
    if get_settings().is_production:
        sys.exit("refusing to reset a production database")
    from app.db.session import make_engine

    eng = make_engine(get_settings().database_url)
    rebuild(eng, create_all="--create-all" in sys.argv)
    print("database reset:", eng.url.render_as_string(hide_password=True))
