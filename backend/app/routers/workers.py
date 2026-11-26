"""Document / image workers (BE1). Live calls go through app.services.adapters; run_mode=simulate returns the
portal mock's deterministic health and PagesResult so the portal's "test invoke" works offline."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, conflict
from app.core.security import AuthContext, get_auth, require_scope
from app.core.time import iso, utcnow
from app.db.models import Connection, Worker
from app.db.session import get_db
from app.routers._be1 import not_found, no_content, node_usages, parse, simulated, validation
from app.services import adapters, audit
from app.services.adapters.auth import connection_auth
from app.services.adapters.http import UpstreamError, safe_url

router = APIRouter(tags=["Workers"])

KINDS = ("ocr", "layout", "image", "spreadsheet", "classifier", "custom")


class WorkerIn(BaseModel):
    name: str | None = None
    kind: str | None = None
    base_url: str | None = None
    adapter: str | None = None
    invoke_path: str | None = None
    health_path: str | None = None
    connection_id: str | None = None
    accepts: list[str] | None = None
    capabilities: list[str] | None = None
    timeout_s: int | None = None
    max_concurrency: int | None = None
    data_region: str | None = None


def worker_out(w: Worker) -> dict:
    out = {"id": w.id, "name": w.name, "kind": w.kind, "base_url": w.base_url, "adapter": w.adapter,
           "invoke_path": w.invoke_path or adapters.get_adapter(w).default_invoke_path,
           "health_path": w.health_path or "/health", "connection_id": w.connection_id,
           "accepts": list(w.accepts or []), "capabilities": list(w.capabilities or []),
           "timeout_s": w.timeout_s or 60, "max_concurrency": w.max_concurrency or 4, "data_region": w.data_region,
           "health": w.health or {"status": "unknown"}, "created_at": iso(w.created_at), "updated_at": iso(w.updated_at)}
    return {k: v for k, v in out.items() if v is not None or k == "connection_id"}


def get_worker_row(db: Session, ws: str, wid: str) -> Worker:
    w = db.scalar(select(Worker).where(Worker.workspace_id == ws, Worker.id == wid))
    if w is None:
        raise not_found("Worker", wid)
    return w


def _url(v: str | None) -> str:
    v = (v or "").strip()
    if not v.startswith(("http://", "https://")):
        raise validation("base_url must be an http(s) URL")
    return v.rstrip("/")


def _adapter(v: str | None) -> str:
    if v not in adapters.ADAPTERS:
        raise validation(f"adapter must be one of {', '.join(adapters.ADAPTERS)}")
    return v


def _positive(name: str, v: int | None) -> int | None:
    if v is not None and v < 1:
        raise validation(f"{name} must be at least 1")
    return v


def _check_connection(db: Session, ws: str, cid: str | None) -> None:
    if cid and db.scalar(select(Connection.id).where(Connection.workspace_id == ws, Connection.id == cid)) is None:
        raise validation(f"Connection {cid} does not exist")


@router.get("/workers")
def list_workers(kind: str | None = Query(None), auth: AuthContext = Depends(get_auth),
                 db: Session = Depends(get_db)) -> dict:
    stmt = select(Worker).where(Worker.workspace_id == auth.workspace_id)
    if kind:
        stmt = stmt.where(Worker.kind == kind)
    return {"data": [worker_out(w) for w in db.scalars(stmt.order_by(Worker.created_at, Worker.id)).all()]}


@router.post("/workers", status_code=201)
def register_worker(body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("pipelines:write")),
                    db: Session = Depends(get_db)) -> dict:
    req = parse(WorkerIn, body)
    if not (req.name or "").strip() or not req.kind or not req.base_url or not req.adapter:
        raise validation("name, kind, base_url and adapter are required")
    if req.kind not in KINDS:
        raise validation(f"kind must be one of {', '.join(KINDS)}")
    adapter = _adapter(req.adapter)
    _check_connection(db, auth.workspace_id, req.connection_id)
    now = utcnow()
    w = Worker(workspace_id=auth.workspace_id, name=req.name.strip(), kind=req.kind, base_url=_url(req.base_url),
               adapter=adapter, invoke_path=req.invoke_path or adapters.ADAPTERS[adapter].default_invoke_path,
               health_path=req.health_path or "/health", connection_id=req.connection_id or None,
               accepts=req.accepts or [], capabilities=req.capabilities or [],
               timeout_s=_positive("timeout_s", req.timeout_s) or 60,
               max_concurrency=_positive("max_concurrency", req.max_concurrency) or 4, data_region=req.data_region,
               health={"status": "unknown"}, created_at=now, updated_at=now)
    db.add(w)
    db.flush()
    audit.record(db, auth, "worker.registered", "worker", w.id, summary=f"Registered worker {w.name}",
                 details={"adapter": w.adapter, "base_url": safe_url(w.base_url)})
    db.commit()
    return worker_out(w)


@router.get("/workers/{id}")
def get_worker(id: str, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:  # noqa: A002
    return worker_out(get_worker_row(db, auth.workspace_id, id))


@router.patch("/workers/{id}")
def update_worker(id: str, body: dict | None = Body(None),  # noqa: A002
                  auth: AuthContext = Depends(require_scope("pipelines:write")), db: Session = Depends(get_db)) -> dict:
    req = parse(WorkerIn, body)
    sent = req.model_fields_set
    w = get_worker_row(db, auth.workspace_id, id)
    if "name" in sent and req.name is not None:
        if not req.name.strip():
            raise validation("name is required")
        w.name = req.name.strip()
    if "kind" in sent and req.kind:
        if req.kind not in KINDS:
            raise validation(f"kind must be one of {', '.join(KINDS)}")
        w.kind = req.kind
    if "base_url" in sent and req.base_url is not None:
        w.base_url = _url(req.base_url)
    if "adapter" in sent and req.adapter is not None:
        w.adapter = _adapter(req.adapter)
    for f in ("invoke_path", "health_path"):
        if f in sent:
            setattr(w, f, getattr(req, f) or None)
    if "connection_id" in sent:
        _check_connection(db, auth.workspace_id, req.connection_id)
        w.connection_id = req.connection_id or None
    for f in ("timeout_s", "max_concurrency"):
        if f in sent and getattr(req, f) is not None:
            setattr(w, f, _positive(f, getattr(req, f)))
    for f in ("accepts", "capabilities"):
        if f in sent and getattr(req, f) is not None:
            setattr(w, f, list(getattr(req, f)))
    if "data_region" in sent:
        w.data_region = req.data_region or None
    w.updated_at = utcnow()
    audit.record(db, auth, "worker.updated", "worker", w.id, summary=f"Updated worker {w.name}",
                 details={"fields": sorted(sent)})
    db.commit()
    return worker_out(w)


def worker_usages(db: Session, ws: str, wid: str) -> list[dict]:
    return node_usages(db, ws, lambda n: (n.get("config") or {}).get("worker") == wid)


@router.delete("/workers/{id}", status_code=204)
def delete_worker(id: str, auth: AuthContext = Depends(require_scope("pipelines:write")),  # noqa: A002
                  db: Session = Depends(get_db)):
    w = get_worker_row(db, auth.workspace_id, id)
    us = worker_usages(db, auth.workspace_id, w.id)
    if us:
        raise conflict("worker_in_use", title="Worker is in use", detail=f"Used by {len(us)} step{'s' if len(us) > 1 else ''}.", usages=us)
    audit.record(db, auth, "worker.deleted", "worker", w.id, summary=f"Deleted worker {w.name}")
    db.delete(w)
    db.commit()
    return no_content()


@router.post("/workers/{id}/health")
def check_worker_health(id: str, auth: AuthContext = Depends(require_scope("pipelines:write")),  # noqa: A002
                        db: Session = Depends(get_db)) -> dict:
    w = get_worker_row(db, auth.workspace_id, id)
    if simulated():
        h = adapters.sample_health(w)
    else:
        try:
            _, settings, creds = connection_auth(db, auth.workspace_id, w.connection_id)
        except ApiError as exc:
            h = {"status": "unreachable", "checked_at": iso(utcnow()), "message": exc.detail or exc.code}
        else:
            h = adapters.health(w, creds, settings=settings)
    w.health = h
    db.commit()
    return h


class InvokeBody(BaseModel):
    document_id: str | None = None
    options: dict[str, Any] | None = None


@router.post("/workers/{id}/invoke")
def invoke_worker(id: str, body: dict | None = Body(None),  # noqa: A002
                  auth: AuthContext = Depends(require_scope("pipelines:write")), db: Session = Depends(get_db)) -> dict:
    req = parse(InvokeBody, body)
    w = get_worker_row(db, auth.workspace_id, id)
    if not req.document_id:
        raise validation("document_id is required")
    from app.services import storage

    if simulated():
        storage.get_document(db, auth.workspace_id, req.document_id)  # 404 for unknown documents
        if (w.health or {}).get("status") == "unreachable":
            raise ApiError(502, "worker_unreachable", f"POST {w.base_url}{w.invoke_path or ''} failed (simulated)",
                           title="Worker unreachable")
        return adapters.sample_pages(w)
    data, content_type, filename = storage.read_bytes(db, auth.workspace_id, req.document_id)
    _, settings, creds = connection_auth(db, auth.workspace_id, w.connection_id)
    try:
        return adapters.invoke(w, creds, data, content_type, filename, options=req.options, settings=settings)
    except UpstreamError as exc:
        code = "worker_unreachable" if exc.status_code is None and exc.code in ("upstream_error", "upstream_timeout") \
            else exc.code
        raise ApiError(502, code, exc.detail, title="Worker unreachable" if code == "worker_unreachable"
                       else "Bad gateway") from exc


__all__ = ["router", "worker_out", "get_worker_row"]
