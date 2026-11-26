"""Runs API (owned by BE2): test runs, single-step test, list/detail, SSE events, result, cancel, retry."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.errors import ApiError
from app.core.pagination import paginate
from app.core.security import AuthContext, require_scope
from app.core.time import ensure_aware, iso, utcnow
from app.db.session import get_db, session_factory
from app.services import audit, run_engine, storage
from app.services import validator as V
from app.services.event_bus import bus
from app.routers.pipelines import (
    actor_name, blocking, get_draft, get_pipeline, json_body, not_found, validation_context, validation_error,
)

router = APIRouter(tags=["Runs"])

KEEPALIVE_S = 15.0
DB_POLL_S = 1.0


def get_run(db: Session, auth: AuthContext, rid: str) -> Any:
    from app.db.models import Run

    r = db.scalar(select(Run).where(Run.workspace_id == auth.workspace_id, Run.id == rid))
    if r is None:
        raise not_found("Run")
    return r


def resolve_graph(db: Session, p: Any, version: Any) -> tuple[dict, int | None]:
    from app.db.models import PipelineVersion

    if version in (None, "draft"):
        return get_draft(db, p).graph, None
    if isinstance(version, bool) or not isinstance(version, int):
        try:
            version = int(version)
        except (TypeError, ValueError) as exc:
            raise validation_error('version must be an integer or "draft"') from exc
    v = db.scalar(select(PipelineVersion).where(PipelineVersion.workspace_id == p.workspace_id,
                                                PipelineVersion.pipeline_id == p.id, PipelineVersion.version == version))
    if v is None:
        raise not_found("Version")
    return v.graph, version


# ───────────────────────── test runs ─────────────────────────

@router.post("/pipelines/{id}/test-runs", status_code=202)
async def create_test_run(id: str, request: Request, auth: AuthContext = Depends(require_scope("runs:write")),
                          db: Session = Depends(get_db)) -> JSONResponse:
    body = await json_body(request)
    p = get_pipeline(db, auth, id)
    graph, version = resolve_graph(db, p, body.get("version", "draft"))
    iss = V.issues(V.normalize_graph(graph), validation_context(db, auth.workspace_id, p))
    if V.error_count(iss):
        raise blocking(iss, "testing")
    doc = storage.get_document(db, auth.workspace_id, str(body.get("document_id") or "doc_inv_sample"))
    run = run_engine.create_run(db, workspace_id=auth.workspace_id, pipeline_id=p.id, graph=V.normalize_graph(graph),
                                version=version, doc=doc, test=True, deliver=bool(body.get("deliver", False)),
                                trigger="test", created_by=actor_name(auth))
    audit.record(db, auth, "run.test_started", "run", run.id, summary=f"Test run of {p.id}",
                 details={"pipeline_id": p.id, "version": version if version is not None else "draft",
                          "document_id": doc.id, "deliver": run.deliver})
    out = run_engine.run_out(db, run, doc)
    db.commit()
    run_engine.enqueue(run.id)
    return JSONResponse(out, status_code=202, headers={"Location": f"/v1/runs/{run.id}"})


@router.post("/pipelines/{id}/steps/{node_id}/test")
async def test_step(id: str, node_id: str, request: Request, auth: AuthContext = Depends(require_scope("runs:write")),
                    db: Session = Depends(get_db)) -> dict:
    body = await json_body(request)
    p = get_pipeline(db, auth, id)
    g = get_draft(db, p).graph
    n = V.node_by_id(g, node_id)
    if n is None:
        raise not_found("Step")
    doc = storage.get_document(db, auth.workspace_id, str(body.get("document_id") or "doc_inv_sample"))
    if V.is_sub(n):
        output: dict = {"component": n.get("name"), "config": n.get("config")}
    else:
        output = run_engine.step_output(g, n, run_engine.engine_doc_for(doc))
    audit.record(db, auth, "pipeline.step_tested", "pipeline", p.id, summary=f"Tested step {node_id}",
                 details={"node_id": node_id, "document_id": doc.id})
    db.commit()
    return {"node_id": n["id"], "status": "completed", "started_at": iso(utcnow()), "duration_ms": 420, "attempt": 1,
            "output": output}


# ───────────────────────── list / detail ─────────────────────────

def _parse_dt(v: str | None, name: str) -> datetime | None:
    if not v:
        return None
    try:
        return ensure_aware(datetime.fromisoformat(v.replace("Z", "+00:00")))
    except ValueError as exc:
        raise ApiError(400, "bad_request", f"{name} must be an RFC 3339 date-time", title="Bad request") from exc


@router.get("/runs")
def list_runs(pipeline_id: str | None = None, status: str | None = None, test: bool | None = None,
              from_: str | None = Query(None, alias="from"), to: str | None = None,
              limit: int | None = Query(None, ge=1, le=200), cursor: str | None = None,
              auth: AuthContext = Depends(require_scope("runs:read")), db: Session = Depends(get_db)) -> dict:
    from app.db.models import Run

    stmt = select(Run).where(Run.workspace_id == auth.workspace_id)
    if pipeline_id:
        stmt = stmt.where(Run.pipeline_id == pipeline_id)
    if status:
        stmt = stmt.where(Run.status == status)
    if test is not None:
        stmt = stmt.where(Run.test.is_(test))
    t0, t1 = _parse_dt(from_, "from"), _parse_dt(to, "to")
    if t0:
        stmt = stmt.where(Run.created_at >= t0)
    if t1:
        stmt = stmt.where(Run.created_at < t1)
    return paginate(db, stmt, Run, limit=limit, cursor=cursor, serialize=lambda r: run_engine.run_out(db, r))


@router.get("/runs/{id}")
def get_run_route(id: str, auth: AuthContext = Depends(require_scope("runs:read")),
                  db: Session = Depends(get_db)) -> dict:
    r = get_run(db, auth, id)
    return {**run_engine.run_out(db, r), "steps": run_engine.run_steps_out(db, r)}


@router.get("/runs/{id}/result")
def get_run_result(id: str, auth: AuthContext = Depends(require_scope("runs:read")),
                   db: Session = Depends(get_db)) -> dict:
    from app.db.models import RunResult

    r = get_run(db, auth, id)
    rr = db.get(RunResult, r.id)
    if rr is not None and (r.status in run_engine.TERMINAL or r.status == "review"):
        return run_engine.result_out(rr)
    if r.status in ("queued", "running"):
        raise ApiError(409, "run_in_progress", "The result is available after run.finished",
                       title="Run has not finished")
    raise not_found("Run result")


# ───────────────────────── SSE ─────────────────────────

def _fetch_events(workspace_id: str, run_id: str, after: int) -> tuple[list[tuple[int, str, dict]], str | None]:
    from app.db.models import Run, RunEvent

    with session_factory()() as db:
        status = db.scalar(select(Run.status).where(Run.workspace_id == workspace_id, Run.id == run_id))
        rows = db.execute(select(RunEvent.seq, RunEvent.event, RunEvent.payload)
                          .where(RunEvent.workspace_id == workspace_id, RunEvent.run_id == run_id, RunEvent.seq > after)
                          .order_by(RunEvent.seq).limit(500)).all()
        return [(int(s), e, p) for s, e, p in rows], status


def _has_finished(workspace_id: str, run_id: str) -> bool:
    from app.db.models import RunEvent

    with session_factory()() as db:
        return db.scalar(select(RunEvent.seq).where(RunEvent.workspace_id == workspace_id, RunEvent.run_id == run_id,
                                                    RunEvent.event == "run.finished").limit(1)) is not None


def frame(seq: int, event: str, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {seq}\nevent: {event}\ndata: {data}\n\n"


async def event_stream(request: Request, workspace_id: str, run_id: str, after: int) -> AsyncIterator[str]:
    last = after
    last_write = time.monotonic()
    yield ": stream open\n\n"  # flush headers immediately (first event < 1 s even when the run is still queued)
    while True:
        events, status = await run_in_threadpool(_fetch_events, workspace_id, run_id, last)
        for seq, ev, payload in events:
            last = seq
            last_write = time.monotonic()
            yield frame(seq, ev, payload)
            if ev == "run.finished":
                return
        if status is None:
            return
        if not events and status in run_engine.TERMINAL and run_engine_finished_without_event(status) and \
                not await run_in_threadpool(_has_finished, workspace_id, run_id):
            # Runs that never went through the engine (seeded history): close with a synthetic terminal event.
            yield frame(last + 1, "run.finished", {"event": "run.finished", "status": status, "run_id": run_id,
                                                   "at": iso(utcnow())})
            return
        if await request.is_disconnected():
            return
        if time.monotonic() - last_write >= KEEPALIVE_S:
            last_write = time.monotonic()
            yield ": keep-alive\n\n"
        # wait for the bus (same process) or fall back to polling the table
        deadline = time.monotonic() + DB_POLL_S
        while time.monotonic() < deadline and bus.latest(run_id) <= last:
            await asyncio.sleep(0.05)


def run_engine_finished_without_event(status: str) -> bool:
    return status in run_engine.TERMINAL


@router.get("/runs/{id}/events")
def stream_run_events(id: str, request: Request, last_event_id: str | None = Query(None),
                      auth: AuthContext = Depends(require_scope("runs:read")),
                      db: Session = Depends(get_db)) -> StreamingResponse:
    get_run(db, auth, id)
    db.close()
    raw = request.headers.get("last-event-id") or last_event_id or "0"
    try:
        after = max(0, int(str(raw).strip() or 0))
    except ValueError:
        after = 0
    return StreamingResponse(event_stream(request, auth.workspace_id, id, after), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


# ───────────────────────── cancel / retry ─────────────────────────

@router.post("/runs/{id}/cancel", status_code=202)
def cancel_run(id: str, auth: AuthContext = Depends(require_scope("runs:write")),
               db: Session = Depends(get_db)) -> Response:
    r = get_run(db, auth, id)
    if r.status in run_engine.TERMINAL or (r.status == "review" and r.test):
        raise ApiError(409, "run_finished", f"The run is already {r.status}", title="Run already finished")
    if r.status == "review":
        if (r.input or {}).get("_engine") is not None or r.graph:
            run_engine.cancel_paused(db, r)
        else:  # seeded run waiting in review without engine state
            _finish_without_engine(db, r, "cancelled")
    else:
        r.cancel_requested = True
    audit.record(db, auth, "run.cancelled", "run", r.id, summary="Cancel requested")
    db.commit()
    bus.publish(r.id, bus.latest(r.id) + 1)
    return Response(status_code=202, headers={"Content-Length": "0"})


def _finish_without_engine(db: Session, r: Any, status: str, error: dict | None = None) -> None:
    """Terminal transition for runs that have no engine state (seeded history): status + run.finished event."""
    from sqlalchemy import func

    from app.core.ids import new_id
    from app.db.models import RunEvent

    now = utcnow()
    r.status = status
    r.finished_at = now
    if error:
        r.error = error
    r.duration_ms = max(1, int((now - (ensure_aware(r.started_at or r.created_at) or now)).total_seconds() * 1000))
    seq = int(db.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == r.id)) or 0) + 1
    db.add(RunEvent(id=new_id("evt"), workspace_id=r.workspace_id, run_id=r.id, seq=seq, event="run.finished",
                    payload={"event": "run.finished", "status": status, "run_id": r.id, "at": iso(now)},
                    created_at=now))


@router.post("/runs/{id}/retry", status_code=202)
async def retry_run(id: str, request: Request, auth: AuthContext = Depends(require_scope("runs:write")),
                    db: Session = Depends(get_db)) -> JSONResponse:
    body = await json_body(request)
    r = get_run(db, auth, id)
    p = get_pipeline(db, auth, r.pipeline_id)
    graph = r.graph
    if not graph:
        graph, _ = resolve_graph(db, p, r.pipeline_version if r.pipeline_version is not None else "draft")
    doc = run_engine.run_document(db, r)
    run = run_engine.create_run(db, workspace_id=auth.workspace_id, pipeline_id=p.id, graph=graph,
                                version=r.pipeline_version, doc=doc, test=bool(r.test), deliver=bool(r.deliver),
                                trigger="retry", retry_of=r.id, created_by=actor_name(auth),
                                input={k: v for k, v in (r.input or {}).items() if not k.startswith("_")})
    audit.record(db, auth, "run.retried", "run", run.id, summary=f"Retry of {r.id}",
                 details={"retry_of": r.id, "from_node_id": body.get("from_node_id")})
    out = run_engine.run_out(db, run, doc)
    db.commit()
    run_engine.enqueue(run.id)
    return JSONResponse(out, status_code=202, headers={"Location": f"/v1/runs/{run.id}"})
