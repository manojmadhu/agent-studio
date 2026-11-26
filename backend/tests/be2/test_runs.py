"""Test runs via SSE (parity with the mock's golden transcripts), resume, result, step test, cancel, retry."""
from __future__ import annotations

import pytest

from app.config import get_settings
from tests.be2.helpers import golden, golden_events, normalise_event, set_threshold, stream_events, wait_status

TRANSCRIPTS = [("pl_ap", "doc_inv_sample"), ("pl_ap", "doc_rcp_sample"), ("pl_exp", "doc_rcp_sample"),
               ("pl_kyc", "doc_inv_sample")]


def _start(client, pid: str, doc: str, **extra) -> dict:
    r = client.post(f"/v1/pipelines/{pid}/test-runs", json={"document_id": doc, "version": "draft", "deliver": False,
                                                            **extra})
    assert r.status_code == 202, r.text
    return r.json()


@pytest.mark.parametrize("pid,doc", TRANSCRIPTS)
def test_test_run_sse_matches_mock_transcript(client, pid, doc):
    key = f"SSE {pid} {doc}"
    gold = golden(key)
    run = _start(client, pid, doc)
    created = {k: v for k, v in run.items() if k not in ("id", "started_at")}
    assert created == {k: v for k, v in gold["create"]["body"].items() if k not in ("id", "started_at")}

    frames = stream_events(client, run["id"])
    want = golden_events(key)
    # ids 1..n, the event: line equals data.event, run_id/at present
    assert [f["id"] for f in frames] == [f["id"] for f in want]
    assert all(f["event"] == f["json"]["event"] and f["json"]["run_id"] == run["id"] and f["json"]["at"] for f in frames)
    assert [normalise_event(f["json"]) for f in frames] == [normalise_event(f["json"]) for f in want]
    # timing semantics: durations follow the mock formula
    for f in frames:
        if f["json"]["event"] == "step.finished":
            assert 380 <= f["json"]["step"]["duration_ms"] <= 1350

    res = client.get(f"/v1/runs/{run['id']}/result").json()
    assert {**res, "run_id": "x"} == {**gold["result"]["body"], "run_id": "x"}

    detail = client.get(f"/v1/runs/{run['id']}").json()
    gd = gold["run_detail"]["body"]
    assert detail["status"] == gd["status"] and detail["field_count"] == gd["field_count"]
    assert detail["finished_at"] and detail["duration_ms"] >= 1
    strip = lambda s: {k: v for k, v in s.items() if k not in ("started_at", "duration_ms")}  # noqa: E731
    assert [strip(s) for s in detail["steps"]] == [strip(s) for s in gd["steps"]]


def test_last_event_id_resume_header_and_query(client):
    run = _start(client, "pl_exp", "doc_rcp_sample")
    full = stream_events(client, run["id"])
    assert len(full) == 14
    tail = stream_events(client, run["id"], headers={"Last-Event-ID": "10"})
    assert [f["id"] for f in tail] == ["11", "12", "13", "14"]
    assert [f["json"] for f in tail] == [f["json"] for f in full[10:]]
    tail2 = stream_events(client, run["id"], params={"last_event_id": "13"})
    assert [f["json"]["event"] for f in tail2] == ["run.finished"]


def test_test_run_reaching_review_continues_like_mock(client, db):
    from sqlalchemy import select

    from app.db.models import ReviewItem

    set_threshold(client, "pl_ap", 0.9)
    run = _start(client, "pl_ap", "doc_inv_sample")
    evs = [f["json"] for f in stream_events(client, run["id"])]
    names = [(e["event"], e.get("node_id")) for e in evs]
    gate = next(e for e in evs if e["event"] == "step.finished" and e["node_id"] == "ap_n11")
    assert gate["step"]["output_port"] == "review" and gate["step"]["output"]["lowest_required_confidence"] == 0.88
    i = names.index(("step.finished", "ap_n14"))
    assert names[i + 1] == ("run.routed", "ap_n14") and evs[i + 1]["status"] == "review"
    assert ("step.finished", "ap_n12") in names and ("step.finished", "ap_n13") in names  # keeps going (ERP, DB)
    assert evs[-1] == {**evs[-1], "event": "run.finished", "status": "review"}
    assert next(e for e in evs if e.get("node_id") == "ap_n14" and e["event"] == "step.finished")["step"]["output"] == \
        {"queue": "Accounts payable", "ticket": "REV-1182", "decision": "approved"}
    assert client.get(f"/v1/runs/{run['id']}").json()["status"] == "review"
    assert db.scalars(select(ReviewItem).where(ReviewItem.run_id == run["id"])).first() is None
    assert client.post(f"/v1/runs/{run['id']}/cancel").status_code == 409


def test_test_run_422_and_404(client):
    put = golden("PUT /pipelines/pl_xls/draft")
    client.put("/v1/pipelines/pl_xls/draft", json=put["request"]["body"], headers=put["request"]["headers"])
    r = client.post("/v1/pipelines/pl_xls/test-runs", json={"document_id": "doc_inv_sample", "version": "draft"})
    gold = golden("POST /pipelines/pl_xls/test-runs")["body"]
    assert r.status_code == 422 and {k: r.json()[k] for k in ("code", "title", "detail", "issues")} == \
        {k: gold[k] for k in ("code", "title", "detail", "issues")}
    assert client.post("/v1/pipelines/pl_ap/test-runs", json={"document_id": "doc_inv_sample", "version": 99}).status_code == 404
    assert client.post("/v1/pipelines/pl_nope/test-runs", json={"document_id": "doc_inv_sample"}).status_code == 404
    assert client.post("/v1/pipelines/pl_ap/test-runs", json={"document_id": "doc_unknown"}).status_code == 404


def test_test_run_on_a_version(client):
    r = client.post("/v1/pipelines/pl_ap/test-runs", json={"document_id": "doc_inv_sample", "version": 13})
    assert r.status_code == 202 and r.json()["pipeline_version"] == 13
    evs = stream_events(client, r.json()["id"])
    assert evs[-1]["json"]["status"] == "completed"


def test_step_test_matches_mock(client):
    gold = golden("POST /pipelines/pl_ap/steps/ap_n4/test")
    r = client.post("/v1/pipelines/pl_ap/steps/ap_n4/test", json=gold["request"]["body"])
    assert {k: v for k, v in r.json().items() if k != "started_at"} == \
        {k: v for k, v in gold["body"].items() if k != "started_at"}
    model = next(n for n in client.get("/v1/pipelines/pl_ap/draft").json()["graph"]["nodes"] if n["type"] == "ai.model")
    sub = client.post(f"/v1/pipelines/pl_ap/steps/{model['id']}/test", json={"document_id": "doc_inv_sample"}).json()
    assert sub["output"]["component"] == "Language model"
    assert client.post("/v1/pipelines/pl_ap/steps/nope/test", json={}).status_code == 404


def test_result_before_finish_is_409_and_list_filters(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "run_step_delay_ms", 350)
    run = _start(client, "pl_ap", "doc_inv_sample")
    r = client.get(f"/v1/runs/{run['id']}/result")
    assert r.status_code == 409 and r.json()["code"] == "run_in_progress"
    assert client.post(f"/v1/runs/{run['id']}/cancel").status_code == 202
    evs = stream_events(client, run["id"])
    assert evs[-1]["json"] == {**evs[-1]["json"], "event": "run.finished", "status": "cancelled"}
    detail = client.get(f"/v1/runs/{run['id']}").json()
    assert detail["status"] == "cancelled" and any(s["status"] == "skipped" for s in detail["steps"])
    c2 = client.post(f"/v1/runs/{run['id']}/cancel")
    assert c2.status_code == 409 and c2.json()["code"] == "run_finished"

    listed = client.get("/v1/runs", params={"pipeline_id": "pl_ap", "test": "true"}).json()["data"]
    assert listed[0]["id"] == run["id"] and all(x["test"] for x in listed)
    assert all(x["status"] == "failed" for x in client.get("/v1/runs", params={"status": "failed"}).json()["data"])
    page = client.get("/v1/runs", params={"limit": 3}).json()
    nxt = client.get("/v1/runs", params={"limit": 3, "cursor": page["next_cursor"]}).json()
    assert len(page["data"]) == 3 and not {x["id"] for x in page["data"]} & {x["id"] for x in nxt["data"]}


def test_retry_creates_a_new_run(client):
    run = _start(client, "pl_exp", "doc_rcp_sample")
    stream_events(client, run["id"])
    r = client.post(f"/v1/runs/{run['id']}/retry")
    assert r.status_code == 202
    new = r.json()
    assert new["id"] != run["id"] and new["status"] == "queued" and new["document"]["id"] == "doc_rcp_sample"
    assert stream_events(client, new["id"])[-1]["json"]["status"] == "completed"


def test_seeded_run_streams_and_results(client):
    evs = stream_events(client, "run_48215")  # seeded failed run: synthetic terminal event, then close
    assert [e["json"]["event"] for e in evs] == ["run.finished"] and evs[0]["json"]["status"] == "failed"
    assert client.get("/v1/runs/run_48215/result").status_code == 404
    d = client.get("/v1/runs/run_48215").json()
    assert d["error"]["code"] == "worker_timeout" and d["pipeline_version"] == 14
    assert client.get("/v1/runs/run_nope").status_code == 404
