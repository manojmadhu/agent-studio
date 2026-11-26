"""Connections: write-only secrets, rotation, usage/409/force, pagination, test (simulated + live), OAuth."""
from __future__ import annotations

import json

import httpx
from sqlalchemy import select

from app.db.models import AuditEvent, Connection, Provider, Secret

SECRETS = {
    "api_key": ("anthropic", {"type": "api_key", "key": "sk-ant-SECRET-aaaa1111", "name": "x-api-key"}),
    "bearer": ("mcp", {"type": "bearer", "token": "tok-SECRET-bbbb2222"}),
    "basic": ("postgresql", {"type": "basic", "username": "u", "password": "pw-SECRET-cccc3333"}),
    "occ": ("http_api", {"type": "oauth2_client_credentials", "token_url": "https://idp.example/token",
                         "client_id": "cid", "client_secret": "cs-SECRET-dddd4444"}),
    "hmac": ("http_api", {"type": "hmac", "secret": "hm-SECRET-eeee5555"}),
    "aws": ("amazon_s3", {"type": "aws_sigv4", "access_key_id": "AKIA1", "secret_access_key": "aws-SECRET-ffff6666",
                          "session_token": "sess-SECRET-gggg7777", "region": "me-central-1"}),
    "mtls": ("document_worker", {"type": "mtls", "client_cert_pem": "CERT", "client_key_pem": "key-SECRET-hhhh8888"}),
    "azure": ("azure_openai", {"type": "azure_ad_client", "tenant_id": "t", "client_id": "c",
                               "client_secret": "az-SECRET-iiii9999"}),
}


def _create(client, name, service, auth, settings=None):
    return client.post("/v1/connections", json={"name": name, "service": service, "auth": auth,
                                                "settings": settings or {}})


def test_secrets_never_returned_anywhere(client, db):
    ids = []
    bodies = []
    for name, (service, auth) in SECRETS.items():
        r = _create(client, f"c-{name}", service, auth)
        assert r.status_code == 201, r.text
        c = r.json()
        ids.append(c["id"])
        bodies.append(r.text)
        primary = next(v for k, v in auth.items() if "SECRET" in str(v))
        assert c["secret_hint"] == "••••" + primary[-4:]
        assert c["status"] == "unverified" and "auth" not in c
    for cid in ids:
        for r in (client.get(f"/v1/connections/{cid}"), client.get(f"/v1/connections/{cid}/usage"),
                  client.post(f"/v1/connections/{cid}/test"),
                  client.patch(f"/v1/connections/{cid}", json={"name": "renamed"})):
            assert r.status_code == 200, r.text
            bodies.append(r.text)
    for path in ("/v1/connections?limit=200", "/v1/audit-events?limit=200", "/v1/me", "/v1/providers"):
        bodies.append(client.get(path).text)
    blob = "\n".join(bodies)
    assert "SECRET" not in blob
    # at rest: ciphertext only
    for s in db.scalars(select(Secret)).all():
        assert "SECRET" not in s.ciphertext
    for c in db.scalars(select(Connection)).all():
        assert "SECRET" not in json.dumps(c.settings or {}) and "SECRET" not in (c.secret_hint or "")[:-4]


def test_create_validation_codes(client):
    cases = [
        ({"service": "anthropic", "auth": {"type": "api_key", "key": "k"}}, "Give the connection a name"),
        ({"name": "x", "service": "nope", "auth": {"type": "none"}}, "Unknown service"),
        ({"name": "x", "service": "gmail", "auth": {"type": "api_key", "key": "k"}}, "Gmail supports"),
        ({"name": "x", "service": "anthropic", "auth": {"type": "api_key"}}, "auth.key is required"),
        ({"name": "x", "service": "anthropic", "auth": {"type": "api_key", "key": "k"},
          "settings": {"password": "p"}}, "must not contain secrets"),
    ]
    for body, msg in cases:
        r = client.post("/v1/connections", json=body)
        assert r.status_code == 422 and r.json()["code"] == "validation", r.text
        assert msg in r.json()["detail"]
    oauth = _create(client, "Mail", "gmail", {"type": "oauth2_authorization_code", "client_id": "c",
                                              "client_secret": "s"}).json()
    assert oauth["status"] == "pending_authorization"


def test_rotation(client, db):
    c = _create(client, "rot", "anthropic", {"type": "api_key", "key": "old-key-AAAA"}).json()
    r = client.patch(f"/v1/connections/{c['id']}", json={"auth": {"type": "api_key", "key": "new-key-BBBB"}})
    assert r.status_code == 200 and r.json()["secret_hint"] == "••••BBBB" and r.json()["status"] == "unverified"
    from app.services import secret_store

    row = db.get(Connection, c["id"])
    db.refresh(row)
    assert secret_store.get(db, row.secret_ref)["key"] == "new-key-BBBB"
    actions = [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.resource_id == c["id"])).all()]
    assert "connection.created" in actions and "connection.rotated" in actions
    bad = client.patch(f"/v1/connections/{c['id']}", json={"auth": {"type": "bearer", "token": "x"}})
    assert bad.status_code == 422 and "Anthropic supports" in bad.json()["detail"]
    # settings merge
    s = client.patch(f"/v1/connections/{c['id']}", json={"settings": {"account": "acme"}})
    assert s.json()["settings"]["account"] == "acme"


def test_delete_in_use_409_and_force(client, db):
    r = client.delete("/v1/connections/con_anthropic?force=false")
    assert r.status_code == 409
    b = r.json()
    assert b["code"] == "connection_in_use" and b["title"] == "Connection is in use"
    assert b["usages"] == [{"resource_type": "provider", "resource_id": "prv_anthropic",
                            "label": "Model provider · Anthropic"}]
    usage = client.get("/v1/connections/con_gmail/usage").json()["data"]
    assert {u["node_id"] for u in usage} >= {"ap_n1"} and all(u["resource_type"] == "pipeline" for u in usage)
    lst = {c["id"]: c for c in client.get("/v1/connections?limit=200").json()["data"]}
    assert lst["con_gmail"]["usage_count"] == len(usage) and lst["con_anthropic"]["usage_count"] == 1
    assert client.delete("/v1/connections/con_anthropic?force=true").status_code == 204
    db.expire_all()
    assert db.get(Connection, "con_anthropic") is None
    assert db.get(Provider, "prv_anthropic").connection_id is None
    assert client.get("/v1/connections/con_anthropic").status_code == 404
    # vector store used by knowledge collections: 409 without force; force unlinks them (status failed)
    r = client.delete("/v1/connections/con_qdrant")
    assert r.status_code == 409 and {u["resource_type"] for u in r.json()["usages"]} >= {"knowledge_collection"}
    assert client.delete("/v1/connections/con_qdrant?force=true").status_code == 204
    kcs = {k["id"]: k for k in client.get("/v1/knowledge-collections").json()["data"]}
    assert kcs["kc_vendor"]["connection_id"] == "" and kcs["kc_vendor"]["status"] == "failed"
    from app.db.models import KnowledgeCollection

    db.expire_all()
    assert db.get(KnowledgeCollection, "kc_vendor").connection_id is None
    assert db.get(Connection, "con_qdrant") is None


def test_unused_delete(client):
    c = _create(client, "tmp", "http_api", {"type": "none"}, {"base_url": "http://x"}).json()
    assert client.delete(f"/v1/connections/{c['id']}").status_code == 204
    assert client.delete(f"/v1/connections/{c['id']}").json()["title"] == "Connection not found"


def test_pagination(client):
    seen, cursor, pages = [], None, 0
    while True:
        q = "/v1/connections?limit=4" + (f"&cursor={cursor}" if cursor else "")
        page = client.get(q).json()
        seen += [c["id"] for c in page["data"]]
        pages += 1
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert pages >= 4 and len(seen) == len(set(seen)) == 13
    assert client.get("/v1/connections?cursor=garbage").status_code == 400
    assert client.get("/v1/connections?limit=0").status_code == 422
    only = client.get("/v1/connections?service=anthropic").json()["data"]
    assert [c["id"] for c in only] == ["con_anthropic"]


def test_test_simulated(client):
    ok = client.post("/v1/connections/con_anthropic/test").json()
    assert ok["ok"] is True and "simulated" in ok["message"]
    bad = client.post("/v1/connections/con_ollama/test").json()
    assert bad["ok"] is False
    lst = {c["id"]: c for c in client.get("/v1/connections?limit=200").json()["data"]}
    assert lst["con_anthropic"]["status"] == "connected" and lst["con_ollama"]["status"] == "failing"
    assert lst["con_anthropic"]["last_tested_at"]


def test_test_live_with_mock_transport(client, live_mode, net):
    c = _create(client, "Anthropic live", "anthropic", {"type": "api_key", "key": "sk-live-1234"}).json()
    net.json("GET", "api.anthropic.com/v1/models", {"data": [{"id": "claude-x"}]})
    r = client.post(f"/v1/connections/{c['id']}/test").json()
    assert r["ok"] is True and "1 models" in r["message"]
    assert net.requests[-1].headers["x-api-key"] == "sk-live-1234"
    # unreachable host → graceful ok:false, connection failing
    h = _create(client, "ERP", "http_api", {"type": "bearer", "token": "t"}, {"base_url": "https://erp.invalid"}).json()
    r = client.post(f"/v1/connections/{h['id']}/test")
    assert r.status_code == 200 and r.json()["ok"] is False and "ConnectError" in r.json()["message"]
    assert client.get(f"/v1/connections/{h['id']}").json()["status"] == "failing"
    net.json("GET", "erp.invalid/health", {"status": "ok"})
    assert client.post(f"/v1/connections/{h['id']}/test").json()["ok"] is True
    assert net.requests[-1].headers["authorization"] == "Bearer t"
    net.json("GET", "erp.invalid/health", {"error": "nope"}, status=401)
    assert client.post(f"/v1/connections/{h['id']}/test").json()["ok"] is False


def test_oauth_simulated(client):
    r = client.post("/v1/connections/con_gmail/oauth/authorize", json={})
    assert r.status_code == 200 and "accounts.example.com" in r.json()["authorization_url"]


def test_oauth_live_flow(client, live_mode, net, db):
    c = _create(client, "Mail", "gmail", {"type": "oauth2_authorization_code", "client_id": "cid",
                                          "client_secret": "csecret-ZZZZ"}).json()
    r = client.post(f"/v1/connections/{c['id']}/oauth/authorize", json={"redirect_after": "http://localhost:5173/x"})
    assert r.status_code == 200
    url = httpx.URL(r.json()["authorization_url"])
    assert url.host == "accounts.google.com" and url.params["client_id"] == "cid"
    assert url.params["code_challenge_method"] == "S256" and "csecret" not in str(url)
    state = r.json()["state"]
    net.json("POST", "oauth2.googleapis.com/token", {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600})
    cb = client.get(f"/v1/oauth/callback?code=abc&state={state}", follow_redirects=False)
    assert cb.status_code == 302 and cb.headers["location"].startswith("http://localhost:5173/x?oauth=connected")
    form = dict(httpx.QueryParams(net.requests[-1].content.decode()))
    assert form["code"] == "abc" and form["code_verifier"] and form["client_secret"] == "csecret-ZZZZ"
    got = client.get(f"/v1/connections/{c['id']}").json()
    assert got["status"] == "connected" and "at-1" not in json.dumps(got)
    from app.services import secret_store

    row = db.get(Connection, c["id"])
    db.refresh(row)
    assert secret_store.get(db, row.secret_ref)["refresh_token"] == "rt-1"
    # state is single-use
    again = client.get(f"/v1/oauth/callback?code=abc&state={state}", follow_redirects=False)
    assert "oauth=error" in again.headers["location"]


def test_oauth_live_exchange_failure(client, live_mode, net):
    c = _create(client, "Mail2", "gmail", {"type": "oauth2_authorization_code", "client_id": "cid",
                                           "client_secret": "s"}).json()
    state = client.post(f"/v1/connections/{c['id']}/oauth/authorize", json={}).json()["state"]
    cb = client.get(f"/v1/oauth/callback?code=abc&state={state}", follow_redirects=False)
    assert cb.status_code == 302 and "oauth=error" in cb.headers["location"]
    assert client.get(f"/v1/connections/{c['id']}").json()["status"] == "pending_authorization"


def test_viewer_cannot_write(client, auth_as):
    auth_as(role="viewer")
    assert client.get("/v1/connections").status_code == 200
    r = client.post("/v1/connections", json={"name": "x", "service": "ollama", "auth": {"type": "none"}})
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    assert client.delete("/v1/connections/con_pg").status_code == 403


def test_workspace_isolation(client, auth_as):
    auth_as(role="admin", workspace_id="ws_other")
    assert client.get("/v1/connections").json()["data"] == []
    assert client.get("/v1/connections/con_anthropic").status_code == 404
    assert client.get("/v1/providers").json()["data"] == []
    assert client.delete("/v1/workers/wrk_paddle").status_code == 404
