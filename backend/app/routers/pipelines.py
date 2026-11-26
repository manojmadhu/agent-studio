"""Pipelines, Versions, Templates API (owned by BE2). Behaviour mirrors portal/src/api/mock/backend.ts.

Shared helpers used by the other BE2 routers live here too: `json_body`, `get_pipeline`, `validation_context`,
`pipeline_out`, `draft_out`, `etag headers`.
"""
from __future__ import annotations

import copy
import json
import secrets
import string
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.ids import new_id
from app.core.pagination import paginate_list
from app.core.security import AuthContext, require_scope
from app.core.time import iso, utcnow
from app.db.session import get_db
from app.services import audit
from app.services import validator as V

router = APIRouter(tags=["Pipelines", "Versions", "Templates"])

NO_STORE = {"Cache-Control": "no-store"}


# ───────────────────────── shared helpers ─────────────────────────

async def json_body(request: Request) -> dict:
    """Optional JSON object body (activate/pause/restore/... are sent without one). 400 on malformed JSON."""
    raw = await request.body()
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ApiError(400, "bad_request", "The request body is not valid JSON", title="Malformed request body") from exc
    if not isinstance(data, dict):
        raise ApiError(400, "bad_request", "The request body must be a JSON object", title="Malformed request body")
    return data


def validation_error(detail: str) -> ApiError:
    return ApiError(422, "validation", detail, title="Validation failed")


def not_found(what: str = "Resource") -> ApiError:
    return ApiError(404, "not_found", title=f"{what} not found")


def blocking(issues: list[dict], action: str) -> ApiError:
    errs = V.errors_only(issues)
    n = len(errs)
    return ApiError(422, "pipeline_invalid", f"Resolve {n} issue{'s' if n > 1 else ''} before {action}",
                    title="Pipeline has blocking issues", issues=errs)


def actor_name(auth: AuthContext) -> str:
    return auth.name or auth.email or auth.principal_id


def get_pipeline(db: Session, auth: AuthContext, pid: str) -> Any:
    from app.db.models import Pipeline

    p = db.scalar(select(Pipeline).where(Pipeline.workspace_id == auth.workspace_id, Pipeline.id == pid,
                                         Pipeline.archived_at.is_(None)))
    if p is None:
        raise not_found("Pipeline")
    return p


def get_draft(db: Session, p: Any) -> Any:
    from app.db.models import PipelineDraft

    d = db.get(PipelineDraft, p.id)
    if d is None:  # self-heal: every pipeline has a draft
        d = PipelineDraft(pipeline_id=p.id, workspace_id=p.workspace_id, graph={"nodes": [], "edges": []},
                          etag=f'W/"{p.id}-1"', changes=[], saved_by=p.updated_by)
        db.add(d)
        db.flush()
    return d


def bump_etag(d: Any) -> str:
    """Mock format W/"<id>-<rev>" (lead decision): rev+1 on every save, publish and restore."""
    try:
        rev = int(str(d.etag).rstrip('"').rsplit("-", 1)[1])
    except (IndexError, ValueError):
        rev = 0
    d.etag = f'W/"{d.pipeline_id}-{rev + 1}"'
    return d.etag


def validation_context(db: Session, workspace_id: str, pipeline: Any | None = None) -> V.ValidationContext:
    from app.db.models import Connection, Policy, Provider, Worker

    conns = db.scalars(select(Connection.id).where(Connection.workspace_id == workspace_id)).all()
    provs = db.scalars(select(Provider).where(Provider.workspace_id == workspace_id)).all()
    workers = db.scalars(select(Worker).where(Worker.workspace_id == workspace_id)).all()
    allowed = None
    if pipeline is not None:
        pols = db.scalars(select(Policy).where(Policy.workspace_id == workspace_id)).all()
        allowed = V.allowed_regions_for(
            [{"kind": x.kind, "enabled": x.enabled, "scope": x.scope or {}, "config": x.config or {}} for x in pols],
            pipeline.id, pipeline.owner_team)
    return V.ValidationContext.of(
        connections=conns,
        providers=[V.ProviderRef(x.id, x.name, bool(x.enabled), x.data_region) for x in provs],
        workers=[{"id": w.id, "name": w.name, "data_region": w.data_region} for w in workers],
        allowed_regions=allowed)


def graph_issues(db: Session, workspace_id: str, graph: dict, pipeline: Any | None = None) -> list[dict]:
    return V.issues(V.normalize_graph(graph), validation_context(db, workspace_id, pipeline))


def _review_waiting(db: Session, workspace_id: str, ids: list[str]) -> dict[str, int]:
    from app.db.models import ReviewItem

    if not ids:
        return {}
    rows = db.execute(select(ReviewItem.pipeline_id, func.count()).where(
        ReviewItem.workspace_id == workspace_id, ReviewItem.pipeline_id.in_(ids),
        ReviewItem.status.in_(["open", "claimed"])).group_by(ReviewItem.pipeline_id)).all()
    return {pid: int(n) for pid, n in rows}


def pipeline_out(db: Session, p: Any, draft: Any = None, waiting: int | None = None) -> dict:
    d = draft if draft is not None else get_draft(db, p)
    g = d.graph or {"nodes": [], "edges": []}
    intake = V.intake_of(g)
    if waiting is None:
        waiting = _review_waiting(db, p.workspace_id, [p.id]).get(p.id, 0)
    kpi = {"documents_24h": 0, "straight_through_rate": 0, "median_ms": 0, "review_waiting": 0, **(p.kpi or {})}
    kpi["review_waiting"] = waiting
    return {
        "id": p.id, "name": p.name, "description": p.description or "", "status": p.status,
        "owner_team": p.owner_team or "", "live_version": p.live_version, "draft_changes": list(d.changes or []),
        "kpi": kpi, "created_at": iso(p.created_at), "updated_at": iso(p.updated_at), "updated_by": p.updated_by,
        "intake_type": intake["type"] if intake else "", "step_count": V.step_count(g),
        "ai_component_count": V.ai_step_count(g),
    }


def draft_out(db: Session, p: Any, d: Any) -> dict:
    return {"pipeline_id": p.id, "graph": d.graph, "etag": d.etag, "changes": list(d.changes or []),
            "saved_at": iso(d.updated_at), "saved_by": d.saved_by,
            "issues": V.issues(d.graph, validation_context(db, p.workspace_id, p))}


def draft_response(db: Session, p: Any, d: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(draft_out(db, p, d), status_code=status, headers={"ETag": d.etag, **NO_STORE})


def _rid(n: int = 6) -> str:
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def new_pipeline(db: Session, auth: AuthContext, name: str, graph: dict, changes: list[str], **extra: Any) -> Any:
    from app.db.models import Pipeline, PipelineDraft

    g = V.normalize_graph(graph)
    intake = V.intake_of(g)
    now = utcnow()
    p = Pipeline(id=new_id("pipeline"), workspace_id=auth.workspace_id, name=name,
                 description=extra.get("description") or "", status="draft", active=False,
                 owner_team=extra.get("owner_team") or "Finance Operations", live_version=None,
                 intake_type=intake["type"] if intake else None, template_id=extra.get("template_id"),
                 kpi={"documents_24h": 0, "straight_through_rate": 0, "median_ms": 0, "review_waiting": 0},
                 created_by=actor_name(auth), updated_by=actor_name(auth), created_at=now, updated_at=now)
    db.add(p)
    db.flush()
    d = PipelineDraft(pipeline_id=p.id, workspace_id=auth.workspace_id, graph=g, etag=f'W/"{p.id}-1"',
                      changes=list(changes), saved_by=actor_name(auth), updated_at=now)
    db.add(d)
    db.flush()
    return p


def _touch(p: Any, auth: AuthContext) -> None:
    p.updated_at = utcnow()
    p.updated_by = actor_name(auth)


# ───────────────────────── pipelines ─────────────────────────

@router.get("/pipelines")
def list_pipelines(status: str | None = None, q: str | None = None, limit: int | None = Query(None, ge=1, le=200),
                   cursor: str | None = None, auth: AuthContext = Depends(require_scope("pipelines:read")),
                   db: Session = Depends(get_db)) -> JSONResponse:
    from app.db.models import Pipeline, PipelineDraft

    rows = db.scalars(select(Pipeline).where(Pipeline.workspace_id == auth.workspace_id, Pipeline.archived_at.is_(None))
                      .order_by(Pipeline.created_at, Pipeline.id)).all()
    needle = (q or "").lower()
    rows = [p for p in rows if (not status or p.status == status) and needle in (p.name + (p.owner_team or "")).lower()]
    drafts = {d.pipeline_id: d for d in db.scalars(select(PipelineDraft).where(
        PipelineDraft.workspace_id == auth.workspace_id, PipelineDraft.pipeline_id.in_([p.id for p in rows]))).all()} \
        if rows else {}
    waiting = _review_waiting(db, auth.workspace_id, [p.id for p in rows])
    page = paginate_list(rows, limit=limit, cursor=cursor, key=lambda p: p.id,
                         serialize=lambda p: pipeline_out(db, p, drafts.get(p.id), waiting.get(p.id, 0)))
    db.commit()  # get_draft may self-heal
    return JSONResponse(page, headers=NO_STORE)


@router.post("/pipelines", status_code=201)
async def create_pipeline(request: Request, auth: AuthContext = Depends(require_scope("pipelines:write")),
                          db: Session = Depends(get_db)) -> JSONResponse:
    body = await json_body(request)
    name = str(body.get("name") or "").strip()
    t = str(body.get("intake_type") or "")
    if not name:
        raise validation_error("Name is required")
    if len(name) > 120:
        raise validation_error("Name must be at most 120 characters")
    d = V.catalog().get(t)
    if not d or d.get("stage") != "intake":
        raise validation_error("intake_type must be an intake step type")
    node = {"id": "n_" + _rid(), "type": t, "name": d["name"], "x": 80, "y": 200, "config": V.defaults(t),
            "settings": dict(V.SETTINGS_DEFAULT)}
    p = new_pipeline(db, auth, name, {"nodes": [node], "edges": []}, ["Created pipeline"],
                     description=str(body.get("description") or ""),
                     owner_team=str(body.get("owner_team") or "Finance Operations"))
    audit.record(db, auth, "pipeline.created", "pipeline", p.id, summary=f"Created pipeline {name}")
    out = pipeline_out(db, p)
    db.commit()
    return JSONResponse(out, status_code=201, headers={"Location": f"/v1/pipelines/{p.id}"})


@router.get("/pipelines/{id}")
def get_pipeline_route(id: str, auth: AuthContext = Depends(require_scope("pipelines:read")),
                       db: Session = Depends(get_db)) -> JSONResponse:
    p = get_pipeline(db, auth, id)
    out = pipeline_out(db, p)
    db.commit()
    return JSONResponse(out, headers=NO_STORE)


@router.patch("/pipelines/{id}")
async def update_pipeline(id: str, request: Request, auth: AuthContext = Depends(require_scope("pipelines:write")),
                          db: Session = Depends(get_db)) -> dict:
    body = await json_body(request)
    p = get_pipeline(db, auth, id)
    changed = {}
    for k in ("name", "description", "owner_team"):
        if isinstance(body.get(k), str):
            if k == "name" and not body[k].strip():
                raise validation_error("Name is required")
            setattr(p, k, body[k])
            changed[k] = body[k]
    _touch(p, auth)
    d = get_draft(db, p)
    if "Edited pipeline details" not in (d.changes or []):
        d.changes = [*(d.changes or []), "Edited pipeline details"]
    audit.record(db, auth, "pipeline.updated", "pipeline", p.id, summary="Edited pipeline details", details=changed)
    out = pipeline_out(db, p, d)
    db.commit()
    return out


@router.delete("/pipelines/{id}", status_code=204)
def delete_pipeline(id: str, auth: AuthContext = Depends(require_scope("pipelines:write")),
                    db: Session = Depends(get_db)) -> Response:
    p = get_pipeline(db, auth, id)
    if p.status == "active":
        raise ApiError(409, "pipeline_active", "Pause the pipeline before deleting it.", title="Pipeline is active")
    # Soft delete: runs and review items keep their FK to the pipeline (retained for audit, per the contract).
    p.archived_at = utcnow()
    p.active = False
    _touch(p, auth)
    audit.record(db, auth, "pipeline.deleted", "pipeline", p.id, summary=f"Deleted pipeline {p.name}")
    db.commit()
    return Response(status_code=204)


@router.post("/pipelines/{id}/duplicate", status_code=201)
async def duplicate_pipeline(id: str, request: Request, auth: AuthContext = Depends(require_scope("pipelines:write")),
                             db: Session = Depends(get_db)) -> JSONResponse:
    body = await json_body(request)
    src = get_pipeline(db, auth, id)
    g = copy.deepcopy(get_draft(db, src).graph)
    name = str(body.get("name") or "").strip() or src.name + " (copy)"
    p = new_pipeline(db, auth, name, g, ["Duplicated"], description=src.description or "", owner_team=src.owner_team)
    audit.record(db, auth, "pipeline.duplicated", "pipeline", p.id, summary=f"Duplicated {src.id}",
                 details={"source": src.id})
    out = pipeline_out(db, p)
    db.commit()
    return JSONResponse(out, status_code=201)


def _live_version(db: Session, p: Any) -> Any:
    from app.db.models import PipelineVersion

    if p.live_version is None:
        return None
    return db.scalar(select(PipelineVersion).where(PipelineVersion.workspace_id == p.workspace_id,
                                                   PipelineVersion.pipeline_id == p.id,
                                                   PipelineVersion.version == p.live_version))


@router.post("/pipelines/{id}/activate")
def activate_pipeline(id: str, auth: AuthContext = Depends(require_scope("pipelines:write")),
                      db: Session = Depends(get_db)) -> dict:
    p = get_pipeline(db, auth, id)
    live = _live_version(db, p)
    if live is None:
        raise ApiError(422, "no_live_version", "Publish a version before activating the pipeline.",
                       title="No published version")
    iss = graph_issues(db, auth.workspace_id, live.graph, p)
    if V.error_count(iss):
        raise blocking(iss, "activating")
    p.status, p.active = "active", True
    _touch(p, auth)
    audit.record(db, auth, "pipeline.activated", "pipeline", p.id, summary=f"Activated v{p.live_version}")
    out = pipeline_out(db, p)
    db.commit()
    return out


@router.post("/pipelines/{id}/pause")
def pause_pipeline(id: str, auth: AuthContext = Depends(require_scope("pipelines:write")),
                   db: Session = Depends(get_db)) -> dict:
    p = get_pipeline(db, auth, id)
    p.status, p.active = "paused", False
    _touch(p, auth)
    audit.record(db, auth, "pipeline.paused", "pipeline", p.id, summary="Paused")
    out = pipeline_out(db, p)
    db.commit()
    return out


# ───────────────────────── draft & validation ─────────────────────────

@router.get("/pipelines/{id}/draft")
def get_draft_route(id: str, auth: AuthContext = Depends(require_scope("pipelines:read")),
                    db: Session = Depends(get_db)) -> JSONResponse:
    p = get_pipeline(db, auth, id)
    d = get_draft(db, p)
    resp = draft_response(db, p, d)
    db.commit()
    return resp


@router.put("/pipelines/{id}/draft")
async def save_draft(id: str, request: Request, auth: AuthContext = Depends(require_scope("pipelines:write")),
                     db: Session = Depends(get_db)) -> JSONResponse:
    p = get_pipeline(db, auth, id)
    d = get_draft(db, p)
    im = request.headers.get("if-match")
    if not im:
        raise ApiError(428, "precondition_required", "Send the draft ETag in If-Match.", title="If-Match required")
    if im != d.etag:  # exact byte comparison of the weak tag (lead decision)
        raise ApiError(412, "etag_mismatch", f"{d.saved_by} saved this draft {iso(d.updated_at)}.",
                       title="The draft was changed by someone else", current_etag=d.etag)
    body = await json_body(request)
    g = body.get("graph")
    if not V.is_valid_graph_shape(g):
        raise validation_error("graph is required")
    d.graph = V.normalize_graph(g)
    bump_etag(d)
    change = body.get("change") if isinstance(body.get("change"), str) else ""
    if change and change not in (d.changes or []):
        d.changes = [*(d.changes or []), change]
    now = utcnow()
    d.updated_at = now
    d.saved_by = actor_name(auth)
    p.intake_type = (V.intake_of(d.graph) or {}).get("type")
    _touch(p, auth)
    p.updated_at = now
    iss = V.issues(d.graph, validation_context(db, auth.workspace_id, p))
    d.issues = iss
    audit.record(db, auth, "pipeline.draft_saved", "pipeline", p.id, summary=change or "Saved draft",
                 details={"etag": d.etag})
    db.commit()
    return draft_response(db, p, d)


@router.post("/pipelines/{id}/validate")
async def validate_pipeline(id: str, request: Request, auth: AuthContext = Depends(require_scope("pipelines:read")),
                            db: Session = Depends(get_db)) -> dict:
    body = await json_body(request)
    p = get_pipeline(db, auth, id)
    g = body if body.get("nodes") else get_draft(db, p).graph
    if not V.is_valid_graph_shape(g):
        raise validation_error("graph must have nodes and edges arrays")
    iss = graph_issues(db, auth.workspace_id, g, p)
    db.commit()
    return {"valid": not V.error_count(iss), "issues": iss}


# ───────────────────────── versions ─────────────────────────

def _versions(db: Session, p: Any) -> list[Any]:
    from app.db.models import PipelineVersion

    return db.scalars(select(PipelineVersion).where(PipelineVersion.workspace_id == p.workspace_id,
                                                    PipelineVersion.pipeline_id == p.id)
                      .order_by(PipelineVersion.version.desc())).all()


def version_summary(v: Any, live: int | None) -> dict:
    return {"version": v.version, "release_note": v.release_note or "", "published_at": iso(v.published_at),
            "published_by": v.published_by or "", "live": v.version == live}


def _get_version(db: Session, p: Any, version: int) -> Any:
    from app.db.models import PipelineVersion

    v = db.scalar(select(PipelineVersion).where(PipelineVersion.workspace_id == p.workspace_id,
                                                PipelineVersion.pipeline_id == p.id, PipelineVersion.version == version))
    if v is None:
        raise not_found("Version")
    return v


@router.get("/pipelines/{id}/versions")
def list_versions(id: str, auth: AuthContext = Depends(require_scope("pipelines:read")),
                  db: Session = Depends(get_db)) -> dict:
    p = get_pipeline(db, auth, id)
    return {"data": [version_summary(v, p.live_version) for v in _versions(db, p)]}


@router.post("/pipelines/{id}/versions", status_code=201)
async def publish_version(id: str, request: Request, auth: AuthContext = Depends(require_scope("pipelines:write")),
                          db: Session = Depends(get_db)) -> JSONResponse:
    from app.db.models import PipelineVersion

    body = await json_body(request)
    p = get_pipeline(db, auth, id)
    d = get_draft(db, p)
    if body.get("draft_etag") and body["draft_etag"] != d.etag:
        raise ApiError(412, "etag_mismatch", title="The draft changed since you reviewed it", current_etag=d.etag)
    note_in = body.get("release_note")
    if note_in is not None and not isinstance(note_in, str):
        raise validation_error("release_note must be a string")
    if isinstance(note_in, str) and len(note_in) > 500:
        raise validation_error("release_note must be at most 500 characters")
    iss = V.issues(d.graph, validation_context(db, auth.workspace_id, p))
    if V.error_count(iss):
        raise blocking(iss, "publishing")
    vs = _versions(db, p)
    nv = max([0, *[v.version for v in vs]]) + 1
    note = (note_in or "").strip() or ((d.changes or [None])[0]) or "Published"
    now = utcnow()
    ver = PipelineVersion(id=new_id("version"), workspace_id=auth.workspace_id, pipeline_id=p.id, version=nv,
                          graph=copy.deepcopy(d.graph), release_note=note, published_at=now,
                          published_by=actor_name(auth), created_at=now)
    db.add(ver)
    p.live_version = nv
    d.changes = []
    d.base_version = nv
    bump_etag(d)
    _touch(p, auth)
    audit.record(db, auth, "pipeline.published", "pipeline", p.id, summary=f"Published v{nv}",
                 details={"version": nv, "release_note": note})
    try:
        from app.services import webhooks

        webhooks.emit(db, auth.workspace_id, "pipeline.published",
                      {"pipeline_id": p.id, "version": nv, "release_note": note})
    except Exception:  # noqa: BLE001
        pass
    out = {**version_summary(ver, nv), "graph": ver.graph}
    db.commit()
    return JSONResponse(out, status_code=201)


@router.get("/pipelines/{id}/versions/{version}")
def get_version(id: str, version: int, auth: AuthContext = Depends(require_scope("pipelines:read")),
                db: Session = Depends(get_db)) -> dict:
    p = get_pipeline(db, auth, id)
    v = _get_version(db, p, version)
    return {**version_summary(v, p.live_version), "graph": v.graph}


@router.post("/pipelines/{id}/versions/{version}/restore")
def restore_version(id: str, version: int, auth: AuthContext = Depends(require_scope("pipelines:write")),
                    db: Session = Depends(get_db)) -> JSONResponse:
    p = get_pipeline(db, auth, id)
    v = _get_version(db, p, version)
    d = get_draft(db, p)
    d.graph = copy.deepcopy(v.graph)
    bump_etag(d)
    ch = f"Restored version {v.version}"
    if ch not in (d.changes or []):
        d.changes = [*(d.changes or []), ch]
    d.updated_at = utcnow()
    d.saved_by = actor_name(auth)
    _touch(p, auth)
    audit.record(db, auth, "pipeline.restored", "pipeline", p.id, summary=ch, details={"version": v.version})
    db.commit()
    return draft_response(db, p, d)


# ───────────────────────── templates ─────────────────────────

@router.get("/templates")
def list_templates(auth: AuthContext = Depends(require_scope("pipelines:read")), db: Session = Depends(get_db)) -> dict:
    from app.db.models import Template

    rows = db.scalars(select(Template).where(Template.workspace_id == auth.workspace_id)
                      .order_by(Template.created_at, Template.id)).all()
    return {"data": [{"id": t.id, "name": t.name, "description": t.description or "",
                      "step_count": V.step_count(t.graph or {}), "ai_component_count": V.sub_count(t.graph or {})}
                     for t in rows]}


def _reid(g: dict, prefix: str) -> dict:
    g = copy.deepcopy(g)
    idmap = {}
    for i, n in enumerate(g.get("nodes", []), 1):
        idmap[n["id"]] = f"{prefix}_n{i}"
        n["id"] = idmap[n["id"]]
    for i, e in enumerate(g.get("edges", []), 1):
        e["id"] = f"{prefix}_e{i}"
        e["from"] = idmap.get(e.get("from"), e.get("from"))
        e["to"] = idmap.get(e.get("to"), e.get("to"))
    return g


@router.post("/templates/{id}/instantiate", status_code=201)
async def instantiate_template(id: str, request: Request, auth: AuthContext = Depends(require_scope("pipelines:write")),
                               db: Session = Depends(get_db)) -> JSONResponse:
    from app.db.models import Template

    body = await json_body(request)
    t = db.scalar(select(Template).where(Template.workspace_id == auth.workspace_id, Template.id == id))
    if t is None:
        raise not_found("Template")
    name = str(body.get("name") or "").strip() or t.name
    p = new_pipeline(db, auth, name, _reid(t.graph or {"nodes": [], "edges": []}, "t" + _rid()),
                     ["Created from template"], template_id=t.id)
    audit.record(db, auth, "pipeline.created", "pipeline", p.id, summary=f"Created from template {t.id}",
                 details={"template_id": t.id})
    out = pipeline_out(db, p)
    db.commit()
    return JSONResponse(out, status_code=201)
