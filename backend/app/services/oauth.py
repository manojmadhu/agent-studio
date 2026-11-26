"""OAuth 2.0 authorization-code flow for connections (Gmail, Microsoft 365 mail, Slack …). Owned by BE1.

    url, state = oauth.start(db, auth, conn, callback_url=..., redirect_after=...)
    location = oauth.finish(db, code=..., state=...)          # → portal URL to redirect to

``start`` stores ``sha256(state)`` + a PKCE verifier in ``oauth_states`` (10 minutes). ``finish`` exchanges the
code at the token URL with the connection's client credentials, merges ``access_token / refresh_token /
expires_at`` into the connection's secret (SecretStore) and marks the connection ``connected``. Failures (e.g. no
network in the sandbox) leave it ``pending_authorization`` with ``status_message`` and redirect with
``?oauth=error``. Nothing secret is ever put in the redirect URL.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import ApiError
from app.core.time import ensure_aware, iso, utcnow
from app.services.adapters.http import UpstreamError, send

STATE_TTL = timedelta(minutes=10)

PROVIDERS: dict[str, dict[str, Any]] = {
    "gmail": {"authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
              "token_url": "https://oauth2.googleapis.com/token",
              "scopes": ["https://www.googleapis.com/auth/gmail.modify"],
              "extra": {"access_type": "offline", "prompt": "consent"}},
    "microsoft_365_mail": {"authorize_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
                           "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                           "scopes": ["offline_access", "https://graph.microsoft.com/Mail.ReadWrite"], "extra": {}},
    "slack": {"authorize_url": "https://slack.com/oauth/v2/authorize",
              "token_url": "https://slack.com/api/oauth.v2.access", "scopes": ["channels:read", "chat:write"],
              "extra": {}},
}


def _endpoints(service: str, auth: dict) -> tuple[str, str, list[str], dict]:
    d = PROVIDERS.get(service, {})
    authorize = auth.get("authorize_url") or d.get("authorize_url")
    token = auth.get("token_url") or d.get("token_url")
    if not authorize or not token:
        raise ApiError(422, "validation", "Set authorize_url and token_url on the connection's auth")
    return str(authorize), str(token), list(auth.get("scopes") or d.get("scopes") or []), dict(d.get("extra") or {})


def _safe_return(url: str | None) -> str | None:
    if not url:
        return None
    try:
        p = urlsplit(url)
    except ValueError:
        return None
    origin = f"{p.scheme}://{p.netloc}"
    return url if origin in get_settings().cors_origin_list else None


def default_return() -> str:
    origins = get_settings().cors_origin_list
    return (origins[0] if origins else "http://localhost:5173") + "/connections"


def start(db: Session, auth_ctx: Any, conn: Any, *, callback_url: str, redirect_after: str | None = None) -> tuple[str, str]:
    from app.db.models import OAuthState
    from app.services import secret_store

    if conn.auth_type != "oauth2_authorization_code":
        raise ApiError(422, "validation", "This connection does not use OAuth 2.0 sign-in")
    auth = secret_store.get(db, conn.secret_ref) if conn.secret_ref else {}
    authorize, _, scopes, extra = _endpoints(conn.service, auth)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    db.add(OAuthState(workspace_id=conn.workspace_id, state_hash=hashlib.sha256(state.encode()).hexdigest(),
                      connection_id=conn.id, code_verifier=verifier, redirect_uri=callback_url,
                      return_to=_safe_return(redirect_after), created_by=getattr(auth_ctx, "principal_id", None),
                      expires_at=utcnow() + STATE_TTL))
    params = {"response_type": "code", "client_id": auth.get("client_id", ""), "redirect_uri": callback_url,
              "scope": " ".join(scopes), "state": state, "code_challenge": challenge,
              "code_challenge_method": "S256", **extra}
    return f"{authorize}?{urlencode(params)}", state


def _redirect(base: str, **params: str) -> str:
    sep = "&" if "?" in base else "?"
    return base + sep + urlencode(params)


def finish(db: Session, *, code: str, state: str, transport: httpx.BaseTransport | None = None) -> str:
    from app.db.models import Connection, OAuthState
    from app.services import audit, secret_store

    row = db.scalar(select(OAuthState).where(OAuthState.state_hash == hashlib.sha256(state.encode()).hexdigest()))
    if row is None or row.used_at is not None or ensure_aware(row.expires_at) < utcnow():
        return _redirect(default_return(), oauth="error", reason="invalid_state")
    row.used_at = utcnow()
    back = row.return_to or default_return()
    conn = db.get(Connection, row.connection_id)
    if conn is None or conn.workspace_id != row.workspace_id:
        db.commit()
        return _redirect(back, oauth="error", reason="connection_missing")
    auth = secret_store.get(db, conn.secret_ref) if conn.secret_ref else {"type": conn.auth_type}
    try:
        _, token_url, _, _ = _endpoints(conn.service, auth)
        resp = send({"method": "POST", "url": token_url, "headers": {"Accept": "application/json"},
                     "data": {"grant_type": "authorization_code", "code": code, "redirect_uri": row.redirect_uri or "",
                              "client_id": auth.get("client_id", ""), "client_secret": auth.get("client_secret", ""),
                              "code_verifier": row.code_verifier or ""}}, timeout=20, transport=transport)
        if resp.status_code >= 400:
            raise UpstreamError(f"Token endpoint returned HTTP {resp.status_code}")
        body = resp.json()
        if not body.get("access_token"):
            raise UpstreamError("Token endpoint returned no access_token")
    except (ApiError, ValueError) as exc:
        conn.status = "pending_authorization"
        conn.status_message = (getattr(exc, "detail", None) or "Token exchange failed")[:1000]
        audit.record(db, None, "connection.authorization_failed", "connection", conn.id, workspace_id=conn.workspace_id,
                     summary="OAuth token exchange failed")
        db.commit()
        return _redirect(back, oauth="error", connection_id=conn.id)
    now = utcnow()
    tokens = {"access_token": body["access_token"], "token_type": body.get("token_type", "Bearer")}
    if body.get("refresh_token"):
        tokens["refresh_token"] = body["refresh_token"]
    if body.get("expires_in"):
        tokens["expires_at"] = iso(now + timedelta(seconds=int(body["expires_in"])))
    merged = {**auth, **tokens, "token_url": auth.get("token_url") or token_url}
    if conn.secret_ref:
        secret_store.rotate(db, conn.secret_ref, merged)
    else:
        conn.secret_ref = secret_store.put(db, conn.workspace_id, merged, purpose="connection")
    conn.status, conn.status_message, conn.last_tested_at = "connected", None, now
    audit.record(db, None, "connection.authorized", "connection", conn.id, workspace_id=conn.workspace_id,
                 summary="OAuth sign-in completed")
    db.commit()
    return _redirect(back, oauth="connected", connection_id=conn.id)


__all__ = ["start", "finish", "PROVIDERS", "default_return"]
