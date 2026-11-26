"""Policies / guardrails (BE1). Mirrors the portal mock."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session


from app.core.security import AuthContext, get_auth, require_scope
from app.core.time import utcnow
from app.db.models import Policy
from app.db.session import get_db
from app.routers._be1 import not_found, no_content, parse, validation
from app.services import audit

router = APIRouter(tags=["Policies"])

KINDS = ("pii_redaction", "prompt_injection", "schema_enforcement", "data_residency", "retention", "model_allowlist",
         "custom")


class PolicyIn(BaseModel):
    name: str | None = None
    description: str | None = None
    kind: str | None = None
    enabled: bool | None = None
    scope: dict[str, Any] | None = None
    config: dict[str, Any] | None = None


def policy_out(p: Policy) -> dict:
    return {"id": p.id, "name": p.name, "description": p.description or "", "kind": p.kind, "enabled": p.enabled,
            "scope": dict(p.scope or {}), "config": dict(p.config or {})}


def _scope(scope: dict | None) -> dict:
    scope = scope if scope is not None else {"all_pipelines": True}
    for k in ("pipeline_ids", "owner_teams"):
        if k in scope and not isinstance(scope[k], list):
            raise validation(f"scope.{k} must be a list")
    return scope


@router.get("/policies")
def list_policies(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Policy).where(Policy.workspace_id == auth.workspace_id)
                      .order_by(Policy.created_at, Policy.id)).all()
    return {"data": [policy_out(p) for p in rows]}


@router.post("/policies", status_code=201)
def create_policy(body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("admin")),
                  db: Session = Depends(get_db)) -> dict:
    req = parse(PolicyIn, body)
    if not (req.name or "").strip() or not req.kind:
        raise validation("name and kind are required")
    if req.kind not in KINDS:
        raise validation(f"kind must be one of {', '.join(KINDS)}")
    now = utcnow()
    p = Policy(workspace_id=auth.workspace_id, name=req.name.strip(), description=req.description or "", kind=req.kind,
               enabled=True if req.enabled is None else req.enabled, scope=_scope(req.scope), config=req.config or {},
               created_at=now, updated_at=now)
    db.add(p)
    db.flush()
    audit.record(db, auth, "policy.created", "policy", p.id, summary=f"Created policy {p.name}", details={"kind": p.kind})
    db.commit()
    return policy_out(p)


def _get(db: Session, ws: str, pid: str) -> Policy:
    p = db.scalar(select(Policy).where(Policy.workspace_id == ws, Policy.id == pid))
    if p is None:
        raise not_found("Policy", pid)
    return p


@router.patch("/policies/{id}")
def update_policy(id: str, body: dict | None = Body(None),  # noqa: A002
                  auth: AuthContext = Depends(require_scope("admin")), db: Session = Depends(get_db)) -> dict:
    req = parse(PolicyIn, body)
    sent = req.model_fields_set
    p = _get(db, auth.workspace_id, id)
    if "kind" in sent and req.kind and req.kind != p.kind:
        raise validation("kind cannot be changed")
    if "name" in sent and req.name is not None:
        if not req.name.strip():
            raise validation("name is required")
        p.name = req.name.strip()
    if "description" in sent:
        p.description = req.description or ""
    if "enabled" in sent and req.enabled is not None:
        p.enabled = req.enabled
    if "scope" in sent and req.scope is not None:
        p.scope = _scope(req.scope)
    if "config" in sent and req.config is not None:
        p.config = req.config
    p.updated_at = utcnow()
    audit.record(db, auth, "policy.updated", "policy", p.id, summary=f"Updated policy {p.name}",
                 details={"fields": sorted(sent), "enabled": p.enabled})
    db.commit()
    return policy_out(p)


@router.delete("/policies/{id}", status_code=204)
def delete_policy(id: str, auth: AuthContext = Depends(require_scope("admin")),  # noqa: A002
                  db: Session = Depends(get_db)):
    p = _get(db, auth.workspace_id, id)
    audit.record(db, auth, "policy.deleted", "policy", p.id, summary=f"Deleted policy {p.name}")
    db.delete(p)
    db.commit()
    return no_content()
