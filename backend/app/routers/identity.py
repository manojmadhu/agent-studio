"""Identity, API keys, Members (BE1). Mirrors portal/src/api/mock/backend.ts identity routes."""
from __future__ import annotations

import ipaddress
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, conflict
from app.core.pagination import paginate
from app.core.security import (
    ALL_SCOPES, AuthContext, api_key_prefix, get_auth, hash_api_key, issue_sse_token, new_api_key_secret,
    require_scope,
)
from app.core.time import iso, utcnow
from app.db.models import ApiKey, Member, Workspace
from app.db.session import get_db
from app.routers._be1 import not_found, no_content, parse, validation
from app.schemas.generated import ApiKeyCreate
from app.services import audit

router = APIRouter(tags=["Identity", "API keys", "Members"])

ROLES = ("admin", "builder", "reviewer", "viewer")


def workspace_out(db: Session, ws_id: str) -> dict:
    ws = db.get(Workspace, ws_id)
    out: dict[str, Any] = {"id": ws_id, "name": ws.name if ws else ws_id}
    if ws is not None:
        if ws.environment in ("sandbox", "production"):
            out["environment"] = ws.environment
        if ws.region:
            out["region"] = ws.region
    return out


@router.get("/me")
def get_me(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:
    out: dict[str, Any] = {"kind": "user" if auth.kind == "user" else "api_key", "id": auth.principal_id,
                           "workspace": workspace_out(db, auth.workspace_id), "scopes": auth.effective_scopes,
                           "sse_token": issue_sse_token(auth)}
    if auth.name:
        out["name"] = auth.name
    if auth.kind == "user":
        out["role"] = auth.role or "viewer"
        if auth.email:
            out["email"] = auth.email
        m = db.get(Member, auth.principal_id)
        if m is not None and m.workspace_id == auth.workspace_id:
            m.last_seen_at = utcnow()
            db.commit()
    return out


# ── API keys ──────────────────────────────────────────────────────────────────────────────────────────

def api_key_out(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "scopes": list(k.scopes or []),
            "expires_at": iso(k.expires_at), "last_used_at": iso(k.last_used_at), "created_by": k.created_by,
            "created_at": iso(k.created_at)}


def _get_key(db: Session, auth: AuthContext, key_id: str) -> ApiKey:
    k = db.scalar(select(ApiKey).where(ApiKey.workspace_id == auth.workspace_id, ApiKey.id == key_id))
    if k is None or k.revoked_at is not None:
        raise not_found("API key", key_id)
    return k


@router.get("/api-keys")
def list_api_keys(limit: int | None = Query(None, ge=1, le=200), cursor: str | None = None,
                  auth: AuthContext = Depends(require_scope("admin")), db: Session = Depends(get_db)) -> dict:
    stmt = select(ApiKey).where(ApiKey.workspace_id == auth.workspace_id, ApiKey.revoked_at.is_(None))
    return paginate(db, stmt, ApiKey, limit=limit, cursor=cursor, serialize=api_key_out)


@router.post("/api-keys", status_code=201)
def create_api_key(body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("admin")),
                   db: Session = Depends(get_db)) -> dict:
    if body is not None and isinstance(body.get("name"), str):
        body = {**body, "name": body["name"].strip()}
    req = parse(ApiKeyCreate, body)
    if not req.name or not req.scopes:
        raise validation("Name and at least one scope are required")
    bad = [s for s in req.scopes if s not in ALL_SCOPES]
    if bad:
        raise validation(f"Unknown scope(s): {', '.join(bad)}. Allowed: {', '.join(sorted(ALL_SCOPES))}")
    for cidr in req.allowed_ips or []:
        try:
            ipaddress.ip_network(cidr.strip(), strict=False)
        except ValueError as exc:
            raise validation(f"allowed_ips: {cidr} is not a valid IP address or CIDR range") from exc
    secret = new_api_key_secret()
    now = utcnow()
    k = ApiKey(workspace_id=auth.workspace_id, name=req.name, prefix=api_key_prefix(secret), key_hash=hash_api_key(secret),
               scopes=list(dict.fromkeys(req.scopes)), allowed_ips=[c.strip() for c in req.allowed_ips or []] or None,
               expires_at=now + timedelta(days=req.expires_in_days) if req.expires_in_days else None,
               created_by=auth.name or auth.principal_id, created_at=now, updated_at=now)
    db.add(k)
    db.flush()
    audit.record(db, auth, "api_key.created", "api_key", k.id, summary=f"Created API key {k.name}",
                 details={"scopes": k.scopes, "prefix": k.prefix})
    db.commit()
    return {**api_key_out(k), "secret": secret}


@router.delete("/api-keys/{id}", status_code=204)
def revoke_api_key(id: str, auth: AuthContext = Depends(require_scope("admin")), db: Session = Depends(get_db)):  # noqa: A002
    k = _get_key(db, auth, id)
    k.revoked_at = utcnow()
    k.previous_key_hash = None
    k.previous_expires_at = None
    audit.record(db, auth, "api_key.revoked", "api_key", k.id, summary=f"Revoked API key {k.name}")
    db.commit()
    return no_content()


class RotateBody(BaseModel):
    grace_period_hours: int = Field(24, ge=0, le=168)


@router.post("/api-keys/{id}/rotate")
def rotate_api_key(id: str, body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("admin")),  # noqa: A002
                   db: Session = Depends(get_db)) -> dict:
    req = parse(RotateBody, body)
    k = _get_key(db, auth, id)
    secret = new_api_key_secret()
    now = utcnow()
    if req.grace_period_hours > 0:
        k.previous_key_hash = k.key_hash
        k.previous_expires_at = now + timedelta(hours=req.grace_period_hours)
    else:
        k.previous_key_hash = None
        k.previous_expires_at = None
    k.key_hash = hash_api_key(secret)
    k.prefix = api_key_prefix(secret)
    audit.record(db, auth, "api_key.rotated", "api_key", k.id, summary=f"Rotated API key {k.name}",
                 details={"grace_period_hours": req.grace_period_hours, "prefix": k.prefix})
    db.commit()
    return {**api_key_out(k), "secret": secret}


# ── Members ───────────────────────────────────────────────────────────────────────────────────────────

def member_out(m: Member) -> dict:
    out = {"id": m.id, "name": m.name, "email": m.email, "role": m.role, "status": m.status}
    if m.last_seen_at:
        out["last_seen_at"] = iso(m.last_seen_at)
    return {k: v for k, v in out.items() if v is not None}


class InviteBody(BaseModel):
    email: EmailStr
    role: str
    name: str | None = None


class MemberPatch(BaseModel):
    role: str | None = None


def _role(role: str | None) -> str:
    if role not in ROLES:
        raise validation(f"role must be one of {', '.join(ROLES)}")
    return role


def _get_member(db: Session, auth: AuthContext, member_id: str) -> Member:
    m = db.scalar(select(Member).where(Member.workspace_id == auth.workspace_id, Member.id == member_id))
    if m is None:
        raise not_found("Member", member_id)
    return m


def _admin_count(db: Session, ws: str) -> int:
    return db.scalar(select(func.count()).select_from(Member).where(
        Member.workspace_id == ws, Member.role == "admin", Member.status != "disabled")) or 0


@router.get("/members")
def list_members(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Member).where(Member.workspace_id == auth.workspace_id)
                      .order_by(Member.created_at, Member.id)).all()
    return {"data": [member_out(m) for m in rows]}


@router.post("/members", status_code=201)
def invite_member(body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("admin")),
                  db: Session = Depends(get_db)) -> dict:
    req = parse(InviteBody, body)
    role = _role(req.role)
    email = str(req.email).strip().lower()
    exists = db.scalar(select(Member).where(Member.workspace_id == auth.workspace_id,
                                            func.lower(Member.email) == email))
    if exists is not None:
        raise conflict("member_exists", f"{email} is already a member of this workspace")
    now = utcnow()
    m = Member(workspace_id=auth.workspace_id, email=email, name=req.name or email.split("@")[0], role=role,
               status="invited", invited_by=auth.name or auth.principal_id, created_at=now, updated_at=now)
    db.add(m)
    db.flush()
    audit.record(db, auth, "member.invited", "member", m.id, summary=f"Invited {email} as {role}",
                 details={"role": role})
    db.commit()
    return member_out(m)


@router.patch("/members/{id}")
def update_member(id: str, body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("admin")),  # noqa: A002
                  db: Session = Depends(get_db)) -> dict:
    req = parse(MemberPatch, body)
    m = _get_member(db, auth, id)
    if req.role is not None:
        role = _role(req.role)
        if m.role == "admin" and role != "admin" and _admin_count(db, auth.workspace_id) <= 1:
            raise conflict("last_admin", "The workspace needs at least one admin")
        old, m.role = m.role, role
        audit.record(db, auth, "member.role_changed", "member", m.id, summary=f"{m.email}: {old} → {role}",
                     details={"from": old, "to": role})
    db.commit()
    return member_out(m)


@router.delete("/members/{id}", status_code=204)
def remove_member(id: str, auth: AuthContext = Depends(require_scope("admin")), db: Session = Depends(get_db)):  # noqa: A002
    m = _get_member(db, auth, id)
    if m.role == "admin" and _admin_count(db, auth.workspace_id) <= 1:
        raise conflict("last_admin", "The workspace needs at least one admin")
    audit.record(db, auth, "member.removed", "member", m.id, summary=f"Removed {m.email}")
    db.delete(m)
    db.commit()
    return no_content()


__all__ = ["router", "workspace_out", "api_key_out", "member_out"]
_ = ApiError
