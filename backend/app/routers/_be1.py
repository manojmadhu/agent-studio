"""Shared helpers for BE1's routers (identity, catalog, connections, providers, workers, mcp_servers, knowledge,
skills, policies, ops). Owned by BE1 — not a public interface.

* ``no_content()`` — 204 without body.
* ``pipeline_graphs(db, ws)`` — every pipeline's draft graph and live-version graph (loaded and scanned in Python,
  portability rule 2) → ``[(pipeline, graph)]`` with the draft first.
* ``node_usages(db, ws, pred)`` — ``UsageRef`` list of pipeline nodes matching ``pred(node)`` (dedup by node).
* ``connection_usage_index(db, ws)`` — ``{connection_id: [UsageRef]}`` over pipelines, providers, workers, MCP
  servers, knowledge collections (mirrors the portal mock's ``usages``) — one scan for list endpoints.
* ``simulated()`` — settings.run_mode == "simulate": component tests/invocations return the portal mock's
  deterministic results instead of calling the network.
"""
from __future__ import annotations

from typing import Any, Callable, Iterable

from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.time import iso


def no_content() -> Response:
    return Response(status_code=204)


def simulated() -> bool:
    return get_settings().run_mode == "simulate"


def ts(obj: Any, *names: str) -> dict:
    return {n: iso(getattr(obj, n, None)) for n in names}


def pipeline_graphs(db: Session, ws: str) -> list[tuple[Any, dict]]:
    from app.db.models import Pipeline, PipelineDraft, PipelineVersion

    pipes = db.scalars(select(Pipeline).where(Pipeline.workspace_id == ws).order_by(Pipeline.created_at, Pipeline.id)).all()
    if not pipes:
        return []
    drafts = {d.pipeline_id: d for d in db.scalars(select(PipelineDraft).where(PipelineDraft.workspace_id == ws)).all()}
    live_wanted = {p.id: p.live_version for p in pipes if p.live_version is not None}
    live: dict[str, dict] = {}
    if live_wanted:
        for v in db.scalars(select(PipelineVersion).where(PipelineVersion.workspace_id == ws,
                                                          PipelineVersion.pipeline_id.in_(list(live_wanted)))).all():
            if live_wanted.get(v.pipeline_id) == v.version:
                live[v.pipeline_id] = v.graph or {}
    out: list[tuple[Any, dict]] = []
    for p in pipes:
        if p.id in drafts:
            out.append((p, drafts[p.id].graph or {}))
        if p.id in live:
            out.append((p, live[p.id]))
    return out


def _nodes(graph: dict) -> Iterable[dict]:
    for n in (graph or {}).get("nodes") or []:
        if isinstance(n, dict):
            yield n


def node_usages(db: Session, ws: str, pred: Callable[[dict], bool],
                graphs: list[tuple[Any, dict]] | None = None) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for p, g in graphs if graphs is not None else pipeline_graphs(db, ws):
        for n in _nodes(g):
            k = (p.id, str(n.get("id")))
            if k in seen:
                continue
            try:
                hit = pred(n)
            except Exception:  # noqa: BLE001
                hit = False
            if hit:
                seen.add(k)
                out.append({"resource_type": "pipeline", "resource_id": p.id, "node_id": str(n.get("id")),
                            "label": f"{p.name} › {n.get('name') or n.get('type')}"})
    return out


def config_refs(node: dict) -> set[str]:
    """Every string value (or list member) in a node's config — connection ids are matched against these."""
    out: set[str] = set()
    for v in (node.get("config") or {}).values():
        if isinstance(v, str):
            out.add(v)
        elif isinstance(v, list):
            out.update(x for x in v if isinstance(x, str))
    return out


def connection_usage_index(db: Session, ws: str) -> dict[str, list[dict]]:
    from app.db.models import KnowledgeCollection, McpServer, Provider, Webhook, Worker  # noqa: F401

    idx: dict[str, list[dict]] = {}
    seen: set[tuple[str, str, str]] = set()
    for p, g in pipeline_graphs(db, ws):
        for n in _nodes(g):
            for ref in config_refs(n):
                if not ref.startswith("con"):
                    continue
                k = (ref, p.id, str(n.get("id")))
                if k in seen:
                    continue
                seen.add(k)
                idx.setdefault(ref, []).append({"resource_type": "pipeline", "resource_id": p.id,
                                                "node_id": str(n.get("id")),
                                                "label": f"{p.name} › {n.get('name') or n.get('type')}"})
    for model, rtype, label in ((Provider, "provider", "Model provider"), (Worker, "worker", "Worker"),
                                (McpServer, "mcp_server", "MCP server"),
                                (KnowledgeCollection, "knowledge_collection", "Knowledge")):
        for r in db.scalars(select(model).where(model.workspace_id == ws, model.connection_id.is_not(None))
                            .order_by(model.created_at, model.id)).all():
            idx.setdefault(r.connection_id, []).append({"resource_type": rtype, "resource_id": r.id,
                                                        "label": f"{label} · {r.name}"})
    return idx


def usages_of_connection(db: Session, ws: str, connection_id: str) -> list[dict]:
    return connection_usage_index(db, ws).get(connection_id, [])


def parse(model: Any, body: Any) -> Any:
    """Validate a JSON body (missing/empty body → {}) with a Pydantic model; failures → 422 `validation`
    (the portal mock's code) with `errors[] {field, message}`. Input values are never echoed."""
    from pydantic import ValidationError

    from app.core.errors import ApiError

    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise ApiError(422, "validation", "The request body must be a JSON object")
    try:
        return model.model_validate(body)
    except ValidationError as exc:
        errors = [{"field": ".".join(str(p) for p in e.get("loc", ())), "message": e.get("msg", "invalid")}
                  for e in exc.errors(include_input=False, include_url=False)]
        detail = "; ".join(f"{x['field']}: {x['message']}" if x["field"] else x["message"] for x in errors[:5])
        raise ApiError(422, "validation", detail or "Invalid request", errors=errors) from exc


def validation(detail: str, **extra: Any) -> Exception:
    from app.core.errors import ApiError

    return ApiError(422, "validation", detail, **extra)


def not_found(what: str = "Resource", rid: str | None = None) -> Exception:
    """404 `not_found` with the mock's title ("Connection not found")."""
    from app.core.errors import ApiError

    return ApiError(404, "not_found", f"{what} {rid} was not found" if rid else f"{what} was not found",
                    title=f"{what} not found")
