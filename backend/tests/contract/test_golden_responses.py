"""QA contract test: replay every recorded portal-mock response (`app/data/mock_golden_responses.json`) against the
live backend (in-process, admin of ws_default) and compare status, content type and JSON shape.

Port of the frontend engineer's /tmp/qa/diff.py, extended to non-GET entries (replayed in recorded order, because
some depend on the previous one, e.g. the pl_xls draft PUT → publish → test-run chain) and to the 4 SSE transcripts
(event/node sequence). Every live response is also validated against `api/openapi.yaml` (tests/contract/oas.py);
those findings are asserted against the documented contract-gap allowlist below (see docs/test-report.md § Defects).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.contract import oas

GOLDEN = json.loads((Path(__file__).resolve().parents[2] / "app" / "data" / "mock_golden_responses.json").read_text())
HTTP_KEYS = [k for k in GOLDEN if k != "_meta" and not k.startswith("SSE")]
SSE_KEYS = [k for k in GOLDEN if k.startswith("SSE")]

# Known, accepted value-level differences recorded in docs/integration-report.md (I-2…I-6) do not affect shape.
# Shape differences that are accepted: none. Contract findings that are accepted as contract gaps (C-xx in the report):
KNOWN_CONTRACT_GAPS = {
    "PUT /pipelines/{id}/draft: status 428 not documented",                     # C-02 (G5)
    "POST /mcp-servers/{id}/tools/{tool}/call: status 422 not documented",      # C-01
}


def _shape(v, path: str, out: dict) -> None:
    if isinstance(v, dict):
        out[path] = "object"
        for k, x in v.items():
            _shape(x, f"{path}.{k}", out)
    elif isinstance(v, list):
        out[path] = "array"
        for x in v:  # every element (a field that only some items carry, e.g. Run.error, must still exist)
            _shape(x, f"{path}[]", out)
    elif v is None:
        out[path] = "null"
    elif isinstance(v, bool):
        out[path] = "bool"
    elif isinstance(v, (int, float)):
        out[path] = "number"
    else:
        out[path] = "string"


def _diff(gold, live) -> tuple[list[str], list[str]]:
    a: dict = {}
    b: dict = {}
    _shape(gold, "", a)
    _shape(live, "", b)
    missing = sorted(k for k in a if k not in b)
    types = sorted(f"{k}: mock={a[k]} live={b[k]}" for k in a if k in b and a[k] != b[k] and "null" not in (a[k], b[k]))
    return missing, types


def test_golden_http_responses_status_and_shape(client):
    """Every recorded request: same status, same problem/JSON content type, no missing keys, no type changes."""
    mismatches: list[str] = []
    contract: set[str] = set()
    for key in HTTP_KEYS:
        g = GOLDEN[key]
        method, path = key.split(" ")[0], key.split(" ")[1]
        req = g.get("request") or {}
        r = client.request(method, "/v1" + path, json=req.get("body"), headers=req.get("headers") or {})
        ctype = r.headers.get("content-type", "")
        body = r.json() if r.content and "json" in ctype else None
        if r.status_code != g["status"]:
            mismatches.append(f"{key}: status mock={g['status']} live={r.status_code} {r.text[:200]}")
            continue
        gct = (g.get("headers") or {}).get("content-type", "")
        if gct and gct.split(";")[0] != ctype.split(";")[0]:
            mismatches.append(f"{key}: content-type mock={gct} live={ctype}")
        if g.get("body") is not None:
            missing, types = _diff(g["body"], body)
            if missing or types:
                mismatches.append(f"{key}: missing={missing[:10]} types={types[:10]}")
        for p in oas.validate_response(method, path, r.status_code, ctype, body):
            contract.add(p.replace(f" {r.status_code}: ", ": ", 1) if "not documented" not in p else p)
    assert not mismatches, "\n".join(mismatches)
    unexpected = sorted(c for c in contract if c not in KNOWN_CONTRACT_GAPS)
    assert not unexpected, "\n".join(unexpected)


@pytest.mark.parametrize("key", SSE_KEYS)
def test_golden_sse_transcripts(client, key):
    """Test run of the recorded pipeline/document: same create status/shape and the same event/node sequence."""
    from tests.be2.helpers import golden_events, stream_events

    _, pid, doc = key.split(" ")
    g = GOLDEN[key]
    r = client.post(f"/v1/pipelines/{pid}/test-runs", json={"document_id": doc})
    assert r.status_code == g["create"]["status"]
    missing, types = _diff(g["create"]["body"], r.json())
    assert not missing and not types, (missing, types)
    live = [(e["json"]["event"], e["json"].get("node_id"), e["json"].get("status")) for e in stream_events(client, r.json()["id"])]
    gold = [(e["json"]["event"], e["json"].get("node_id"), e["json"].get("status")) for e in golden_events(key)]
    assert live == gold
    res = client.get(f"/v1/runs/{r.json()['id']}/result")
    assert res.status_code == 200
    missing, types = _diff(g["result"]["body"] if "body" in g["result"] else g["result"], res.json())
    assert not missing and not types, (missing, types)
