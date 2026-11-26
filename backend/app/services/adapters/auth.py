"""Apply a Connection's credentials (``AuthConfig``) to an outbound HTTP request (owned by BE1).

    req = {"method": "POST", "url": "https://ocr.internal/parse", "files": {"file": (...)}}
    req = apply_auth(req, auth_config)          # auth_config = SecretStore payload (dict with "type")
    resp = send(req, timeout=60)

Supported ``auth.type`` values over HTTP:

=============================  ==========================================================================
api_key                        header (default) or query param ``name`` with optional ``prefix``
bearer                         ``Authorization: Bearer <token>``
basic                          ``Authorization: Basic base64(user:pass)``
oauth2_client_credentials      token fetched from ``token_url`` (form post), cached until ~60 s before expiry
azure_ad_client                client-credentials against ``login.microsoftonline.com/{tenant}``
oauth2_authorization_code      stored ``access_token`` (from /oauth/callback); refreshed with ``refresh_token``
hmac                           ``<header>: hex(HMAC(secret, timestamp + "." + body))`` + ``X-Timestamp``
aws_sigv4                      AWS Signature V4 (service from settings ``aws_service``/``service``, default s3;
                               ``role_arn`` assumption is not implemented — use static/session credentials)
mtls                           client cert/key loaded into an ``ssl.SSLContext`` (temp files removed at once)
none                           nothing
=============================  ==========================================================================

The request body is materialised (``httpx.Request(...).read()``) before signing so multipart, JSON and raw bodies
are all signed over the exact bytes that are sent.
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import os
import ssl
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx

from app.core.errors import ApiError
from app.services.adapters.http import UpstreamError, raise_for_status, safe_url, send

# Fields of AuthConfig that are secret material (write-only; never returned, logged or audited).
SECRET_FIELDS = frozenset({
    "key", "token", "password", "client_secret", "secret", "client_key_pem", "secret_access_key", "session_token",
    "access_token", "refresh_token", "id_token",
})

# The field whose last four characters become `secret_hint` (mirrors portal PRIMARY_SECRET).
PRIMARY_SECRET: dict[str, str | None] = {
    "api_key": "key",
    "bearer": "token",
    "basic": "password",
    "oauth2_client_credentials": "client_secret",
    "oauth2_authorization_code": "client_secret",
    "hmac": "secret",
    "mtls": "client_key_pem",
    "aws_sigv4": "secret_access_key",
    "azure_ad_client": "client_secret",
    "none": None,
}

_token_cache: dict[tuple, tuple[str, float]] = {}
_token_lock = threading.Lock()


def clear_token_cache() -> None:
    with _token_lock:
        _token_cache.clear()


def _materialise(req: dict[str, Any]) -> tuple[httpx.Request, dict[str, Any]]:
    """Build the exact httpx.Request (url with params, encoded body, content-type) for signing."""
    kw = {k: v for k, v in req.items() if k in ("params", "headers", "content", "json", "data", "files")}
    r = httpx.Request(req.get("method", "GET"), req["url"], **kw)
    r.read()
    extra = {k: v for k, v in req.items()
             if k not in ("method", "url", "params", "headers", "content", "json", "data", "files")}
    return r, extra


def _rebuild(r: httpx.Request, extra: dict[str, Any]) -> dict[str, Any]:
    headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in r.headers.raw
               if k.lower() not in (b"content-length", b"host")}
    out: dict[str, Any] = {"method": r.method, "url": str(r.url), "headers": headers}
    if r.content:
        out["content"] = r.content
    out.update(extra)
    return out


def apply_auth(req: dict[str, Any], auth: dict[str, Any] | None, *, settings: dict[str, Any] | None = None,
               transport: httpx.BaseTransport | None = None) -> dict[str, Any]:
    """Return a new request dict with the credentials applied (see module docstring)."""
    auth = auth or {"type": "none"}
    kind = auth.get("type") or "none"
    settings = settings or {}
    r, extra = _materialise(req)

    if kind == "none":
        pass
    elif kind == "api_key":
        name = auth.get("name") or "Authorization"
        value = f"{auth.get('prefix') or ''}{auth.get('key') or ''}"
        if (auth.get("placement") or "header") == "query":
            r.url = r.url.copy_merge_params({name: value})
        else:
            r.headers[name] = value
    elif kind == "bearer":
        r.headers["Authorization"] = f"Bearer {auth.get('token') or ''}"
    elif kind == "basic":
        raw = f"{auth.get('username') or ''}:{auth.get('password') or ''}".encode()
        r.headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    elif kind in ("oauth2_client_credentials", "azure_ad_client"):
        r.headers["Authorization"] = f"Bearer {client_credentials_token(auth, transport=transport)}"
    elif kind == "oauth2_authorization_code":
        r.headers["Authorization"] = f"Bearer {authorization_code_token(auth, transport=transport)}"
    elif kind == "hmac":
        ts = str(int(time.time()))
        algo = hashlib.sha512 if auth.get("algorithm") == "sha512" else hashlib.sha256
        sig = _hmac.new(str(auth.get("secret") or "").encode(), ts.encode() + b"." + (r.content or b""), algo).hexdigest()
        r.headers[auth.get("header") or "X-Signature"] = sig
        r.headers["X-Timestamp"] = ts
    elif kind == "aws_sigv4":
        service = settings.get("aws_service") or settings.get("service") or "s3"
        region = auth.get("region") or settings.get("region") or "us-east-1"
        sigv4_sign(r, access_key=str(auth.get("access_key_id") or ""), secret_key=str(auth.get("secret_access_key") or ""),
                   region=region, service=service, session_token=auth.get("session_token"))
    elif kind == "mtls":
        extra["verify"] = mtls_context(auth)
    else:
        raise ApiError(422, "unsupported_auth_type", f"Auth type {kind} cannot be used over HTTP")
    return _rebuild(r, extra)


# ── OAuth 2.0 ────────────────────────────────────────────────────────────────────────────────────────

def _token_url(auth: dict[str, Any]) -> str:
    if auth.get("type") == "azure_ad_client":
        return f"https://login.microsoftonline.com/{auth.get('tenant_id')}/oauth2/v2.0/token"
    return str(auth.get("token_url") or "")


def client_credentials_token(auth: dict[str, Any], *, transport: httpx.BaseTransport | None = None) -> str:
    """Fetch (and cache) an access token with the client-credentials grant."""
    url = _token_url(auth)
    if not url:
        raise ApiError(422, "invalid_auth", "token_url is required for OAuth 2.0 client credentials")
    if auth.get("type") == "azure_ad_client":
        scope = auth.get("scope") or "https://cognitiveservices.azure.com/.default"
    else:
        scope = " ".join(auth.get("scopes") or [])
    secret_fp = hashlib.sha256(str(auth.get("client_secret") or "").encode()).hexdigest()[:16]
    key = (url, auth.get("client_id"), scope, auth.get("audience"), secret_fp)
    now = time.time()
    with _token_lock:
        hit = _token_cache.get(key)
        if hit and hit[1] > now:
            return hit[0]
    form = {"grant_type": "client_credentials", "client_id": auth.get("client_id") or "",
            "client_secret": auth.get("client_secret") or ""}
    if scope:
        form["scope"] = scope
    if auth.get("audience"):
        form["audience"] = auth["audience"]
    resp = send({"method": "POST", "url": url, "data": form, "headers": {"Accept": "application/json"}},
                timeout=20, transport=transport)
    raise_for_status(resp, f"Token request to {safe_url(url)}")
    try:
        body = resp.json()
        token = body["access_token"]
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError(f"Token endpoint {safe_url(url)} returned no access_token") from exc
    ttl = float(body.get("expires_in") or 3600)
    with _token_lock:
        _token_cache[key] = (token, now + max(30.0, ttl - 60))
    return token


def authorization_code_token(auth: dict[str, Any], *, transport: httpx.BaseTransport | None = None) -> str:
    """Access token obtained at /oauth/callback; refreshed in memory with the refresh token when expired."""
    token = auth.get("access_token")
    if not token:
        raise ApiError(409, "authorization_pending", "Sign-in has not been completed for this connection")
    exp = auth.get("expires_at")
    expired = False
    if exp:
        try:
            expired = datetime.fromisoformat(str(exp).replace("Z", "+00:00")).timestamp() < time.time() + 30
        except ValueError:
            expired = False
    if expired and auth.get("refresh_token") and auth.get("token_url"):
        key = ("refresh", auth.get("token_url"), auth.get("client_id"),
               hashlib.sha256(str(auth["refresh_token"]).encode()).hexdigest()[:16])
        with _token_lock:
            hit = _token_cache.get(key)
            if hit and hit[1] > time.time():
                return hit[0]
        resp = send({"method": "POST", "url": str(auth["token_url"]), "data": {
            "grant_type": "refresh_token", "refresh_token": auth["refresh_token"],
            "client_id": auth.get("client_id") or "", "client_secret": auth.get("client_secret") or ""}},
            timeout=20, transport=transport)
        raise_for_status(resp, "Token refresh")
        body = resp.json()
        token = body.get("access_token") or token
        with _token_lock:
            _token_cache[key] = (token, time.time() + max(30.0, float(body.get("expires_in") or 3600) - 60))
    return str(token)


# ── AWS Signature V4 ─────────────────────────────────────────────────────────────────────────────────

def _h(key: bytes, msg: str) -> bytes:
    return _hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_sign(r: httpx.Request, *, access_key: str, secret_key: str, region: str, service: str,
               session_token: str | None = None, now: datetime | None = None,
               include_content_sha: bool = True) -> None:
    """Sign `r` in place (Authorization, x-amz-date, x-amz-content-sha256, x-amz-security-token)."""
    now = now or datetime.now(timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    payload_hash = hashlib.sha256(r.content or b"").hexdigest()
    r.headers["x-amz-date"] = amz_date
    if include_content_sha:
        r.headers["x-amz-content-sha256"] = payload_hash
    if session_token:
        r.headers["x-amz-security-token"] = session_token
    host = r.url.netloc.decode() if isinstance(r.url.netloc, bytes) else str(r.url.netloc)
    signed = {"host": host}
    for k, v in r.headers.items():
        lk = k.lower()
        if lk.startswith("x-amz-") or lk == "content-type":
            signed[lk] = " ".join(v.strip().split())
    names = sorted(signed)
    canonical_headers = "".join(f"{n}:{signed[n]}\n" for n in names)
    signed_headers = ";".join(names)
    path = r.url.path or "/"
    canonical_uri = quote(path, safe="/~")
    pairs = sorted((quote(k, safe="~"), quote(v, safe="~")) for k, v in r.url.params.multi_items())
    canonical_query = "&".join(f"{k}={v}" for k, v in pairs)
    canonical_request = "\n".join([r.method, canonical_uri, canonical_query, canonical_headers, signed_headers,
                                   payload_hash])
    scope = f"{date}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
    k = _h(_h(_h(_h(("AWS4" + secret_key).encode(), date), region), service), "aws4_request")
    signature = _hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    r.headers["Authorization"] = (f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
                                  f"SignedHeaders={signed_headers}, Signature={signature}")


# ── mTLS ─────────────────────────────────────────────────────────────────────────────────────────────

def mtls_context(auth: dict[str, Any]) -> ssl.SSLContext:
    """SSLContext with the client certificate loaded. PEMs are written to 0600 temp files only for the
    duration of `load_cert_chain` and deleted immediately."""
    ctx = ssl.create_default_context()
    if auth.get("ca_cert_pem"):
        ctx.load_verify_locations(cadata=str(auth["ca_cert_pem"]))
    cert_path = key_path = None
    try:
        fd, cert_path = tempfile.mkstemp(prefix="di_crt_", suffix=".pem")
        with os.fdopen(fd, "w") as f:
            f.write(str(auth.get("client_cert_pem") or ""))
        fd, key_path = tempfile.mkstemp(prefix="di_key_", suffix=".pem")
        with os.fdopen(fd, "w") as f:
            f.write(str(auth.get("client_key_pem") or ""))
        try:
            ctx.load_cert_chain(cert_path, key_path)
        except (ssl.SSLError, OSError) as exc:
            raise ApiError(422, "invalid_auth", "The client certificate or key could not be loaded") from exc
    finally:
        for p in (cert_path, key_path):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
    return ctx


# ── helpers for components ───────────────────────────────────────────────────────────────────────────

def secret_value(auth: dict[str, Any] | None) -> str | None:
    """The primary secret (API key / token) of an auth config, for clients with native header conventions."""
    if not auth:
        return None
    if auth.get("type") == "api_key":
        return auth.get("key")
    if auth.get("type") == "bearer":
        return auth.get("token")
    return None


def connection_auth(db: Any, workspace_id: str, connection_id: str | None) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    """Load (connection row, non-secret settings, decrypted auth config) for a component's connection_id.

    Returns ``(None, {}, {"type": "none"})`` when connection_id is empty. Raises 422 ``connection_not_found`` when
    the id does not exist in the workspace."""
    if not connection_id:
        return None, {}, {"type": "none"}
    from sqlalchemy import select

    from app.db.models import Connection
    from app.services.secret_store import store_for_ref

    conn = db.scalar(select(Connection).where(Connection.workspace_id == workspace_id, Connection.id == connection_id))
    if conn is None:
        raise ApiError(422, "connection_not_found", f"Connection {connection_id} does not exist")
    auth: dict[str, Any] = {"type": conn.auth_type}
    if conn.secret_ref:
        try:
            auth = store_for_ref(conn.secret_ref).get(db, conn.secret_ref)
        except (KeyError, ValueError, NotImplementedError):
            auth = {"type": conn.auth_type}
    return conn, dict(conn.settings or {}), auth


__all__ = ["SECRET_FIELDS", "PRIMARY_SECRET", "apply_auth", "client_credentials_token", "authorization_code_token",
           "sigv4_sign", "mtls_context", "secret_value", "connection_auth", "clear_token_cache"]
