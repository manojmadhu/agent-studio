"""Documents (upload, page image), review (claim/decision resuming a paused run), ingest (API key + HMAC),
cross-workspace isolation."""
from __future__ import annotations

import hashlib
import io
import json
import time

import pytest

from app.config import get_settings
from app.services import storage
from tests.be2.helpers import golden, set_threshold, stream_events, wait_status


def _pdf() -> bytes:
    return storage._minimal_pdf(["TAX INVOICE", "INV-9", "Total: 100"])


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="PNG")
    return buf.getvalue()


def _upload(client, name: str, data: bytes, mime: str):
    return client.post("/v1/documents", files={"file": (name, data, mime)}, data={"purpose": "test"})


# ── documents ──────────────────────────────────────────────────────────────

def test_upload_get_page_image_delete(client):
    data = _pdf()
    r = _upload(client, "INV-9.pdf", data, "application/pdf")
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["sha256"] == hashlib.sha256(data).hexdigest() and d["size_bytes"] == len(data)
    assert d["mime_type"] == "application/pdf" and d["pages"] == 1 and d["id"].startswith("doc_")
    assert client.get(f"/v1/documents/{d['id']}").json() == d
    img = client.get(f"/v1/documents/{d['id']}/pages/1/image")
    assert img.status_code == 200 and img.headers["content-type"] == "image/png" and img.content[:4] == b"\x89PNG"
    assert img.headers["x-di-redacted"] == "false" and img.headers["x-di-redaction-count"] == "0"
    assert client.get(f"/v1/documents/{d['id']}/pages/2/image").status_code == 404
    assert client.delete(f"/v1/documents/{d['id']}").status_code == 204
    assert client.get(f"/v1/documents/{d['id']}").status_code == 404


def test_image_page_and_sample_documents(client, auth_as):
    r = _upload(client, "scan.png", _png(), "image/png")
    img = client.get(f"/v1/documents/{r.json()['id']}/pages/1/image")
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    s = client.get("/v1/documents/doc_inv_sample").json()
    assert (s["filename"], s["mime_type"], s["pages"]) == ("INV-2026-0417.pdf", "application/pdf", 1)
    assert client.get("/v1/documents/doc_rcp_sample/pages/1/image").status_code == 200
    assert client.get("/v1/documents/doc_inv_sample/pages/1/image").status_code == 200
    assert client.delete("/v1/documents/doc_inv_sample").status_code == 409
    auth_as(role="viewer")
    assert client.get("/v1/documents/doc_inv_sample/pages/1/image", params={"redacted": "false"}).status_code == 403


def test_upload_limits(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    r = _upload(client, "big.pdf", b"%PDF" + b"0" * (1024 * 1024 + 10), "application/pdf")
    assert r.status_code == 413 and r.json()["code"] == "file_too_large"
    assert client.post("/v1/documents", data={"purpose": "test"}, files={"x": ("a", b"1")}).status_code == 422
    assert client.post("/v1/documents", json={}).status_code == 415


def test_read_bytes_helper_for_workers(db):
    data, mime, name = storage.read_bytes(db, "ws_default", "doc_inv_sample")
    assert data.startswith(b"%PDF") and mime == "application/pdf" and name == "INV-2026-0417.pdf"
    data, mime, _ = storage.read_bytes(db, "ws_default", "doc_rcp_sample")
    assert data[:3] == b"\xff\xd8\xff" and mime == "image/jpeg"
    with pytest.raises(Exception):
        storage.read_bytes(db, "ws_other", "doc_rv1")


# ── review ─────────────────────────────────────────────────────────────────

def test_review_list_get_claim_conflict(client):
    items = client.get("/v1/review-items").json()["data"]
    assert sorted(i["id"] for i in items) == ["rv1", "rv2", "rv3", "rv4"]
    gold = {i["id"]: i for i in golden("GET /review-items")["body"]["data"]}
    for i in items:
        assert {k: v for k, v in i.items() if k not in ("sla_due_at", "created_at", "document")} == \
            {k: v for k, v in gold[i["id"]].items() if k not in ("sla_due_at", "created_at", "document")}
        assert i["document"]["filename"] == gold[i["id"]]["document"]["filename"]
    det = client.get("/v1/review-items/rv1").json()
    assert det["result"]["fields"] == golden("GET /review-items/rv1")["body"]["result"]["fields"]
    b = client.post("/v1/review-items/rv1/claim").json()
    assert b["code"] == "already_claimed" and b["detail"] == golden("POST /review-items/rv1/claim")["body"]["detail"]
    ok = client.post("/v1/review-items/rv2/claim")
    assert ok.status_code == 200 and ok.json()["status"] == "claimed" and ok.json()["assignee"] == "Admin"
    assert [i["id"] for i in client.get("/v1/review-items", params={"assignee": "me"}).json()["data"]] == ["rv2"]
    assert [i["id"] for i in client.get("/v1/review-items", params={"assignee": "unassigned"}).json()["data"]] == ["rv4"]
    assert {i["id"] for i in client.get("/v1/review-items", params={"queue": "Expenses"}).json()["data"]} == {"rv3"}


def test_seeded_review_decision_like_mock(client):
    r = client.post("/v1/review-items/rv2/decision", json={"action": "approve", "corrections": {"total_amount": "13020"}})
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["document"]["filename"]
    assert client.get("/v1/runs/run_48204").json()["status"] == "completed"
    f = {x["name"]: x for x in client.get("/v1/runs/run_48204/result").json()["fields"]}
    assert f["total_amount"]["value"] == 13020 and f["total_amount"]["confidence"] == 1.0
    assert "rv2" not in [i["id"] for i in client.get("/v1/review-items").json()["data"]]
    assert client.post("/v1/review-items/rv2/decision", json={"action": "reject"}).status_code == 409
    assert client.post("/v1/review-items/rv4/decision", json={"action": "maybe"}).json()["code"] == "validation"
    rj = client.post("/v1/review-items/rv4/decision", json={"action": "reject"})
    assert rj.json()["status"] == "rejected" and client.get("/v1/runs/run_48208").json()["status"] == "failed"
    ret = client.post("/v1/review-items/rv3/decision", json={"action": "return_to_queue"}).json()
    assert ret["status"] == "open" and ret["assignee"] is None


def _publish_review_version(client) -> None:
    etag = set_threshold(client, "pl_ap", 0.9)
    r = client.post("/v1/pipelines/pl_ap/versions", json={"release_note": "Gate 0.9", "draft_etag": etag})
    assert r.status_code == 201


def _ingest(client, name: str = "INV-2026-0500.pdf", **headers):
    return client.post("/v1/ingest/pl_ap", files={"file": (name, _pdf(), "application/pdf")},
                       data={"metadata": json.dumps({"source": "erp"})}, headers=headers)


def _wait_routed(client, run_id: str, timeout: float = 15.0) -> None:
    from app.db.models import RunEvent
    from app.db.session import session_factory
    from sqlalchemy import select

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session_factory()() as s:
            if s.scalar(select(RunEvent.seq).where(RunEvent.run_id == run_id, RunEvent.event == "run.routed")):
                return
        time.sleep(0.05)
    raise AssertionError("run never routed to review")


def test_production_run_pauses_at_review_and_resumes_on_approve(client, auth_as):
    _publish_review_version(client)
    auth_as(scopes=["ingest:write", "runs:read"])
    r = _ingest(client, **{"Idempotency-Key": "abc-1"})
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["test"] is False and run["pipeline_version"] == 15 and run["document"]["filename"] == "INV-2026-0500.pdf"
    replay = _ingest(client, **{"Idempotency-Key": "abc-1"})
    assert replay.status_code == 202 and replay.json()["id"] == run["id"] and replay.headers["idempotency-replayed"] == "true"
    _wait_routed(client, run["id"])
    assert wait_status(client, run["id"], {"review"})["status"] == "review"
    auth_as(role="reviewer")
    items = [i for i in client.get("/v1/review-items").json()["data"] if i["run_id"] == run["id"]]
    assert len(items) == 1
    rv = items[0]
    assert rv["reason"] == "Low confidence · total_amount 0.88" and rv["reason_code"] == "low_confidence"
    assert rv["queue"] == "Accounts payable" and rv["status"] == "open"
    assert client.get(f"/v1/review-items/{rv['id']}").json()["result"]["fields"]
    assert client.post(f"/v1/review-items/{rv['id']}/claim").status_code == 200
    d = client.post(f"/v1/review-items/{rv['id']}/decision",
                    json={"action": "approve", "corrections": {"total_amount": "13020.00"}, "comment": "ok"})
    assert d.status_code == 200 and d.json()["status"] == "approved"
    evs = [f["json"] for f in stream_events(client, run["id"])]  # full replay incl. the resumed part
    names = [(e["event"], e.get("node_id")) for e in evs]
    i = names.index(("run.routed", "ap_n14"))
    assert names[i - 1] == ("step.started", "ap_n14") and names[i + 1] == ("step.finished", "ap_n14")
    out = evs[i + 1]["step"]["output"]
    assert out["decision"] == "approved" and out["ticket"] == rv["id"] and out["corrections"] == {"total_amount": 13020}
    assert names[i + 2:] == [("step.started", "ap_n12"), ("step.finished", "ap_n12"), ("step.started", "ap_n13"),
                             ("step.finished", "ap_n13"), ("run.finished", None)]
    assert evs[-1]["status"] == "completed"
    assert [int(f) for f in range(1, len(evs) + 1)] == list(range(1, len(evs) + 1))
    res = client.get(f"/v1/runs/{run['id']}/result").json()
    tot = next(f for f in res["fields"] if f["name"] == "total_amount")
    assert tot["value"] == 13020 and tot["confidence"] == 1.0


def test_production_run_rejected_in_review_fails(client, auth_as):
    _publish_review_version(client)
    auth_as(scopes=["ingest:write"])
    run = _ingest(client).json()
    auth_as(role="admin")
    _wait_routed(client, run["id"])
    rv = next(i for i in client.get("/v1/review-items").json()["data"] if i["run_id"] == run["id"])
    client.post(f"/v1/review-items/{rv['id']}/decision", json={"action": "reject", "comment": "Duplicate"})
    evs = [f["json"] for f in stream_events(client, run["id"])]
    assert evs[-1]["status"] == "failed"
    d = client.get(f"/v1/runs/{run['id']}").json()
    assert d["status"] == "failed" and d["error"]["code"] == "review_rejected"


def test_cancel_paused_production_run(client, auth_as):
    _publish_review_version(client)
    auth_as(scopes=["ingest:write", "runs:write", "runs:read"])
    run = _ingest(client).json()
    _wait_routed(client, run["id"])
    assert client.post(f"/v1/runs/{run['id']}/cancel").status_code == 202
    assert stream_events(client, run["id"])[-1]["json"]["status"] == "cancelled"


# ── ingest auth ────────────────────────────────────────────────────────────

def _multipart(fields: list[tuple[str, str | tuple[str, bytes, str]]]) -> tuple[bytes, str]:
    boundary = "----be2boundary"
    out = io.BytesIO()
    for name, v in fields:
        out.write(f"--{boundary}\r\n".encode())
        if isinstance(v, tuple):
            fn, data, mime = v
            out.write(f'Content-Disposition: form-data; name="{name}"; filename="{fn}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
            out.write(data)
        else:
            out.write(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{v}'.encode())
        out.write(b"\r\n")
    out.write(f"--{boundary}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={boundary}"


def test_ingest_with_hmac_signature(raw_client, db):
    from app.core.security import sign_payload
    from app.db.models import Pipeline
    from app.routers.ingest import ingest_secret

    secret = ingest_secret(db, db.get(Pipeline, "pl_ap"))
    body, ctype = _multipart([("file", ("INV-77.pdf", _pdf(), "application/pdf")), ("metadata", '{"po": "PO-1"}')])
    ts = str(int(time.time()))
    sig = "sha256=" + sign_payload(secret, ts, body)
    r = raw_client.post("/v1/ingest/pl_ap", content=body,
                        headers={"Content-Type": ctype, "X-DI-Timestamp": ts, "X-DI-Signature": sig})
    assert r.status_code == 202, r.text
    assert r.json()["test"] is False and r.json()["document"]["filename"] == "INV-77.pdf"
    bad = raw_client.post("/v1/ingest/pl_ap", content=body,
                          headers={"Content-Type": ctype, "X-DI-Timestamp": ts, "X-DI-Signature": "sha256=00"})
    assert bad.status_code == 401
    old = str(int(time.time()) - 3600)
    exp = raw_client.post("/v1/ingest/pl_ap", content=body, headers={
        "Content-Type": ctype, "X-DI-Timestamp": old, "X-DI-Signature": sign_payload(secret, old, body)})
    assert exp.status_code == 401
    assert raw_client.post("/v1/ingest/pl_ap", content=body, headers={"Content-Type": ctype}).status_code == 401


def test_ingest_scope_state_and_json_body(client, auth_as):
    import base64

    auth_as(scopes=["runs:read"])
    assert _ingest(client).status_code == 403
    auth_as(scopes=["ingest:write"])
    r = client.post("/v1/ingest/pl_kyc", files={"file": ("a.pdf", _pdf(), "application/pdf")})
    assert r.status_code == 409 and r.json()["code"] == "pipeline_not_active"
    r = client.post("/v1/ingest/pl_exp", json={"filename": "receipt_1.png", "content_type": "image/png",
                                               "content_base64": base64.b64encode(_png()).decode()})
    assert r.status_code == 202 and r.json()["pipeline_version"] == 6
    auth_as(role="admin")
    done = wait_status(client, r.json()["id"], {"completed", "failed"})
    assert done["status"] == "completed"


# ── isolation ──────────────────────────────────────────────────────────────

def test_cross_workspace_isolation(client, auth_as):
    doc = _upload(client, "INV-9.pdf", _pdf(), "application/pdf").json()
    run = client.post("/v1/pipelines/pl_exp/test-runs", json={"document_id": "doc_rcp_sample"}).json()
    stream_events(client, run["id"])
    auth_as(role="admin", workspace_id="ws_other")
    for path in ["/v1/pipelines/pl_ap", "/v1/pipelines/pl_ap/draft", "/v1/pipelines/pl_ap/versions",
                 f"/v1/runs/{run['id']}", f"/v1/runs/{run['id']}/result", f"/v1/runs/{run['id']}/events",
                 "/v1/review-items/rv1", f"/v1/documents/{doc['id']}", f"/v1/documents/{doc['id']}/pages/1/image"]:
        assert client.get(path).status_code == 404, path
    assert client.get("/v1/pipelines").json()["data"] == []
    assert client.get("/v1/runs").json()["data"] == []
    assert client.get("/v1/review-items").json()["data"] == []
    assert client.post("/v1/review-items/rv2/claim").status_code == 404
    assert client.post(f"/v1/runs/{run['id']}/cancel").status_code == 404
    assert client.post("/v1/pipelines/pl_ap/test-runs", json={"document_id": "doc_inv_sample"}).status_code == 404
    assert client.post("/v1/ingest/pl_ap", files={"file": ("a.pdf", _pdf(), "application/pdf")}).status_code == 404
    assert client.delete(f"/v1/documents/{doc['id']}").status_code == 404
