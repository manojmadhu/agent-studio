"""Engine, session factory and the FastAPI `get_db` dependency.

Request handlers use `Depends(get_db)`. Code that opens its own sessions (background run engine, webhook
dispatcher, startup seeding) must use `session_factory()` — NOT import `SessionLocal` directly — so tests can
redirect it to the test database with `bind_session_factory(...)`:

    from app.db.session import session_factory
    with session_factory()() as db:   # session_factory() returns the current sessionmaker
        ...
        db.commit()

or the convenience context manager `with session_scope() as db:` (commits on success, rolls back on error).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def make_engine(url: str, *, echo: bool | None = None, **kw: Any) -> Engine:
    """Create an Engine with the project's portable defaults (pre-ping, READ COMMITTED, fast executemany)."""
    settings = get_settings()
    u = make_url(url)
    opts: dict[str, Any] = {
        "pool_pre_ping": True,
        "echo": settings.db_echo if echo is None else echo,
        "future": True,
    }
    if u.get_backend_name() != "sqlite":
        opts["isolation_level"] = "READ COMMITTED"
        opts["pool_size"] = settings.db_pool_size
        opts["max_overflow"] = settings.db_pool_size
    if u.drivername == "mssql+pyodbc":
        opts["fast_executemany"] = True
    opts.update(kw)
    return create_engine(u, **opts)


engine: Engine = make_engine(get_settings().database_url)

SessionLocal: sessionmaker[Session] = sessionmaker(bind=engine, expire_on_commit=False, autoflush=True)

_factory: sessionmaker[Session] = SessionLocal


def session_factory() -> sessionmaker[Session]:
    """The sessionmaker background code should use (tests rebind it to the test engine)."""
    return _factory


def bind_session_factory(factory: sessionmaker[Session] | None) -> None:
    """Redirect `session_factory()` (and `get_db`) to another sessionmaker; `None` restores SessionLocal."""
    global _factory
    _factory = factory or SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background work: commit on success, rollback on exception, always close."""
    db = _factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency. Routes commit explicitly (`db.commit()`); anything uncommitted is rolled back."""
    db = _factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


__all__ = ["engine", "SessionLocal", "make_engine", "get_db", "session_factory", "bind_session_factory",
           "session_scope"]
