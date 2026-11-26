"""Harmless authenticated probe per service for POST /connections/{id}/test (owned by BE1).

``probe(service, settings, auth, transport=None) -> TestResult dict`` never raises: every failure becomes
``{ok: false, message}``. ``simulated_probe`` mirrors the portal mock (run_mode=simulate).
"""
from __future__ import annotations

import socket
import time
from typing import Any

import httpx

from app.services.adapters.auth import apply_auth
from app.services.adapters.http import join_url, latency_of, safe_url, send

WHAT = {"gmail": "Gmail profile", "anthropic": "List models", "openai": "List models", "azure_openai": "List deployments",
        "google_gemini": "List models", "postgresql": "SELECT 1", "amazon_s3": "HeadBucket", "qdrant": "GET /collections",
        "http_api": "GET /health", "whatsapp_business": "Phone number lookup", "mcp": "initialize"}
MODEL_SERVICES = {"anthropic", "openai", "azure_openai", "google_gemini", "ollama", "openai_compatible"}


def simulated_probe(conn: Any) -> dict:
    ok = conn.service != "ollama" and conn.status != "pending_authorization"
    latency = 60 + (sum(map(ord, conn.id)) % 180)
    if ok:
        return {"ok": True, "latency_ms": latency,
                "message": f"{WHAT.get(conn.service, 'Authenticated request')} succeeded (simulated)"}
    return {"ok": False, "latency_ms": latency,
            "message": "Sign-in has not been completed" if conn.status == "pending_authorization"
            else "Connection refused: 10.0.4.12:11434 (simulated)"}


def _http(req: dict, auth: dict, settings: dict, what: str, transport: httpx.BaseTransport | None) -> dict:
    req = apply_auth(req, auth, settings=settings, transport=transport)
    resp = send(req, timeout=10, transport=transport)
    ok = resp.status_code < 400
    return {"ok": ok, "latency_ms": latency_of(resp),
            "message": f"{what} → HTTP {resp.status_code}" + ("" if ok else " (check the credentials)")}


def _tcp(host: str, port: int, what: str, payload: bytes | None = None, expect: bytes | None = None) -> dict:
    t0 = time.perf_counter()
    with socket.create_connection((host, port), timeout=5) as s:
        s.settimeout(5)
        if payload:
            s.sendall(payload)
        data = s.recv(64) if (payload or expect) else b""
    ok = expect is None or data.startswith(expect)
    return {"ok": ok, "latency_ms": int((time.perf_counter() - t0) * 1000),
            "message": f"{what} {'succeeded' if ok else 'returned an unexpected reply'}"}


def _sql(service: str, settings: dict, auth: dict) -> dict:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import URL

    if auth.get("type") not in ("basic", None):
        return {"ok": False, "message": f"SELECT 1 with {auth.get('type')} auth is not supported by the probe"}
    t0 = time.perf_counter()
    if service == "mssql":
        url = URL.create("mssql+pyodbc", username=auth.get("username"), password=auth.get("password"),
                         host=settings.get("host"), port=int(settings["port"]) if settings.get("port") else None,
                         database=settings.get("database"),
                         query={"driver": "ODBC Driver 18 for SQL Server", "TrustServerCertificate": "yes",
                                "LoginTimeout": "5"})
        eng = create_engine(url, pool_pre_ping=False)
    else:
        url = URL.create("postgresql+psycopg", username=auth.get("username"), password=auth.get("password"),
                         host=settings.get("host"), port=int(settings.get("port") or 5432),
                         database=settings.get("database"))
        eng = create_engine(url, connect_args={"connect_timeout": 5, "sslmode": settings.get("sslmode") or "prefer"})
    try:
        with eng.connect() as c:
            c.execute(text("SELECT 1"))
    finally:
        eng.dispose()
    return {"ok": True, "latency_ms": int((time.perf_counter() - t0) * 1000), "message": "SELECT 1 succeeded"}


def probe(service: str, settings: dict, auth: dict, *, transport: httpx.BaseTransport | None = None) -> dict:
    t0 = time.perf_counter()
    try:
        if service in MODEL_SERVICES:
            from app.services import llm

            models = llm.list_models(None, {"kind": service, "settings": settings, "auth": auth,
                                            "base_url": settings.get("base_url") or settings.get("endpoint")},
                                     transport=transport)
            return {"ok": True, "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "message": f"{WHAT.get(service, 'List models')} succeeded ({len(models)} models)"}
        if service in ("http_api", "document_worker"):
            base = settings.get("base_url")
            if not base:
                return {"ok": False, "message": "settings.base_url is required"}
            return _http({"method": "GET", "url": join_url(base, settings.get("health_path") or "/health")}, auth,
                         settings, "GET /health", transport)
        if service == "qdrant":
            return _http({"method": "GET", "url": join_url(settings.get("url") or "", "/collections")}, auth, settings,
                         "GET /collections", transport)
        if service == "azure_ai_search":
            req = {"method": "GET", "url": join_url(settings.get("endpoint") or "", "/indexes"),
                   "params": {"api-version": "2023-11-01", "$select": "name"}}
            if auth.get("type") == "api_key":
                auth = {**auth, "name": auth.get("name") if auth.get("name") not in (None, "Authorization") else "api-key"}
            return _http(req, auth, settings, "List indexes", transport)
        if service == "mcp":
            from app.services.mcp_client import McpClient

            with McpClient(settings.get("url") or "", auth=auth, http_transport=transport, timeout=10) as c:
                info = c.initialize()
            name = (info.get("serverInfo") or {}).get("name", "server")
            return {"ok": True, "latency_ms": int((time.perf_counter() - t0) * 1000),
                    "message": f"initialize succeeded ({name})"}
        if service == "gmail":
            return _http({"method": "GET", "url": "https://gmail.googleapis.com/gmail/v1/users/me/profile"}, auth,
                         settings, WHAT["gmail"], transport)
        if service == "microsoft_365_mail":
            return _http({"method": "GET", "url": "https://graph.microsoft.com/v1.0/me"}, auth, settings,
                         "Graph /me", transport)
        if service == "whatsapp_business":
            pid = settings.get("phone_number_id") or ""
            return _http({"method": "GET", "url": f"https://graph.facebook.com/v19.0/{pid}"}, auth, settings,
                         WHAT["whatsapp_business"], transport)
        if service == "slack":
            return _http({"method": "POST", "url": "https://slack.com/api/auth.test"}, auth, settings, "auth.test",
                         transport)
        if service == "amazon_s3":
            bucket, region = settings.get("bucket") or "", settings.get("region") or auth.get("region") or "us-east-1"
            return _http({"method": "HEAD", "url": f"https://{bucket}.s3.{region}.amazonaws.com/"}, auth,
                         {**settings, "aws_service": "s3", "region": region}, "HeadBucket", transport)
        if service == "azure_blob":
            acct, cont = settings.get("account") or "", settings.get("container") or ""
            return _http({"method": "GET", "url": f"https://{acct}.blob.core.windows.net/{cont}",
                          "params": {"restype": "container"}, "headers": {"x-ms-version": "2021-08-06"}},
                         auth, settings, "Get container properties", transport)
        if service in ("postgresql", "pgvector", "mssql"):
            return _sql(service, settings, auth)
        if service == "redis":
            u = httpx.URL(settings.get("url") or "redis://localhost:6379")
            return _tcp(u.host or "localhost", u.port or 6379, "PING", b"PING\r\n", b"+PONG")
        if service == "sftp":
            return _tcp(settings.get("host") or "", int(settings.get("port") or 22), "SSH handshake", None, b"SSH-")
        return {"ok": False, "message": f"No test available for {service}"}
    except Exception as exc:  # noqa: BLE001 — graceful failure is the contract here
        detail = getattr(exc, "detail", None) or f"{type(exc).__name__}: {str(exc)[:160]}"
        return {"ok": False, "latency_ms": int((time.perf_counter() - t0) * 1000), "message": str(detail)}


__all__ = ["probe", "simulated_probe", "safe_url"]
