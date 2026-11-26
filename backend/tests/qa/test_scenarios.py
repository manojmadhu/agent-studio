"""QA functional scenarios (black-box, HTTP only, real auth path). IDs match docs/test-report.md § Test case catalogue.

Run: DI_TEST_DATABASE_URL=…/docintel_test_qa DI_RUN_STEP_DELAY_MS=0 pytest tests/qa -q
"""
from __future__ import annotations

import io
import json
import time
import uuid

import pytest

from app.config import get_settings
from tests.qa.conftest import ADMIN_KEY, Api, bearer, key_header

PROBLEM = "application/problem+json"
# Contract gaps already recorded in docs/test-report.md (C-01…): undocumented error statuses.
KNOWN_UNDOCUMENTED = {"not documented"}


def _pdf() -> bytes:
    return (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def _problem(r, status: int, code: str | None = None) -> dict:
    assert r.status_code == status, (r.status_code, r.text[:300])
    assert r.headers["content-type"].startswith(PROBLEM), r.headers["content-type"]
    b = r.json()
    assert b["status"] == status and b["title"] and b["type"]
    if code:
        assert b["code"] == code, b
    return b


def _parse_sse(text: str) -> list[dict]:
    out, cur = [], {}
    for line in text.replace("\r\n", "\n").split("\n"):
        if line == "":
            if "data" in cur:
                out.append({**cur, "json": json.loads(cur["data"])})
            cur = {}
        elif not line.startswith(":") and ":" in line:
            k, v = line.split(":", 1)
            cur[k] = v[1:] if v.startswith(" ") else v
    return out


def _stream(api: Api, run_id: str, **headers) -> list[dict]:
    with api.client.stream("GET", f"/v1/runs/{run_id}/events",
                           headers={"Accept": "text/event-stream", "X-API-Key": ADMIN_KEY, **headers}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        text = "".join(r.iter_text())
    return _parse_sse(text)


def _wait(api: Api, run_id: str, statuses: set[str], timeout: float = 20) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = api.get(f"/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.05)
    raise AssertionError(f"{run_id} never reached {statuses}: {run}")


def _draft(api: Api, pid: str) -> tuple[dict, str]:
    r = api.get(f"/pipelines/{pid}/draft")
    assert r.status_code == 200 and r.headers["etag"] == r.json()["etag"]
    return r.json(), r.headers["etag"]


def _walk(api: Api, path: str, limit: int, auth: dict | None = None) -> list[str]:
    ids, cursor, pages = [], None, 0
    while True:
        sep = "&" if "?" in path else "?"
        r = api.get(f"{path}{sep}limit={limit}" + (f"&cursor={cursor}" if cursor else ""), auth=auth)
        assert r.status_code == 200, r.text
        b = r.json()
        assert len(b["data"]) <= limit
        ids += [x["id"] for x in b["data"]]
        cursor = b.get("next_cursor")
        pages += 1
        assert pages < 200
        if not cursor:
            return ids


# ── QA-01 Builder lifecycle ────────────────────────────────────────────────────────────────────────────────────

def test_qa01_builder_lifecycle(api):
    r = api.post("/pipelines", json={"name": "QA lifecycle", "intake_type": "intake.email", "owner_team": "QA"})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert r.json()["status"] == "draft"

    d, etag = _draft(api, pid)
    # an invalid graph first: AI extract without a model / unconnected nodes → validate reports issues
    graph = {"nodes": [{"id": "n1", "type": "intake.email", "name": "Mail", "x": 0, "y": 0, "config": {}},
                       {"id": "n2", "type": "ai.extract", "name": "Extract", "x": 200, "y": 0, "config": {}}],
             "edges": []}
    r = api.put(f"/pipelines/{pid}/draft", json={"graph": graph, "change": "QA invalid"}, headers={"If-Match": etag})
    assert r.status_code == 200, r.text
    etag2 = r.headers["etag"]
    assert etag2 != etag and r.json()["etag"] == etag2

    # stale ETag → 412 etag_mismatch with the current etag; missing If-Match → 428
    b = _problem(api.put(f"/pipelines/{pid}/draft", json={"graph": graph}, headers={"If-Match": etag}), 412, "etag_mismatch")
    assert b["current_etag"] == etag2
    _problem(api.put(f"/pipelines/{pid}/draft", json={"graph": graph}), 428, "precondition_required")

    v = api.post(f"/pipelines/{pid}/validate", json={})
    assert v.status_code == 200 and v.json()["valid"] is False and v.json()["issues"]
    assert all({"level", "message"} <= set(i) for i in v.json()["issues"])
    # publishing / activating an invalid pipeline is refused
    _problem(api.post(f"/pipelines/{pid}/versions", json={"release_note": "bad", "draft_etag": etag2}), 422)
    _problem(api.post(f"/pipelines/{pid}/activate"), 422)

    # now a valid graph (the seeded AP graph) → valid → publish v1 → activate → pause
    good = _draft(api, "pl_ap")[0]["graph"]
    r = api.put(f"/pipelines/{pid}/draft", json={"graph": good, "change": "QA valid"}, headers={"If-Match": etag2})
    assert r.status_code == 200, r.text
    etag3 = r.headers["etag"]
    v = api.post(f"/pipelines/{pid}/validate", json={})
    assert v.json()["valid"] is True, v.json()
    pub = api.post(f"/pipelines/{pid}/versions", json={"release_note": "QA v1", "draft_etag": etag3})
    assert pub.status_code == 201, pub.text
    assert pub.json()["version"] == 1
    assert [x["version"] for x in api.get(f"/pipelines/{pid}/versions").json()["data"]] == [1]
    assert api.get(f"/pipelines/{pid}/versions/1").status_code == 200
    a = api.post(f"/pipelines/{pid}/activate")
    assert a.status_code == 200 and a.json()["status"] == "active"
    p = api.post(f"/pipelines/{pid}/pause")
    assert p.status_code == 200 and p.json()["status"] == "paused"
    assert api.get(f"/pipelines/{pid}").json()["status"] == "paused"
    assert not api.contract_problems(KNOWN_UNDOCUMENTED)


# ── QA-02 Test run over SSE ────────────────────────────────────────────────────────────────────────────────────

def test_qa02_test_run_sse_and_result(api):
    r = api.post("/pipelines/pl_ap/test-runs", json={"document_id": "doc_inv_sample"})
    assert r.status_code == 202 and r.json()["test"] is True, r.text
    run_id = r.json()["id"]
    evs = _stream(api, run_id)
    names = [e["json"]["event"] for e in evs]
    assert names[0] == "run.started" and names[-1] == "run.finished"
    assert [int(e["id"]) for e in evs] == list(range(1, len(evs) + 1))
    assert all(e["json"]["run_id"] == run_id and e["json"]["at"] for e in evs)
    assert names.count("step.started") == names.count("step.finished") + names.count("step.failed")
    final = evs[-1]["json"]["status"]
    assert final in {"completed", "review"}
    # resume from Last-Event-ID replays only the tail
    tail = _stream(api, run_id, **{"Last-Event-ID": str(len(evs) - 2)})
    assert [e["id"] for e in tail] == [evs[-2]["id"], evs[-1]["id"]]
    res = api.get(f"/runs/{run_id}/result")
    assert res.status_code == 200 and res.json()["fields"]
    assert api.get(f"/runs/{run_id}").json()["status"] == final
    # SSE without credentials → 401 problem
    _problem(api.client.get(f"/v1/runs/{run_id}/events"), 401)
    assert not api.contract_problems(KNOWN_UNDOCUMENTED)


# ── QA-03 Ingest (API key + HMAC) → review → claim → decision → completed ─────────────────────────────────────

def _multipart(fields) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    out = io.BytesIO()
    for name, val in fields:
        out.write(f"--{boundary}\r\n".encode())
        if isinstance(val, tuple):
            fn, data, mime = val
            out.write(f'Content-Disposition: form-data; name="{name}"; filename="{fn}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
            out.write(data)
        else:
            out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{val}'.encode())
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def _route_ap_to_review(api: Api) -> None:
    d, etag = _draft(api, "pl_ap")
    for n in d["graph"]["nodes"]:
        if n["type"] == "route.confidence":
            n["config"]["threshold"] = 0.9
    r = api.put("/pipelines/pl_ap/draft", json={"graph": d["graph"], "change": "QA gate 0.9"}, headers={"If-Match": etag})
    assert r.status_code == 200
    assert api.post("/pipelines/pl_ap/versions", json={"release_note": "QA gate", "draft_etag": r.headers["etag"]}).status_code == 201


def test_qa03_ingest_hmac_review_claim_decision(api, db):
    _route_ap_to_review(api)
    k = api.post("/api-keys", json={"name": "QA ingest", "scopes": ["ingest:write", "runs:read"]})
    assert k.status_code == 201
    ingest_key = k.json()["secret"]

    # API-key ingest
    r = api.post("/ingest/pl_ap", auth=key_header(ingest_key),
                 files={"file": ("INV-QA-1.pdf", _pdf(), "application/pdf")}, data={"metadata": '{"src": "qa"}'})
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]
    assert r.json()["test"] is False

    # HMAC ingest: the pipeline's intake secret is provisioned out of band (no API exposes it, see C-07)
    from app.db.models import Pipeline
    from app.routers.ingest import ingest_secret

    secret = ingest_secret(db, db.get(Pipeline, "pl_ap"))
    import hashlib
    import hmac

    body, ctype = _multipart([("file", ("INV-QA-2.pdf", _pdf(), "application/pdf"))])
    ts = str(int(time.time()))
    sig = "sha256=" + hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    ok = api.post("/ingest/pl_ap", auth={}, content=body,
                  headers={"Content-Type": ctype, "X-DI-Timestamp": ts, "X-DI-Signature": sig})
    assert ok.status_code == 202, ok.text
    _problem(api.post("/ingest/pl_ap", auth={}, content=body, headers={
        "Content-Type": ctype, "X-DI-Timestamp": ts, "X-DI-Signature": "sha256=" + "0" * 64}), 401, "invalid_signature")
    old = str(int(time.time()) - 600)
    old_sig = "sha256=" + hmac.new(secret.encode(), old.encode() + b"." + body, hashlib.sha256).hexdigest()
    _problem(api.post("/ingest/pl_ap", auth={}, content=body,
                      headers={"Content-Type": ctype, "X-DI-Timestamp": old, "X-DI-Signature": old_sig}), 401, "signature_expired")
    _problem(api.post("/ingest/pl_ap", auth={}, content=body, headers={"Content-Type": ctype}), 401)
    # a key without ingest:write → 403; a paused pipeline → 409
    ro = api.post("/api-keys", json={"name": "QA ro", "scopes": ["runs:read"]}).json()["secret"]
    _problem(api.post("/ingest/pl_ap", auth=key_header(ro), files={"file": ("a.pdf", _pdf(), "application/pdf")}), 403)
    _problem(api.post("/ingest/pl_kyc", auth=key_header(ingest_key), files={"file": ("a.pdf", _pdf(), "application/pdf")}), 409)

    # the API-key run pauses at Human review
    assert _wait(api, run_id, {"review"})["status"] == "review"
    deadline = time.monotonic() + 10
    item = None
    while item is None and time.monotonic() < deadline:
        item = next((i for i in api.get("/review-items").json()["data"] if i["run_id"] == run_id), None)
        time.sleep(0.05)
    assert item and item["status"] == "open"

    rev_a, rev_b = bearer("reviewer", "usr_qa_rev_a", name="Rev A"), bearer("reviewer", "usr_qa_rev_b", name="Rev B")
    c = api.post(f"/review-items/{item['id']}/claim", auth=rev_a)
    assert c.status_code == 200 and c.json()["status"] == "claimed", c.text
    _problem(api.post(f"/review-items/{item['id']}/claim", auth=rev_b), 409)
    d = api.post(f"/review-items/{item['id']}/decision", auth=rev_a,
                 json={"action": "approve", "corrections": {"total_amount": "13020.00"}, "comment": "QA ok"})
    assert d.status_code == 200 and d.json()["status"] == "approved", d.text
    _problem(api.post(f"/review-items/{item['id']}/decision", auth=rev_a, json={"action": "reject"}), 409)
    assert _wait(api, run_id, {"completed", "failed"})["status"] == "completed"
    res = api.get(f"/runs/{run_id}/result").json()
    assert next(f for f in res["fields"] if f["name"] == "total_amount")["value"] == 13020
    assert not api.contract_problems(KNOWN_UNDOCUMENTED)


# ── QA-04 Connection secrets, rotation, 409 usage, force delete ─────────────────────────────────────────────────

def test_qa04_connection_secrets_never_returned(api):
    s1, s2 = "sk-QA-" + uuid.uuid4().hex, "sk-QA-" + uuid.uuid4().hex
    r = api.post("/connections", json={"name": "QA OpenAI", "service": "openai",
                                       "auth": {"type": "api_key", "key": s1, "name": "Authorization", "prefix": "Bearer "}})
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["secret_hint"] == "••••" + s1[-4:]
    assert "auth" not in r.json() or "key" not in (r.json().get("auth") or {})
    api.get(f"/connections/{cid}")
    api.get("/connections?limit=200")
    assert api.post(f"/connections/{cid}/test").status_code == 200
    rot = api.patch(f"/connections/{cid}", json={"auth": {"type": "api_key", "key": s2}})
    assert rot.status_code == 200 and rot.json()["secret_hint"] == "••••" + s2[-4:]
    pr = api.post("/providers", json={"kind": "openai", "name": "QA OpenAI provider", "connection_id": cid})
    assert pr.status_code == 201, pr.text
    use = api.get(f"/connections/{cid}/usage").json()["data"]
    assert any(u["resource_id"] == pr.json()["id"] for u in use)
    b = _problem(api.delete(f"/connections/{cid}"), 409, "connection_in_use")
    assert any(u["resource_id"] == pr.json()["id"] for u in b["usages"])
    assert api.delete(f"/connections/{cid}?force=true").status_code == 204
    _problem(api.get(f"/connections/{cid}"), 404)
    assert api.get(f"/providers/{pr.json()['id']}").json()["connection_id"] in (None, "")
    # I-1 regression: a vector-store connection used by knowledge collections can be force-deleted too
    _problem(api.delete("/connections/con_qdrant"), 409, "connection_in_use")
    assert api.delete("/connections/con_qdrant?force=true").status_code == 204
    kcs = [k for k in api.get("/knowledge-collections").json()["data"]]
    assert kcs and all(k.get("connection_id") != "con_qdrant" for k in kcs)
    api.get("/audit-events?limit=200")
    for seen in api.seen:
        blob = seen.text + json.dumps(seen.headers)
        assert s1 not in blob and s2 not in blob, f"secret leaked in {seen.method} {seen.path}"
    assert not api.contract_problems(KNOWN_UNDOCUMENTED)


# ── QA-05 API keys ─────────────────────────────────────────────────────────────────────────────────────────────

def test_qa05_api_key_create_rotate_revoke(api):
    r = api.post("/api-keys", json={"name": "QA key", "scopes": ["pipelines:read"]})
    assert r.status_code == 201, r.text
    kid, s1 = r.json()["id"], r.json()["secret"]
    assert r.json()["prefix"] and s1.startswith(r.json()["prefix"])
    listed = [k for k in api.get("/api-keys").json()["data"] if k["id"] == kid]
    assert listed and "secret" not in listed[0]
    assert api.get("/pipelines", auth=key_header(s1)).status_code == 200
    _problem(api.post("/pipelines", auth=key_header(s1), json={"name": "x", "intake_type": "intake.email"}), 403)
    rot = api.post(f"/api-keys/{kid}/rotate", json={"grace_period_hours": 0})
    assert rot.status_code == 200, rot.text
    s2 = rot.json()["secret"]
    assert s2 != s1
    assert api.get("/pipelines", auth=key_header(s2)).status_code == 200
    _problem(api.get("/pipelines", auth=key_header(s1)), 401)
    # rotation with a grace period keeps the old secret valid
    rot2 = api.post(f"/api-keys/{kid}/rotate", json={"grace_period_hours": 1}).json()["secret"]
    assert api.get("/pipelines", auth=key_header(s2)).status_code == 200
    assert api.delete(f"/api-keys/{kid}").status_code == 204
    _problem(api.get("/pipelines", auth=key_header(rot2)), 401)
    _problem(api.get("/pipelines", auth=key_header(s2)), 401)
    _problem(api.get("/me", auth=key_header("dik_test_nope")), 401)
    assert not api.contract_problems(KNOWN_UNDOCUMENTED)


# ── QA-06 Roles ────────────────────────────────────────────────────────────────────────────────────────────────

def test_qa06_roles_viewer_and_reviewer(api):
    viewer, reviewer = bearer("viewer"), bearer("reviewer")
    assert api.get("/pipelines", auth=viewer).status_code == 200
    assert api.get("/me", auth=viewer).json()["role"] == "viewer"
    _problem(api.post("/pipelines", auth=viewer, json={"name": "v", "intake_type": "intake.email"}), 403)
    _problem(api.patch("/pipelines/pl_ap", auth=viewer, json={"name": "v"}), 403)
    _problem(api.post("/connections", auth=viewer, json={"name": "v", "service": "redis", "auth": {"type": "none"}}), 403)
    _problem(api.post("/review-items/rv2/claim", auth=viewer), 403)
    _problem(api.post("/review-items/rv2/decision", auth=viewer, json={"action": "approve"}), 403)
    _problem(api.post("/api-keys", auth=viewer, json={"name": "v", "scopes": ["admin"]}), 403)

    etag = _draft(api, "pl_ap")[1]
    _problem(api.put("/pipelines/pl_ap/draft", auth=reviewer, json={"graph": {"nodes": [], "edges": []}},
                     headers={"If-Match": etag}), 403)
    _problem(api.patch("/pipelines/pl_ap", auth=reviewer, json={"name": "r"}), 403)
    _problem(api.post("/pipelines/pl_ap/versions", auth=reviewer, json={"release_note": "r"}), 403)
    _problem(api.delete("/pipelines/pl_xls", auth=reviewer), 403)
    d = api.post("/review-items/rv2/decision", auth=reviewer, json={"action": "approve", "corrections": {"total_amount": "13020"}})
    assert d.status_code == 200 and d.json()["status"] == "approved", d.text
    assert not api.contract_problems(KNOWN_UNDOCUMENTED)


# ── QA-07 Workspace isolation ─────────────────────────────────────────────────────────────────────────────────

def test_qa07_workspace_isolation(api):
    mine = api.post("/pipelines", json={"name": "QA isolated", "intake_type": "intake.email"}).json()["id"]
    other = bearer("admin", "usr_qa_other", ws="ws_qa_other")
    for path in (f"/pipelines/{mine}", "/pipelines/pl_ap", "/pipelines/pl_ap/draft", "/pipelines/pl_ap/versions",
                 "/runs/run_48210", "/runs/run_48210/result", "/runs/run_48210/events", "/review-items/rv1",
                 "/connections/con_s3", "/connections/con_s3/usage", "/providers/prv_openai", "/workers/wrk_paddle",
                 "/mcp-servers/mcp_vendors", "/skills/skl_invoice", "/documents/doc_rcp_sample/pages/1/image?redacted=true"):
        r = api.get(path, auth=other)
        if path.startswith("/documents"):
            assert r.status_code in (404, 200)  # sample documents are virtual in every workspace (BE2 decision)
            continue
        _problem(r, 404)
    _problem(api.patch("/pipelines/pl_ap", auth=other, json={"name": "hijack"}), 404)
    _problem(api.delete("/connections/con_pg?force=true", auth=other), 404)
    _problem(api.post("/review-items/rv2/claim", auth=other), 404)
    _problem(api.post("/pipelines/pl_ap/test-runs", auth=other, json={"document_id": "doc_inv_sample"}), 404)
    _problem(api.post("/ingest/pl_ap", auth=other, files={"file": ("a.pdf", _pdf(), "application/pdf")}), 404)
    for lst in ("/pipelines?limit=200", "/runs?limit=200", "/connections?limit=200", "/review-items", "/providers"):
        ids = {x["id"] for x in api.get(lst, auth=other).json()["data"]}
        assert not ids & {mine, "pl_ap", "run_48210", "con_s3", "rv1", "prv_openai"}, (lst, ids)
    assert api.get("/pipelines/pl_ap").json()["name"] != "hijack"
    assert not api.contract_problems(KNOWN_UNDOCUMENTED)


# ── QA-08 Pagination ───────────────────────────────────────────────────────────────────────────────────────────

def test_qa08_pagination_walks_without_duplicates(api):
    for i in range(7):
        assert api.post("/pipelines", json={"name": f"QA page {i}", "intake_type": "intake.email"}).status_code == 201
    for path, limit in (("/pipelines", 2), ("/runs", 3), ("/connections", 4), ("/audit-events", 3), ("/api-keys", 1)):
        full = [x["id"] for x in api.get(f"{path}?limit=200").json()["data"]]
        walked = _walk(api, path, limit)
        assert len(walked) == len(set(walked)), f"duplicates in {path}"
        assert walked == full, path
    for path in ("/runs", "/connections", "/audit-events", "/api-keys"):  # keyset lists; pipelines/review: D-06
        _problem(api.get(f"{path}?limit=2&cursor=not-a-cursor"), 400, "invalid_cursor")
    _problem(api.get("/pipelines?limit=0"), 422)
    assert not api.contract_problems(KNOWN_UNDOCUMENTED)


# ── QA-09 Idempotency ─────────────────────────────────────────────────────────────────────────────────────────

def test_qa09_idempotency_replay(api):
    key = "qa-" + uuid.uuid4().hex
    body = {"name": "QA idem", "intake_type": "intake.email"}
    a = api.post("/pipelines", json=body, headers={"Idempotency-Key": key})
    b = api.post("/pipelines", json=body, headers={"Idempotency-Key": key})
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] == b.json()["id"] and b.headers.get("idempotency-replayed") == "true"
    _problem(api.post("/pipelines", json={**body, "name": "other"}, headers={"Idempotency-Key": key}), 422,
             "idempotency_key_reused")
    names = [p["name"] for p in api.get("/pipelines?limit=200").json()["data"]]
    assert names.count("QA idem") == 1
    # the key is scoped by credential: another principal re-using it creates its own resource
    c = api.post("/pipelines", json=body, headers={"Idempotency-Key": key}, auth=bearer("builder"))
    assert c.status_code == 201 and c.json()["id"] != a.json()["id"]
    assert not api.contract_problems(KNOWN_UNDOCUMENTED)


# ── QA-10 Upload limit ────────────────────────────────────────────────────────────────────────────────────────

def test_qa10_upload_limit_413(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    big = b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 1)
    _problem(api.post("/documents", files={"file": ("big.pdf", big, "application/pdf")}, data={"purpose": "test"}),
             413, "file_too_large")
    ok = api.post("/documents", files={"file": ("small.pdf", _pdf(), "application/pdf")}, data={"purpose": "test"})
    assert ok.status_code == 201, ok.text
    assert api.get(f"/documents/{ok.json()['id']}").status_code == 200
    assert not api.contract_problems(KNOWN_UNDOCUMENTED)


# ── QA-11 CORS ────────────────────────────────────────────────────────────────────────────────────────────────

def test_qa11_cors_preflight_and_expose_etag(api):
    origin = "http://localhost:5173"
    pre = api.client.options("/v1/pipelines/pl_ap/draft", headers={
        "Origin": origin, "Access-Control-Request-Method": "PUT",
        "Access-Control-Request-Headers": "if-match,content-type,x-api-key,idempotency-key"})
    assert pre.status_code == 200, pre.text
    assert pre.headers["access-control-allow-origin"] == origin
    assert "PUT" in pre.headers["access-control-allow-methods"]
    allowed = pre.headers["access-control-allow-headers"].lower()
    assert all(h in allowed for h in ("if-match", "content-type", "x-api-key", "idempotency-key"))
    r = api.get("/pipelines/pl_ap/draft", headers={"Origin": origin})
    assert r.headers["access-control-allow-origin"] == origin
    assert "etag" in r.headers["access-control-expose-headers"].lower()
    bad = api.client.options("/v1/pipelines", headers={"Origin": "https://evil.example",
                                                        "Access-Control-Request-Method": "POST"})
    assert bad.headers.get("access-control-allow-origin") != "https://evil.example"
