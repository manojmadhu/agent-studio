"""Authentication: API keys, dev bearer tokens, sse_token, problem+json 401/403, ingest HMAC helper."""
from __future__ import annotations

import time
from datetime import timedelta

import pytest
from sqlalchemy import select
from starlette.requests import Request

from app.config import get_settings
from app.core.errors import ApiError
from app.core.security import (
    AuthContext, hash_api_key, issue_dev_token, issue_sse_token, sign_payload, verify_ingest_hmac,
)
from app.core.time import utcnow
from app.db.models import ApiKey

PROBLEM = "application/problem+json"


def _problem(r, status, code=None):
    assert r.status_code == status, r.text
    assert r.headers["content-type"].startswith(PROBLEM)
    b = r.json()
    assert b["status"] == status and b["title"] and b["type"].startswith("https://")
    if code:
        assert b["code"] == code
    return b


def test_no_credentials_is_401_problem(raw_client):
    b = _problem(raw_client.get("/v1/me"), 401, "unauthorized")
    assert "detail" in b


def test_api_key_ok_and_me_shape(raw_client, make_key, db):
    secret = make_key(["runs:read", "ingest:write"], name="ERP")
    r = raw_client.get("/v1/me", headers={"X-API-Key": secret})
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["kind"] == "api_key" and me["name"] == "ERP"
    assert sorted(me["scopes"]) == ["ingest:write", "runs:read"]
    assert me["workspace"]["id"] == "ws_default" and me["workspace"]["name"]
    assert me["sse_token"].startswith("sse_")
    k = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(secret)))
    db.refresh(k)
    assert k.last_used_at is not None


def test_unknown_key_401(raw_client):
    _problem(raw_client.get("/v1/me", headers={"X-API-Key": "dik_test_nope"}), 401)


def test_revoked_key_401(raw_client, make_key):
    secret = make_key(revoked_at=utcnow())
    b = _problem(raw_client.get("/v1/me", headers={"X-API-Key": secret}), 401)
    assert "revoked" in b["detail"]


def test_expired_key_401(raw_client, make_key):
    secret = make_key(expires_at=utcnow() - timedelta(minutes=1))
    b = _problem(raw_client.get("/v1/me", headers={"X-API-Key": secret}), 401)
    assert "expired" in b["detail"]


def test_ip_allow_list(raw_client, make_key):
    blocked = make_key(allowed_ips=["10.0.0.0/8"])
    _problem(raw_client.get("/v1/me", headers={"X-API-Key": blocked}), 403, "forbidden")
    allowed = make_key(allowed_ips=["10.0.0.0/8", "testclient", "0.0.0.0/0"])
    assert raw_client.get("/v1/me", headers={"X-API-Key": allowed}).status_code == 200


def test_scope_enforced_403(raw_client, make_key):
    secret = make_key(["runs:read"])
    _problem(raw_client.post("/v1/connections", headers={"X-API-Key": secret}, json={}), 403, "forbidden")
    _problem(raw_client.get("/v1/api-keys", headers={"X-API-Key": secret}), 403)


def test_rotation_grace_period(raw_client, make_key):
    admin = make_key(["admin"])
    h = {"X-API-Key": admin}
    created = raw_client.post("/v1/api-keys", headers=h, json={"name": "CI", "scopes": ["runs:read"]})
    assert created.status_code == 201
    old = created.json()["secret"]
    rot = raw_client.post(f"/v1/api-keys/{created.json()['id']}/rotate", headers=h, json={"grace_period_hours": 1})
    assert rot.status_code == 200
    new = rot.json()["secret"]
    assert new != old and rot.json()["prefix"] == new[:13]
    assert raw_client.get("/v1/me", headers={"X-API-Key": old}).status_code == 200   # grace
    assert raw_client.get("/v1/me", headers={"X-API-Key": new}).status_code == 200
    # rotate with no grace: the previous secret stops working immediately
    rot2 = raw_client.post(f"/v1/api-keys/{created.json()['id']}/rotate", headers=h, json={"grace_period_hours": 0})
    _problem(raw_client.get("/v1/me", headers={"X-API-Key": new}), 401)
    assert raw_client.get("/v1/me", headers={"X-API-Key": rot2.json()["secret"]}).status_code == 200


def test_rotation_grace_expired(raw_client, make_key, db):
    secret = make_key()
    k = db.scalar(select(ApiKey).where(ApiKey.key_hash == hash_api_key(secret)))
    k.previous_key_hash, k.previous_expires_at = k.key_hash, utcnow() - timedelta(seconds=1)
    k.key_hash = hash_api_key("dik_test_other")
    db.commit()
    b = _problem(raw_client.get("/v1/me", headers={"X-API-Key": secret}), 401)
    assert "grace" in b["detail"]


def test_revoke_via_api(raw_client, make_key):
    admin = make_key(["admin"])
    h = {"X-API-Key": admin}
    k = raw_client.post("/v1/api-keys", headers=h, json={"name": "tmp", "scopes": ["runs:read"]}).json()
    assert raw_client.delete(f"/v1/api-keys/{k['id']}", headers=h).status_code == 204
    _problem(raw_client.get("/v1/me", headers={"X-API-Key": k["secret"]}), 401)
    ids = [x["id"] for x in raw_client.get("/v1/api-keys", headers=h).json()["data"]]
    assert k["id"] not in ids
    _problem(raw_client.delete(f"/v1/api-keys/{k['id']}", headers=h), 404, "not_found")


def test_bootstrap_key(raw_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "bootstrap_api_key", "dik_test_bootstrap_xyz")
    r = raw_client.get("/v1/me", headers={"X-API-Key": "dik_test_bootstrap_xyz"})
    assert r.status_code == 200 and r.json()["scopes"] == ["admin"]


def test_dev_bearer_token(raw_client):
    tok = issue_dev_token("usr_sara", role="builder", name="Sara K.", email="sara@example.com")
    r = raw_client.get("/v1/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["kind"] == "user" and me["role"] == "builder" and me["id"] == "usr_sara"
    assert "pipelines:write" in me["scopes"] and "admin" not in me["scopes"]
    # builder cannot manage API keys
    _problem(raw_client.get("/v1/api-keys", headers={"Authorization": f"Bearer {tok}"}), 403)


def test_dev_bearer_rejections(raw_client, monkeypatch):
    expired = issue_dev_token(ttl_s=-10)
    _problem(raw_client.get("/v1/me", headers={"Authorization": f"Bearer {expired}"}), 401)
    _problem(raw_client.get("/v1/me", headers={"Authorization": "Bearer not.a.jwt"}), 401)
    _problem(raw_client.get("/v1/me", headers={"Authorization": "Basic abc"}), 401)
    import jwt

    forged = jwt.encode({"sub": "x", "role": "admin", "exp": int(time.time()) + 60}, "wrong-key", algorithm="HS256")
    _problem(raw_client.get("/v1/me", headers={"Authorization": f"Bearer {forged}"}), 401)
    ok = issue_dev_token()
    monkeypatch.setattr(get_settings(), "dev_auth", False)
    _problem(raw_client.get("/v1/me", headers={"Authorization": f"Bearer {ok}"}), 401)
    monkeypatch.setattr(get_settings(), "dev_auth", True)
    monkeypatch.setattr(get_settings(), "env", "production")
    _problem(raw_client.get("/v1/me", headers={"Authorization": f"Bearer {ok}"}), 401)


def test_oidc_rs256_via_jwks(raw_client, monkeypatch):
    """OIDC path: RS256 token verified against a JWKS (PyJWKClient patched to a local key), mapped by email."""
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.core import security

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class FakeJwks:
        def __init__(self, *a, **k):  # noqa: ANN002, ANN003
            pass

        def get_signing_key_from_jwt(self, token):  # noqa: ANN001
            return type("K", (), {"key": key.public_key()})()

    s = get_settings()
    monkeypatch.setattr(s, "oidc_jwks_url", "https://login.example/jwks")
    monkeypatch.setattr(s, "oidc_issuer", "https://login.example/")
    monkeypatch.setattr(s, "oidc_audience", "docintel")
    monkeypatch.setattr(jwt, "PyJWKClient", FakeJwks)
    security._jwks_clients.clear()
    claims = {"sub": "abc", "email": "MANOJ@example.com", "iss": "https://login.example/", "aud": "docintel",
              "exp": int(time.time()) + 60}
    tok = jwt.encode(claims, key, algorithm="RS256")
    r = raw_client.get("/v1/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "usr_admin" and r.json()["role"] == "admin"
    bad_aud = jwt.encode({**claims, "aud": "other"}, key, algorithm="RS256")
    _problem(raw_client.get("/v1/me", headers={"Authorization": f"Bearer {bad_aud}"}), 401)
    stranger = jwt.encode({**claims, "email": "nobody@else.example", "sub": "zzz"}, key, algorithm="RS256")
    _problem(raw_client.get("/v1/me", headers={"Authorization": f"Bearer {stranger}"}), 403)
    security._jwks_clients.clear()


def test_sse_token_only_on_events_path(raw_client, make_key):
    secret = make_key(["runs:read"])
    sse = raw_client.get("/v1/me", headers={"X-API-Key": secret}).json()["sse_token"]
    r = raw_client.get(f"/v1/runs/run_missing/events?access_token={sse}")
    assert r.status_code != 401, r.text       # authenticated (404 / stream from BE2's route)
    _problem(raw_client.get(f"/v1/me?access_token={sse}"), 401)
    _problem(raw_client.get(f"/v1/connections?access_token={sse}"), 401)
    _problem(raw_client.get("/v1/runs/run_missing/events?access_token=sse_forged.sig"), 401)


def test_sse_token_expiry():
    from app.core.security import _verify_sse_token

    ctx = AuthContext(workspace_id="ws_default", kind="user", principal_id="usr_admin", role="admin")
    ok = _verify_sse_token(issue_sse_token(ctx))
    assert ok.workspace_id == "ws_default" and ok.role == "admin"
    with pytest.raises(ApiError) as e:
        _verify_sse_token(issue_sse_token(ctx, ttl_s=-1))
    assert e.value.status == 401


def test_dev_anonymous_admin(raw_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "dev_anonymous_admin", True, raising=False)
    r = raw_client.get("/v1/me")
    assert r.status_code == 200 and r.json()["role"] == "admin" and r.json()["id"] == "usr_admin"
    monkeypatch.setattr(get_settings(), "env", "production")
    _problem(raw_client.get("/v1/me"), 401)


# ── ingest HMAC helper (used by BE2) ──

def _req(headers: dict) -> Request:
    return Request({"type": "http", "method": "POST", "path": "/v1/ingest/pl_x", "query_string": b"",
                    "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]})


def test_verify_ingest_hmac():
    body = b'{"a":1}'
    ts = str(int(time.time()))
    sig = sign_payload("s3cret", ts, body)
    verify_ingest_hmac(_req({"X-DI-Timestamp": ts, "X-DI-Signature": sig}), body, "s3cret")
    verify_ingest_hmac(_req({"X-DI-Timestamp": ts, "X-DI-Signature": "sha256=" + sig}), body, "s3cret")
    for headers, code in [({"X-DI-Timestamp": ts, "X-DI-Signature": sig[:-1] + "0"}, "invalid_signature"),
                          ({"X-DI-Signature": sig}, "invalid_signature"),
                          ({"X-DI-Timestamp": str(int(time.time()) - 301),
                            "X-DI-Signature": sign_payload("s3cret", str(int(time.time()) - 301), body)},
                           "signature_expired")]:
        with pytest.raises(ApiError) as e:
            verify_ingest_hmac(_req(headers), body, "s3cret")
        assert e.value.status == 401 and e.value.code == code
    with pytest.raises(ApiError):
        verify_ingest_hmac(_req({"X-DI-Timestamp": ts, "X-DI-Signature": sig}), body + b" ", "s3cret")
