"""Metrics, audit events, webhooks, workspace settings (BE1)."""
from __future__ import annotations

import secrets
import statistics
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session


from app.core.pagination import paginate
from app.core.security import AuthContext, get_auth, require_scope
from app.core.time import ensure_aware, iso, utcnow
from app.db.models import AuditEvent, Pipeline, ReviewItem, Run, Webhook, WebhookDelivery, Workspace, WorkspaceSettings
from app.db.session import get_db
from app.routers._be1 import not_found, no_content, parse, validation
from app.services import audit, secret_store
from app.services.webhooks import EVENTS

router = APIRouter(tags=["Metrics", "Audit", "Webhooks", "Settings"])

WINDOWS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}
NEAR_SLA = timedelta(hours=3)


# ── metrics ───────────────────────────────────────────────────────────────────────────────────────────

def _run_stats(runs: list[Run]) -> tuple[int, float | None, int | None]:
    prod = [r for r in runs if not r.test]
    finished = [r for r in prod if r.status in ("completed", "failed", "review", "cancelled")]
    stp = (sum(1 for r in finished if r.status == "completed") / len(finished)) if finished else None
    durations = [r.duration_ms for r in finished if r.duration_ms]
    return len(prod), stp, int(statistics.median(durations)) if durations else None


@router.get("/metrics/overview")
def metrics_overview(window: str = Query("24h"), auth: AuthContext = Depends(get_auth),
                     db: Session = Depends(get_db)) -> dict:
    """Documents / straight-through rate / median duration from `runs` (non-test) in the window vs the previous
    window; pipelines' cached `kpi` snapshots (seeded demo volumes, maintained by the run engine) are used as the
    24 h baseline when they are larger than the raw run counts. Review numbers come from `review_items`."""
    if window not in WINDOWS:
        raise validation("window must be one of 24h, 7d, 30d")
    ws, now, span = auth.workspace_id, utcnow(), WINDOWS[window]
    runs = db.scalars(select(Run).where(Run.workspace_id == ws, Run.created_at >= now - 2 * span)).all()
    cur = [r for r in runs if ensure_aware(r.created_at) >= now - span]
    prev = [r for r in runs if ensure_aware(r.created_at) < now - span]
    docs, stp, median = _run_stats(cur)
    pdocs, pstp, pmedian = _run_stats(prev)

    pipes = db.scalars(select(Pipeline).where(Pipeline.workspace_id == ws)).all()
    factor = span / WINDOWS["24h"]
    kpi_docs = int(sum((p.kpi or {}).get("documents_24h", 0) or 0 for p in pipes) * factor)
    active = [p for p in pipes if p.status == "active" and (p.kpi or {}).get("documents_24h")]
    act_docs = sum(p.kpi["documents_24h"] for p in active)
    using_kpi = kpi_docs > docs
    if using_kpi:
        docs = kpi_docs
        stp = (sum((p.kpi.get("straight_through_rate") or 0) * p.kpi["documents_24h"] for p in active) / act_docs) \
            if act_docs else stp
        if median is None and act_docs:
            median = int(sum((p.kpi.get("median_ms") or 0) * p.kpi["documents_24h"] for p in active) / act_docs)

    open_items = db.scalars(select(ReviewItem).where(ReviewItem.workspace_id == ws,
                                                     ReviewItem.status.in_(["open", "claimed"]))).all()
    near = sum(1 for r in open_items if r.sla_due_at and ensure_aware(r.sla_due_at) - now < NEAR_SLA)
    return {
        "window": window,
        "documents": docs,
        "documents_change": round((docs - pdocs) / pdocs, 3) if pdocs and not using_kpi else 0.0,
        "straight_through_rate": round(stp or 0.0, 4),
        "straight_through_change": round((stp or 0) - pstp, 3) if pstp is not None and stp is not None else 0.0,
        "review_waiting": len(open_items),
        "review_near_sla": near,
        "median_ms": median or 0,
        "median_change_ms": (median - pmedian) if median is not None and pmedian is not None else 0,
    }


# ── audit ─────────────────────────────────────────────────────────────────────────────────────────────

def audit_out(e: AuditEvent) -> dict:
    out = {"id": e.id, "at": iso(e.created_at), "actor": dict(e.actor or {}), "action": e.action,
           "resource_type": e.resource_type, "resource_id": e.resource_id or "", "details": dict(e.details or {})}
    if e.ip:
        out["ip"] = e.ip
    if e.summary:
        out["summary"] = e.summary
    return out


@router.get("/audit-events")
def list_audit_events(limit: int | None = Query(None, ge=1, le=200), cursor: str | None = None,
                      resource_type: str | None = None, actor_id: str | None = None,
                      auth: AuthContext = Depends(require_scope("admin")), db: Session = Depends(get_db)) -> dict:
    stmt = select(AuditEvent).where(AuditEvent.workspace_id == auth.workspace_id)
    if resource_type:
        stmt = stmt.where(AuditEvent.resource_type == resource_type)
    if actor_id:
        stmt = stmt.where(AuditEvent.actor_id == actor_id)
    return paginate(db, stmt, AuditEvent, limit=limit, cursor=cursor, serialize=audit_out)


# ── webhooks ──────────────────────────────────────────────────────────────────────────────────────────

class WebhookIn(BaseModel):
    url: str | None = None
    events: list[str] | None = None
    pipeline_ids: list[str] | None = None
    description: str | None = None


def webhook_out(w: Webhook) -> dict:
    return {"id": w.id, "url": w.url, "events": list(w.events or []), "enabled": w.enabled,
            "pipeline_ids": list(w.pipeline_ids or [])}


@router.get("/webhooks")
def list_webhooks(auth: AuthContext = Depends(require_scope("admin")), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Webhook).where(Webhook.workspace_id == auth.workspace_id)
                      .order_by(Webhook.created_at, Webhook.id)).all()
    return {"data": [webhook_out(w) for w in rows]}


@router.post("/webhooks", status_code=201)
def create_webhook(body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("admin")),
                   db: Session = Depends(get_db)) -> dict:
    req = parse(WebhookIn, body)
    url = (req.url or "").strip()
    if not url.startswith(("http://", "https://")) or not req.events:
        raise validation("A URL and at least one event are required")
    unknown = [e for e in req.events if e not in EVENTS]
    if unknown:
        raise validation(f"Unknown event(s): {', '.join(unknown)}. Allowed: {', '.join(EVENTS)}")
    signing = "whsec_" + secrets.token_urlsafe(24)
    now = utcnow()
    w = Webhook(workspace_id=auth.workspace_id, url=url, events=list(dict.fromkeys(req.events)), enabled=True,
                pipeline_ids=list(req.pipeline_ids or []), description=req.description,
                secret_hint=secret_store.hint(signing), created_by=auth.name or auth.principal_id,
                created_at=now, updated_at=now)
    db.add(w)
    db.flush()
    w.secret_ref = secret_store.put(db, auth.workspace_id, {"type": "hmac", "secret": signing}, purpose="webhook")
    audit.record(db, auth, "webhook.created", "webhook", w.id, summary=f"Created webhook {url}",
                 details={"events": w.events})
    db.commit()
    return {**webhook_out(w), "signing_secret": signing}


@router.delete("/webhooks/{id}", status_code=204)
def delete_webhook(id: str, auth: AuthContext = Depends(require_scope("admin")),  # noqa: A002
                   db: Session = Depends(get_db)):
    w = db.scalar(select(Webhook).where(Webhook.workspace_id == auth.workspace_id, Webhook.id == id))
    if w is None:
        raise not_found("Webhook", id)
    db.execute(delete(WebhookDelivery).where(WebhookDelivery.webhook_id == w.id))
    if w.secret_ref and w.secret_ref.startswith("local:"):
        secret_store.delete(db, w.secret_ref)
    audit.record(db, auth, "webhook.deleted", "webhook", w.id, summary=f"Deleted webhook {w.url}")
    db.delete(w)
    db.commit()
    return no_content()


# ── settings ──────────────────────────────────────────────────────────────────────────────────────────

class SsoIn(BaseModel):
    provider: str | None = None
    issuer: str | None = None


class SettingsIn(BaseModel):
    name: str | None = None
    environment: str | None = None
    default_region: str | None = None
    retention_days: int | None = None
    allowed_regions: list[str] | None = None
    sso: dict[str, Any] | None = None


def settings_out(db: Session, ws_id: str) -> dict:
    ws = db.get(Workspace, ws_id)
    st = db.get(WorkspaceSettings, ws_id)
    out: dict[str, Any] = {"name": ws.name if ws else ws_id,
                           "environment": ws.environment if ws and ws.environment in ("sandbox", "production") else "sandbox"}
    if st is not None:
        if st.default_region:
            out["default_region"] = st.default_region
        if st.retention_days is not None:
            out["retention_days"] = st.retention_days
        out["allowed_regions"] = list(st.allowed_regions or [])
        if st.sso:
            out["sso"] = dict(st.sso)
    return out


@router.get("/settings")
def get_settings_(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:
    return settings_out(db, auth.workspace_id)


@router.patch("/settings")
def update_settings(body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("admin")),
                    db: Session = Depends(get_db)) -> dict:
    req = parse(SettingsIn, body)
    sent = req.model_fields_set
    ws = db.get(Workspace, auth.workspace_id)
    if ws is None:
        raise not_found("Workspace", auth.workspace_id)
    st = db.get(WorkspaceSettings, auth.workspace_id)
    if st is None:
        st = WorkspaceSettings(workspace_id=ws.id)
        db.add(st)
    if "name" in sent and req.name is not None:
        if not req.name.strip():
            raise validation("name is required")
        ws.name = req.name.strip()
    if "environment" in sent and req.environment is not None:
        if req.environment not in ("sandbox", "production"):
            raise validation("environment must be sandbox or production")
        ws.environment = req.environment
    if "default_region" in sent:
        st.default_region = req.default_region or None
        ws.region = req.default_region or ws.region
    if "retention_days" in sent:
        if req.retention_days is not None and not 1 <= req.retention_days <= 36500:
            raise validation("retention_days must be between 1 and 36500")
        st.retention_days = req.retention_days
    if "allowed_regions" in sent:
        st.allowed_regions = list(req.allowed_regions or [])
    if "sso" in sent:
        sso = parse(SsoIn, req.sso) if req.sso is not None else None
        st.sso = {**(req.sso or {}), **sso.model_dump(exclude_none=True)} if sso else None
    st.updated_by = auth.name or auth.principal_id
    st.updated_at = utcnow()
    audit.record(db, auth, "settings.updated", "workspace", ws.id, summary="Updated workspace settings",
                 details={"fields": sorted(sent)})
    db.commit()
    return settings_out(db, ws.id)
