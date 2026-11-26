"""Shared outbound HTTP plumbing for worker adapters, provider clients, the MCP client, connection tests and
webhook delivery (owned by BE1).

* Every outbound call goes through :func:`send`, which accepts an injectable ``httpx`` transport so unit tests can
  use ``httpx.MockTransport`` and the sandbox (no external network) fails gracefully.
* Network / protocol failures are raised as :class:`UpstreamError` → ``502 application/problem+json`` with
  ``code=upstream_error`` (or a more specific code). Messages never contain query strings (they may carry
  ``api_key`` placements) or secret material.

Tests: ``set_transport(httpx.MockTransport(handler))`` redirects every client built here; ``set_transport(None)``
restores real networking.
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.errors import ApiError

_transport_override: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    """Process-wide transport override (tests / offline sandbox)."""
    global _transport_override
    _transport_override = transport


def get_transport(explicit: httpx.BaseTransport | None = None) -> httpx.BaseTransport | None:
    return explicit if explicit is not None else _transport_override


class UpstreamError(ApiError):
    """A call to an external component failed. Rendered as 502 problem+json."""

    def __init__(self, detail: str, *, code: str = "upstream_error", status_code: int | None = None,
                 latency_ms: int | None = None) -> None:
        super().__init__(502, code, detail, title="Bad gateway")
        self.status_code = status_code
        self.latency_ms = latency_ms


def safe_url(url: str | httpx.URL) -> str:
    """URL without query string / userinfo, safe to show in messages and logs."""
    try:
        u = httpx.URL(str(url))
        return str(u.copy_with(query=None, fragment=None, username=None, password=None))
    except Exception:  # noqa: BLE001
        return "<invalid url>"


def join_url(base: str, path: str | None) -> str:
    base = (base or "").rstrip("/")
    if not path:
        return base
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return base + "/" + path.lstrip("/")


def client(*, timeout: float = 30.0, transport: httpx.BaseTransport | None = None, verify: Any = True) -> httpx.Client:
    t = get_transport(transport)
    if t is not None:
        return httpx.Client(transport=t, timeout=timeout, follow_redirects=False)
    return httpx.Client(timeout=timeout, verify=verify, follow_redirects=False)


def send(req: dict[str, Any], *, timeout: float = 30.0, transport: httpx.BaseTransport | None = None,
         stream: bool = False) -> httpx.Response:
    """Send a prepared request dict (``method, url, headers, params, content|json|data|files`` and optional
    ``verify`` from :func:`app.services.adapters.auth.apply_auth`). Raises UpstreamError on transport failure.
    Response bodies are read eagerly (``stream=False``)."""
    kw = dict(req)
    verify = kw.pop("verify", True)
    method = kw.pop("method", "GET")
    url = kw.pop("url")
    t0 = time.perf_counter()
    try:
        with client(timeout=timeout, transport=transport, verify=verify) as c:
            resp = c.request(method, url, **kw)
            resp.read()
            resp.elapsed_ms = int((time.perf_counter() - t0) * 1000)  # type: ignore[attr-defined]
            return resp
    except httpx.TimeoutException as exc:
        raise UpstreamError(f"{method} {safe_url(url)} timed out after {timeout:g}s", code="upstream_timeout",
                            latency_ms=int((time.perf_counter() - t0) * 1000)) from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(f"{method} {safe_url(url)} failed: {type(exc).__name__}: {_short(exc)}",
                            latency_ms=int((time.perf_counter() - t0) * 1000)) from exc


def latency_of(resp: httpx.Response) -> int:
    return int(getattr(resp, "elapsed_ms", 0) or 0)


def raise_for_status(resp: httpx.Response, what: str | None = None) -> None:
    if resp.status_code >= 400:
        snippet = resp.text[:200].replace("\n", " ") if resp.content else ""
        raise UpstreamError(f"{what or resp.request.method + ' ' + safe_url(resp.request.url)} → HTTP "
                            f"{resp.status_code}{': ' + snippet if snippet else ''}", status_code=resp.status_code,
                            latency_ms=latency_of(resp))


def _short(exc: Exception) -> str:
    msg = str(exc) or exc.__class__.__name__
    return msg[:200]


__all__ = ["set_transport", "get_transport", "UpstreamError", "safe_url", "join_url", "client", "send",
           "latency_of", "raise_for_status"]
