"""The shared fixtures behave as documented (isolation, auth override, session factory rebinding)."""
from __future__ import annotations

from sqlalchemy import func, select

import app.db.session as db_session
from app.db import models as m


def test_sample_documents_seeded(db):  # noqa: ANN001
    for doc_id, mime in (("doc_inv_sample", "application/pdf"), ("doc_rcp_sample", "image/jpeg")):
        d = db.get(m.Document, doc_id)
        assert d is not None and d.workspace_id == "ws_default" and d.mime_type == mime
        if d.object_key:  # stored through app.services.storage when available
            from app.services import storage

            assert storage.get_store().exists(d.object_key) and d.size_bytes and len(d.sha256) == 64


def test_write_in_one_test(db):  # noqa: ANN001
    db.get(m.Pipeline, "pl_ap").name = "changed"
    db.add(m.Policy(workspace_id="ws_default", name="tmp", kind="retention", scope={}))
    db.commit()


def test_previous_write_was_reset(db):  # noqa: ANN001
    assert db.get(m.Pipeline, "pl_ap").name != "changed"
    assert db.scalar(select(func.count()).select_from(m.Policy).where(m.Policy.name == "tmp")) == 0


def test_session_factory_is_bound_to_test_engine(engine):  # noqa: ANN001
    assert db_session.session_factory().kw["bind"] is engine
    assert db_session.SessionLocal is db_session.session_factory()
    with db_session.session_scope() as s:
        assert s.get(m.Workspace, "ws_default") is not None


def test_client_and_auth_override(client, auth_as):  # noqa: ANN001
    from app.core.security import get_auth
    from app.main import app

    assert client.get("/healthz").status_code == 200
    assert app.dependency_overrides[get_auth]().role == "admin"
    ctx = auth_as(role="viewer")
    assert app.dependency_overrides[get_auth]() is ctx and ctx.role == "viewer"
    key = auth_as(scopes=["runs:read"])
    assert key.kind == "api_key" and key.has_scope("runs:read") and not key.has_scope("pipelines:write")


def test_raw_client_has_no_auth_override(raw_client):  # noqa: ANN001
    from app.core.security import get_auth

    assert get_auth not in raw_client.app.dependency_overrides
    assert raw_client.get("/healthz").status_code == 200
