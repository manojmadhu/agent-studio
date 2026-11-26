"""Review queue API (owned by BE2): list, detail, claim, decision (approve with corrections / reject / return)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.pagination import paginate_list
from app.core.security import AuthContext, require_scope
from app.core.time import iso, utcnow
from app.db.session import get_db
from app.services import audit, run_engine, storage
from app.routers.pipelines import json_body, not_found, validation_error
from app.routers.runs import _finish_without_engine

router = APIRouter(tags=["Review"])

OPEN = ("open", "claimed")


def _doc_out(db: Session, rv: Any) -> dict:
    from app.db.models import Document, Run

    if rv.document_id:
        d = db.get(Document, rv.document_id)
        if d is not None:
            return storage.document_out(d)
    run = db.get(Run, rv.run_id)
    if run is not None:
        out = run_engine.run_out(db, run).get("document")
        if out:
            return out
    return {"id": rv.document_id or "", "filename": "document", "mime_type": "application/octet-stream", "pages": 1}


def item_out(db: Session, rv: Any) -> dict:
    return {"id": rv.id, "run_id": rv.run_id, "pipeline_id": rv.pipeline_id, "document": _doc_out(db, rv),
            "reason": rv.reason, "reason_code": rv.reason_code, "queue": rv.queue, "assignee": rv.assignee,
            "sla_due_at": iso(rv.sla_due_at) if rv.sla_due_at else None, "created_at": iso(rv.created_at),
            "status": rv.status}


def get_item(db: Session, auth: AuthContext, rid: str) -> Any:
    from app.db.models import ReviewItem

    rv = db.scalar(select(ReviewItem).where(ReviewItem.workspace_id == auth.workspace_id, ReviewItem.id == rid))
    if rv is None:
        raise not_found("Review item")
    return rv


def _is_me(rv: Any, auth: AuthContext) -> bool:
    return (rv.assignee_id is not None and rv.assignee_id == auth.principal_id) or \
        (rv.assignee is not None and rv.assignee == auth.name)


@router.get("/review-items")
def list_review_items(queue: str | None = None, assignee: str | None = None, status: str | None = None,
                      limit: int | None = Query(None, ge=1, le=200), cursor: str | None = None,
                      auth: AuthContext = Depends(require_scope("runs:read")), db: Session = Depends(get_db)) -> dict:
    from app.db.models import ReviewItem

    statuses = [s for s in (status or "").split(",") if s] or list(OPEN)
    stmt = select(ReviewItem).where(ReviewItem.workspace_id == auth.workspace_id, ReviewItem.status.in_(statuses))
    if queue:
        stmt = stmt.where(ReviewItem.queue == queue)
    rows = list(db.scalars(stmt).all())
    if assignee == "unassigned":
        rows = [r for r in rows if not r.assignee]
    elif assignee == "me":
        rows = [r for r in rows if _is_me(r, auth)]
    elif assignee:
        rows = [r for r in rows if r.assignee_id == assignee or r.assignee == assignee]
    far = utcnow().replace(year=9999)
    rows.sort(key=lambda r: (r.sla_due_at or far, r.created_at, r.id))  # most urgent first
    return paginate_list(rows, limit=limit, cursor=cursor, key=lambda r: r.id, serialize=lambda r: item_out(db, r))


@router.get("/review-items/{id}")
def get_review_item(id: str, auth: AuthContext = Depends(require_scope("runs:read")),
                    db: Session = Depends(get_db)) -> dict:
    from app.db.models import RunResult

    rv = get_item(db, auth, id)
    out = item_out(db, rv)
    rr = db.get(RunResult, rv.run_id)
    if rr is not None and rr.workspace_id == auth.workspace_id:
        out["result"] = run_engine.result_out(rr)
    return out


@router.post("/review-items/{id}/claim")
def claim_review_item(id: str, auth: AuthContext = Depends(require_scope("review:write")),
                      db: Session = Depends(get_db)) -> dict:
    rv = get_item(db, auth, id)
    if rv.status not in OPEN:
        raise ApiError(409, "review_decided", f"This item was already {rv.status}", title="Already decided")
    if rv.assignee and not _is_me(rv, auth):
        raise ApiError(409, "already_claimed", f"{rv.assignee} is reviewing this document.", title="Already claimed")
    rv.assignee = auth.name or auth.principal_id
    rv.assignee_id = auth.principal_id
    rv.status = "claimed"
    audit.record(db, auth, "review.claimed", "review_item", rv.id, summary=f"Claimed {rv.id}")
    out = item_out(db, rv)
    db.commit()
    return out


def _coerce(value: Any, ftype: str | None) -> Any:
    if ftype == "number" and isinstance(value, str):
        try:
            f = float(value.replace(",", "").strip())
            return int(f) if f.is_integer() else f
        except ValueError:
            return value
    if ftype == "boolean" and isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1")
    return value


@router.post("/review-items/{id}/decision")
async def decide_review_item(id: str, request: Request, auth: AuthContext = Depends(require_scope("review:write")),
                             db: Session = Depends(get_db)) -> dict:
    from app.db.models import Run, RunResult

    body = await json_body(request)
    action = body.get("action")
    if action not in ("approve", "reject", "return_to_queue"):
        raise validation_error("action must be approve, reject or return_to_queue")
    corrections = body.get("corrections")
    if corrections is not None and not isinstance(corrections, dict):
        raise validation_error("corrections must be an object of field name → value")
    comment = body.get("comment")
    rv = get_item(db, auth, id)
    if rv.status not in OPEN:
        raise ApiError(409, "review_decided", f"This item was already {rv.status}", title="Already decided")
    resume = False
    if action == "return_to_queue":
        rv.assignee = None
        rv.assignee_id = None
        rv.status = "open"
    else:
        now = utcnow()
        rr = db.get(RunResult, rv.run_id)
        types = {f.get("name"): f.get("type") for f in ((rr.fields if rr is not None else None) or [])}
        corr = {k: _coerce(v, types.get(k)) for k, v in (corrections or {}).items()} if action == "approve" else None
        rv.status = "approved" if action == "approve" else "rejected"
        rv.decision = action
        rv.corrections = corr or None
        rv.comment = comment if isinstance(comment, str) else None
        rv.decided_by = auth.name or auth.principal_id
        rv.decided_at = now
        if not rv.assignee:
            rv.assignee, rv.assignee_id = auth.name or auth.principal_id, auth.principal_id
        if rr is not None and corr:
            rr.fields = [{**f, "value": corr[f["name"]], "confidence": 1.0} if f.get("name") in corr else f
                         for f in (rr.fields or [])]
            rr.corrections = {**(rr.corrections or {}), **corr}
        run = db.get(Run, rv.run_id)
        if run is not None and run.status == "review":
            if (run.input or {}).get("_engine") is not None and not run.test:
                resume = True  # the engine continues (or fails) the run after commit
            else:  # seeded/legacy run without engine state: mock behaviour
                _finish_without_engine(db, run, "completed" if action == "approve" else "failed",
                                       None if action == "approve" else run_engine.problem(
                                           422, "review_rejected", "Rejected in review", rv.comment or None))
        try:
            from app.services import webhooks

            webhooks.emit(db, auth.workspace_id, "review.decided",
                          {"review_item_id": rv.id, "run_id": rv.run_id, "pipeline_id": rv.pipeline_id,
                           "decision": action, "corrected_fields": sorted((corr or {}).keys())})
        except Exception:  # noqa: BLE001
            pass
    audit.record(db, auth, f"review.{action}", "review_item", rv.id, summary=f"{action} {rv.id}",
                 details={"run_id": rv.run_id, "corrected_fields": sorted((corrections or {}).keys()),
                          "comment": comment if isinstance(comment, str) else None})
    out = item_out(db, rv)
    db.commit()
    if resume:
        run_engine.enqueue_resume(rv.run_id, rv.id)
    return out
