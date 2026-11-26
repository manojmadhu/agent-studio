"""Connections — credentials for every external component (BE1). Secrets are write-only: stored through the
SecretStore, responses only carry `secret_hint`. Mirrors the portal mock (validation messages, codes, usages)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, conflict
from app.core.pagination import paginate
from app.core.security import AuthContext, get_auth, require_scope
from app.core.time import iso, utcnow
from app.db.models import Connection, KnowledgeCollection, McpServer, OAuthState, Provider, Worker
from app.db.session import get_db
from app.routers._be1 import not_found, connection_usage_index, no_content, simulated, usages_of_connection, validation
from app.schemas import generated as g
from app.services import audit, catalog_data, oauth, secret_store, webhooks
from app.services.adapters.auth import PRIMARY_SECRET, SECRET_FIELDS
from app.services.adapters.connection_test import probe, simulated_probe

router = APIRouter(tags=["Connections"])

AUTH_MODELS: dict[str, Any] = {
    "api_key": g.ApiKeyAuth, "bearer": g.BearerAuth, "basic": g.BasicAuth,
    "oauth2_client_credentials": g.OAuth2ClientCredentials, "oauth2_authorization_code": g.OAuth2AuthorizationCode,
    "hmac": g.HmacAuth, "mtls": g.MtlsAuth, "aws_sigv4": g.AwsSigV4Auth, "azure_ad_client": g.AzureAdClientAuth,
    "none": g.NoAuth,
}


def connection_out(c: Connection, usage_count: int | None = None) -> dict:
    return {"id": c.id, "name": c.name, "service": c.service, "auth_type": c.auth_type, "settings": dict(c.settings or {}),
            "secret_hint": c.secret_hint, "status": c.status, "last_tested_at": iso(c.last_tested_at),
            "last_used_at": iso(c.last_used_at), "expires_at": iso(c.expires_at),
            "usage_count": usage_count if usage_count is not None else 0,
            "created_at": iso(c.created_at), "updated_at": iso(c.updated_at)}


def get_connection_row(db: Session, ws: str, cid: str) -> Connection:
    c = db.scalar(select(Connection).where(Connection.workspace_id == ws, Connection.id == cid))
    if c is None:
        raise not_found("Connection", cid)
    return c


def _service(service: Any) -> dict:
    svc = catalog_data.services_by_id().get(str(service)) if service else None
    if svc is None:
        raise validation("Unknown service")
    return svc


def validate_auth(svc: dict, auth: Any) -> dict:
    """Validate an AuthConfig for a service → cleaned dict (only declared fields, empty optionals dropped)."""
    if not isinstance(auth, dict) or auth.get("type") not in svc.get("auth_types", []):
        raise validation(f"{svc['name']} supports {', '.join(svc.get('auth_types', []))}")
    t = auth["type"]
    pk = PRIMARY_SECRET.get(t)
    if pk and not auth.get(pk):
        raise validation(f"auth.{pk} is required")
    model = AUTH_MODELS[t]
    try:
        model.model_validate(auth)
    except ValidationError as exc:
        errs = [{"field": "auth." + ".".join(str(p) for p in e.get("loc", ())), "message": e.get("msg", "invalid")}
                for e in exc.errors(include_input=False, include_url=False)]
        raise validation("; ".join(f"{e['field']}: {e['message']}" for e in errs[:5]), errors=errs) from exc
    return {k: v for k, v in auth.items() if k in model.model_fields and v not in (None, "", [])}


def _settings(settings: Any) -> dict:
    if settings is None:
        return {}
    if not isinstance(settings, dict):
        raise validation("settings must be an object")
    leaked = [k for k in settings if str(k).lower() in SECRET_FIELDS]
    if leaked:
        raise validation(f"settings must not contain secrets ({', '.join(leaked)}); put them in auth")
    return settings


def _status_for(auth_type: str) -> str:
    return "pending_authorization" if auth_type == "oauth2_authorization_code" else "unverified"


@router.get("/connections")
def list_connections(limit: int | None = Query(None, ge=1, le=200), cursor: str | None = None,
                     service: str | None = None, auth: AuthContext = Depends(get_auth),
                     db: Session = Depends(get_db)) -> dict:
    stmt = select(Connection).where(Connection.workspace_id == auth.workspace_id)
    if service:
        stmt = stmt.where(Connection.service == service)
    idx = connection_usage_index(db, auth.workspace_id)
    return paginate(db, stmt, Connection, limit=limit, cursor=cursor,
                    serialize=lambda c: connection_out(c, len(idx.get(c.id, []))))


@router.post("/connections", status_code=201)
def create_connection(body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("connections:write")),
                      db: Session = Depends(get_db)) -> dict:
    body = body or {}
    name = str(body.get("name") or "").strip()
    if not name:
        raise validation("Give the connection a name")
    if len(name) > 120:
        raise validation("name: at most 120 characters")
    svc = _service(body.get("service"))
    clean = validate_auth(svc, body.get("auth"))
    settings = _settings(body.get("settings"))
    t = clean["type"]
    pk = PRIMARY_SECRET.get(t)
    now = utcnow()
    c = Connection(workspace_id=auth.workspace_id, name=name, service=svc["id"], auth_type=t, settings=settings,
                   secret_hint=secret_store.hint(clean.get(pk)) if pk else None, status=_status_for(t),
                   created_by=auth.name or auth.principal_id, created_at=now, updated_at=now)
    db.add(c)
    db.flush()
    c.secret_ref = secret_store.put(db, auth.workspace_id, clean, purpose="connection")
    audit.record(db, auth, "connection.created", "connection", c.id, summary=f"Created connection {name}",
                 details={"service": c.service, "auth_type": t})
    db.commit()
    return connection_out(c, 0)


@router.get("/connections/{id}")
def get_connection(id: str, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:  # noqa: A002
    c = get_connection_row(db, auth.workspace_id, id)
    return connection_out(c, len(usages_of_connection(db, auth.workspace_id, c.id)))


@router.patch("/connections/{id}")
def update_connection(id: str, body: dict | None = Body(None),  # noqa: A002
                      auth: AuthContext = Depends(require_scope("connections:write")), db: Session = Depends(get_db)) -> dict:
    body = body or {}
    c = get_connection_row(db, auth.workspace_id, id)
    changed: list[str] = []
    if isinstance(body.get("name"), str):
        if not body["name"].strip():
            raise validation("Give the connection a name")
        c.name = body["name"].strip()
        changed.append("name")
    if body.get("settings") is not None:
        c.settings = {**(c.settings or {}), **_settings(body["settings"])}
        changed.append("settings")
    if body.get("auth") is not None:
        svc = _service(c.service)
        clean = validate_auth(svc, body["auth"])
        t = clean["type"]
        pk = PRIMARY_SECRET.get(t)
        if c.secret_ref and c.secret_ref.startswith("local:"):
            try:
                secret_store.rotate(db, c.secret_ref, clean)
            except KeyError:
                c.secret_ref = secret_store.put(db, auth.workspace_id, clean, purpose="connection")
        else:
            c.secret_ref = secret_store.put(db, auth.workspace_id, clean, purpose="connection")
        c.auth_type = t
        c.secret_hint = secret_store.hint(clean.get(pk)) if pk else None
        c.status = _status_for(t)
        c.status_message = None
        audit.record(db, auth, "connection.rotated", "connection", c.id, summary=f"Rotated credentials of {c.name}",
                     details={"auth_type": t})
    if changed:
        audit.record(db, auth, "connection.updated", "connection", c.id, summary=f"Updated {c.name}",
                     details={"fields": changed})
    c.updated_at = utcnow()
    db.commit()
    return connection_out(c, len(usages_of_connection(db, auth.workspace_id, c.id)))


@router.delete("/connections/{id}", status_code=204)
def delete_connection(id: str, force: bool = False,  # noqa: A002
                      auth: AuthContext = Depends(require_scope("connections:write")), db: Session = Depends(get_db)):
    c = get_connection_row(db, auth.workspace_id, id)
    us = usages_of_connection(db, auth.workspace_id, c.id)
    if us and not force:
        raise conflict("connection_in_use", title="Connection is in use", detail=f"Used by {len(us)} component{'s' if len(us) > 1 else ''}.", usages=us)
    ws = auth.workspace_id
    for model in (Provider, Worker, McpServer):
        for r in db.scalars(select(model).where(model.workspace_id == ws, model.connection_id == c.id)).all():
            r.connection_id = None
    for k in db.scalars(select(KnowledgeCollection).where(KnowledgeCollection.workspace_id == ws,
                                                          KnowledgeCollection.connection_id == c.id)).all():
        k.connection_id, k.status = None, "failed"   # contract status enum: ready | indexing | failed
    for st in db.scalars(select(OAuthState).where(OAuthState.workspace_id == ws, OAuthState.connection_id == c.id)).all():
        db.delete(st)
    if c.secret_ref and c.secret_ref.startswith("local:"):
        secret_store.delete(db, c.secret_ref)
    db.flush()
    audit.record(db, auth, "connection.deleted", "connection", c.id, summary=f"Deleted connection {c.name}",
                 details={"force": force, "usages": len(us)})
    db.delete(c)
    db.commit()
    return no_content()


@router.post("/connections/{id}/test")
def test_connection(id: str, auth: AuthContext = Depends(require_scope("connections:write")),  # noqa: A002
                    db: Session = Depends(get_db)) -> dict:
    from app.services.adapters.auth import connection_auth

    c = get_connection_row(db, auth.workspace_id, id)
    if simulated():
        res = simulated_probe(c)
    elif c.auth_type == "oauth2_authorization_code" and c.status == "pending_authorization":
        res = {"ok": False, "message": "Sign-in has not been completed"}
    else:
        _, settings, creds = connection_auth(db, auth.workspace_id, c.id)
        res = probe(c.service, settings, creds)
    was = c.status
    if res["ok"]:
        c.status = "connected"
    elif c.status != "pending_authorization":
        c.status = "failing"
    c.status_message = None if res["ok"] else (res.get("message") or "")[:1000]
    c.last_tested_at = utcnow()
    audit.record(db, auth, "connection.tested", "connection", c.id,
                 summary=f"Test {'passed' if res['ok'] else 'failed'}", details={"ok": res["ok"]})
    if c.status == "failing" and was != "failing":
        webhooks.emit(db, auth.workspace_id, "connection.failing",
                      {"connection_id": c.id, "name": c.name, "service": c.service, "message": res.get("message")})
    db.commit()
    return res


@router.get("/connections/{id}/usage")
def connection_usage(id: str, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:  # noqa: A002
    c = get_connection_row(db, auth.workspace_id, id)
    return {"data": usages_of_connection(db, auth.workspace_id, c.id)}


@router.post("/connections/{id}/oauth/authorize")
def start_oauth(id: str, request: Request, body: dict | None = Body(None),  # noqa: A002
                auth: AuthContext = Depends(require_scope("connections:write")), db: Session = Depends(get_db)) -> dict:
    c = get_connection_row(db, auth.workspace_id, id)
    if simulated():
        # Simulated consent (portal mock parity): the URL is not opened by the portal; mark connected.
        import secrets

        state = "st_" + secrets.token_urlsafe(8)
        c.status, c.last_tested_at, c.status_message = "connected", utcnow(), None
        audit.record(db, auth, "connection.authorized", "connection", c.id, summary="OAuth sign-in (simulated)")
        db.commit()
        return {"authorization_url": f"https://accounts.example.com/o/oauth2/auth?client_id=mock&state={state}",
                "state": state}
    callback = str(request.url_for("oauth_callback"))
    url, state = oauth.start(db, auth, c, callback_url=callback, redirect_after=(body or {}).get("redirect_after"))
    audit.record(db, auth, "connection.authorization_started", "connection", c.id, summary="OAuth sign-in started")
    db.commit()
    return {"authorization_url": url, "state": state}


@router.get("/oauth/callback", name="oauth_callback")
def oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                   db: Session = Depends(get_db)) -> RedirectResponse:
    if error or not code or not state:
        return RedirectResponse(oauth.default_return() + "?oauth=error", status_code=302)
    return RedirectResponse(oauth.finish(db, code=code, state=state), status_code=302)


__all__ = ["router", "connection_out", "get_connection_row", "validate_auth"]
_ = ApiError
