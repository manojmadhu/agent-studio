"""Documents API (owned by BE2): multipart upload (≤ settings.max_upload_mb, sha256), get, delete, page images."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.security import AuthContext, require_scope
from app.core.time import utcnow
from app.db.session import get_db
from app.services import audit, storage

router = APIRouter(tags=["Documents"])


async def read_upload(request: Request, field: str = "file") -> tuple[bytes, str, str | None, dict]:
    """Parse multipart/form-data → (bytes, filename, content_type, other form fields). Enforces the size limit
    from Content-Length before reading and again after."""
    ctype = request.headers.get("content-type", "")
    if "multipart/form-data" not in ctype:
        raise ApiError(415, "unsupported_media_type", "Send the file as multipart/form-data",
                       title="Unsupported media type")
    try:
        clen = int(request.headers.get("content-length") or 0)
    except ValueError:
        clen = 0
    if clen > storage.max_bytes() + 1024 * 1024:  # + multipart overhead
        raise storage.too_large()
    try:
        form = await request.form(max_part_size=storage.max_bytes() + 1)
    except Exception as exc:  # noqa: BLE001
        raise ApiError(400, "bad_request", "Malformed multipart body", title="Malformed request body") from exc
    f = form.get(field)
    if f is None or isinstance(f, str):
        raise ApiError(422, "validation", f"{field} is required", title="Validation failed")
    data = await f.read()
    if len(data) > storage.max_bytes():
        raise storage.too_large()
    others = {k: v for k, v in form.items() if k != field and isinstance(v, str)}
    return data, f.filename or "document", f.content_type, others


@router.post("/documents", status_code=201)
async def upload_document(request: Request, auth: AuthContext = Depends(require_scope("runs:write")),
                          db: Session = Depends(get_db)) -> JSONResponse:
    data, filename, ctype, fields = await read_upload(request)
    purpose = fields.get("purpose")
    if purpose not in (None, "", "test", "ingest"):
        raise ApiError(422, "validation", "purpose must be test or ingest", title="Validation failed")
    doc = storage.save_document(db, auth.workspace_id, data, filename, ctype, source="upload",
                                meta={"purpose": purpose} if purpose else None,
                                uploaded_by=auth.name or auth.principal_id)
    audit.record(db, auth, "document.uploaded", "document", doc.id, summary=f"Uploaded {doc.filename}",
                 details={"sha256": doc.sha256, "size_bytes": doc.size_bytes, "mime_type": doc.mime_type})
    out = storage.document_out(doc)
    db.commit()
    return JSONResponse(out, status_code=201, headers={"Location": f"/v1/documents/{doc.id}"})


@router.get("/documents/{id}")
def get_document(id: str, auth: AuthContext = Depends(require_scope("runs:read")),
                 db: Session = Depends(get_db)) -> dict:
    return storage.document_out(storage.get_document(db, auth.workspace_id, id))


@router.delete("/documents/{id}", status_code=204)
def delete_document(id: str, auth: AuthContext = Depends(require_scope("runs:write")),
                    db: Session = Depends(get_db)) -> Response:
    doc = storage.get_document(db, auth.workspace_id, id)
    if storage.is_sample(doc) or getattr(doc, "source", None) == "sample":
        raise ApiError(409, "sample_document", "Sample documents cannot be deleted", title="Conflict")
    if doc.retention_until and doc.retention_until > utcnow():
        raise ApiError(409, "retention_applies", f"Retained until {doc.retention_until.date().isoformat()} by policy",
                       title="Retention applies")
    storage.delete_document(db, doc)
    audit.record(db, auth, "document.deleted", "document", doc.id, summary=f"Deleted {doc.filename}")
    db.commit()
    return Response(status_code=204)


@router.get("/documents/{id}/pages/{page}/image")
def get_page_image(id: str, page: int, redacted: bool = Query(True),
                   auth: AuthContext = Depends(require_scope("runs:read")), db: Session = Depends(get_db)) -> Response:
    if not redacted and not auth.has_scope("pipelines:write"):
        raise ApiError(403, "forbidden", "Unredacted page images require the pipelines:write scope", title="Forbidden")
    content, media, headers = storage.page_image(db, auth.workspace_id, id, page, redacted)
    if not redacted:
        audit.record(db, auth, "document.viewed_unredacted", "document", id, summary=f"Viewed page {page} unredacted")
        db.commit()
    return Response(content=content, media_type=media, headers=headers)
