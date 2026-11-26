"""Cross-cutting HTTP middleware (owned by BE1): Idempotency-Key replay, RateLimit-* headers, request context.

Installed by ``app.main.create_app`` via :func:`install_middleware`. Pure ASGI (no BaseHTTPMiddleware) so SSE
responses stream untouched.

Idempotency (``Idempotency-Key`` on POST / PUT / PATCH, JSON bodies only — multipart and SSE are skipped):

* scope = sha256 of the credential header (``X-API-Key`` or ``Authorization``; ``hmac:<path>`` for signed ingest)
  + key — workspace-agnostic; requests without credentials are not tracked (they fail auth anyway).
* fingerprint = sha256(method, path, query, body). Same key + different fingerprint → **422
  ``idempotency_key_reused``**; same key while the first request is still running → **409
  ``idempotency_in_progress``**.
* completed 2xx and deterministic 4xx (400/404/422) responses are stored for 24 h (status, body, selected
  headers) and replayed with ``Idempotency-Replayed: true``; other outcomes release the key so it can be retried.
* DB failures never break the request (fail open, logged).

Rate limiting: in-memory fixed window per credential (or client IP). ``RateLimit-Limit/Remaining/Reset`` are sent
on every response. Requests over the limit get **429 ``rate_limited``** only when enforcement is on
(``DI_RATE_LIMIT_ENFORCE=true`` or ``DI_ENV=production``), so tests never block by default.
Configure with ``DI_RATE_LIMIT_PER_MINUTE`` (default 600) and ``DI_RATE_LIMIT_WINDOW_S`` (default 60).
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import timedelta
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings
from app.core.errors import problem_body

log = logging.getLogger("docintel.middleware")

IDEMPOTENCY_TTL = timedelta(hours=24)
IDEMPOTENT_METHODS = {"POST", "PUT", "PATCH"}
STORE_STATUSES = set(range(200, 300)) | {400, 404, 422}
REPLAY_HEADERS = {"content-type", "etag", "location", "content-disposition"}
MAX_KEY_LEN = 255


class MiddlewareSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DI_", env_file=".env", extra="ignore")

    rate_limit_per_minute: int = 600
    rate_limit_window_s: int = 60
    rate_limit_enforce: bool | None = None
    idempotency_enabled: bool = True


def _mw_settings() -> MiddlewareSettings:
    return MiddlewareSettings()


def _header(scope: Scope, name: bytes) -> str | None:
    for k, v in scope.get("headers") or []:
        if k.lower() == name:
            return v.decode("latin-1")
    return None


def _credential(scope: Scope) -> str | None:
    key = _header(scope, b"x-api-key")
    if key:
        return "k:" + key.strip()
    authz = _header(scope, b"authorization")
    if authz:
        return "a:" + authz.strip()
    if _header(scope, b"x-di-signature"):
        return "h:" + scope.get("path", "")
    return None


def _problem(status: int, code: str, detail: str, path: str) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    from app.core.errors import _TITLES  # noqa: PLC0415

    body = json.dumps(problem_body(status, code, _TITLES.get(status, "Error"), detail, path)).encode()
    return status, [(b"content-type", b"application/problem+json"), (b"content-length", str(len(body)).encode())], body


async def _send_simple(send: Send, status: int, headers: list[tuple[bytes, bytes]], body: bytes) -> None:
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


# ── rate limiter ─────────────────────────────────────────────────────────────────────────────────────

class FixedWindowLimiter:
    def __init__(self, limit: int, window_s: int) -> None:
        self.limit = max(1, limit)
        self.window = max(1, window_s)
        self._lock = threading.Lock()
        self._buckets: dict[str, tuple[int, int]] = {}  # key → (window_start, count)

    def hit(self, key: str, now: float | None = None) -> tuple[int, int, int]:
        """Count a request → (limit, remaining, reset_seconds). remaining < 0 means over the limit."""
        now = now or time.time()
        start = int(now // self.window) * self.window
        with self._lock:
            ws, count = self._buckets.get(key, (start, 0))
            if ws != start:
                ws, count = start, 0
            count += 1
            self._buckets[key] = (ws, count)
            if len(self._buckets) > 10000:  # drop stale windows
                self._buckets = {k: v for k, v in self._buckets.items() if v[0] == start}
        return self.limit, self.limit - count, int(ws + self.window - now) or 1

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, limiter: FixedWindowLimiter | None = None, enforce: bool | None = None) -> None:
        self.app = app
        s = _mw_settings()
        self.limiter = limiter or FixedWindowLimiter(s.rate_limit_per_minute, s.rate_limit_window_s)
        if enforce is None:
            enforce = s.rate_limit_enforce if s.rate_limit_enforce is not None else get_settings().is_production
        self.enforce = enforce

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") == "OPTIONS" or not str(scope.get("path", "")).startswith("/v1"):
            await self.app(scope, receive, send)
            return
        cred = _credential(scope)
        client = scope.get("client") or ("anonymous", 0)
        key = hashlib.sha256((cred or f"ip:{client[0]}").encode()).hexdigest()
        limit, remaining, reset = self.limiter.hit(key)
        rl = [(b"ratelimit-limit", str(limit).encode()), (b"ratelimit-remaining", str(max(0, remaining)).encode()),
              (b"ratelimit-reset", str(reset).encode())]
        if remaining < 0 and self.enforce:
            status, headers, body = _problem(429, "rate_limited", f"Rate limit of {limit} requests exceeded",
                                             scope.get("path", ""))
            await _send_simple(send, status, headers + rl + [(b"retry-after", str(reset).encode())], body)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = list(message.get("headers") or []) + rl
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ── request context (client IP for audit) ────────────────────────────────────────────────────────────

class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from app.services.audit import current_ip

        client = scope.get("client")
        token = current_ip.set(client[0] if client else None)
        try:
            await self.app(scope, receive, send)
        finally:
            current_ip.reset(token)


# ── idempotency ──────────────────────────────────────────────────────────────────────────────────────

def _db_lookup_or_reserve(scope_hash: str, key: str, method: str, path: str, fingerprint: str) -> tuple[str, Any]:
    """→ ("new", row_id) | ("replay", (status, headers, body)) | ("reused", None) | ("in_progress", None)."""
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from app.core.ids import new_id
    from app.core.time import ensure_aware, utcnow
    from app.db.models import IdempotencyKey
    from app.db.session import session_factory

    now = utcnow()
    with session_factory()() as db:
        row = db.scalar(select(IdempotencyKey).where(IdempotencyKey.scope_hash == scope_hash, IdempotencyKey.key == key))
        if row is not None and ensure_aware(row.expires_at) <= now:
            db.delete(row)
            db.commit()
            row = None
        if row is not None:
            if row.request_hash != fingerprint:
                return "reused", None
            if row.state != "completed":
                return "in_progress", None
            return "replay", (row.status_code, row.response_headers or {}, row.response_body or "")
        rid = new_id("idk")
        db.add(IdempotencyKey(id=rid, scope_hash=scope_hash, key=key, method=method, path=path[:1000],
                              request_hash=fingerprint, state="in_progress", expires_at=now + IDEMPOTENCY_TTL))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            row = db.scalar(select(IdempotencyKey).where(IdempotencyKey.scope_hash == scope_hash,
                                                         IdempotencyKey.key == key))
            if row is not None and row.request_hash != fingerprint:
                return "reused", None
            return "in_progress", None
        return "new", rid


def _db_complete(row_id: str, status: int | None, headers: dict | None, body: str | None, workspace_id: str | None) -> None:
    from app.db.models import IdempotencyKey
    from app.db.session import session_factory

    with session_factory()() as db:
        row = db.get(IdempotencyKey, row_id)
        if row is None:
            return
        if status is None or status not in STORE_STATUSES:
            db.delete(row)
        else:
            row.state = "completed"
            row.status_code = status
            row.response_headers = headers
            row.response_body = body
            row.workspace_id = workspace_id
        db.commit()


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in IDEMPOTENT_METHODS:
            await self.app(scope, receive, send)
            return
        key = _header(scope, b"idempotency-key")
        ctype = (_header(scope, b"content-type") or "").lower()
        cred = _credential(scope)
        if not key or not cred or "multipart/" in ctype or not _mw_settings().idempotency_enabled:
            await self.app(scope, receive, send)
            return
        key = key.strip()
        path = scope.get("path", "")
        if len(key) > MAX_KEY_LEN:
            status, headers, body = _problem(400, "bad_request", "Idempotency-Key must be at most 255 characters", path)
            await _send_simple(send, status, headers, body)
            return

        # Buffer the request body (JSON; bounded by the upload limit) and replay it to the app.
        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        raw = b"".join(chunks)
        fingerprint = hashlib.sha256(b"\n".join([scope["method"].encode(), path.encode(),
                                                 scope.get("query_string", b""), raw])).hexdigest()
        scope_hash = hashlib.sha256(cred.encode()).hexdigest()
        try:
            outcome, data = await run_in_threadpool(_db_lookup_or_reserve, scope_hash, key, scope["method"], path,
                                                    fingerprint)
        except Exception:  # noqa: BLE001 — fail open
            log.warning("Idempotency store unavailable; processing without replay protection", exc_info=True)
            outcome, data = "bypass", None

        if outcome == "reused":
            status, headers, body = _problem(422, "idempotency_key_reused",
                                             "This Idempotency-Key was already used with a different request", path)
            await _send_simple(send, status, headers, body)
            return
        if outcome == "in_progress":
            status, headers, body = _problem(409, "idempotency_in_progress",
                                             "A request with this Idempotency-Key is still being processed", path)
            await _send_simple(send, status, headers + [(b"retry-after", b"1")], body)
            return
        if outcome == "replay":
            st, hdrs, text = data
            body = text.encode() if isinstance(text, str) else (text or b"")
            headers = [(k.encode(), str(v).encode()) for k, v in (hdrs or {}).items()]
            headers += [(b"idempotency-replayed", b"true"), (b"content-length", str(len(body)).encode())]
            await _send_simple(send, int(st), headers, body if st not in (204, 304) else b"")
            return

        sent = False

        async def replay_receive() -> Message:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()

        captured: dict[str, Any] = {"status": None, "headers": {}, "body": [], "stream": False}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                captured["status"] = message["status"]
                hdrs = {}
                for k, v in message.get("headers") or []:
                    lk = k.decode("latin-1").lower()
                    if lk in REPLAY_HEADERS:
                        hdrs[lk] = v.decode("latin-1")
                    if lk == "content-type" and b"text/event-stream" in v:
                        captured["stream"] = True
                captured["headers"] = hdrs
            elif message["type"] == "http.response.body" and not captured["stream"]:
                captured["body"].append(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, replay_receive, send_wrapper)
        finally:
            if outcome == "new":
                status = None if captured["stream"] else captured["status"]
                body_bytes = b"".join(captured["body"])
                try:
                    body_text = body_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    status = None
                    body_text = None
                state = scope.get("state") or {}
                auth = state.get("auth") if isinstance(state, dict) else None
                ws = getattr(auth, "workspace_id", None)
                try:
                    await run_in_threadpool(_db_complete, data, status, captured["headers"], body_text, ws)
                except Exception:  # noqa: BLE001
                    log.warning("Could not store idempotent response", exc_info=True)


def install_middleware(app: Any) -> None:
    """Called by app.main before CORS is added (so CORS stays the outermost layer)."""
    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)


__all__ = ["install_middleware", "IdempotencyMiddleware", "RateLimitMiddleware", "RequestContextMiddleware",
           "FixedWindowLimiter", "MiddlewareSettings"]
