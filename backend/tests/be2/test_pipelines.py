"""Pipelines, drafts (ETag), validation, versions, templates — parity with the portal mock."""
from __future__ import annotations

from sqlalchemy import select

from tests.be2.helpers import draft, golden


def _problem(r, status: int, code: str) -> dict:
    assert r.status_code == status, (r.status_code, r.text)
    assert r.headers["content-type"].startswith("application/problem+json")
    b = r.json()
    assert b["code"] == code and b["title"] and b["status"] == status
    return b


def test_list_pipelines_shape_and_order(client):
    r = client.get("/v1/pipelines", params={"limit": 200})
    assert r.status_code == 200
    data = r.json()["data"]
    assert [p["id"] for p in data] == ["pl_ap", "pl_exp", "pl_kyc", "pl_xls"]
    ap = data[0]
    gold = golden("GET /pipelines/pl_ap")["body"]
    for k in ("id", "name", "description", "status", "owner_team", "live_version", "draft_changes", "intake_type",
              "step_count", "ai_component_count", "updated_by"):
        assert ap[k] == gold[k], k
    assert ap["kpi"] == gold["kpi"]  # incl. review_waiting = 3 open/claimed items
    assert [p["id"] for p in client.get("/v1/pipelines", params={"status": "active"}).json()["data"]] == ["pl_ap", "pl_exp"]
    assert [p["id"] for p in client.get("/v1/pipelines", params={"q": "compliance"}).json()["data"]] == ["pl_kyc"]
    page = client.get("/v1/pipelines", params={"limit": 2}).json()
    assert len(page["data"]) == 2 and page["next_cursor"]
    rest = client.get("/v1/pipelines", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert [p["id"] for p in rest["data"]] == ["pl_kyc", "pl_xls"] and rest["next_cursor"] is None


def test_create_get_patch_duplicate_delete(client):
    _problem(client.post("/v1/pipelines", json={"intake_type": "intake.api"}), 422, "validation")
    _problem(client.post("/v1/pipelines", json={"name": "X", "intake_type": "read.ocr"}), 422, "validation")
    r = client.post("/v1/pipelines", json={"name": "Claims", "intake_type": "intake.api"})
    assert r.status_code == 201
    p = r.json()
    assert p["status"] == "draft" and p["live_version"] is None and p["draft_changes"] == ["Created pipeline"]
    assert p["intake_type"] == "intake.api" and p["step_count"] == 1 and p["owner_team"] == "Finance Operations"
    d, etag = draft(client, p["id"])
    assert etag == f'W/"{p["id"]}-1"' and d["graph"]["nodes"][0]["config"]["path"] == "/v1/documents"
    r = client.patch(f"/v1/pipelines/{p['id']}", json={"name": "Claims v2"})
    assert r.json()["name"] == "Claims v2" and "Edited pipeline details" in r.json()["draft_changes"]
    dup = client.post(f"/v1/pipelines/{p['id']}/duplicate", json={})
    assert dup.status_code == 201 and dup.json()["name"] == "Claims v2 (copy)" and dup.json()["draft_changes"] == ["Duplicated"]
    assert client.delete(f"/v1/pipelines/{p['id']}").status_code == 204
    _problem(client.get(f"/v1/pipelines/{p['id']}"), 404, "not_found")
    assert p["id"] not in [x["id"] for x in client.get("/v1/pipelines").json()["data"]]


def test_delete_active_pipeline_is_409(client):
    b = _problem(client.delete("/v1/pipelines/pl_ap"), 409, "pipeline_active")
    assert b["detail"] == golden("DELETE /pipelines/pl_ap")["body"]["detail"]


def test_audit_written_for_writes(client, db):
    from app.db.models import AuditEvent

    client.post("/v1/pipelines/pl_exp/pause")
    rows = db.scalars(select(AuditEvent).where(AuditEvent.resource_id == "pl_exp")).all()
    assert any(r.action == "pipeline.paused" for r in rows)


# ── draft + ETag ───────────────────────────────────────────────────────────

def test_draft_matches_mock(client):
    for pid in ("pl_ap", "pl_exp", "pl_kyc", "pl_xls"):
        gold = golden(f"GET /pipelines/{pid}/draft")
        r = client.get(f"/v1/pipelines/{pid}/draft")
        assert r.headers["etag"] == gold["headers"]["etag"]
        b = r.json()
        for k in ("pipeline_id", "graph", "etag", "changes", "issues", "saved_by"):
            assert b[k] == gold["body"][k], (pid, k)


def test_etag_flow_428_412_200(client):
    d, etag = draft(client, "pl_ap")
    g428 = golden("PUT /pipelines/pl_ap/draft")["body"]
    b = _problem(client.put("/v1/pipelines/pl_ap/draft", json={"graph": d["graph"]}), 428, "precondition_required")
    assert (b["title"], b["detail"]) == (g428["title"], g428["detail"])
    b = _problem(client.put("/v1/pipelines/pl_ap/draft", json={"graph": d["graph"]}, headers={"If-Match": 'W/"pl_ap-999"'}),
                 412, "etag_mismatch")
    assert b["title"] == golden("PUT /pipelines/pl_ap/draft #2")["body"]["title"] and b["current_etag"] == etag
    g = d["graph"]
    g["nodes"][0]["x"] = 44.4
    r = client.put("/v1/pipelines/pl_ap/draft", json={"graph": g, "change": "Moved intake"}, headers={"If-Match": etag})
    assert r.status_code == 200
    new = r.headers["etag"]
    assert new == r.json()["etag"] == 'W/"pl_ap-2"' and new != etag
    assert r.json()["graph"]["nodes"][0]["x"] == 44 and r.json()["changes"] == ["Moved intake"]
    # the old tag is now stale
    _problem(client.put("/v1/pipelines/pl_ap/draft", json={"graph": g}, headers={"If-Match": etag}), 412, "etag_mismatch")
    # same change text is not duplicated
    r2 = client.put("/v1/pipelines/pl_ap/draft", json={"graph": g, "change": "Moved intake"}, headers={"If-Match": new})
    assert r2.json()["changes"] == ["Moved intake"] and r2.headers["etag"] == 'W/"pl_ap-3"'
    _problem(client.put("/v1/pipelines/pl_ap/draft", json={"nope": 1}, headers={"If-Match": r2.headers["etag"]}),
             422, "validation")


def test_draft_save_normalises_graph(client):
    d, etag = draft(client, "pl_xls")
    g = d["graph"]
    g["nodes"].append({"id": "zz", "type": "magic.step", "name": "Magic", "x": 1, "y": 1, "config": {}})
    g["edges"].append({"id": "bad", "from": g["nodes"][0]["id"], "from_port": "nope", "to": g["nodes"][1]["id"]})
    r = client.put("/v1/pipelines/pl_xls/draft", json={"graph": g}, headers={"If-Match": etag})
    ids = [n["id"] for n in r.json()["graph"]["nodes"]]
    assert "zz" not in ids and "bad" not in [e["id"] for e in r.json()["graph"]["edges"]]


def test_validate_endpoint_matches_mock(client):
    for pid in ("pl_ap", "pl_exp", "pl_kyc", "pl_xls"):
        assert client.post(f"/v1/pipelines/{pid}/validate").json() == golden(f"POST /pipelines/{pid}/validate")["body"]
    r = client.post("/v1/pipelines/pl_ap/validate", json={"nodes": [{"id": "a", "type": "out.db", "name": "DB", "x": 0,
                                                                         "y": 0, "config": {}}], "edges": []})
    b = r.json()
    assert b["valid"] is False and b["issues"][0]["code"] == "missing_intake"


# ── publish / activate / versions ──────────────────────────────────────────

def test_publish_422_issues_and_etag_checks(client):
    put = golden("PUT /pipelines/pl_xls/draft")
    r = client.put("/v1/pipelines/pl_xls/draft", json=put["request"]["body"], headers=put["request"]["headers"])
    for k in ("graph", "etag", "changes", "issues"):
        assert r.json()[k] == put["body"][k], k
    gold = golden("POST /pipelines/pl_xls/versions")["body"]
    etag = r.headers["etag"]
    assert etag == golden("POST /pipelines/pl_xls/versions")["request"]["body"]["draft_etag"]
    b = _problem(client.post("/v1/pipelines/pl_xls/versions", json={"release_note": "x", "draft_etag": etag}),
                 422, "pipeline_invalid")
    assert (b["title"], b["detail"], b["issues"]) == (gold["title"], gold["detail"], gold["issues"])
    _problem(client.post("/v1/pipelines/pl_ap/versions", json={"release_note": "x", "draft_etag": 'W/"pl_ap-0"'}),
             412, "etag_mismatch")


def test_activate_without_live_version_matches_mock(client):
    gold = golden("POST /pipelines/pl_xls/activate")["body"]
    b = _problem(client.post("/v1/pipelines/pl_xls/activate"), 422, "no_live_version")
    assert (b["title"], b["detail"]) == (gold["title"], gold["detail"])


def test_activate_validates_live_version(client, db):
    from app.db.models import Provider

    p = db.get(Provider, "prv_anthropic")
    p.enabled = False
    db.commit()
    b = _problem(client.post("/v1/pipelines/pl_ap/activate"), 422, "pipeline_invalid")
    assert b["detail"].endswith("before activating")
    assert {i["code"] for i in b["issues"]} == {"provider_disabled"}
    assert all(i["level"] == "error" for i in b["issues"])
    p.enabled = True
    db.commit()
    assert client.post("/v1/pipelines/pl_kyc/activate").json()["status"] == "active"
    assert client.post("/v1/pipelines/pl_kyc/pause").json()["status"] == "paused"


def test_versions_publish_get_restore(client):
    vs = client.get("/v1/pipelines/pl_ap/versions").json()
    assert vs == golden("GET /pipelines/pl_ap/versions")["body"] | {"data": [
        {**v, "published_at": vs["data"][i]["published_at"]} for i, v in enumerate(golden("GET /pipelines/pl_ap/versions")["body"]["data"])]}
    v14 = client.get("/v1/pipelines/pl_ap/versions/14").json()
    assert v14["live"] is True and v14["graph"] == golden("GET /pipelines/pl_ap/versions/14")["body"]["graph"]
    assert client.get("/v1/pipelines/pl_ap/versions/99").status_code == 404

    d, etag = draft(client, "pl_ap")
    g = d["graph"]
    g["nodes"][0]["name"] = "AP inbox"
    etag = client.put("/v1/pipelines/pl_ap/draft", json={"graph": g, "change": "Renamed intake"},
                      headers={"If-Match": etag}).headers["etag"]
    r = client.post("/v1/pipelines/pl_ap/versions", json={"release_note": "", "draft_etag": etag})
    assert r.status_code == 201
    v = r.json()
    assert v["version"] == 15 and v["live"] is True and v["release_note"] == "Renamed intake"
    assert v["graph"]["nodes"][0]["name"] == "AP inbox"
    p = client.get("/v1/pipelines/pl_ap").json()
    assert p["live_version"] == 15 and p["draft_changes"] == []
    d2, etag2 = draft(client, "pl_ap")
    assert etag2 != etag and d2["changes"] == []  # publish bumps the etag
    assert [x["live"] for x in client.get("/v1/pipelines/pl_ap/versions").json()["data"]] == [True, False, False, False]

    r = client.post("/v1/pipelines/pl_ap/versions/14/restore")
    assert r.status_code == 200
    assert r.headers["etag"] == r.json()["etag"] != etag2
    assert r.json()["graph"]["nodes"][0]["name"] == "Email inbox"
    assert r.json()["changes"] == ["Restored version 14"]


def test_templates_list_and_instantiate(client):
    assert client.get("/v1/templates").json() == golden("GET /templates")["body"]
    r = client.post("/v1/templates/tpl_kyc/instantiate", json={})
    assert r.status_code == 201
    p = r.json()
    assert p["name"] == "Identity documents" and p["status"] == "draft" and p["draft_changes"] == ["Created from template"]
    d, _ = draft(client, p["id"])
    assert len(d["graph"]["nodes"]) == 11 and not [i for i in d["issues"] if i["level"] == "error"]
    assert client.post("/v1/templates/tpl_nope/instantiate").status_code == 404


def test_viewer_cannot_write(client, auth_as):
    auth_as(role="viewer")
    assert client.get("/v1/pipelines").status_code == 200
    assert client.post("/v1/pipelines/pl_ap/pause").status_code == 403
    auth_as(scopes=["pipelines:read"])
    assert client.put("/v1/pipelines/pl_ap/draft", json={"graph": {"nodes": [], "edges": []}}).status_code == 403
