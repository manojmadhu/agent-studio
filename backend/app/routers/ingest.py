"""Ingest API (owned by BE2): POST /ingest/{pipeline_id} starts a production run on the live version.

Auth: an API key with ``ingest:write`` (normal `X-API-Key` path) **or** an HMAC signature over the raw body
(``X-DI-Signature``/``X-DI-Timestamp``, BE1's ``verify_ingest_hmac``) with the pipeline's ingest secret
(``pipelines.ingest_secret_ref`` in the SecretStore, payload ``{"secret": "..."}``; settings.secret_key-derived
fallback ``hmac(secret_key, "ingest:" + pipeline_id)`` when no secret was provisioned, so dev setups work).

Bodies: ``multipart/form-data`` (``file`` + optional ``metadata`` JSON string) per the contract, plus a JSON
extension ``{"filename", "content_type", "content_base64" | "url", "metadata"}``.
Idempotency-Key: replays the original 202 (same run) for 24 h; the same key with a different payload → 422.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac as hmac_mod
import json
from datetime import timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import ApiError, unauthorized
from app.core.security import AuthContext, get_auth
from app.core.time import utcnow
from app.db.session import session_factory
from app.services import audit, run_engine, storage
from app.services import validator as V

router = APIRouter(tags=["Ingest"])


def ingest_secret(db: Session, p: Any) -> str:
    if p.ingest_secret_ref:
        try:
            from app.services.secret_store import get_secret_store

            payload = get_secret_store().get(db, p.ingest_secret_ref)
            s = payload.get("secret") or payload.get("key")
            if s:
                return str(s)
        except Exception:  # noqa: BLE001
            pass
    key = get_settings().secret_key.encode()
    return hmac_mod.new(key, f"ingest:{p.id}".encode(), hashlib.sha256).hexdigest()


def _auth(request: Request, db: Session, p_lookup: Any, body: bytes) -> AuthContext:
    """API key / bearer through security.get_auth, or HMAC when X-DI-Signature is present."""
    if request.headers.get("x-di-signature"):
        from app.core.security import hmac_context, verify_ingest_hmac

        p = p_lookup()
        if p is None:
            raise ApiError(404, "not_found", title="Pipeline not found")
        verify_ingest_hmac(request, body, ingest_secret(db, p))
        return hmac_context(p.workspace_id, p.id)
    override = request.app.dependency_overrides.get(get_auth)  # tests override get_auth
    auth = override() if override else get_auth(request)
    if not auth.has_scope("ingest:write"):
        raise ApiError(403, "forbidden", "Requires scope ingest:write", title="Forbidden")
    return auth


def _idem_scope(ws: str, path: str) -> str:
    return hashlib.sha256(f"be2-ingest:{ws}:{path}".encode()).hexdigest()


@router.post("/ingest/{pipeline_id}", status_code=202)
async def ingest_document(pipeline_id: str, request: Request) -> JSONResponse:
    from app.db.models import IdempotencyKey, Pipeline, PipelineVersion

    body = await request.body()
    if len(body) > storage.max_bytes() + 1024 * 1024:
        raise storage.too_large()
    with session_factory()() as db:
        def lookup() -> Any:
            return db.scalar(select(Pipeline).where(Pipeline.id == pipeline_id, Pipeline.archived_at.is_(None)))

        auth = _auth(request, db, lookup, body)
        p = db.scalar(select(Pipeline).where(Pipeline.workspace_id == auth.workspace_id, Pipeline.id == pipeline_id,
                                             Pipeline.archived_at.is_(None)))
        if p is None:
            raise ApiError(404, "not_found", title="Pipeline not found")

        data, filename, ctype, meta = await _payload(request, body)
        key = request.headers.get("idempotency-key")
        # fingerprint of the *content* (multipart boundaries differ between otherwise identical requests)
        req_hash = hashlib.sha256(hashlib.sha256(data).digest() + json.dumps(
            [filename, meta], sort_keys=True, default=str).encode()).hexdigest()
        scope = _idem_scope(auth.workspace_id, request.url.path)
        if key:
            prev = db.scalar(select(IdempotencyKey).where(IdempotencyKey.scope_hash == scope,
                                                          IdempotencyKey.key == key[:255]))
            if prev is not None and prev.expires_at > utcnow():
                if prev.request_hash != req_hash:
                    raise ApiError(422, "idempotency_key_reused", "This Idempotency-Key was used with another payload",
                                   title="Validation failed")
                if prev.state == "completed" and prev.response_body:
                    return JSONResponse(json.loads(prev.response_body), status_code=prev.status_code or 202,
                                        headers={"Idempotency-Replayed": "true"})
                raise ApiError(409, "idempotency_in_progress", "The original request is still being processed",
                               title="Conflict")
            if prev is not None:
                db.delete(prev)
                db.flush()

        if p.status != "active":
            raise ApiError(409, "pipeline_not_active", f"The pipeline is {p.status}; activate it to accept documents",
                           title="Pipeline is not active")
        live = db.scalar(select(PipelineVersion).where(PipelineVersion.workspace_id == p.workspace_id,
                                                       PipelineVersion.pipeline_id == p.id,
                                                       PipelineVersion.version == p.live_version)) \
            if p.live_version is not None else None
        if live is None:
            raise ApiError(409, "no_live_version", "Publish a version before sending documents",
                           title="No published version")

        doc = storage.save_document(db, auth.workspace_id, data, filename, ctype, source="ingest", pipeline_id=p.id,
                                    meta=meta, uploaded_by=auth.name or auth.principal_id)
        graph = V.normalize_graph(live.graph)
        run = run_engine.create_run(db, workspace_id=auth.workspace_id, pipeline_id=p.id, graph=graph,
                                    version=p.live_version, doc=doc, test=False, deliver=True, trigger="ingest",
                                    input={"metadata": meta or {}, "document": storage.document_out(doc)},
                                    created_by=auth.name or auth.principal_id)
        audit.record(db, auth, "run.ingested", "run", run.id, summary=f"Ingested {doc.filename}",
                     details={"pipeline_id": p.id, "document_id": doc.id, "sha256": doc.sha256})
        out = run_engine.run_out(db, run, doc)
        if key:
            from app.core.ids import new_id

            db.add(IdempotencyKey(id=new_id("idk"), workspace_id=auth.workspace_id, scope_hash=scope, key=key[:255],
                                  method="POST", path=request.url.path[:1000], request_hash=req_hash,
                                  state="completed", status_code=202, response_body=json.dumps(out),
                                  response_headers={}, expires_at=utcnow() + timedelta(hours=24)))
        db.commit()
        run_engine.enqueue(run.id)
        return JSONResponse(out, status_code=202, headers={"Location": f"/v1/runs/{run.id}"})


async def _payload(request: Request, raw: bytes) -> tuple[bytes, str, str | None, dict | None]:
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" in ctype:
        try:
            form = await request.form(max_part_size=storage.max_bytes() + 1)
        except Exception as exc:  # noqa: BLE001
            raise ApiError(400, "bad_request", "Malformed multipart body", title="Malformed request body") from exc
        f = form.get("file")
        if f is None or isinstance(f, str):
            raise ApiError(422, "validation", "file is required", title="Validation failed")
        data = await f.read()
        meta = _meta(form.get("metadata"))
        return data, f.filename or "document", f.content_type, meta
    if "application/json" in ctype:
        try:
            body = json.loads(raw or b"{}")
        except ValueError as exc:
            raise ApiError(400, "bad_request", "The request body is not valid JSON", title="Malformed request body") from exc
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", "The request body must be a JSON object", title="Malformed request body")
        meta = _meta(body.get("metadata"))
        if body.get("content_base64"):
            try:
                data = base64.b64decode(body["content_base64"], validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ApiError(422, "validation", "content_base64 is not valid base64", title="Validation failed") from exc
        elif body.get("url"):
            data = _download(str(body["url"]))
        else:
            raise ApiError(422, "validation", "Send content_base64 or url", title="Validation failed")
        return data, str(body.get("filename") or "document"), body.get("content_type"), meta
    raise ApiError(415, "unsupported_media_type", "Send multipart/form-data or application/json",
                   title="Unsupported media type")


def _meta(v: Any) -> dict | None:
    if v is None or v == "":
        return None
    if isinstance(v, dict):
        return v
    try:
        m = json.loads(v)
    except (TypeError, ValueError) as exc:
        raise ApiError(422, "validation", "metadata must be a JSON object", title="Validation failed") from exc
    if not isinstance(m, dict):
        raise ApiError(422, "validation", "metadata must be a JSON object", title="Validation failed")
    return m


def _download(url: str) -> bytes:
    if not url.startswith("https://") and not (get_settings().env != "production" and url.startswith("http://")):
        raise ApiError(422, "validation", "url must be https", title="Validation failed")
    limit = storage.max_bytes()
    try:
        with httpx.stream("GET", url, timeout=30.0, follow_redirects=True) as r:
            if r.status_code >= 400:
                raise ApiError(502, "download_failed", f"GET {url} → {r.status_code}", title="Bad gateway")
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > limit:
                    raise storage.too_large()
            return bytes(buf)
    except httpx.HTTPError as exc:
        raise ApiError(502, "download_failed", f"Could not download the document: {exc}", title="Bad gateway") from exc


__all__ = ["router", "ingest_secret", "unauthorized"]
