"""Seed: idempotency, portal ids, no plaintext secrets, bootstrap API key hashing."""
from __future__ import annotations

import hashlib

from sqlalchemy import func, select

from app.config import get_settings
from app.db import models as m
from app.db.seed import seed, seed_if_empty
from tests.conftest import reset_database


def _counts(db):  # noqa: ANN001, ANN202
    return {t.name: db.scalar(select(func.count()).select_from(t)) for t in m.metadata.sorted_tables}


def test_seed_if_empty_is_idempotent(db):  # noqa: ANN001
    before = _counts(db)
    assert seed_if_empty(db) is False
    assert _counts(db) == before


def test_portal_ids_and_core_rows(db):  # noqa: ANN001
    from app.services.catalog_data import seed_data

    data = seed_data()
    ws = db.get(m.Workspace, "ws_default")
    assert ws.name == data["settings"]["name"] and ws.environment == "sandbox"  # I-2: matches the mock
    admin = db.get(m.Member, "usr_admin")
    assert (admin.name, admin.email, admin.role) == (data["me"]["name"], data["me"]["email"], "admin")
    assert db.get(m.Member, data["me"]["id"]) is None  # the mock principal is represented by usr_admin
    for model, pk in [(m.Pipeline, "pl_ap"), (m.Pipeline, "pl_xls"), (m.Connection, "con_gmail"),
                      (m.Provider, "prv_anthropic"), (m.Worker, "wrk_paddle"), (m.McpServer, "mcp_vendors"),
                      (m.Skill, "skl_invoice"), (m.Policy, "pol_pii"), (m.KnowledgeCollection, "kc_vendor"),
                      (m.ReviewItem, "rv1"), (m.Run, "run_48210"), (m.Template, "tpl_ap"), (m.Webhook, "wh_1")]:
        assert db.get(model, pk) is not None, (model.__name__, pk)
    assert db.get(m.PipelineDraft, "pl_ap").etag == 'W/"pl_ap-1"'
    assert db.scalar(select(func.count()).select_from(m.PipelineVersion).where(m.PipelineVersion.pipeline_id == "pl_ap")) == 3
    assert db.get(m.Pipeline, "pl_ap").live_version == 14
    rv1 = db.get(m.ReviewItem, "rv1")
    assert db.get(m.Run, rv1.run_id).status == "review" and db.get(m.RunResult, rv1.run_id) is not None
    assert len(db.get(m.Provider, "prv_anthropic") and db.scalars(
        select(m.ProviderModel).where(m.ProviderModel.provider_id == "prv_anthropic")).all()) == 3


def test_every_row_is_in_default_workspace(db):  # noqa: ANN001
    for t in m.metadata.sorted_tables:
        if "workspace_id" in t.c and t.name != "idempotency_keys":
            other = db.scalar(select(func.count()).select_from(t).where(t.c.workspace_id != "ws_default"))
            assert other == 0, t.name


def test_no_plaintext_secrets(db):  # noqa: ANN001
    for c in db.scalars(select(m.Connection)).all():
        blob = repr(c.settings) + (c.secret_ref or "") + (c.secret_hint or "")
        assert "demo-placeholder" not in blob
        if c.auth_type == "none":
            assert c.secret_ref is None
        else:
            assert c.secret_ref
    for s in db.scalars(select(m.Secret)).all():
        assert "demo-placeholder" not in s.ciphertext


def test_bootstrap_api_key_is_hashed(clean_db, monkeypatch):  # noqa: ANN001
    secret = "dik_live_bootstrap_test_secret"
    monkeypatch.setattr(get_settings(), "bootstrap_api_key", secret)
    from sqlalchemy.orm import Session

    with clean_db.begin() as conn:
        for t in reversed(m.metadata.sorted_tables):
            conn.execute(t.delete())
    with Session(clean_db) as s:
        seed(s)
        key = s.get(m.ApiKey, "key_bootstrap")
        assert key.key_hash == hashlib.sha256(secret.encode()).hexdigest()
        assert key.prefix == secret[:13] and key.scopes == ["admin"]
        assert secret not in repr([(k.prefix, k.key_hash) for k in s.scalars(select(m.ApiKey)).all()])
    monkeypatch.setattr(get_settings(), "bootstrap_api_key", None)
    reset_database(clean_db)


def test_seed_without_data_file(clean_db):  # noqa: ANN001
    from sqlalchemy.orm import Session

    with clean_db.begin() as conn:
        for t in reversed(m.metadata.sorted_tables):
            conn.execute(t.delete())
    with Session(clean_db) as s:
        seed(s, data={})
        assert s.get(m.Workspace, "ws_default") and s.get(m.Member, "usr_admin")
    reset_database(clean_db)


# endpoint → (model, router ordering): keyset lists are (created_at DESC, id DESC); the rest (created_at, id) ASC.
GOLDEN_LISTS = {
    "GET /connections?limit=200": (m.Connection, "desc"), "GET /runs?limit=200": (m.Run, "desc"),
    "GET /policies": (m.Policy, "asc"), "GET /pipelines?limit=200": (m.Pipeline, "asc"),
    "GET /providers": (m.Provider, "asc"), "GET /workers": (m.Worker, "asc"), "GET /mcp-servers": (m.McpServer, "asc"),
    "GET /knowledge-collections": (m.KnowledgeCollection, "asc"), "GET /skills": (m.Skill, "asc"),
    "GET /templates": (m.Template, "asc"), "GET /webhooks": (m.Webhook, "asc"),
}


def test_keyset_order_matches_mock_lists(db):  # noqa: ANN001
    """I-3: each list's router ordering returns the seeded rows in the mock's list order."""
    import json

    from app.core.pagination import paginate
    from sqlalchemy import select
    from app.services.catalog_data import DATA_DIR

    golden = json.loads((DATA_DIR / "mock_golden_responses.json").read_text(encoding="utf-8"))
    for key, (model, direction) in GOLDEN_LISTS.items():
        body = golden[key]["body"]
        expected = [x["id"] for x in (body["data"] if isinstance(body, dict) else body)]
        stmt = select(model).where(model.workspace_id == "ws_default")
        if direction == "desc":
            got = paginate(db, stmt, model, limit=200, cursor=None, serialize=lambda r: r.id)["data"]
        else:
            got = list(db.scalars(stmt.with_only_columns(model.id).order_by(model.created_at, model.id)).all())
        got = [i for i in got if i in set(expected)]  # runs: ignore the review-linked runs the mock doesn't list
        assert got == expected, key


def test_seeded_timestamps_are_not_in_the_future(db):  # noqa: ANN001
    from app.core.time import utcnow

    now = utcnow()
    for t in m.metadata.sorted_tables:
        if "created_at" in t.c:
            latest = db.scalar(select(func.max(t.c.created_at)))
            assert latest is None or latest <= now, t.name
