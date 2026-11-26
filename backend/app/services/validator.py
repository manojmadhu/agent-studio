"""Pipeline graph helpers + validator (owned by BE2).

A 1:1 port of the portal's `features/builder/validate.ts` (`issues()` / `fieldErr()`) and the graph helpers in
`features/builder/graph.ts`, plus the server-only checks from docs/04 §7:

* ``unknown_type`` — a node whose type is not in the catalogue (lead decision: the portal's code, not docs/04's
  ``unknown_step_type``). Note ``normalize_graph`` (= the portal's deserialize+serialize, applied on every draft save
  and before validating) drops such nodes exactly like the portal/mock, so this only fires for raw graphs.
* ``provider_disabled`` / ``missing_provider`` — checked against the workspace's providers table (same as the portal).
* ``loop`` — main-flow cycle (same as the portal).
* ``data_residency`` — an enabled ``data_residency`` policy that applies to the pipeline lists ``allowed_regions``; any
  language model whose provider, or any step/tool whose worker, carries a ``data_region`` outside that list is an error.

Everything here is a pure function over (graph, catalog, context) so it is unit-testable without a database.
Messages, levels, codes, node ids and field keys are byte-for-byte identical to the portal for every shared rule.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Iterable

from app.services import catalog_data

SNAKE = re.compile(r"^[a-z][a-z0-9_]*$")
SETTINGS_DEFAULT: dict[str, Any] = {"retries": 2, "timeout_s": 60, "on_error": "stop", "notes": ""}

Graph = dict[str, Any]
Node = dict[str, Any]


# ───────────────────────── JS-compatible helpers ─────────────────────────

_JS_NUM = re.compile(r"^[+-]?(\d+\.?\d*(e[+-]?\d+)?|\.\d+(e[+-]?\d+)?|infinity)$", re.I)


def js_number(v: Any) -> float:
    """`Number(v)` semantics (enough for config values that came from JSON)."""
    if v is None:
        return 0.0  # Number(null) === 0 (undefined → NaN is handled by callers via MISSING)
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip()
        if s == "":
            return 0.0
        if re.fullmatch(r"0[xX][0-9a-fA-F]+", s):
            return float(int(s, 16))
        if re.fullmatch(r"0[bB][01]+", s):
            return float(int(s[2:], 2))
        if re.fullmatch(r"0[oO][0-7]+", s):
            return float(int(s[2:], 8))
        if _JS_NUM.match(s):
            return float(s)
        return math.nan
    if isinstance(v, list):
        if not v:
            return 0.0
        if len(v) == 1:
            return js_number(js_string(v[0]))
        return math.nan
    return math.nan


def js_string(v: Any) -> str:
    """`String(v)` for JSON values."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, list):
        return ",".join("" if x is None else js_string(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return str(v)


def js_list(s: Any) -> list[str]:
    """Portal `list()`: comma separated string → trimmed non-empty items. `String(s ?? '')`."""
    raw = "" if s is None else js_string(s)
    return [x.strip() for x in raw.split(",") if x.strip()]


def truthy(v: Any) -> bool:
    """JS truthiness for JSON values ([] and {} are truthy in JS)."""
    if v is None or v is False:
        return False
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v != 0 and not (isinstance(v, float) and math.isnan(v))
    if isinstance(v, str):
        return v != ""
    return True


# ───────────────────────── catalogue + graph helpers (graph.ts) ─────────────────────────

@lru_cache
def catalog() -> dict[str, dict]:
    """Step types keyed by `type` (catalog_data.step_types_by_id keys by "id", which StepType does not have)."""
    return {s.get("type") or s.get("id"): s for s in catalog_data.step_types()}


def _cat(cat: dict[str, dict] | None) -> dict[str, dict]:
    return cat if cat is not None else catalog()


def defaults(type_: str, cat: dict[str, dict] | None = None) -> dict[str, Any]:
    d = _cat(cat).get(type_)
    if not d:
        return {}
    return {f["key"]: copy.deepcopy(f.get("default")) for f in d.get("fields", []) if "default" in f}


def is_sub(n: Node, cat: dict[str, dict] | None = None) -> bool:
    d = _cat(cat).get(n.get("type"))
    return bool(d and d.get("plugs_into"))


def ins_of(n: Node, cat: dict[str, dict] | None = None) -> int:
    d = _cat(cat).get(n.get("type"))
    if is_sub(n, cat) or not (d and d.get("has_input")):
        return 0
    return 1


def slots_of(n: Node, cat: dict[str, dict] | None = None) -> list[str]:
    d = _cat(cat).get(n.get("type")) or {}
    return list(d.get("slots") or [])


def outs_of(n: Node, cat: dict[str, dict] | None = None) -> list[str]:
    if is_sub(n, cat):
        return []
    t = n.get("type")
    c = n.get("config") or {}
    if t == "route.type":
        r = [*js_list(c.get("paths")), "other"]
    elif t == "route.confidence":
        r = ["pass", "review"]
    else:
        r = ["out"]
    if (n.get("settings") or {}).get("on_error") == "error_output" and r:
        r = [*r, "error"]
    return r


def main_nodes(g: Graph, cat: dict[str, dict] | None = None) -> list[Node]:
    return [n for n in g.get("nodes", []) if not is_sub(n, cat)]


def node_by_id(g: Graph, nid: str | None) -> Node | None:
    if not nid:
        return None
    return next((n for n in g.get("nodes", []) if n.get("id") == nid), None)


def subs_of(g: Graph, n: Node, slot: str | None = None) -> list[Node]:
    out = []
    for e in g.get("edges", []):
        if e.get("slot") and e.get("to") == n.get("id") and (not slot or e.get("slot") == slot):
            x = node_by_id(g, e.get("from"))
            if x:
                out.append(x)
    return out


def host_of(g: Graph, sub: Node) -> Node | None:
    e = next((x for x in g.get("edges", []) if x.get("slot") and x.get("from") == sub.get("id")), None)
    return node_by_id(g, e.get("to")) if e else None


def model_of(g: Graph, n: Node) -> Node | None:
    s = subs_of(g, n, "model")
    return s[0] if s else None


def intake_of(g: Graph, cat: dict[str, dict] | None = None) -> Node | None:
    return next((n for n in main_nodes(g, cat) if not ins_of(n, cat)), None)


def edge_ok(e: dict, g: Graph, cat: dict[str, dict] | None = None) -> bool:
    cat = _cat(cat)
    a = node_by_id(g, e.get("from"))
    b = node_by_id(g, e.get("to"))
    if not a or not b or a.get("type") not in cat or b.get("type") not in cat:
        return False
    if e.get("slot"):
        return is_sub(a, cat) and cat[a["type"]].get("plugs_into") == e["slot"] and e["slot"] in slots_of(b, cat)
    return e.get("from_port") in outs_of(a, cat) and bool(ins_of(b, cat))


def _num_pos(v: Any) -> int | float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if math.isnan(f) or math.isinf(f):
        return 0
    return int(math.floor(f + 0.5))  # Math.round


def normalize_graph(g: Graph, cat: dict[str, dict] | None = None) -> Graph:
    """`serializeGraph(deserializeGraph(g))` from graph.ts: drops nodes of unknown type, fills config defaults
    (`step_defaults`) and settings, rounds positions, drops edges failing `edgeOK`, strips unknown keys."""
    cat = _cat(cat)
    nodes: list[Node] = []
    for n in g.get("nodes", []) or []:
        if (not isinstance(n, dict) or not isinstance(n.get("type"), str) or n.get("type") not in cat
                or not isinstance(n.get("id"), str)):
            continue  # Lead (QA D-04): malformed nodes are dropped like unknown types instead of raising
        t = n["type"]
        nodes.append({
            "id": n.get("id"),
            "type": t,
            "name": n.get("name") if n.get("name") is not None else cat[t]["name"],
            "x": _num_pos(n.get("x")),
            "y": _num_pos(n.get("y")),
            "config": {**defaults(t, cat), **copy.deepcopy(n["config"] if isinstance(n.get("config"), dict) else {})},
            "settings": {**SETTINGS_DEFAULT, **(n["settings"] if isinstance(n.get("settings"), dict) else {})},
        })
    ng: Graph = {"nodes": nodes, "edges": []}
    for e in g.get("edges", []) or []:
        if not isinstance(e, dict) or not edge_ok(e, ng, cat):
            continue
        out = {"id": e.get("id"), "from": e.get("from"), "from_port": "plug" if e.get("slot") else e.get("from_port"),
               "to": e.get("to")}
        if e.get("slot"):
            out["slot"] = e["slot"]
        ng["edges"].append(out)
    return ng


def step_count(g: Graph, cat: dict[str, dict] | None = None) -> int:
    return len(main_nodes(g, cat))


def ai_step_count(g: Graph, cat: dict[str, dict] | None = None) -> int:
    cat = _cat(cat)
    return sum(1 for n in g.get("nodes", []) if (cat.get(n.get("type")) or {}).get("ai"))


def sub_count(g: Graph, cat: dict[str, dict] | None = None) -> int:
    return sum(1 for n in g.get("nodes", []) if is_sub(n, cat))


# ───────────────────────── validation context ─────────────────────────

@dataclass
class ProviderRef:
    id: str
    name: str
    enabled: bool = True
    data_region: str | None = None


@dataclass
class ValidationContext:
    """What exists in the workspace. Built from the DB by the router (see `context_from_db`)."""

    connections: set[str] = field(default_factory=set)
    providers: dict[str, ProviderRef] = field(default_factory=dict)
    worker_regions: dict[str, str | None] = field(default_factory=dict)  # worker id → data_region
    worker_names: dict[str, str] = field(default_factory=dict)
    allowed_regions: list[str] | None = None  # from an enabled data_residency policy; None = no restriction

    @classmethod
    def of(cls, connections: Iterable[str] = (), providers: Iterable[dict | ProviderRef] = (),
           workers: Iterable[dict] = (), allowed_regions: list[str] | None = None) -> "ValidationContext":
        pv: dict[str, ProviderRef] = {}
        for p in providers:
            ref = p if isinstance(p, ProviderRef) else ProviderRef(p["id"], p.get("name") or p["id"],
                                                                   bool(p.get("enabled", True)), p.get("data_region"))
            pv[ref.id] = ref
        ws = list(workers)
        return cls(set(connections), pv, {w["id"]: w.get("data_region") for w in ws},
                   {w["id"]: w.get("name") or w["id"] for w in ws}, allowed_regions)


# ───────────────────────── validate.ts ─────────────────────────

_MISSING = object()


def field_err(f: dict, c: dict, ctx: ValidationContext) -> str:
    v = c.get(f["key"], _MISSING)
    if f.get("required") and (v == "" or v is None or v is _MISSING or (isinstance(v, list) and not v)):
        return "Required"
    if f.get("type") == "connection" and v is not _MISSING and truthy(v) and v not in ctx.connections:
        return "Connection was removed"
    if f.get("type") == "number" and v != "" and v is not None and v is not _MISSING and math.isnan(js_number(v)):
        return "Must be a number"
    return ""


def _provider_id(c: dict) -> Any:
    return c.get("provider") if c.get("provider") not in (None, "") else c.get("provider_id")


def issues(g: Graph, ctx: ValidationContext, cat: dict[str, dict] | None = None) -> list[dict]:
    cat = _cat(cat)
    out: list[dict] = []

    def add(level: str, message: str, node_id: str | None = None, code: str | None = None, fld: str | None = None):
        i: dict[str, Any] = {"level": level, "message": message}
        if node_id:
            i["node_id"] = node_id
        if code:
            i["code"] = code
        if fld:
            i["field"] = fld
        out.append(i)

    nodes = g.get("nodes", []) or []
    edges = g.get("edges", []) or []
    main = main_nodes(g, cat)
    if not any(not ins_of(n, cat) for n in main):
        add("error", "Add an intake step to start the pipeline", None, "missing_intake")
    if not any((cat.get(n.get("type")) or {}).get("stage") == "deliver" for n in main):
        add("warning", "No deliver step: results are not stored anywhere", None, "missing_deliver")

    for n in nodes:
        d = cat.get(n.get("type"))
        name = n.get("name", "")
        if not d:
            add("error", f"{name}: unknown step type {n.get('type')}", n.get("id"), "unknown_type")
            continue
        c = n.get("config") or {}
        for f in d.get("fields", []):
            er = field_err(f, c, ctx)
            if er:
                add("error", f"{name}: {f['label']} — {er.lower()}", n.get("id"), "required_field", f["key"])
        if is_sub(n, cat):
            if not host_of(g, n):
                add("warning", f"{name} is not plugged into an AI step", n.get("id"), "unplugged_component")
            if n.get("type") == "ai.model":
                pv = ctx.providers.get(_provider_id(c)) if isinstance(_provider_id(c), str) else None
                if not pv:
                    add("error", f"{name}: choose a provider", n.get("id"), "missing_provider")
                elif not pv.enabled:
                    add("error", f"{name}: {pv.name} is disabled in Models", n.get("id"), "provider_disabled")
            if n.get("type") == "ai.parser":
                schema = c.get("schema") if isinstance(c.get("schema"), list) else []
                if not schema:
                    add("error", f"{name}: add at least one field", n.get("id"), "invalid_schema_field")
                elif any(not (isinstance(f, dict) and isinstance(f.get("name"), str) and SNAKE.match(f["name"]))
                         for f in schema):
                    add("error", f"{name}: field names must be snake_case", n.get("id"), "invalid_schema_field")
            continue
        if ins_of(n, cat) and not any(not e.get("slot") and e.get("to") == n.get("id") for e in edges):
            add("error", f"{name} has no incoming connection", n.get("id"), "no_incoming_connection")
        if d.get("stage") != "deliver":
            outs = outs_of(n, cat)
            for o in outs:
                if o != "error" and not any(e.get("from") == n.get("id") and e.get("from_port") == o for e in edges):
                    suffix = f" → “{o}”" if len(outs) > 1 else ""
                    add("warning", f"{name}{suffix} leads nowhere", n.get("id"), "dangling_output")
        need = d.get("required_slots")
        if need is None:
            need = [s for s in (d.get("slots") or []) if s == "model"]
        for s in need:
            if not subs_of(g, n, s):
                add("error", f"{name} needs {'an output schema' if s == 'parser' else 'a language model'}", n.get("id"),
                    "missing_parser" if s == "parser" else "missing_model")
        if n.get("type") == "route.confidence":
            raw = c.get("threshold", _MISSING)
            t = math.nan if raw is _MISSING else js_number(raw)
            if not (t > 0 and t <= 1):
                add("error", f"{name}: threshold must be between 0 and 1", n.get("id"), "invalid_threshold", "threshold")

    # loop detection over main-flow edges
    adj: dict[str, list[str]] = {}
    for e in edges:
        if not e.get("slot"):
            adj.setdefault(e.get("from"), []).append(e.get("to"))
    st: dict[str, int] = {}
    cyc = False

    def dfs(start: str) -> None:  # iterative DFS (same colouring as the recursive portal version)
        nonlocal cyc
        st[start] = 1
        stack = [(start, iter(adj.get(start, [])))]
        while stack:
            nid, it = stack[-1]
            nxt = next(it, None)
            if nxt is None:
                st[nid] = 2
                stack.pop()
                continue
            if st.get(nxt) == 1:
                cyc = True
            elif not st.get(nxt):
                st[nxt] = 1
                stack.append((nxt, iter(adj.get(nxt, []))))

    for n in main:
        if not st.get(n.get("id")):
            dfs(n.get("id"))
    if cyc:
        add("error", "The pipeline contains a loop. Use retries instead of wiring back.", None, "loop")

    # server-only: data residency
    if ctx.allowed_regions is not None:
        allowed = set(ctx.allowed_regions)
        regions_txt = ", ".join(ctx.allowed_regions) or "none"
        for n in nodes:
            if n.get("type") not in cat:
                continue
            c = n.get("config") or {}
            name = n.get("name", "")
            if n.get("type") == "ai.model":
                pv = ctx.providers.get(_provider_id(c)) if isinstance(_provider_id(c), str) else None
                if pv and pv.data_region and pv.data_region not in allowed:
                    add("error", f"{name}: {pv.name} is hosted in {pv.data_region}, outside the allowed regions "
                                 f"({regions_txt})", n.get("id"), "data_residency", "provider")
            w = c.get("worker")
            if isinstance(w, str) and w in ctx.worker_regions:
                region = ctx.worker_regions[w]
                if region and region not in allowed:
                    add("error", f"{name}: {ctx.worker_names.get(w, w)} runs in {region}, outside the allowed regions "
                                 f"({regions_txt})", n.get("id"), "data_residency", "worker")
    return out


def error_count(lst: list[dict]) -> int:
    return sum(1 for i in lst if i.get("level") == "error")


def errors_only(lst: list[dict]) -> list[dict]:
    return [i for i in lst if i.get("level") == "error"]


def is_valid_graph_shape(g: Any) -> bool:
    return isinstance(g, dict) and isinstance(g.get("nodes"), list) and isinstance(g.get("edges"), list)


def policy_applies(policy: dict, pipeline_id: str, owner_team: str | None) -> bool:
    scope = policy.get("scope") or {}
    if scope.get("all_pipelines"):
        return True
    if pipeline_id in (scope.get("pipeline_ids") or []):
        return True
    if owner_team and owner_team in (scope.get("owner_teams") or []):
        return True
    return False


def allowed_regions_for(policies: Iterable[dict], pipeline_id: str, owner_team: str | None) -> list[str] | None:
    """Intersection of `allowed_regions` of every enabled data_residency policy that applies; None when none apply."""
    result: list[str] | None = None
    for p in policies:
        if p.get("kind") != "data_residency" or not p.get("enabled"):
            continue
        if not policy_applies(p, pipeline_id, owner_team):
            continue
        regions = list((p.get("config") or {}).get("allowed_regions") or [])
        result = regions if result is None else [r for r in result if r in regions]
    return result
