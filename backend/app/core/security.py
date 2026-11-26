"""Authentication & authorization — PUBLIC INTERFACE (owned by Backend Engineer 1).

Everyone codes against these names; BE1 replaces the internals (OIDC JWKS, API-key hash lookup, HMAC ingest,
dev HS256 tokens) without changing the signatures:

    from app.core.security import AuthContext, get_auth, require_role, require_scope

    @router.get("/pipelines")
    def list_pipelines(auth: AuthContext = Depends(require_scope("pipelines:read")), db: Session = Depends(get_db)):
        ... always filter by auth.workspace_id ...

Role hierarchy: admin > builder > reviewer > viewer. API keys carry scopes instead of roles
(`pipelines:read|write`, `runs:read|write`, `ingest:write`, `review:write`, `connections:write`, `admin`).
`require_scope(s)` passes for users whose role implies the scope (see ROLE_SCOPES) and for API keys holding `s`
or `admin`. `require_role(r)` passes for users with role >= r and for API keys with `admin`.

Credentials accepted by :func:`get_auth` (first match wins):

1. ``X-API-Key: dik_…`` — sha256 lookup in ``api_keys`` (also the previous hash during a rotation grace period);
   rejects revoked / expired keys and callers outside ``allowed_ips`` (``request.client.host``); ``last_used_at``
   is updated at most once per minute. ``DI_BOOTSTRAP_API_KEY`` is accepted as an admin key of ``ws_default``
   even before it is seeded.
2. ``Authorization: Bearer <jwt>`` — HS256 dev tokens signed with ``secret_key`` (claims ``sub, ws, role, email,
   name``) when ``settings.dev_auth`` and not production; otherwise OIDC (RS*/ES*) verified against
   ``oidc_jwks_url`` (``iss``/``aud`` checked) and mapped to a member by ``email``/``sub``.
3. ``?access_token=<sse_token>`` — short-lived HMAC token from ``GET /me``; only for ``GET /v1/runs/{id}/events``.

Helpers for other modules: :func:`issue_dev_token`, :func:`issue_sse_token`, :func:`verify_ingest_hmac`,
:func:`sign_payload`, :func:`hash_api_key`, :func:`new_api_key_secret`, :func:`hmac_context`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable

from fastapi import Depends, Request

from app.config import get_settings
from app.core.errors import ApiError, forbidden, unauthorized

log = logging.getLogger("docintel.security")

DEFAULT_WORKSPACE_ID = "ws_default"  # the seeded demo workspace

ROLE_ORDER = {"viewer": 0, "reviewer": 1, "builder": 2, "admin": 3}

READ_SCOPES = {"pipelines:read", "runs:read"}
ROLE_SCOPES: dict[str, set[str]] = {
    "viewer": READ_SCOPES,
    "reviewer": READ_SCOPES | {"review:write"},
    "builder": READ_SCOPES | {"pipelines:write", "runs:write", "connections:write", "ingest:write", "review:write"},
    "admin": READ_SCOPES | {"pipelines:write", "runs:write", "connections:write", "ingest:write", "review:write", "admin"},
}
ALL_SCOPES = frozenset(ROLE_SCOPES["admin"])

SSE_TOKEN_TTL_S = 600
SSE_TOKEN_PREFIX = "sse_"
INGEST_WINDOW_S = 300
LAST_USED_RESOLUTION_S = 60
_SSE_PATH = re.compile(r"/runs/[^/]+/events/?$")


@dataclass(frozen=True)
class AuthContext:
    workspace_id: str
    kind: str                 # "user" | "api_key" | "hmac"
    principal_id: str         # usr_… | key_…
    name: str | None = None
    email: str | None = None
    role: str | None = None   # users only
    scopes: frozenset[str] = field(default_factory=frozenset)  # api keys (users: derived from role)

    def has_scope(self, scope: str) -> bool:
        if self.kind == "user":
            return scope in ROLE_SCOPES.get(self.role or "viewer", set())
        return scope in self.scopes or "admin" in self.scopes

    def has_role(self, role: str) -> bool:
        if self.kind == "user":
            return ROLE_ORDER.get(self.role or "viewer", 0) >= ROLE_ORDER[role]
        return "admin" in self.scopes

    @property
    def actor(self) -> dict:
        """Shape used in audit events: {kind, id, name}."""
        return {"kind": self.kind, "id": self.principal_id, "name": self.name}

    @property
    def effective_scopes(self) -> list[str]:
        if self.kind == "user":
            return sorted(ROLE_SCOPES.get(self.role or "viewer", set()))
        return sorted(self.scopes)


# Principal used for credential-less requests when settings.dev_anonymous_admin (never in production).
ANONYMOUS_ADMIN = AuthContext(workspace_id=DEFAULT_WORKSPACE_ID, kind="user", principal_id="usr_admin", name="Manoj M.",
                              email="manoj@example.com", role="admin")


# ── small crypto helpers ─────────────────────────────────────────────────────────────────────────────

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _signing_key(purpose: str) -> bytes:
    """Per-purpose key derived from settings.secret_key (sse tokens, oauth state …)."""
    return hmac.new(get_settings().secret_key.encode(), purpose.encode(), hashlib.sha256).digest()


def hash_api_key(secret: str) -> str:
    """Hex sha256 of the full API key (the only form stored in api_keys.key_hash)."""
    return hashlib.sha256(secret.encode()).hexdigest()


def new_api_key_secret(live: bool | None = None) -> str:
    """`dik_live_…` (production) / `dik_test_…` (other environments) + 32 url-safe random characters."""
    import secrets

    live = get_settings().is_production if live is None else live
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return ("dik_live_" if live else "dik_test_") + "".join(secrets.choice(alphabet) for _ in range(32))


def api_key_prefix(secret: str) -> str:
    """First 13 characters shown in lists (`dik_live_7Hf2`)."""
    return secret[:13]


def sign_payload(secret: str, timestamp: str | int, body: bytes) -> str:
    """hex(HMAC-SHA256(secret, f"{timestamp}." + body)) — ingest and outbound webhook signature."""
    return hmac.new(secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256).hexdigest()


# ── tokens ───────────────────────────────────────────────────────────────────────────────────────────

def issue_dev_token(sub: str = "usr_manoj", *, ws: str = DEFAULT_WORKSPACE_ID, role: str = "admin",
                    email: str | None = None, name: str | None = None, ttl_s: int = 3600,
                    extra: dict[str, Any] | None = None) -> str:
    """HS256 dev bearer token signed with settings.secret_key (accepted only when dev_auth and not production)."""
    import jwt

    now = int(time.time())
    claims: dict[str, Any] = {"sub": sub, "ws": ws, "role": role, "iat": now, "exp": now + ttl_s, "typ": "dev"}
    if email:
        claims["email"] = email
    if name:
        claims["name"] = name
    claims.update(extra or {})
    return jwt.encode(claims, get_settings().secret_key, algorithm="HS256")


def issue_sse_token(auth: AuthContext, ttl_s: int = SSE_TOKEN_TTL_S) -> str:
    """Short-lived token for EventSource (`?access_token=`) bound to the workspace and principal."""
    payload = {"ws": auth.workspace_id, "k": auth.kind, "p": auth.principal_id, "n": auth.name, "r": auth.role,
               "s": sorted(auth.scopes), "e": auth.email, "exp": int(time.time()) + ttl_s}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64e(hmac.new(_signing_key("sse"), body.encode(), hashlib.sha256).digest())
    return f"{SSE_TOKEN_PREFIX}{body}.{sig}"


def _verify_sse_token(token: str) -> AuthContext:
    if not token.startswith(SSE_TOKEN_PREFIX) or "." not in token:
        raise unauthorized("Invalid access_token")
    body, sig = token[len(SSE_TOKEN_PREFIX):].rsplit(".", 1)
    expected = _b64e(hmac.new(_signing_key("sse"), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise unauthorized("Invalid access_token")
    try:
        p = json.loads(_b64d(body))
    except ValueError as exc:
        raise unauthorized("Invalid access_token") from exc
    if int(p.get("exp", 0)) < time.time():
        raise unauthorized("access_token expired; fetch a new sse_token from GET /me")
    return AuthContext(workspace_id=p["ws"], kind=p["k"], principal_id=p["p"], name=p.get("n"), email=p.get("e"),
                       role=p.get("r"), scopes=frozenset(p.get("s") or []))


# ── ingest HMAC (used by BE2's /ingest router) ───────────────────────────────────────────────────────

def verify_ingest_hmac(request: Request, body: bytes, secret: str, *, window_s: int = INGEST_WINDOW_S) -> None:
    """Verify ``X-DI-Signature`` = hex HMAC-SHA256(secret, X-DI-Timestamp + "." + raw body) (optionally prefixed
    ``sha256=``) within a ±5 minute window, constant-time. Raises 401 problem+json (``invalid_signature`` /
    ``signature_expired``) on failure; returns None on success."""
    sig = (request.headers.get("x-di-signature") or "").strip()
    ts = (request.headers.get("x-di-timestamp") or "").strip()
    if not sig or not ts:
        raise ApiError(401, "invalid_signature", "X-DI-Signature and X-DI-Timestamp are required",
                       headers={"WWW-Authenticate": 'HMAC realm="docintel"'})
    try:
        ts_i = int(float(ts))
    except ValueError as exc:
        raise ApiError(401, "invalid_signature", "X-DI-Timestamp must be Unix seconds") from exc
    if abs(time.time() - ts_i) > window_s:
        raise ApiError(401, "signature_expired", "The request timestamp is outside the 5-minute window")
    if sig.lower().startswith("sha256="):
        sig = sig[7:]
    expected = sign_payload(secret, ts, body)
    if not hmac.compare_digest(sig.lower(), expected):
        raise ApiError(401, "invalid_signature", "The request signature does not match")


def hmac_context(workspace_id: str, pipeline_id: str) -> AuthContext:
    """AuthContext for a request authenticated by a pipeline's ingest HMAC secret."""
    return AuthContext(workspace_id=workspace_id, kind="hmac", principal_id=f"hmac:{pipeline_id}",
                       name="Signed ingest", scopes=frozenset({"ingest:write"}))


# ── DB-backed resolution ─────────────────────────────────────────────────────────────────────────────

def _session():
    from app.db.session import session_factory

    return session_factory()()


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _ip_allowed(ip: str | None, allowed: list[str] | None) -> bool:
    if not allowed:
        return True
    if not ip:
        return False
    if any(str(c).strip() == ip for c in allowed):
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for cidr in allowed:
        try:
            if addr in ipaddress.ip_network(str(cidr).strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


_last_used_lock = threading.Lock()
_last_used_mem: dict[str, float] = {}


def _authenticate_api_key(request: Request, secret: str) -> AuthContext:
    from sqlalchemy import or_, select

    from app.core.time import ensure_aware, utcnow
    from app.db.models import ApiKey

    settings = get_settings()
    digest = hash_api_key(secret)
    now = utcnow()
    with _session() as db:
        rows = db.scalars(select(ApiKey).where(or_(ApiKey.key_hash == digest, ApiKey.previous_key_hash == digest))).all()
        key = next((r for r in rows if r.key_hash == digest), None)
        if key is None:
            key = next((r for r in rows if r.previous_key_hash == digest and r.previous_expires_at
                        and ensure_aware(r.previous_expires_at) > now), None)
            if key is None and rows:
                raise unauthorized("This API key was rotated and its grace period has ended")
        if key is None:
            if settings.bootstrap_api_key and hmac.compare_digest(secret, settings.bootstrap_api_key):
                return AuthContext(workspace_id=DEFAULT_WORKSPACE_ID, kind="api_key", principal_id="key_bootstrap",
                                   name="Bootstrap key", scopes=frozenset({"admin"}))
            raise unauthorized("Invalid API key")
        if key.revoked_at is not None:
            raise unauthorized("This API key has been revoked")
        if key.expires_at is not None and ensure_aware(key.expires_at) <= now:
            raise unauthorized("This API key has expired")
        ip = _client_ip(request)
        if not _ip_allowed(ip, key.allowed_ips):
            raise forbidden("This API key is not allowed from your IP address")
        ctx = AuthContext(workspace_id=key.workspace_id, kind="api_key", principal_id=key.id, name=key.name,
                          scopes=frozenset(key.scopes or []))
        # last_used_at at most once per minute (in-memory throttle + DB check)
        with _last_used_lock:
            due = _last_used_mem.get(key.id, 0.0) + LAST_USED_RESOLUTION_S <= time.time()
            if due:
                _last_used_mem[key.id] = time.time()
        if due and (key.last_used_at is None
                    or ensure_aware(key.last_used_at) <= now - timedelta(seconds=LAST_USED_RESOLUTION_S)):
            try:
                key.last_used_at = now
                key.last_used_ip = ip
                db.commit()
            except Exception:  # noqa: BLE001 — never fail auth on bookkeeping
                db.rollback()
                log.warning("Could not update api key last_used_at", exc_info=True)
        return ctx


def _member_lookup(ws: str | None, sub: str | None, email: str | None) -> Any:
    from sqlalchemy import func, select

    from app.db.models import Member

    with _session() as db:
        q = select(Member)
        if ws:
            q = q.where(Member.workspace_id == ws)
        if sub:
            m = db.scalar(q.where(Member.external_subject == sub)) or db.scalar(q.where(Member.id == sub))
            if m is not None:
                return m
        if email:
            return db.scalar(q.where(func.lower(Member.email) == email.lower()))
    return None


_jwks_clients: dict[str, Any] = {}


def _authenticate_bearer(request: Request, token: str) -> AuthContext:
    import jwt

    settings = get_settings()
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise unauthorized("Malformed bearer token") from exc
    alg = header.get("alg", "")

    if alg == "HS256":
        if not settings.dev_auth or settings.is_production:
            raise unauthorized("Development tokens are disabled")
        try:
            claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"],
                                options={"require": ["sub", "exp"]})
        except jwt.ExpiredSignatureError as exc:
            raise unauthorized("Token expired") from exc
        except jwt.PyJWTError as exc:
            raise unauthorized("Invalid bearer token") from exc
        role = claims.get("role") or "viewer"
        if role not in ROLE_ORDER:
            raise unauthorized("Invalid role claim")
        ws = claims.get("ws") or DEFAULT_WORKSPACE_ID
        name, email = claims.get("name"), claims.get("email")
        if not name or not email:
            try:
                m = _member_lookup(ws, claims["sub"], email)
            except Exception:  # noqa: BLE001
                m = None
            if m is not None:
                name, email = name or m.name, email or m.email
        return AuthContext(workspace_id=ws, kind="user", principal_id=str(claims["sub"]), name=name, email=email,
                           role=role)

    if not settings.oidc_jwks_url:
        raise unauthorized("OIDC is not configured on this server")
    try:
        client = _jwks_clients.get(settings.oidc_jwks_url)
        if client is None:
            client = _jwks_clients[settings.oidc_jwks_url] = jwt.PyJWKClient(settings.oidc_jwks_url, cache_keys=True)
        key = client.get_signing_key_from_jwt(token)
        opts: dict[str, Any] = {"require": ["exp", "sub"]}
        if not settings.oidc_audience:
            opts["verify_aud"] = False
        claims = jwt.decode(token, key.key, algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"],
                            audience=settings.oidc_audience, issuer=settings.oidc_issuer, options=opts)
    except jwt.ExpiredSignatureError as exc:
        raise unauthorized("Token expired") from exc
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise unauthorized("Invalid bearer token") from exc
    except Exception as exc:  # noqa: BLE001 — JWKS endpoint unreachable etc.
        log.warning("OIDC verification failed: %s", type(exc).__name__)
        raise unauthorized("Could not verify the bearer token") from exc
    sub = str(claims.get("sub"))
    email = claims.get("email") or claims.get("preferred_username") or claims.get("upn")
    ws = claims.get("ws") or claims.get("workspace_id")
    subject = f"{claims.get('iss', '')}|{sub}"
    m = _member_lookup(ws, subject, email) or _member_lookup(ws, sub, None)
    if m is None:
        raise forbidden("You are not a member of this workspace")
    if m.status == "disabled":
        raise forbidden("Your membership is disabled")
    return AuthContext(workspace_id=m.workspace_id, kind="user", principal_id=m.id, name=m.name or claims.get("name"),
                       email=m.email, role=m.role)


def _authenticate(request: Request) -> AuthContext:
    """Resolve the caller (see module docstring). Raises 401/403 problem+json on failure."""
    api_key = request.headers.get("x-api-key")
    if api_key:
        return _authenticate_api_key(request, api_key.strip())
    authz = request.headers.get("authorization")
    if authz:
        scheme, _, token = authz.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise unauthorized("Use Authorization: Bearer <token> or X-API-Key")
        return _authenticate_bearer(request, token.strip())
    access_token = request.query_params.get("access_token")
    if access_token:
        if request.method == "GET" and _SSE_PATH.search(request.url.path):
            return _verify_sse_token(access_token)
        raise unauthorized("access_token is only accepted on GET /runs/{id}/events")
    settings = get_settings()
    if getattr(settings, "dev_anonymous_admin", False) and not settings.is_production:
        return ANONYMOUS_ADMIN
    raise unauthorized()


def get_auth(request: Request) -> AuthContext:
    ctx = getattr(request.state, "auth", None)
    if ctx is None:
        ctx = _authenticate(request)
        request.state.auth = ctx
    return ctx


def require_scope(scope: str) -> Callable[..., AuthContext]:
    def dep(auth: AuthContext = Depends(get_auth)) -> AuthContext:
        if not auth.has_scope(scope):
            raise forbidden(f"Requires scope {scope}")
        return auth
    return dep


def require_role(role: str) -> Callable[..., AuthContext]:
    def dep(auth: AuthContext = Depends(get_auth)) -> AuthContext:
        if not auth.has_role(role):
            raise forbidden(f"Requires role {role}")
        return auth
    return dep


__all__ = ["AuthContext", "get_auth", "require_scope", "require_role", "DEFAULT_WORKSPACE_ID", "ROLE_ORDER",
           "ROLE_SCOPES", "ALL_SCOPES", "issue_dev_token", "issue_sse_token", "verify_ingest_hmac", "hmac_context",
           "sign_payload", "hash_api_key", "new_api_key_secret", "api_key_prefix"]
