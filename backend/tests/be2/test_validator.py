"""Table-driven port of portal/src/features/builder/validate.test.ts + server-only checks. Pure: no database."""
from __future__ import annotations

import copy
import itertools

import pytest

from app.services import catalog_data
from app.services.validator import (
    catalog, ProviderRef, ValidationContext, defaults, field_err, issues, normalize_graph, SETTINGS_DEFAULT,
)

CTX = ValidationContext.of(
    connections=["con_gmail", "con_wa", "con_pg", "con_erp", "con_qdrant", "con_s3"],
    providers=[
        ProviderRef("prv_anthropic", "Anthropic", True, "Global"),
        ProviderRef("prv_openai", "OpenAI", True, "Global"),
        ProviderRef("prv_azure", "Azure OpenAI", True, "UAE North"),
        ProviderRef("prv_gemini", "Google Gemini", True, "Global"),
        ProviderRef("prv_ollama", "Ollama (self-hosted)", False, "On-premises"),
    ],
)

_k = itertools.count(1)


def N(type_: str, cfg: dict | None = None, name: str | None = None) -> dict:
    d = catalog()[type_]
    return {"id": f"n{next(_k)}", "type": type_, "name": name or d["name"], "x": 0, "y": 0,
            "config": {**defaults(type_), **(cfg or {})}, "settings": dict(SETTINGS_DEFAULT)}


def E(a: str, b: str, port: str = "out") -> dict:
    return {"id": f"e{next(_k)}", "from": a, "from_port": port, "to": b}


def S(a: str, b: str, slot: str) -> dict:
    return {"id": f"e{next(_k)}", "from": a, "from_port": "plug", "to": b, "slot": slot}


def minimal() -> dict:
    a, b = N("intake.upload"), N("out.db")
    return {"nodes": [a, b], "edges": [E(a["id"], b["id"])]}


def msgs(g: dict, ctx: ValidationContext = CTX) -> list[str]:
    return [f"{i['level']}:{i['message']}" for i in issues(g, ctx)]


def seed_graph(pid: str) -> dict:
    sp = next(p for p in catalog_data.seed_data()["pipelines"] if p["pipeline"]["id"] == pid)
    return copy.deepcopy(sp["draft"]["graph"])


# ── seeded graphs ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("pid", ["pl_ap", "pl_exp", "pl_kyc"])
def test_seeded_pipelines_have_no_blocking_errors(pid):
    assert [i for i in issues(seed_graph(pid), CTX) if i["level"] == "error"] == []


@pytest.mark.parametrize("tpl", ["tpl_ap", "tpl_exp", "tpl_kyc", "tpl_xls"])
def test_templates_have_no_blocking_errors(tpl):
    t = next(x for x in catalog_data.seed_data()["templates"] if x["id"] == tpl)
    assert [i for i in issues(t["graph"], CTX) if i["level"] == "error"] == []


def test_minimal_valid_pipeline_has_no_issues():
    assert issues(minimal(), CTX) == []


# ── single-rule cases: (builder, expected message) ─────────────────────────

def _required_field():
    g = minimal(); g["nodes"][0]["config"]["types"] = ""; return g


def _conn_removed():
    g = minimal(); g["nodes"][1]["config"]["conn"] = "con_gone"; return g


def _unplugged():
    g = minimal(); g["nodes"].append(N("tool.calc", {}, "Calculator")); return g


def _no_incoming():
    g = minimal(); g["nodes"].append(N("read.ocr", {}, "OCR")); return g


def _loop():
    g = minimal(); a, b = N("read.ocr"), N("read.layout"); g["nodes"] += [a, b]
    g["edges"] += [E(g["nodes"][0]["id"], a["id"]), E(a["id"], b["id"]), E(b["id"], a["id"])]
    return g


def _unknown():
    g = minimal(); g["nodes"].append({"id": "zz", "type": "magic.step", "name": "Magic", "x": 0, "y": 0, "config": {}})
    return g


CASES = [
    ("missing intake", lambda: {"nodes": [N("out.db")], "edges": []}, "error:Add an intake step to start the pipeline"),
    ("no deliver", lambda: {"nodes": [N("intake.upload")], "edges": []},
     "warning:No deliver step: results are not stored anywhere"),
    ("required field", _required_field, "error:Upload portal: Accepted file types — required"),
    ("connection removed", _conn_removed, "error:Save to database: Database — connection was removed"),
    ("unplugged component", _unplugged, "warning:Calculator is not plugged into an AI step"),
    ("no incoming", _no_incoming, "error:OCR has no incoming connection"),
    ("loop", _loop, "error:The pipeline contains a loop. Use retries instead of wiring back."),
    ("unknown step type", _unknown, "error:Magic: unknown step type magic.step"),
]


@pytest.mark.parametrize("name,build,expected", CASES, ids=[c[0] for c in CASES])
def test_rule(name, build, expected):
    assert expected in msgs(build())


def test_must_be_a_number():
    f = next(x for x in catalog()["intake.upload"]["fields"] if x["key"] == "maxMb")
    assert field_err(f, {"maxMb": "abc"}, CTX) == "Must be a number"
    assert field_err(f, {"maxMb": 12}, CTX) == ""
    assert field_err(f, {"maxMb": " 12 "}, CTX) == ""


def test_model_provider_missing_or_disabled():
    g = minimal(); a = g["nodes"][0]
    x, m = N("ai.classify"), N("ai.model", {"provider": "prv_nope"})
    g["nodes"] += [x, m]
    g["edges"] = [E(a["id"], x["id"]), E(x["id"], g["nodes"][1]["id"]), S(m["id"], x["id"], "model")]
    assert "error:Language model: choose a provider" in msgs(g)
    m["config"]["provider"] = "prv_ollama"
    assert "error:Language model: Ollama (self-hosted) is disabled in Models" in msgs(g)
    codes = [i["code"] for i in issues(g, CTX)]
    assert "provider_disabled" in codes


def test_output_schema_rules():
    g = minimal(); p = N("ai.parser", {"schema": []}); g["nodes"].append(p)
    assert "error:Output schema: add at least one field" in msgs(g)
    p["config"]["schema"] = [{"name": "Bad Name", "type": "string"}]
    assert "error:Output schema: field names must be snake_case" in msgs(g)


def test_outputs_leading_nowhere():
    g = minimal(); r = N("route.confidence", {}, "Gate")
    g["edges"].append(E(g["nodes"][0]["id"], r["id"])); g["nodes"].append(r)
    m = msgs(g)
    assert "warning:Gate → “pass” leads nowhere" in m
    assert "warning:Gate → “review” leads nowhere" in m
    o = N("read.ocr", {}, "OCR"); g["nodes"].append(o); g["edges"].append(E(g["nodes"][0]["id"], o["id"]))
    assert "warning:OCR leads nowhere" in msgs(g)


def test_ai_steps_need_model_and_extract_needs_schema():
    g = minimal(); x = N("ai.extract", {}, "Extract"); g["nodes"].append(x)
    g["edges"] += [E(g["nodes"][0]["id"], x["id"]), E(x["id"], g["nodes"][1]["id"])]
    m = msgs(g)
    assert "error:Extract needs a language model" in m
    assert "error:Extract needs an output schema" in m
    c = N("ai.classify", {}, "Classify"); g["nodes"].append(c)
    g["edges"] += [E(g["nodes"][0]["id"], c["id"]), E(c["id"], g["nodes"][1]["id"])]
    assert "error:Classify needs a language model" in msgs(g)
    assert not [x for x in msgs(g) if x.startswith("error:Classify needs an output")]


@pytest.mark.parametrize("value,bad", [(1.5, True), (0, True), ("abc", True), (None, True), (0.85, False), ("0.9", False), (1, False)])
def test_confidence_threshold_range(value, bad):
    g = minimal(); r = N("route.confidence", {"threshold": value}, "Gate"); g["nodes"].append(r)
    assert ("error:Gate: threshold must be between 0 and 1" in msgs(g)) is bad


def test_issues_carry_node_ids_codes_and_fields():
    g = minimal(); g["nodes"].append(N("read.ocr", {}, "OCR"))
    i = next(x for x in issues(g, CTX) if x["code"] == "no_incoming_connection")
    assert i["node_id"] == g["nodes"][2]["id"]
    g2 = _required_field()
    i2 = next(x for x in issues(g2, CTX) if x["code"] == "required_field")
    assert i2 == {"level": "error", "message": "Upload portal: Accepted file types — required",
                  "node_id": g2["nodes"][0]["id"], "code": "required_field", "field": "types"}


def test_issue_order_matches_portal_for_seeded_xls_draft():
    # golden output of the portal mock's POST /pipelines/pl_xls/validate
    gold = catalog_data._load("mock_golden_responses.json", {})["POST /pipelines/pl_xls/validate"]["body"]
    sp = next(p for p in catalog_data.seed_data()["pipelines"] if p["pipeline"]["id"] == "pl_xls")
    got = issues(sp["draft"]["graph"], ValidationContext.of(
        connections=[c["id"] for c in catalog_data.seed_data()["connections"]],
        providers=catalog_data.seed_data()["providers"]))
    assert got == gold["issues"]


@pytest.mark.parametrize("pid", ["pl_ap", "pl_exp", "pl_kyc", "pl_xls"])
def test_validate_matches_mock_golden(pid):
    gold = catalog_data._load("mock_golden_responses.json", {})[f"POST /pipelines/{pid}/validate"]["body"]
    sd = catalog_data.seed_data()
    ctx = ValidationContext.of(connections=[c["id"] for c in sd["connections"]], providers=sd["providers"])
    got = issues(seed_graph(pid), ctx)
    assert {"valid": not any(i["level"] == "error" for i in got), "issues": got} == gold


# ── server-only: data residency ────────────────────────────────────────────

def test_data_residency_blocks_providers_and_workers_outside_region():
    g = seed_graph("pl_kyc")  # Azure (UAE North) model + Paddle worker
    ctx = ValidationContext.of(connections=CTX.connections, providers=CTX.providers.values(),
                               workers=[{"id": "wrk_paddle", "name": "Paddle DocParse", "data_region": "EU West"}],
                               allowed_regions=["UAE North"])
    res = [i for i in issues(g, ctx) if i.get("code") == "data_residency"]
    assert len(res) == 1 and res[0]["field"] == "worker" and "Paddle DocParse runs in EU West" in res[0]["message"]
    g2 = seed_graph("pl_exp")  # Gemini (Global)
    res2 = [i for i in issues(g2, ctx) if i.get("code") == "data_residency"]
    assert any("Google Gemini is hosted in Global" in i["message"] for i in res2)


def test_no_residency_policy_means_no_residency_issues():
    assert not [i for i in issues(seed_graph("pl_exp"), CTX) if i.get("code") == "data_residency"]


# ── normalisation (serializeGraph ∘ deserializeGraph) ──────────────────────

def test_normalize_fills_defaults_rounds_and_drops_invalid_edges():
    g = {"nodes": [{"id": "a", "type": "intake.upload", "name": "Up", "x": 10.6, "y": 2.4, "config": {}},
                   {"id": "b", "type": "out.db", "name": "DB", "x": 0, "y": 0, "config": {"table": "t"}}],
         "edges": [{"id": "e1", "from": "a", "from_port": "out", "to": "b"},
                   {"id": "e2", "from": "a", "from_port": "nope", "to": "b"},
                   {"id": "e3", "from": "b", "from_port": "out", "to": "a"}]}
    n = normalize_graph(g)
    assert n["nodes"][0]["x"] == 11 and n["nodes"][0]["y"] == 2
    assert n["nodes"][0]["config"] == {"types": "pdf, png, jpg, tiff", "maxMb": 25}
    assert n["nodes"][1]["config"]["table"] == "t" and n["nodes"][1]["settings"] == SETTINGS_DEFAULT
    assert [e["id"] for e in n["edges"]] == ["e1"]


def test_normalize_drops_unknown_nodes_like_the_portal():
    g = _unknown()
    assert "zz" not in [n["id"] for n in normalize_graph(g)["nodes"]]
    assert [i["code"] for i in issues(g, CTX) if i.get("node_id") == "zz"] == ["unknown_type"]


def test_normalize_is_identity_on_seeded_graphs():
    for pid in ["pl_ap", "pl_exp", "pl_kyc", "pl_xls"]:
        g = seed_graph(pid)
        assert normalize_graph(g) == g
