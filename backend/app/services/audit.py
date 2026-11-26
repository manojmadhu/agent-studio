"""Audit trail — PUBLIC INTERFACE (owned by Backend Engineer 1).

Every write endpoint calls `record(...)` in the same DB transaction as the change:

    from app.services import audit
    audit.record(db, auth, "pipeline.published", "pipeline", pl.id, summary=f"Published v{v}", details={...})

Never put secret values in summary/details (keys that look like secrets are dropped defensively).
The caller's IP is taken from the request context set by BE1's middleware (`current_ip`).
"""
from __future__ import annotations

import contextvars
from typing import Any

from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.core.security import AuthContext
from app.core.time import utcnow

current_ip: contextvars.ContextVar[str | None] = contextvars.ContextVar("di_current_ip", default=None)

_SECRETISH = {"key", "token", "password", "client_secret", "secret", "client_key_pem", "secret_access_key",
              "session_token", "access_token", "refresh_token", "signing_secret", "auth", "api_key"}


def _scrub(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _scrub(x) for k, x in v.items() if str(k).lower() not in _SECRETISH}
    if isinstance(v, list):
        return [_scrub(x) for x in v]
    return v


def record(db: Session, auth: AuthContext | None, action: str, resource_type: str, resource_id: str | None,
           *, summary: str | None = None, details: dict[str, Any] | None = None, workspace_id: str | None = None) -> None:
    """Insert an append-only audit event. Must not commit — the caller's transaction does."""
    from app.db.models import AuditEvent

    ws = workspace_id or (auth.workspace_id if auth else None)
    if not ws:
        return
    actor = auth.actor if auth else {"kind": "system", "id": "system", "name": "System"}
    if actor.get("kind") == "hmac":
        actor = {"kind": "api_key", "id": actor.get("id"), "name": actor.get("name")}
    db.add(AuditEvent(
        id=new_id("aud"), workspace_id=ws, actor=actor, actor_id=actor.get("id"), action=action,
        resource_type=resource_type, resource_id=resource_id, summary=(summary or None) and summary[:1000],
        ip=current_ip.get(), details=_scrub(details) if details else None, created_at=utcnow(),
    ))


__all__ = ["record", "current_ip"]
