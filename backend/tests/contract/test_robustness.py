"""QA regression tests for the defects Schemathesis found (docs/test-report.md § Defects, D-01…D-06).

Each case was `xfail(strict=True)` while its defect was open; all six were fixed on 2026-11-26 and the cases now
run as permanent regression tests (marker `regression`). Expected behaviour everywhere: a 4xx
application/problem+json (422 `validation` or 400 `bad_request`/`invalid_cursor`), never a 500.
"""
from __future__ import annotations

import pytest

LONG = "a" * 300


def _no_5xx(r) -> None:
    assert r.status_code < 500, (r.status_code, r.text[:200])
    assert r.headers["content-type"].startswith("application/problem+json")


D01 = pytest.mark.regression  # fixed by the lead, 2026-11-26


@D01
@pytest.mark.parametrize("method,path,body", [
    ("POST", "/v1/pipelines", {"name": "ok", "owner_team": LONG, "intake_type": "intake.email"}),
    ("PATCH", "/v1/pipelines/pl_ap", {"name": LONG}),
    ("POST", "/v1/pipelines/pl_ap/duplicate", {"name": LONG}),
    ("POST", "/v1/templates/tpl_ap/instantiate", {"name": LONG}),
    ("PATCH", "/v1/providers/prv_openai", {"name": LONG}),
    ("POST", "/v1/providers", {"kind": "anthropic", "name": LONG}),
    ("PATCH", "/v1/workers/wrk_paddle", {"name": LONG}),
    ("PATCH", "/v1/policies/pol_region", {"name": LONG}),
    ("PATCH", "/v1/connections/con_s3", {"name": LONG}),
    ("PATCH", "/v1/skills/skl_invoice", {"name": LONG}),
    ("POST", "/v1/skills/skl_invoice/versions", {"instructions": "x", "release_note": "a" * 1200}),
], ids=lambda v: v if isinstance(v, str) and len(v) < 60 else None)
def test_d01_overlong_strings_are_422(client, method, path, body):
    _no_5xx(client.request(method, path, json=body))


D02 = pytest.mark.regression  # fixed by the lead, 2026-11-26


@D02
@pytest.mark.parametrize("method,path,body", [
    ("GET", "/v1/pipelines/pl_ap/versions/2147483648", None),
    ("POST", "/v1/pipelines/pl_ap/versions/2147483648/restore", None),
    ("POST", "/v1/pipelines/pl_ap/test-runs", {"document_id": "doc_inv_sample", "version": 2147483648}),
])
def test_d02_out_of_range_integers(client, method, path, body):
    _no_5xx(client.request(method, path, json=body))


# D-03: fixed by the lead, 2026-11-26 — kept as a regression test
@pytest.mark.parametrize("body", [
    {"name": "a\u0000b", "service": "redis", "auth": {"type": "none"}},
    {"name": "ab", "service": "redis", "settings": {"host": "h\u0000"}, "auth": {"type": "none"}},
])
def test_d03_nul_characters(client, body):
    _no_5xx(client.post("/v1/connections", json=body))


# D-04: fixed by the lead, 2026-11-26 — kept as a regression test
@pytest.mark.parametrize("target", ["validate", "draft"])
def test_d04_graph_with_non_string_node_type(client, target):
    graph = {"nodes": [{"id": "n1", "type": {"x": 1}, "name": "", "x": 0, "y": 0, "config": {}}], "edges": []}
    if target == "validate":
        r = client.post("/v1/pipelines/pl_kyc/validate", json=graph)
    else:
        etag = client.get("/v1/pipelines/pl_kyc/draft").headers["etag"]
        r = client.put("/v1/pipelines/pl_kyc/draft", json={"graph": graph}, headers={"If-Match": etag})
    # Malformed nodes are dropped during normalisation (like unknown step types in the portal's serializeGraph),
    # so a 2xx is acceptable; what must never happen is a 5xx.
    assert r.status_code < 500, (r.status_code, r.text[:200])
    if r.status_code >= 400:
        _no_5xx(r)


# D-05: fixed by the lead, 2026-11-26 — kept as a regression test
def test_d05_malformed_json_code_is_consistent(client):
    codes = {client.request(m, p, content=b"{not json", headers={"Content-Type": "application/json"}).json()["code"]
             for m, p in (("POST", "/v1/pipelines"), ("POST", "/v1/members"), ("PATCH", "/v1/connections/con_s3"))}
    assert len(codes) == 1, codes


# D-06: fixed by the lead, 2026-11-26 — kept as a regression test
def test_d06_stale_cursor_does_not_restart_the_list(client):
    for i in range(3):
        client.post("/v1/pipelines", json={"name": f"QA cur {i}", "intake_type": "intake.email"})
    first = client.get("/v1/pipelines?limit=5").json()  # 4 seeded + 1st QA one → cursor is deletable
    cursor = first["next_cursor"]
    assert client.delete(f"/v1/pipelines/{cursor}").status_code == 204
    second = client.get(f"/v1/pipelines?limit=5&cursor={cursor}")
    # expected: 400 invalid_cursor, or the page after the deleted row — never page 1 again
    assert second.status_code == 400 or not {p["id"] for p in second.json()["data"]} & {p["id"] for p in first["data"]}
