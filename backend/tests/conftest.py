"""Shared pytest fixtures (owned by the DB engineer) — see backend/docs/TEAM.md § Tests and docs/database.md.

Fixtures
    engine       session-scoped Engine on DI_TEST_DATABASE_URL (default …/docintel_test_db); once per session every
                 table is dropped, the schema re-created (create_all) and stamped `alembic stamp head`, demo seeded.
    db           a Session on the test engine (closed after the test).
    client       TestClient(app.main.app) — get_db bound to the test engine, get_auth overridden to an admin of
                 ws_default (usr_admin / admin@acme.example). Lifespan runs (startup/shutdown hooks).
    raw_client   TestClient on a fresh app instance with NO auth override (real auth path) — same test DB.
    auth_as      auth_as(role="viewer") / auth_as(scopes=["runs:read"]) swaps the `client` principal.
    clean_db     the isolation fixture the others depend on (use directly for DB-only tests).

Isolation: "reset when dirty". A cursor hook marks the DB dirty on any INSERT/UPDATE/DELETE/MERGE; before the
next test that uses the DB, every table is emptied (DELETE in reverse FK order — portable, no TRUNCATE) and the
demo workspace re-seeded (~0.1 s). Read-only tests pay nothing. Tests therefore always start from the pristine
seed, may commit freely, and background threads (run engine) see the same data because they use the same
engine via app.db.session.session_factory().

Background work: app.db.session.bind_session_factory(TestSession) is applied for the whole session and
app.db.session.SessionLocal is patched too, so code that opens its own sessions hits the test DB.
"""
from __future__ import annotations

import os
import tempfile
from typing import Callable, Iterator

DEFAULT_TEST_URL = "postgresql+psycopg://di:di@localhost:5432/docintel_test_db"
TEST_DATABASE_URL = os.environ.get("DI_TEST_DATABASE_URL", DEFAULT_TEST_URL)

# Must happen before anything imports app.config / app.db.session (module-level engine, cached settings).
os.environ["DI_DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("DI_ENV", "test")
# Keep object-store writes (seeded sample documents, uploads) out of the repo.
os.environ.setdefault("DI_STORAGE_DIR", tempfile.mkdtemp(prefix="di-test-storage-"))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

import app.db.session as db_session  # noqa: E402
from app.core.security import AuthContext, get_auth  # noqa: E402
from app.db.models import metadata  # noqa: E402
from app.db.reset import rebuild  # noqa: E402
from app.db.seed import seed  # noqa: E402
from app.db.session import get_db, make_engine  # noqa: E402

ADMIN_CTX = AuthContext(workspace_id="ws_default", kind="user", principal_id="usr_admin", name="Admin",
                        email="admin@acme.example", role="admin")

_WRITE_PREFIXES = ("INSERT", "UPDATE", "DELETE", "MERGE")
_state = {"dirty": True}


def _mark_dirty(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001, ARG001
    if statement.lstrip()[:6].upper().startswith(_WRITE_PREFIXES):
        _state["dirty"] = True


def reset_database(engine) -> None:  # noqa: ANN001
    """Empty every table (reverse dependency order) and re-seed the demo workspace."""
    with engine.begin() as conn:
        for table in reversed(metadata.sorted_tables):
            conn.execute(table.delete())
    with Session(engine, expire_on_commit=False) as s:
        seed(s)
    _state["dirty"] = False


@pytest.fixture(scope="session")
def engine():  # noqa: ANN201
    eng = make_engine(TEST_DATABASE_URL, pool_size=5, max_overflow=10) if not TEST_DATABASE_URL.startswith(
        "sqlite") else make_engine(TEST_DATABASE_URL)
    # Drop every table (incl. leftovers / alembic_version), create_all, then `alembic stamp head`, so the test DB
    # always stays consistent with Alembic (a later `alembic upgrade/downgrade` works) — see docs/database.md.
    rebuild(eng, create_all=True)
    event.listen(eng, "before_cursor_execute", _mark_dirty)
    # The app's own module-level engine also points at the test DB (env set above); track it too.
    if db_session.engine is not eng:
        event.listen(db_session.engine, "before_cursor_execute", _mark_dirty)

    factory = sessionmaker(bind=eng, expire_on_commit=False)
    original_local = db_session.SessionLocal
    db_session.bind_session_factory(factory)
    db_session.SessionLocal = factory
    reset_database(eng)
    yield eng
    db_session.SessionLocal = original_local
    db_session.bind_session_factory(None)
    eng.dispose()


@pytest.fixture()
def clean_db(engine):  # noqa: ANN001, ANN201
    """Guarantees the pristine demo seed at the start of the test."""
    if _state["dirty"]:
        reset_database(engine)
    return engine


@pytest.fixture()
def db(clean_db) -> Iterator[Session]:  # noqa: ANN001
    session = db_session.session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _override_get_db() -> Iterator[Session]:
    yield from get_db()  # get_db uses session_factory(), already bound to the test engine


@pytest.fixture()
def client(clean_db) -> Iterator[TestClient]:  # noqa: ANN001
    from app.main import app

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_auth] = lambda: ADMIN_CTX
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_auth, None)


@pytest.fixture()
def raw_client(clean_db) -> Iterator[TestClient]:  # noqa: ANN001
    """Separate app instance (own dependency_overrides) so it can be combined with `client` in one test."""
    from app.main import create_app

    fresh = create_app()
    fresh.dependency_overrides[get_db] = _override_get_db
    with TestClient(fresh) as c:
        yield c


@pytest.fixture()
def auth_as(client) -> Callable[..., AuthContext]:  # noqa: ANN001
    """auth_as(role="viewer") → user principal with that role; auth_as(scopes=[...]) → API-key principal.
    Extra keyword arguments override AuthContext fields (e.g. workspace_id="ws_other")."""
    from app.main import app

    def _swap(role: str | None = None, scopes: list[str] | None = None, **fields) -> AuthContext:  # noqa: ANN003
        if scopes is not None:
            base = dict(workspace_id="ws_default", kind="api_key", principal_id="key_test", name="Test key",
                        scopes=frozenset(scopes))
        else:
            base = dict(workspace_id="ws_default", kind="user", principal_id="usr_admin", name="Admin",
                        email="admin@acme.example", role=role or "admin")
        base.update(fields)
        ctx = AuthContext(**base)
        app.dependency_overrides[get_auth] = lambda: ctx
        return ctx

    yield _swap
    app.dependency_overrides[get_auth] = lambda: ADMIN_CTX
