"""Document storage (owned by BE2).

* ``ObjectStore`` protocol + ``LocalObjectStore`` (files under ``settings.storage_dir/<workspace>/<document>/…``).
  ``S3ObjectStore`` is a documented stub: implement ``put/get/delete/exists`` with boto3 (``put_object`` with
  ``ChecksumSHA256``, ``get_object``, ``delete_object``, ``head_object``) and select it with ``DI_OBJECT_STORE=s3``.
* ``save_document`` — size limit (``settings.max_upload_mb`` → 413 ``file_too_large``), sha256, page count, row in
  ``documents``. Caller commits.
* ``read_bytes(db, workspace_id, document_id) -> (bytes, content_type, filename)`` — public helper used by BE1's
  workers router (``POST /workers/{id}/invoke``) and by the live run engine. The portal's sample documents
  (``doc_inv_sample``, ``doc_rcp_sample``) have no stored bytes; a small deterministic PDF/JPEG is synthesised so
  workers and page images still have something real to process.
* ``page_image`` — PNG raster of one page. Images are converted to PNG (original bytes if Pillow is missing); PDFs
  are rendered with ``pypdfium2`` when installed, otherwise 404 ``page_image_unavailable``.
  **Redaction**: ``redacted=true`` (default) paints opaque boxes over every redaction overlay with a bbox recorded
  for the document (``run_results.overlays.redactions`` of runs on this document). Simulated runs (like the portal
  mock) record no redaction boxes, so the raster is unchanged; the response says so honestly in
  ``X-DI-Redacted`` (``true``/``false``) and ``X-DI-Redaction-Count`` headers.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.errors import ApiError, not_found
from app.core.ids import new_id
from app.services import catalog_data

# ───────────────────────── object stores ─────────────────────────


class ObjectStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalObjectStore:
    """Filesystem store. Keys are relative POSIX paths; path traversal is rejected."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).resolve()

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError("invalid object key")
        return p

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except FileNotFoundError:
            pass

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3ObjectStore:  # pragma: no cover - stub
    """S3-compatible store (AWS S3, MinIO, Azure Blob via S3 gateway). Not implemented yet.

    Planned settings: DI_S3_BUCKET, DI_S3_ENDPOINT_URL, DI_S3_REGION, credentials from the default AWS chain.
    Objects are written with server-side encryption and ``ChecksumSHA256``; keys are the same as LocalObjectStore.
    """

    def __init__(self, *_: Any, **__: Any) -> None:
        raise NotImplementedError("S3ObjectStore is not implemented; use DI_OBJECT_STORE=local")

    def put(self, key: str, data: bytes, content_type: str | None = None) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


_store: ObjectStore | None = None


def get_store() -> ObjectStore:
    global _store
    if _store is None:
        kind = os.environ.get("DI_OBJECT_STORE", "local")
        _store = S3ObjectStore() if kind == "s3" else LocalObjectStore(get_settings().storage_dir)
    return _store


def set_store(store: ObjectStore | None) -> None:
    """Tests: point storage at a temp dir."""
    global _store
    _store = store


# ───────────────────────── documents ─────────────────────────

ALLOWED_MIME = {
    "application/pdf", "image/png", "image/jpeg", "image/tiff", "image/webp", "image/gif",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "text/csv", "text/plain",
    "application/json", "application/octet-stream",
}
_EXT_MIME = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".tif": "image/tiff",
    ".tiff": "image/tiff", ".webp": "image/webp", ".gif": "image/gif",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xls": "application/vnd.ms-excel",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".csv": "text/csv",
    ".txt": "text/plain", ".json": "application/json",
}


def max_bytes() -> int:
    return int(get_settings().max_upload_mb) * 1024 * 1024


def too_large() -> ApiError:
    return ApiError(413, "file_too_large", f"The file is larger than {get_settings().max_upload_mb} MB",
                    title="File too large")


def guess_mime(filename: str, declared: str | None, data: bytes) -> str:
    declared = (declared or "").split(";")[0].strip().lower()
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\x89PNG"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if declared and declared != "application/octet-stream":
        return declared
    return _EXT_MIME.get(Path(filename or "").suffix.lower(), declared or "application/octet-stream")


def count_pages(data: bytes, mime: str) -> int:
    try:
        if mime == "application/pdf":
            import pypdfium2 as pdfium  # type: ignore

            pdf = pdfium.PdfDocument(data)
            try:
                return max(1, len(pdf))
            finally:
                pdf.close()
        if mime == "image/tiff":
            from PIL import Image  # type: ignore

            with Image.open(io.BytesIO(data)) as im:
                return max(1, getattr(im, "n_frames", 1))
    except Exception:  # noqa: BLE001 - unreadable or renderer missing: 1 page
        pass
    if mime == "application/pdf":
        n = len(re.findall(rb"/Type\s*/Page[^s]", data))
        return max(1, n)
    return 1


def _safe_name(filename: str) -> str:
    name = Path(filename or "document").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._") or "document"
    return name[:120]


def save_document(db: Session, workspace_id: str, data: bytes, filename: str, content_type: str | None, *,
                  source: str = "upload", pipeline_id: str | None = None, meta: dict | None = None,
                  uploaded_by: str | None = None, doc_id: str | None = None) -> Any:
    """Store bytes + insert a `documents` row (flush, no commit). Raises 413 / 422."""
    from app.db.models import Document

    if len(data) > max_bytes():
        raise too_large()
    if not data:
        raise ApiError(422, "validation", "The file is empty", title="Validation failed")
    mime = guess_mime(filename, content_type, data)
    doc_id = doc_id or new_id("doc")
    key = f"{workspace_id}/{doc_id}/{_safe_name(filename)}"
    get_store().put(key, data, mime)
    doc = Document(id=doc_id, workspace_id=workspace_id, filename=Path(filename or "document").name or "document",
                   mime_type=mime, size_bytes=len(data), pages=count_pages(data, mime),
                   sha256=hashlib.sha256(data).hexdigest(), object_key=key, source=source,
                   pipeline_id=pipeline_id, meta=meta, uploaded_by=uploaded_by)
    retention = retention_days_for(db, workspace_id, pipeline_id)
    if retention:
        from datetime import timedelta

        from app.core.time import utcnow

        doc.retention_until = utcnow() + timedelta(days=retention)
    db.add(doc)
    db.flush()
    return doc


def retention_days_for(db: Session, workspace_id: str, pipeline_id: str | None) -> int | None:
    """Enabled `retention` policies that apply to the pipeline (or to all pipelines)."""
    try:
        from app.db.models import Pipeline, Policy
    except Exception:  # noqa: BLE001
        return None
    owner = None
    if pipeline_id:
        p = db.get(Pipeline, pipeline_id)
        owner = p.owner_team if p is not None and p.workspace_id == workspace_id else None
    days: int | None = None
    for pol in db.scalars(select(Policy).where(Policy.workspace_id == workspace_id)).all():
        if getattr(pol, "kind", None) != "retention" or not getattr(pol, "enabled", False):
            continue
        scope = getattr(pol, "scope", None) or {}
        applies = scope.get("all_pipelines") or (pipeline_id and pipeline_id in (scope.get("pipeline_ids") or [])) \
            or (owner and owner in (scope.get("owner_teams") or []))
        if applies:
            d = (getattr(pol, "config", None) or {}).get("retention_days")
            if isinstance(d, (int, float)) and d > 0:
                days = max(days or 0, int(d))
    return days


@dataclass
class SampleDocument:
    """The portal's sample documents (doc_inv_sample, doc_rcp_sample) are virtual: available in every workspace,
    never stored as rows (document ids are global primary keys), bytes synthesised on demand."""

    id: str
    filename: str
    mime_type: str
    pages: int = 1
    kind: str = "inv"
    doc_kind: str = "invoice"
    workspace_id: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    object_key: str | None = None
    created_at: Any = None
    retention_until: Any = None
    deleted_at: Any = None
    redacted: bool = True
    is_sample: bool = True


def get_document(db: Session, workspace_id: str, document_id: str) -> Any:
    """The workspace's document row, or a virtual SampleDocument for the portal's sample ids. 404 otherwise."""
    from app.db.models import Document

    doc = db.get(Document, document_id)
    if doc is not None and doc.workspace_id == workspace_id and doc.deleted_at is None:
        return doc
    sample = sample_document(document_id)
    if sample and (doc is None or doc.workspace_id != workspace_id):
        return SampleDocument(id=document_id, filename=sample["filename"], mime_type=sample["mime_type"],
                              pages=int(sample.get("pages") or 1), kind=sample.get("kind") or "inv",
                              doc_kind=sample.get("doc_kind") or "invoice", workspace_id=workspace_id)
    raise not_found("Document", document_id)


def ensure_sample_documents(db: Session, workspace_id: str) -> list[Any]:
    """Seed helper (for app.db.seed): store the synthesised sample files and insert `documents` rows for the portal's
    sample ids in `workspace_id`. Idempotent; ids that already exist (in any workspace) are left alone — other
    workspaces still see them as virtual SampleDocuments. Flushes, does not commit."""
    from app.db.models import Document

    out = []
    for sid, s in sample_documents().items():
        if db.get(Document, sid) is not None:
            continue
        data = synth_sample_bytes(sid, s["filename"], s["mime_type"])
        out.append(save_document(db, workspace_id, data, s["filename"], s["mime_type"], source="sample", doc_id=sid))
    return out


def is_sample(doc: Any) -> bool:
    return bool(getattr(doc, "is_sample", False))


def document_out(doc: Any) -> dict:
    from app.core.time import iso

    out: dict[str, Any] = {"id": doc.id, "filename": doc.filename, "mime_type": doc.mime_type, "pages": doc.pages or 1}
    if doc.size_bytes is not None:
        out["size_bytes"] = doc.size_bytes
    if doc.sha256:
        out["sha256"] = doc.sha256
    if doc.created_at is not None:
        out["uploaded_at"] = iso(doc.created_at)
    if doc.retention_until:
        out["retention_until"] = iso(doc.retention_until)
    return out


def read_bytes(db: Session, workspace_id: str, document_id: str) -> tuple[bytes, str, str]:
    """(bytes, content_type, filename) of a workspace document. 404 for unknown ids / other workspaces."""
    doc = get_document(db, workspace_id, document_id)
    if doc.object_key:
        try:
            return get_store().get(doc.object_key), doc.mime_type, doc.filename
        except FileNotFoundError as exc:
            raise ApiError(404, "document_content_missing", "The stored file is no longer available") from exc
    return synth_sample_bytes(doc.id, doc.filename, doc.mime_type), doc.mime_type, doc.filename


def delete_document(db: Session, doc: Any) -> None:
    from app.core.time import utcnow

    if doc.object_key:
        get_store().delete(doc.object_key)
    doc.deleted_at = utcnow()
    doc.object_key = None


# ───────────────────────── samples ─────────────────────────

def sample_documents() -> dict[str, dict]:
    data = catalog_data.sample_documents() or {}
    return {d["id"]: d for d in data.get("documents", [])}


def sample_document(document_id: str) -> dict | None:
    return sample_documents().get(document_id)


def _sample_lines(filename: str, kind: str) -> list[str]:
    vals = ((catalog_data.sample_documents() or {}).get("values") or {}).get(kind) or {}
    title = "TAX INVOICE" if kind == "inv" else "RECEIPT"
    lines = [title, filename]
    lines += [f"{k.replace('_', ' ').title()}: {v[0]}" for k, v in vals.items()]
    return lines


def _minimal_pdf(lines: list[str]) -> bytes:
    """A valid one-page PDF (Helvetica text) without external libraries."""
    def esc(s: str) -> str:
        return s.encode("latin-1", "replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    y = 780
    ops = ["BT", "/F1 12 Tf"]
    for i, line in enumerate(lines):
        ops.append(f"1 0 0 1 48 {y - i * 20} Tm ({esc(line)}) Tj")
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def _text_image(lines: list[str], fmt: str) -> bytes:
    try:
        from PIL import Image, ImageDraw  # type: ignore
    except Exception:  # noqa: BLE001
        return b""
    im = Image.new("RGB", (600, 40 + 24 * len(lines)), "white")
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        d.text((24, 20 + i * 24), line.encode("latin-1", "replace").decode("latin-1"), fill="black")
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    return buf.getvalue()


def synth_sample_bytes(document_id: str, filename: str, mime: str) -> bytes:
    s = sample_document(document_id) or {}
    kind = s.get("kind") or ("rcp" if (filename or "").lower().startswith("receipt") else "inv")
    lines = _sample_lines(filename, kind)
    if mime == "application/pdf":
        return _minimal_pdf(lines)
    if mime in ("image/jpeg", "image/png"):
        data = _text_image(lines, "JPEG" if mime == "image/jpeg" else "PNG")
        if data:
            return data
    return ("\n".join(lines) + "\n").encode()


# ───────────────────────── page images ─────────────────────────

def _redaction_boxes(db: Session, workspace_id: str, document_id: str, page: int) -> list[list[float]]:
    from app.db.models import Run, RunResult

    boxes: list[list[float]] = []
    run_ids = db.scalars(select(Run.id).where(Run.workspace_id == workspace_id, Run.document_id == document_id)).all()
    if not run_ids:
        return boxes
    for rr in db.scalars(select(RunResult).where(RunResult.workspace_id == workspace_id,
                                                 RunResult.run_id.in_(list(run_ids)))).all():
        for r in ((rr.overlays or {}).get("redactions") or []):
            bb = r.get("bbox")
            if isinstance(bb, list) and len(bb) == 4 and int(r.get("page") or 1) == page:
                boxes.append([float(x) for x in bb])
    return boxes


def page_image(db: Session, workspace_id: str, document_id: str, page: int, redacted: bool = True
               ) -> tuple[bytes, str, dict[str, str]]:
    """(content, media_type, headers) for GET /documents/{id}/pages/{page}/image."""
    data, mime, _ = read_bytes(db, workspace_id, document_id)
    doc = get_document(db, workspace_id, document_id)
    if page < 1 or page > max(1, doc.pages or 1):
        raise ApiError(404, "page_not_found", f"The document has {doc.pages or 1} page(s)", title="Not found")
    boxes = _redaction_boxes(db, workspace_id, document_id, page) if redacted else []
    png: bytes | None = None
    scale = 1.0
    try:
        from PIL import Image  # type: ignore
    except Exception:  # noqa: BLE001
        Image = None  # type: ignore
    if mime == "application/pdf":
        try:
            import pypdfium2 as pdfium  # type: ignore
        except Exception as exc:  # noqa: BLE001
            raise ApiError(404, "page_image_unavailable", "No PDF renderer is installed on the server",
                           title="Not found") from exc
        pdf = pdfium.PdfDocument(data)
        try:
            pg = pdf[page - 1]
            scale = 2.0
            bitmap = pg.render(scale=scale)
            pil = bitmap.to_pil()
            height_pt = pg.get_height()
        finally:
            pdf.close()
        # PDF bboxes are in points with a top-left origin (docintel.pages.v1) → pixels
        png = _to_png(pil, [[b[0] * scale, b[1] * scale, b[2] * scale, b[3] * scale] for b in boxes])
        del height_pt
    elif mime.startswith("image/"):
        if Image is None:
            return data, mime, {"X-DI-Redacted": "false", "X-DI-Redaction-Count": "0", "Cache-Control": "private"}
        with Image.open(io.BytesIO(data)) as im:
            if page > 1:
                im.seek(page - 1)
            png = _to_png(im.convert("RGB"), boxes)
    else:
        raise ApiError(404, "page_image_unavailable", f"Page images are not available for {mime}", title="Not found")
    headers = {"X-DI-Redacted": "true" if (redacted and boxes) else "false",
               "X-DI-Redaction-Count": str(len(boxes)), "Cache-Control": "private, max-age=60"}
    return png, "image/png", headers


def _to_png(pil: Any, boxes: list[list[float]]) -> bytes:
    if boxes:
        from PIL import ImageDraw  # type: ignore

        d = ImageDraw.Draw(pil)
        for b in boxes:
            d.rectangle([b[0], b[1], b[2], b[3]], fill="black")
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return buf.getvalue()
