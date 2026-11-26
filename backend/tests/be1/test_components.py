"""Providers, workers, MCP servers, knowledge, skills, policies, identity and ops endpoints."""
from __future__ import annotations

import json

import httpx
from sqlalchemy import select

from app.core.security import sign_payload
from app.db.models import AuditEvent


def _actions(db, rid):
    return [e.action for e in db.scalars(select(AuditEvent).where(AuditEvent.resource_id == rid)).all()]


# ── providers ──

def test_provider_crud_and_409(client, db):
    r = client.post("/v1/providers", json={"kind": "openai_compatible", "name": "vLLM", "base_url": "http://vllm:8000/",
                                           "data_region": "On-premises", "models": [{"id": "llama-4", "enabled": True}]})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["enabled"] and p["default_model"] == "llama-4" and p["base_url"] == "http://vllm:8000"
    assert p["models"][0]["id"] == "llama-4" and p["usage_count"] == 0
    # portal PATCH shapes: "" and null
    u = client.patch(f"/v1/providers/{p['id']}", json={"name": "vLLM 2", "base_url": "", "data_region": "",
                                                       "connection_id": None, "default_model": "",
                                                       "models": [{"id": "a"}, {"id": "b", "embedding": True}]})
    assert u.status_code == 200, u.text
    assert u.json()["name"] == "vLLM 2" and "base_url" not in u.json() and [m["id"] for m in u.json()["models"]] == ["a", "b"]
    assert client.patch(f"/v1/providers/{p['id']}", json={"enabled": False}).json()["enabled"] is False
    assert client.post("/v1/providers", json={"name": "x"}).json()["code"] == "validation"
    assert client.post("/v1/providers", json={"kind": "openai", "name": "x", "connection_id": "con_nope"}).status_code == 422
    assert client.delete(f"/v1/providers/{p['id']}").status_code == 204
    assert {"provider.created", "provider.updated", "provider.deleted"} <= set(_actions(db, p["id"]))
    busy = client.delete("/v1/providers/prv_anthropic")
    assert busy.status_code == 409 and busy.json()["code"] == "provider_in_use"
    assert busy.json()["detail"] == "Used by 2 language model components." and len(busy.json()["usages"]) == 2
    assert client.get("/v1/providers/prv_nope").json()["title"] == "Provider not found"


def test_provider_test_and_sync_simulated(client):
    t = client.post("/v1/providers/prv_anthropic/test", json={"model": "claude-opus-5-5"}).json()
    assert t["ok"] and t["message"] == "claude-opus-5-5 answered a test prompt (simulated)"
    assert client.post("/v1/providers/prv_ollama/test").json()["ok"] is False
    s = client.post("/v1/providers/prv_anthropic/models/sync")
    assert s.status_code == 200 and any(m["id"] == "claude-opus-5-5" for m in s.json()["models"])
    o = client.post("/v1/providers/prv_ollama/models/sync")
    assert o.status_code == 502 and o.json()["code"] == "provider_unreachable"


def test_provider_live(client, live_mode, net):
    net.json("POST", "api.anthropic.com/v1/messages", {"content": [{"type": "text", "text": "OK"}],
                                                       "usage": {"input_tokens": 5, "output_tokens": 1}})
    t = client.post("/v1/providers/prv_anthropic/test", json={}).json()
    assert t["ok"] is True, t
    req = net.requests[-1]
    assert req.headers["anthropic-version"] and json.loads(req.content)["model"] == "claude-sonnet-5-5"
    net.json("GET", "api.anthropic.com/v1/models", {"data": [{"id": "claude-new", "display_name": "Claude New"}]})
    s = client.post("/v1/providers/prv_anthropic/models/sync").json()
    ids = [m["id"] for m in s["models"]]
    assert "claude-new" in ids and "claude-sonnet-5-5" in ids
    net.json("GET", "api.anthropic.com/v1/models", {"error": "x"}, status=500)
    bad = client.post("/v1/providers/prv_anthropic/models/sync")
    assert bad.status_code == 502 and bad.json()["code"] == "provider_unreachable"
    # no network at all → graceful ok:false
    t2 = client.post("/v1/providers/prv_openai/test").json()
    assert t2["ok"] is False and t2["message"]


# ── workers ──

def test_worker_crud(client, db):
    r = client.post("/v1/workers", json={"name": "Excel", "kind": "spreadsheet", "base_url": "http://excel:8000",
                                         "adapter": "excel_parser_v1"})
    assert r.status_code == 201, r.text
    w = r.json()
    assert w["invoke_path"] == "/to-text" and w["health_path"] == "/health" and w["health"] == {"status": "unknown"}
    assert w["timeout_s"] == 60 and w["max_concurrency"] == 4
    u = client.patch(f"/v1/workers/{w['id']}", json={"name": "Excel 2", "connection_id": None, "timeout_s": 30,
                                                     "invoke_path": "/convert"})
    assert u.json()["name"] == "Excel 2" and u.json()["timeout_s"] == 30 and u.json()["invoke_path"] == "/convert"
    for bad in ({"name": "x"}, {"name": "x", "kind": "ocr", "base_url": "ftp://x", "adapter": "generic_pages_v1"},
                {"name": "x", "kind": "ocr", "base_url": "http://x", "adapter": "nope"}):
        assert client.post("/v1/workers", json=bad).json()["code"] == "validation"
    assert client.get("/v1/workers?kind=spreadsheet").json()["data"][-1]["id"] == w["id"]
    assert client.delete(f"/v1/workers/{w['id']}").status_code == 204
    busy = client.delete("/v1/workers/wrk_paddle")
    assert busy.status_code == 409 and busy.json()["code"] == "worker_in_use" and busy.json()["usages"]
    assert "worker.registered" in _actions(db, w["id"])


def test_worker_health_invoke_simulated(client):
    assert client.post("/v1/workers/wrk_paddle/health").json()["status"] == "healthy"
    assert client.post("/v1/workers/wrk_enhance/health").json()["status"] == "unreachable"
    inv = client.post("/v1/workers/wrk_paddle/invoke", json={"document_id": "doc_inv_sample"})
    assert inv.status_code == 200 and inv.json()["pages"][0]["tables"]
    assert len(client.post("/v1/workers/wrk_excel/invoke", json={"document_id": "doc_inv_sample"}).json()["pages"]) == 2
    down = client.post("/v1/workers/wrk_enhance/invoke", json={"document_id": "doc_inv_sample"})
    assert down.status_code == 502 and down.json()["code"] == "worker_unreachable"
    assert client.post("/v1/workers/wrk_paddle/invoke", json={}).json()["code"] == "validation"
    assert client.post("/v1/workers/wrk_paddle/invoke", json={"document_id": "doc_nope"}).status_code == 404


def test_worker_live_paddle(client, live_mode, net):
    seen = {}

    def parse(req: httpx.Request) -> httpx.Response:
        seen["ct"] = req.headers["content-type"]
        seen["auth"] = req.headers.get("authorization")
        return httpx.Response(200, json={"result": {"layoutParsingResults": [{"prunedResult": {
            "width": 600, "height": 800, "parsing_res_list": [
                {"block_label": "text", "block_content": "INVOICE", "block_bbox": [1, 2, 3, 4]},
                {"block_label": "table", "block_content": "<table><tr><td>a</td><td>b</td></tr></table>",
                 "block_bbox": [0, 0, 10, 10]}]}}]}})

    net.add("POST", "localhost:8001/parse", parse)
    net.json("GET", "localhost:8001/health", {"status": "ok"})
    h = client.post("/v1/workers/wrk_paddle/health").json()
    assert h["status"] == "healthy", h
    r = client.post("/v1/workers/wrk_paddle/invoke", json={"document_id": "doc_inv_sample"})
    assert r.status_code == 200, r.text
    page = r.json()["pages"][0]
    assert page["blocks"][0]["text"] == "INVOICE" and page["tables"][0]["rows"] == [["a", "b"]]
    assert seen["ct"].startswith("multipart/form-data")
    net.add("POST", "localhost:8001/parse", lambda req: httpx.Response(500, text="boom"))
    r = client.post("/v1/workers/wrk_paddle/invoke", json={"document_id": "doc_inv_sample"})
    assert r.status_code == 502 and r.json()["code"] == "worker_error"


def test_worker_live_unreachable(client, live_mode, net):
    assert client.post("/v1/workers/wrk_excel/health").json()["status"] == "unreachable"
    r = client.post("/v1/workers/wrk_excel/invoke", json={"document_id": "doc_inv_sample"})
    assert r.status_code == 502 and r.json()["code"] == "worker_unreachable"


# ── MCP ──

def test_mcp_crud_tools_and_call_simulated(client, db):
    r = client.post("/v1/mcp-servers", json={"name": "Docs", "transport": "streamable_http", "url": "https://m/x"})
    assert r.status_code == 201 and r.json()["tools"] == [] and r.json()["status"] == "unknown"
    sid = r.json()["id"]
    assert client.post("/v1/mcp-servers", json={"name": "x", "transport": "sse", "url": "u",
                                                "headers": {"Authorization": "Bearer x"}}).json()["code"] == "validation"
    d = client.post(f"/v1/mcp-servers/{sid}/discover").json()
    assert [t["name"] for t in d["tools"]] == ["ping", "search"] and d["status"] == "connected"
    p = client.patch(f"/v1/mcp-servers/{sid}", json={"tools": [{"name": "search", "enabled": False}]}).json()
    assert {t["name"]: t["enabled"] for t in p["tools"]} == {"ping": True, "search": False}
    assert client.post(f"/v1/mcp-servers/{sid}/tools/search/call", json={"arguments": {"query": "x"}}).json()["code"] == "tool_disabled"
    assert client.post(f"/v1/mcp-servers/{sid}/tools/nope/call", json={}).status_code == 404
    ok = client.post("/v1/mcp-servers/mcp_vendors/tools/match_vendor/call", json={"arguments": {"trn": "100"}}).json()
    assert ok["is_error"] is False and "V-00418" in ok["content"][0]["text"]
    miss = client.post("/v1/mcp-servers/mcp_vendors/tools/match_po/call", json={"arguments": {"po_number": ""}})
    assert miss.status_code == 422 and miss.json()["detail"] == "Missing: po_number"
    wrong = client.post("/v1/mcp-servers/mcp_vendors/tools/match_po/call", json={"arguments": {"po_number": 5}})
    assert wrong.status_code == 422 and wrong.json()["code"] == "invalid_arguments"
    dis = client.post("/v1/mcp-servers/mcp_customers/tools/find_customer/call", json={"arguments": {"name": "a"}})
    assert dis.status_code == 409 and dis.json()["code"] == "mcp_disabled"
    busy = client.delete("/v1/mcp-servers/mcp_vendors")
    assert busy.status_code == 409 and busy.json()["code"] == "mcp_in_use" and busy.json()["usages"]
    assert client.delete(f"/v1/mcp-servers/{sid}").status_code == 204
    assert "mcp_server.discovered" in _actions(db, sid)


def test_mcp_live_streamable_http(client, live_mode, net):
    calls = []

    def rpc(req: httpx.Request) -> httpx.Response:
        msg = json.loads(req.content)
        calls.append((msg["method"], req.headers.get("authorization"), req.headers.get("mcp-session-id")))
        if "id" not in msg:
            return httpx.Response(202)
        if msg["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {"serverInfo": {"name": "v"}}},
                                  headers={"Mcp-Session-Id": "sess-1"})
        if msg["method"] == "tools/list":
            body = {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": [
                {"name": "lookup", "description": "Look up", "inputSchema": {"type": "object", "required": ["q"],
                                                                             "properties": {"q": {"type": "string"}}}}]}}
            return httpx.Response(200, text=f"event: message\ndata: {json.dumps(body)}\n\n",
                                  headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"], "result": {
            "content": [{"type": "text", "text": "found " + msg["params"]["arguments"]["q"]}], "isError": False}})

    net.add("POST", "mcp.internal/vendors", rpc)
    d = client.post("/v1/mcp-servers/mcp_vendors/discover")
    assert d.status_code == 200, d.text
    assert [t["name"] for t in d.json()["tools"]] == ["lookup"]
    assert calls[0][0] == "initialize" and calls[0][1] and calls[0][1].startswith("Bearer ")
    assert calls[-1][2] == "sess-1"
    out = client.post("/v1/mcp-servers/mcp_vendors/tools/lookup/call", json={"arguments": {"q": "TRN"}}).json()
    assert out["content"][0]["text"] == "found TRN" and out["is_error"] is False
    # server down → 502 and status failing
    r = client.post("/v1/mcp-servers/mcp_docs/discover")
    assert r.status_code == 502 and r.json()["code"] == "mcp_unreachable"
    assert client.get("/v1/mcp-servers/mcp_docs").json()["status"] == "failing"


# ── knowledge, skills, policies ──

def test_knowledge(client):
    bad = client.post("/v1/knowledge-collections", json={"name": "Bad Name"})
    assert bad.status_code == 422 and bad.json()["detail"] == "name must be snake_case"
    body = {"name": "contracts", "connection_id": "con_qdrant", "embedding_provider_id": "prv_openai",
            "embedding_model": "text-embedding-3-large"}
    assert client.post("/v1/knowledge-collections", json={**body, "connection_id": "con_pg"}).status_code == 422
    k = client.post("/v1/knowledge-collections", json=body)
    assert k.status_code == 201 and k.json()["status"] == "ready"
    assert client.post("/v1/knowledge-collections", json=body).status_code == 409
    a = client.post(f"/v1/knowledge-collections/{k.json()['id']}/documents", json={"document_ids": ["doc_inv_sample"]})
    assert a.status_code == 202 and a.content == b""
    lst = {c["name"]: c for c in client.get("/v1/knowledge-collections").json()["data"]}
    assert lst["contracts"]["document_count"] == 1 and lst["contracts"]["chunk_count"] >= 1
    assert client.post(f"/v1/knowledge-collections/{k.json()['id']}/documents", json={"document_ids": []}).status_code == 422
    assert client.post("/v1/knowledge-collections/kc_nope/documents", json={"document_ids": ["x"]}).status_code == 404


def test_skills(client, db):
    assert client.post("/v1/skills", json={"name": "x"}).json()["detail"] == "name and instructions are required"
    s = client.post("/v1/skills", json={"name": "Lease", "description": "d", "instructions": "v1"}).json()
    assert s["version"] == 1 and s["versions"][0]["release_note"] == "Created"
    v = client.post(f"/v1/skills/{s['id']}/versions", json={"instructions": "v2", "release_note": "better"})
    assert v.status_code == 201 and v.json()["version"] == 2 and v.json()["versions"][0]["version"] == 2
    assert v.json()["instructions"] == "v2"
    assert client.patch(f"/v1/skills/{s['id']}", json={"name": "Lease 2"}).json()["name"] == "Lease 2"
    assert client.get(f"/v1/skills/{s['id']}").json()["version"] == 2
    busy = client.delete("/v1/skills/skl_invoice")
    assert busy.status_code == 409 and busy.json()["code"] == "skill_in_use" and busy.json()["usages"]
    assert client.delete(f"/v1/skills/{s['id']}").status_code == 204
    assert {"skill.created", "skill.published", "skill.updated", "skill.deleted"} <= set(_actions(db, s["id"]))


def test_policies(client, auth_as):
    p = client.post("/v1/policies", json={"name": "Allow-list", "kind": "model_allowlist", "scope": {"all_pipelines": True},
                                          "config": {"models": ["gpt-5"]}})
    assert p.status_code == 201 and p.json()["enabled"] is True
    assert client.patch(f"/v1/policies/{p.json()['id']}", json={"enabled": False}).json()["enabled"] is False
    assert client.post("/v1/policies", json={"name": "x", "kind": "nope", "scope": {}}).status_code == 422
    assert client.delete(f"/v1/policies/{p.json()['id']}").status_code == 204
    auth_as(role="builder")
    assert client.get("/v1/policies").status_code == 200
    assert client.patch("/v1/policies/pol_pii", json={"enabled": False}).status_code == 403


# ── identity ──

def test_api_keys_and_members(client, db):
    k = client.post("/v1/api-keys", json={"name": "ERP", "scopes": ["ingest:write"], "expires_in_days": 30,
                                          "allowed_ips": ["10.0.0.0/8"]})
    assert k.status_code == 201 and k.json()["secret"].startswith("dik_") and k.json()["expires_at"]
    listed = client.get("/v1/api-keys").json()["data"]
    assert all("secret" not in x for x in listed) and k.json()["secret"] not in json.dumps(listed)
    assert client.post("/v1/api-keys", json={"name": "x", "scopes": ["root"]}).status_code == 422
    assert client.post("/v1/api-keys", json={"name": "x", "scopes": []}).status_code == 422
    assert client.post("/v1/api-keys", json={"name": "x", "scopes": ["admin"], "allowed_ips": ["nope"]}).status_code == 422
    m = client.post("/v1/members", json={"email": "New@Example.com", "role": "reviewer"})
    assert m.status_code == 201 and m.json()["status"] == "invited" and m.json()["email"] == "new@example.com"
    assert client.post("/v1/members", json={"email": "new@example.com", "role": "viewer"}).status_code == 409
    assert client.patch(f"/v1/members/{m.json()['id']}", json={"role": "builder"}).json()["role"] == "builder"
    assert client.patch(f"/v1/members/{m.json()['id']}", json={"role": "god"}).status_code == 422
    assert client.delete(f"/v1/members/{m.json()['id']}").status_code == 204
    for x in client.get("/v1/members").json()["data"]:
        if x["role"] == "admin" and x["id"] != "usr_admin":
            assert client.patch(f"/v1/members/{x['id']}", json={"role": "builder"}).status_code == 200
    last = client.patch("/v1/members/usr_admin", json={"role": "viewer"})
    assert last.status_code == 409 and last.json()["code"] == "last_admin"
    assert client.delete("/v1/members/usr_admin").status_code == 409
    assert {"member.invited", "member.role_changed", "member.removed"} <= set(_actions(db, m.json()["id"]))


def test_me_user(client):
    me = client.get("/v1/me").json()
    assert me["kind"] == "user" and me["role"] == "admin" and me["workspace"]["id"] == "ws_default"
    assert me["sse_token"].startswith("sse_") and "admin" in me["scopes"]


# ── ops ──

def test_metrics(client):
    for w in ("24h", "7d", "30d"):
        m = client.get(f"/v1/metrics/overview?window={w}").json()
        assert m["window"] == w and m["review_waiting"] == 4
        assert 0 <= m["straight_through_rate"] <= 1 and m["documents"] >= 0
    assert client.get("/v1/metrics/overview?window=1y").status_code == 422


def test_audit_events_filters_and_pagination(client):
    for i in range(3):
        client.post("/v1/policies", json={"name": f"p{i}", "kind": "custom", "scope": {}})
    client.post("/v1/skills", json={"name": "s", "instructions": "i"})
    all_ = client.get("/v1/audit-events?limit=200").json()["data"]
    assert len(all_) >= 4 and all_[0]["actor"] == {"kind": "user", "id": "usr_admin", "name": "Admin"}
    assert all_[0]["action"] == "skill.created" and all_[0]["at"].endswith("Z")
    pol = client.get("/v1/audit-events?resource_type=policy").json()["data"]
    assert len(pol) == 3 and all(e["resource_type"] == "policy" for e in pol)
    assert client.get("/v1/audit-events?actor_id=usr_nobody").json()["data"] == []
    p1 = client.get("/v1/audit-events?limit=2").json()
    p2 = client.get(f"/v1/audit-events?limit=2&cursor={p1['next_cursor']}").json()
    assert not {e["id"] for e in p1["data"]} & {e["id"] for e in p2["data"]}


def test_webhooks_and_delivery(client, db, net):
    from app.services import webhooks

    r = client.post("/v1/webhooks", json={"url": "https://hooks.test/di", "events": ["run.completed"]})
    assert r.status_code == 201
    secret = r.json()["signing_secret"]
    assert secret.startswith("whsec_")
    assert secret not in client.get("/v1/webhooks").text
    assert client.post("/v1/webhooks", json={"url": "ftp://x", "events": ["run.completed"]}).json()["code"] == "validation"
    assert client.post("/v1/webhooks", json={"url": "https://x", "events": ["nope"]}).status_code == 422
    got = {}

    def hook(req: httpx.Request) -> httpx.Response:
        got["sig"], got["ts"], got["body"] = req.headers["x-di-signature"], req.headers["x-di-timestamp"], req.content
        return httpx.Response(204)

    net.add("POST", "hooks.test/di", hook)
    old = webhooks.SYNC_DELIVERY
    webhooks.SYNC_DELIVERY = True
    try:
        ids = webhooks.emit(db, "ws_default", "run.completed", {"run_id": "run_1", "pipeline_id": "pl_ap"})
        assert len(ids) == 1
        db.commit()
    finally:
        webhooks.SYNC_DELIVERY = old
    assert got["sig"] == "sha256=" + sign_payload(secret, got["ts"], got["body"])
    assert json.loads(got["body"])["data"]["run_id"] == "run_1"
    from app.db.models import WebhookDelivery

    d = db.get(WebhookDelivery, ids[0])
    db.refresh(d)
    assert d.status == "succeeded" and d.attempt == 1
    assert client.delete(f"/v1/webhooks/{r.json()['id']}").status_code == 204
    assert client.delete(f"/v1/webhooks/{r.json()['id']}").status_code == 404


def test_settings(client, auth_as):
    s = client.get("/v1/settings").json()
    assert s["name"] and s["environment"] in ("sandbox", "production")
    full = {**s, "name": "Acme 2", "retention_days": 90, "allowed_regions": ["UAE North"],
            "sso": {"provider": "Okta", "issuer": "https://okta.example"}}
    u = client.patch("/v1/settings", json=full)
    assert u.status_code == 200, u.text
    assert u.json()["name"] == "Acme 2" and u.json()["retention_days"] == 90 and u.json()["sso"]["provider"] == "Okta"
    assert client.get("/v1/me").json()["workspace"]["name"] == "Acme 2"
    assert client.patch("/v1/settings", json={"environment": "staging"}).status_code == 422
    auth_as(role="viewer")
    assert client.get("/v1/settings").status_code == 200
    assert client.patch("/v1/settings", json={"name": "x"}).status_code == 403


def test_catalog(client):
    st = client.get("/v1/catalog/step-types").json()["data"]
    assert len(st) == 34 and {"type", "stage", "name", "fields"} <= set(st[0])
    sv = client.get("/v1/catalog/services").json()["data"]
    assert len(sv) == 22
