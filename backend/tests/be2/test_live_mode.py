"""run_mode=live: read steps call the worker adapter, AI steps call the provider (both mocked with httpx), retries and
on_error are honoured, failures surface as step.failed."""
from __future__ import annotations

import json

import httpx
import pytest

from app.config import get_settings
from tests.be2.helpers import draft, stream_events

PAGES = {"pages": [{"n": 1, "width": 595, "height": 842, "blocks": [
    {"text": "Example Hypermarket", "bbox": [10, 10, 200, 30], "confidence": 0.9},
    {"text": "Total 186.40 card 4417", "bbox": [10, 40, 200, 60], "confidence": 0.8}]}],
    "text": "Example Hypermarket\nTotal 186.40 card 4417", "worker_latency_ms": 12}
EXTRACTED = {"merchant": {"value": "Example Hypermarket", "confidence": 0.91},
             "purchase_date": {"value": "2026-11-16", "confidence": 0.7},
             "total_amount": {"value": 186.4, "confidence": 0.95}, "vat_amount": {"value": None, "confidence": 0},
             "card_last4": {"value": "4417", "confidence": 0.99}}


@pytest.fixture()
def live(monkeypatch, db):
    from app.db.models import Worker
    from app.services.adapters import http as ahttp

    monkeypatch.setattr(get_settings(), "run_mode", "live")
    w = db.get(Worker, "wrk_paddle")
    w.adapter, w.connection_id = "generic_pages_v1", None
    db.commit()
    calls = {"worker": 0, "llm": 0, "fail_worker": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "localhost":
            calls["worker"] += 1
            if calls["fail_worker"] > 0:
                calls["fail_worker"] -= 1
                return httpx.Response(503, json={"error": "busy"})
            return httpx.Response(200, json=PAGES)
        calls["llm"] += 1
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(EXTRACTED)}]},
                                                         "finishReason": "STOP"}],
                                         "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 40}})

    ahttp.set_transport(httpx.MockTransport(handler))
    yield calls
    ahttp.set_transport(None)


def test_live_run_calls_worker_and_model(client, live):
    r = client.post("/v1/pipelines/pl_exp/test-runs", json={"document_id": "doc_rcp_sample"})
    evs = [f["json"] for f in stream_events(client, r.json()["id"])]
    assert evs[-1]["status"] == "completed", evs
    ocr = next(e for e in evs if e["event"] == "step.finished" and e["node_id"] == "exp_n3")["step"]["output"]
    assert ocr["pages"] == 1 and ocr["mean_confidence"] == 0.85 and ocr["worker"] == "wrk_paddle"
    ext = next(e for e in evs if e["event"] == "step.finished" and e["node_id"] == "exp_n4")["step"]
    assert ext["usage"] == {"input_tokens": 120, "output_tokens": 40} and ext["output"]["fields"] == 5
    db_step = next(e for e in evs if e["event"] == "step.finished" and e["node_id"] == "exp_n6")["step"]["output"]
    assert db_step == {"status": "ok", "simulated": True}  # deliver=false
    res = client.get(f"/v1/runs/{r.json()['id']}/result").json()
    f = {x["name"]: x for x in res["fields"]}
    assert f["merchant"]["value"] == "Example Hypermarket" and f["purchase_date"]["confidence"] == 0.7
    assert f["card_last4"]["masked"] is True and f["card_last4"]["value"] is None  # val.pii ran
    assert len(res["overlays"]["blocks"]) == 2
    assert live["worker"] == 1 and live["llm"] == 1


def test_live_retries_then_fails_with_step_failed(client, live):
    live["fail_worker"] = 10
    d, etag = draft(client, "pl_exp")
    for n in d["graph"]["nodes"]:
        if n["id"] == "exp_n3":
            n["settings"]["retries"] = 1
    client.put("/v1/pipelines/pl_exp/draft", json={"graph": d["graph"]}, headers={"If-Match": etag})
    r = client.post("/v1/pipelines/pl_exp/test-runs", json={"document_id": "doc_rcp_sample"})
    evs = [f["json"] for f in stream_events(client, r.json()["id"])]
    assert [e["step"]["attempt"] for e in evs if e["event"] == "step.started" and e["node_id"] == "exp_n3"] == [1, 2]
    failed = next(e for e in evs if e["event"] == "step.failed")
    assert failed["node_id"] == "exp_n3" and failed["step"]["error"]["title"]
    assert evs[-1]["status"] == "failed" and live["worker"] == 2
    run = client.get(f"/v1/runs/{r.json()['id']}").json()
    assert run["status"] == "failed" and run["error"]["code"]


def test_live_on_error_continue(client, live):
    live["fail_worker"] = 10
    d, etag = draft(client, "pl_exp")
    for n in d["graph"]["nodes"]:
        if n["id"] == "exp_n3":
            n["settings"].update(retries=0, on_error="continue")
    client.put("/v1/pipelines/pl_exp/draft", json={"graph": d["graph"]}, headers={"If-Match": etag})
    r = client.post("/v1/pipelines/pl_exp/test-runs", json={"document_id": "doc_rcp_sample"})
    evs = [f["json"] for f in stream_events(client, r.json()["id"])]
    assert any(e["event"] == "step.failed" for e in evs) and evs[-1]["status"] == "completed"
    assert live["llm"] == 1
