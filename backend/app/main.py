"""FastAPI application factory. Run: uvicorn app.main:app --port 8080 (from backend/)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from app.config import get_settings
from app.core.errors import install_error_handlers
from app.routers import (
    catalog, connections, documents, identity, ingest, knowledge, mcp_servers, ops, pipelines, policies,
    providers, review, runs, skills, workers,
)

log = logging.getLogger("docintel")

CONTRACT = Path(__file__).resolve().parents[2] / "api" / "openapi.yaml"

# Headers the portal must be able to send / read cross-origin (see docs/portal-integration-notes.md).
ALLOW_HEADERS = ["Authorization", "X-API-Key", "Content-Type", "Idempotency-Key", "If-Match", "If-None-Match",
                 "Last-Event-ID", "Accept", "X-DI-Signature", "X-DI-Timestamp", "Cache-Control"]
EXPOSE_HEADERS = ["ETag", "Location", "RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset",
                  "Idempotency-Replayed", "Retry-After", "Content-Disposition"]

ROUTERS = [identity, catalog, pipelines, documents, runs, ingest, review, connections, providers, workers,
           mcp_servers, knowledge, skills, policies, ops]


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    settings = get_settings()
    if settings.seed_on_startup:
        try:
            from app.db.seed import seed_if_empty  # provided by the DB engineer
            from app.db.session import session_scope

            with session_scope() as db:
                seed_if_empty(db)
        except Exception:  # noqa: BLE001
            log.exception("Seeding skipped")
    try:
        from app.services import lifecycle  # optional startup/shutdown hooks (run engine, webhook dispatcher)

        lifecycle.startup(app)
    except ImportError:
        pass
    yield
    try:
        from app.services import lifecycle

        lifecycle.shutdown(app)
    except ImportError:
        pass


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Doc Intelligence Platform API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    install_error_handlers(app)

    # Cross-cutting middleware (idempotency, rate-limit headers) is installed by BE1 in app.core.middleware.
    try:
        from app.core.middleware import install_middleware

        install_middleware(app)
    except ImportError:
        pass

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=ALLOW_HEADERS,
        expose_headers=EXPOSE_HEADERS,
        max_age=600,
    )

    for mod in ROUTERS:
        app.include_router(mod.router, prefix="/v1")

    @app.get("/healthz", include_in_schema=False)
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/openapi.yaml", include_in_schema=False)
    def contract() -> FileResponse:
        """The authoritative contract (api/openapi.yaml). Used by Schemathesis contract tests."""
        return FileResponse(CONTRACT, media_type="application/yaml")

    return app


app = create_app()
