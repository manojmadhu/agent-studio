"""Idempotency-Key replay / reuse / in-progress, RateLimit-* headers, CORS exposure."""
from __future__ import annotations

import hashlib
from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.middleware import FixedWindowLimiter, RateLimitMiddleware
from app.core.time import utcnow
from app.db.models import IdempotencyKey, Policy

BODY = {"name": "Idem policy", "kind": "custom", "scope": {"all_pipelines": True}}


def _count(db, name):
    return db.scalar(select(func.count()).select_from(Policy).where(Policy.name == name))


def test_replay_same_key(raw_client, make_key, db):
    h = {"X-API-Key": make_key(), "Idempotency-Key": "k-1"}
    a = raw_client.post("/v1/policies", headers=h, json=BODY)
    assert a.status_code == 201 and "idempotency-replayed" not in a.headers
    b = raw_client.post("/v1/policies", headers=h, json=BODY)
    assert b.status_code == 201
    assert b.headers["idempotency-replayed"] == "true"
    assert b.json() == a.json()
    assert b.headers["content-type"].startswith("application/json")
    assert _count(db, "Idem policy") == 1


def test_same_key_different_body_422(raw_client, make_key):
    h = {"X-API-Key": make_key(), "Idempotency-Key": "k-2"}
    assert raw_client.post("/v1/policies", headers=h, json=BODY).status_code == 201
    r = raw_client.post("/v1/policies", headers=h, json={**BODY, "name": "Other"})
    assert r.status_code == 422 and r.json()["code"] == "idempotency_key_reused" and r.json()["title"]


def test_key_scoped_by_credential(raw_client, make_key, db):
    k1, k2 = make_key(), make_key()
    raw_client.post("/v1/policies", headers={"X-API-Key": k1, "Idempotency-Key": "shared"}, json=BODY)
    r = raw_client.post("/v1/policies", headers={"X-API-Key": k2, "Idempotency-Key": "shared"}, json=BODY)
    assert r.status_code == 201 and "idempotency-replayed" not in r.headers
    assert _count(db, "Idem policy") == 2


def test_in_progress_409(raw_client, make_key, db):
    secret = make_key()
    import json as _json

    raw = _json.dumps(BODY, separators=(",", ":")).encode()
    # same fingerprint the middleware computes (method, path, query, raw body)
    fp = hashlib.sha256(b"\n".join([b"POST", b"/v1/policies", b"", raw])).hexdigest()
    db.add(IdempotencyKey(scope_hash=hashlib.sha256(("k:" + secret).encode()).hexdigest(), key="busy", method="POST",
                          path="/v1/policies", request_hash=fp, state="in_progress",
                          expires_at=utcnow() + timedelta(hours=1)))
    db.commit()
    r = raw_client.post("/v1/policies", headers={"X-API-Key": secret, "Idempotency-Key": "busy",
                                                 "Content-Type": "application/json"}, content=raw)
    assert r.status_code == 409 and r.json()["code"] == "idempotency_in_progress"


def test_expired_record_is_reprocessed(raw_client, make_key, db):
    secret = make_key()
    h = {"X-API-Key": secret, "Idempotency-Key": "old"}
    raw_client.post("/v1/policies", headers=h, json=BODY)
    row = db.scalar(select(IdempotencyKey).where(IdempotencyKey.key == "old"))
    row.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    r = raw_client.post("/v1/policies", headers=h, json=BODY)
    assert r.status_code == 201 and "idempotency-replayed" not in r.headers
    assert _count(db, "Idem policy") == 2


def test_errors_release_key(raw_client, make_key, db):
    """403 is not stored: after fixing permissions the same key works."""
    h = {"X-API-Key": make_key(["runs:read"]), "Idempotency-Key": "perm"}
    assert raw_client.post("/v1/policies", headers=h, json=BODY).status_code == 403
    assert db.scalar(select(IdempotencyKey).where(IdempotencyKey.key == "perm")) is None


def test_validation_422_replayed(raw_client, make_key):
    h = {"X-API-Key": make_key(), "Idempotency-Key": "bad"}
    a = raw_client.post("/v1/policies", headers=h, json={"name": ""})
    b = raw_client.post("/v1/policies", headers=h, json={"name": ""})
    assert a.status_code == b.status_code == 422 and b.headers.get("idempotency-replayed") == "true"
    assert b.headers["content-type"].startswith("application/problem+json")


def test_no_key_no_tracking(raw_client, make_key, db):
    h = {"X-API-Key": make_key()}
    raw_client.post("/v1/policies", headers=h, json=BODY)
    raw_client.post("/v1/policies", headers=h, json=BODY)
    assert _count(db, "Idem policy") == 2


def test_body_less_post_with_key(raw_client, make_key):
    """Portal sends Idempotency-Key on body-less POSTs (connection test, worker health, discover …)."""
    h = {"X-API-Key": make_key(), "Idempotency-Key": "nobody"}
    r = raw_client.post("/v1/workers/wrk_paddle/health", headers=h)
    assert r.status_code == 200 and r.json()["status"] in ("healthy", "unreachable", "degraded", "unknown")


def test_ratelimit_headers(raw_client, make_key):
    r = raw_client.get("/v1/me", headers={"X-API-Key": make_key()})
    assert int(r.headers["ratelimit-limit"]) > 0
    assert int(r.headers["ratelimit-remaining"]) >= 0 and int(r.headers["ratelimit-reset"]) >= 1


def test_ratelimit_enforced_when_enabled():
    app = FastAPI()

    @app.get("/v1/x")
    def x() -> dict:
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, limiter=FixedWindowLimiter(2, 60), enforce=True)
    c = TestClient(app)
    assert c.get("/v1/x").status_code == 200
    assert c.get("/v1/x").headers["ratelimit-remaining"] == "0"
    r = c.get("/v1/x")
    assert r.status_code == 429 and r.json()["code"] == "rate_limited" and "retry-after" in r.headers


def test_ratelimit_not_enforced_by_default(raw_client):
    from app.core.middleware import _mw_settings

    s = _mw_settings()
    assert s.rate_limit_enforce in (None, False)


def test_cors_preflight_exposes_etag(raw_client):
    r = raw_client.options("/v1/connections", headers={"Origin": "http://localhost:5173",
                                                       "Access-Control-Request-Method": "POST",
                                                       "Access-Control-Request-Headers": "x-api-key,idempotency-key,content-type"})
    assert r.status_code == 200
    r2 = raw_client.get("/v1/catalog/services", headers={"Origin": "http://localhost:5173"})
    assert "ETag" in r2.headers.get("access-control-expose-headers", "")
