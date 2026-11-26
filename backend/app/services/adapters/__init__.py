"""Worker adapters: call a document/image worker and normalise its response into ``PagesResult``
(contract ``docintel.pages.v1``). Owned by BE1; used by the workers router and BE2's run engine.

Public interface::

    from app.services import adapters
    result = adapters.invoke(worker, auth, data, "application/pdf", "inv.pdf", options={}, settings={})
    health = adapters.health(worker, auth)                       # WorkerHealth dict (never raises)
    adapter = adapters.get_adapter(worker)                       # WorkerAdapter instance
    sample = adapters.sample_pages(worker)                       # deterministic offline output (simulate mode)

``worker`` may be an ORM row or a dict with the Worker fields (base_url, adapter, invoke_path, health_path,
timeout_s). ``auth`` is the decrypted AuthConfig of the worker's connection (``{"type": "none"}`` if none) — use
:func:`app.services.adapters.auth.connection_auth` to load it. ``invoke`` raises
:class:`app.services.adapters.http.UpstreamError` (502 problem+json) when the worker fails.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any, Callable

import httpx

from app.core.time import iso, utcnow
from app.services.adapters.auth import apply_auth
from app.services.adapters.http import UpstreamError, join_url, latency_of, safe_url, send


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        v = obj.get(key, default)
    else:
        v = getattr(obj, key, default)
    return default if v is None else v


def _bbox(v: Any) -> list[float]:
    """Accept [x0,y0,x1,y1], [[x,y]×4] polygons or {x,y,w,h}/{x0..} dicts."""
    try:
        if isinstance(v, dict):
            if {"x0", "y0", "x1", "y1"} <= v.keys():
                return [float(v["x0"]), float(v["y0"]), float(v["x1"]), float(v["y1"])]
            if {"x", "y", "w", "h"} <= v.keys():
                return [float(v["x"]), float(v["y"]), float(v["x"]) + float(v["w"]), float(v["y"]) + float(v["h"])]
            if {"left", "top", "width", "height"} <= v.keys():
                return [float(v["left"]), float(v["top"]), float(v["left"]) + float(v["width"]),
                        float(v["top"]) + float(v["height"])]
        if isinstance(v, (list, tuple)) and v:
            if isinstance(v[0], (list, tuple)):
                xs = [float(p[0]) for p in v]
                ys = [float(p[1]) for p in v]
                return [min(xs), min(ys), max(xs), max(ys)]
            if len(v) >= 8:  # flat polygon
                xs = [float(x) for x in v[0::2]]
                ys = [float(y) for y in v[1::2]]
                return [min(xs), min(ys), max(xs), max(ys)]
            if len(v) == 4:
                return [float(x) for x in v]
    except (TypeError, ValueError, KeyError, IndexError):
        pass
    return [0.0, 0.0, 0.0, 0.0]


def validate_pages(data: Any) -> dict:
    """Validate/normalise a PagesResult dict (raises UpstreamError when it doesn't match the contract)."""
    from pydantic import ValidationError

    from app.schemas.generated import PagesResult

    try:
        return PagesResult.model_validate(data).model_dump(mode="json", exclude_none=True)
    except ValidationError as exc:
        raise UpstreamError(f"Worker returned an invalid PagesResult: {exc.errors()[0].get('msg', 'invalid')}",
                            code="invalid_worker_response") from exc


class WorkerAdapter:
    name = "base"
    default_invoke_path = "/invoke"
    default_health_path = "/health"

    def invoke_path(self, worker: Any) -> str:
        return _get(worker, "invoke_path") or self.default_invoke_path

    def health_path(self, worker: Any) -> str:
        return _get(worker, "health_path") or self.default_health_path

    def build_request(self, worker: Any, data: bytes, content_type: str, filename: str,
                      options: dict | None) -> dict[str, Any]:
        req: dict[str, Any] = {
            "method": "POST",
            "url": join_url(str(_get(worker, "base_url", "")), self.invoke_path(worker)),
            "files": {"file": (filename or "document", data, content_type or "application/octet-stream")},
            "headers": {"Accept": "application/json"},
        }
        if options:
            req["data"] = {"options": json.dumps(options)}
        return req

    def normalise(self, body: Any) -> dict:
        raise NotImplementedError

    def invoke(self, worker: Any, auth: dict | None, data: bytes, content_type: str, filename: str, *,
               options: dict | None = None, settings: dict | None = None,
               transport: httpx.BaseTransport | None = None) -> dict:
        req = apply_auth(self.build_request(worker, data, content_type, filename, options), auth,
                         settings=settings, transport=transport)
        t0 = time.perf_counter()
        resp = send(req, timeout=float(_get(worker, "timeout_s", 60)), transport=transport)
        if resp.status_code >= 400:
            raise UpstreamError(f"POST {safe_url(req['url'])} → HTTP {resp.status_code}", code="worker_error",
                                status_code=resp.status_code, latency_ms=latency_of(resp))
        try:
            body = resp.json()
        except ValueError as exc:
            raise UpstreamError("Worker returned a non-JSON response", code="invalid_worker_response") from exc
        out = validate_pages(self.normalise(body))
        out.setdefault("worker_latency_ms", int((time.perf_counter() - t0) * 1000))
        return out


class GenericPagesV1(WorkerAdapter):
    """POST multipart `file` to invoke_path; the worker returns PagesResult directly."""

    name = "generic_pages_v1"

    def normalise(self, body: Any) -> dict:
        if isinstance(body, dict) and "result" in body and "pages" not in body and isinstance(body["result"], dict):
            body = body["result"]
        return body


class ExcelParserV1(WorkerAdapter):
    """Existing Excel parser service: POST /to-text → {text_repr, sheet_names, total_cells, char_count};
    one page with one text block per sheet."""

    name = "excel_parser_v1"
    default_invoke_path = "/to-text"

    @staticmethod
    def split_sheets(text: str, sheets: list[str]) -> list[str]:
        if not sheets:
            return [text]
        if len(sheets) == 1:
            return [text]
        positions: list[int] = []
        cursor = 0
        for name in sheets:
            # Prefer a header-looking occurrence ("Sheet: X", "=== X ===", "[X]", "# X"), else the bare name.
            m = re.compile(r"(?im)^[^\n]*" + re.escape(name) + r"[^\n]*$").search(text, cursor)
            if not m:
                return [text if i == 0 else "" for i in range(len(sheets))]
            positions.append(m.start())
            cursor = m.end()
        chunks = []
        for i, start in enumerate(positions):
            end = positions[i + 1] if i + 1 < len(positions) else len(text)
            chunks.append(text[start:end].strip())
        return chunks

    def normalise(self, body: Any) -> dict:
        if not isinstance(body, dict) or "text_repr" not in body:
            raise UpstreamError("Excel parser response has no text_repr", code="invalid_worker_response")
        text = str(body.get("text_repr") or "")
        sheets = [str(s) for s in (body.get("sheet_names") or [])]
        chunks = self.split_sheets(text, sheets)
        pages = []
        for i, chunk in enumerate(chunks):
            label = sheets[i] if i < len(sheets) else f"Sheet {i + 1}"
            pages.append({"n": i + 1, "width": 1, "height": 1,
                          "blocks": [{"text": chunk or f'Sheet "{label}"', "bbox": [0, 0, 1, 1]}],
                          "key_values": [{"key": "sheet_name", "value": label}]})
        if not pages:
            pages = [{"n": 1, "width": 1, "height": 1, "blocks": [{"text": text, "bbox": [0, 0, 1, 1]}]}]
        return {"pages": pages, "text": text}


_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.I | re.S)
_TD = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")


def html_table_rows(html: str) -> list[list[str]]:
    return [[_TAG.sub("", c).strip() for c in _TD.findall(tr)] for tr in _TR.findall(html or "")]


class PaddleDocParseV1(WorkerAdapter):
    """Paddle DocParse (routes still to confirm). Tolerant mapping of the common shapes:

    * PagesResult already (``{"pages": [...]}`` with ``n/width/height/blocks``)
    * PP-Structure / layout parsing: ``{"result": {"layoutParsingResults": [{"prunedResult": {"width", "height",
      "parsing_res_list": [{"block_label", "block_content", "block_bbox"}]}}]}}`` (tables as HTML)
    * PaddleOCR: ``{"results": [[ [[x,y]×4], [text, score] ], ...]]}`` (one list per page) or
      ``{"pages": [{"page", "width", "height", "items"|"blocks": [{"text", "bbox"|"box"|"points", "score"}],
      "tables": [{"bbox", "html"|"rows"}]}]}``
    """

    name = "paddle_docparse_v1"
    default_invoke_path = "/parse"

    def normalise(self, body: Any) -> dict:
        if isinstance(body, dict) and isinstance(body.get("result"), dict):
            res = body["result"]
            if "layoutParsingResults" in res:
                return self._layout(res["layoutParsingResults"])
            body = res
        if isinstance(body, dict) and "layoutParsingResults" in body:
            return self._layout(body["layoutParsingResults"])
        if isinstance(body, dict) and isinstance(body.get("results"), list):
            return self._ocr_lists(body["results"])
        if isinstance(body, dict) and isinstance(body.get("pages"), list):
            return self._pages(body)
        raise UpstreamError("Unrecognised Paddle DocParse response", code="invalid_worker_response")

    def _layout(self, items: list) -> dict:
        pages, texts = [], []
        for i, item in enumerate(items or []):
            pr = (item or {}).get("prunedResult") or item or {}
            blocks, tables = [], []
            for b in pr.get("parsing_res_list") or []:
                label = str(b.get("block_label") or "text").lower()
                content = str(b.get("block_content") or "")
                bbox = _bbox(b.get("block_bbox"))
                if label == "table":
                    tables.append({"bbox": bbox, "rows": html_table_rows(content)})
                elif content:
                    blocks.append({"text": content, "bbox": bbox})
                    texts.append(content)
            pages.append({"n": i + 1, "width": float(pr.get("width") or 0), "height": float(pr.get("height") or 0),
                          "blocks": blocks, "tables": tables})
        return {"pages": pages, "text": "\n".join(texts)}

    def _ocr_lists(self, results: list) -> dict:
        pages, texts = [], []
        for i, lines in enumerate(results):
            blocks = []
            for line in lines or []:
                try:
                    box, (text, score) = line[0], line[1]
                except (TypeError, ValueError, IndexError):
                    continue
                blocks.append({"text": str(text), "bbox": _bbox(box), "confidence": float(score)})
                texts.append(str(text))
            xs = [b["bbox"][2] for b in blocks] or [0]
            ys = [b["bbox"][3] for b in blocks] or [0]
            pages.append({"n": i + 1, "width": max(xs), "height": max(ys), "blocks": blocks})
        return {"pages": pages, "text": "\n".join(texts)}

    def _pages(self, body: dict) -> dict:
        pages, texts = [], []
        for i, p in enumerate(body["pages"]):
            blocks = []
            for b in p.get("blocks") or p.get("items") or p.get("lines") or []:
                text = b.get("text") or b.get("content") or ""
                conf = b.get("confidence", b.get("score"))
                blk = {"text": str(text), "bbox": _bbox(b.get("bbox") or b.get("box") or b.get("points"))}
                if conf is not None:
                    blk["confidence"] = float(conf)
                blocks.append(blk)
                texts.append(str(text))
            tables = []
            for t in p.get("tables") or []:
                rows = t.get("rows") or html_table_rows(t.get("html") or "")
                tables.append({"bbox": _bbox(t.get("bbox") or t.get("box")), "rows": [[str(c) for c in r] for r in rows]})
            kvs = [{"key": str(k.get("key", "")), "value": str(k.get("value", "")), "bbox": _bbox(k.get("bbox"))}
                   for k in p.get("key_values") or []]
            page = {"n": int(p.get("n") or p.get("page") or i + 1), "width": float(p.get("width") or 0),
                    "height": float(p.get("height") or 0), "blocks": blocks, "tables": tables}
            if kvs:
                page["key_values"] = kvs
            if p.get("language"):
                page["language"] = p["language"]
            pages.append(page)
        return {"pages": pages, "text": body.get("text") or "\n".join(texts)}


ADAPTERS: dict[str, WorkerAdapter] = {a.name: a for a in (GenericPagesV1(), ExcelParserV1(), PaddleDocParseV1())}


def register_adapter(adapter: WorkerAdapter) -> None:
    ADAPTERS[adapter.name] = adapter


def get_adapter(worker: Any) -> WorkerAdapter:
    name = _get(worker, "adapter", "generic_pages_v1")
    try:
        return ADAPTERS[name]
    except KeyError as exc:
        from app.core.errors import ApiError

        raise ApiError(422, "unknown_adapter", f"Unknown worker adapter {name}") from exc


def invoke(worker: Any, connection_auth: dict | None, document_bytes: bytes, content_type: str, filename: str, *,
           options: dict | None = None, settings: dict | None = None,
           transport: httpx.BaseTransport | None = None) -> dict:
    """Send the document to the worker and return a validated PagesResult dict. Raises UpstreamError (502)."""
    return get_adapter(worker).invoke(worker, connection_auth, document_bytes, content_type, filename,
                                      options=options, settings=settings, transport=transport)


def health(worker: Any, connection_auth: dict | None, *, settings: dict | None = None,
           transport: httpx.BaseTransport | None = None) -> dict:
    """GET the worker's health_path with its credentials → WorkerHealth dict (never raises)."""
    adapter = get_adapter(worker)
    path = adapter.health_path(worker)
    url = join_url(str(_get(worker, "base_url", "")), path)
    checked = iso(utcnow())
    try:
        req = apply_auth({"method": "GET", "url": url, "headers": {"Accept": "application/json"}}, connection_auth,
                         settings=settings, transport=transport)
        resp = send(req, timeout=min(10.0, float(_get(worker, "timeout_s", 60))), transport=transport)
    except Exception as exc:  # noqa: BLE001 — health must degrade gracefully
        detail = getattr(exc, "detail", None) or str(exc)
        return {"status": "unreachable", "checked_at": checked, "message": f"GET {path} → {detail}"[:300]}
    latency = latency_of(resp)
    if resp.status_code >= 500 or resp.status_code in (401, 403, 404):
        return {"status": "unreachable" if resp.status_code >= 500 else "degraded", "latency_ms": latency,
                "checked_at": checked, "message": f"GET {path} → {resp.status_code}"}
    status = "healthy"
    try:
        body = resp.json()
        reported = str(body.get("status", "")).lower() if isinstance(body, dict) else ""
        if reported in ("degraded", "warn", "warning", "starting"):
            status = "degraded"
        elif reported in ("down", "error", "unhealthy", "fail", "failed"):
            status = "unreachable"
    except ValueError:
        pass
    if status == "healthy" and latency > 3000:
        status = "degraded"
    return {"status": status, "latency_ms": latency, "checked_at": checked, "message": f"GET {path} → {resp.status_code}"}


# ── deterministic offline output (run_mode=simulate) — mirrors the portal mock ────────────────────────

_EXCEL_SAMPLE = {
    "pages": [
        {"n": 1, "width": 1, "height": 1,
         "blocks": [{"text": 'Sheet "Summary": Revenue 4,820,000 · COGS 3,104,500 · Gross margin 35.6%', "bbox": [0, 0, 1, 1]}]},
        {"n": 2, "width": 1, "height": 1,
         "blocks": [{"text": 'Sheet "By region": UAE 61% · KSA 27% · Oman 12%', "bbox": [0, 0, 1, 1]}]},
    ],
    "text": "Summary …\nBy region …",
    "worker_latency_ms": 212,
}
_DOC_SAMPLE = {
    "pages": [{
        "n": 1, "width": 595, "height": 842, "language": "en",
        "blocks": [
            {"text": "Gulf Office Supplies LLC", "bbox": [48, 60, 260, 84], "confidence": 0.98},
            {"text": "TAX INVOICE", "bbox": [400, 60, 547, 86], "confidence": 0.99},
            {"text": "Invoice no. INV-2026-0417", "bbox": [380, 96, 547, 110], "confidence": 0.99},
        ],
        "tables": [{"bbox": [48, 300, 547, 420], "rows": [["Description", "Qty", "Unit price", "Amount (AED)"],
                                                          ["A4 copy paper, 80 gsm, box of 5", "40", "95.00", "3,800.00"]]}],
        "key_values": [{"key": "TRN", "value": "100354872600003", "bbox": [48, 120, 230, 134]}],
    }],
    "text": "Gulf Office Supplies LLC\nTAX INVOICE\n…",
    "worker_latency_ms": 840,
}


def sample_pages(worker: Any) -> dict:
    """Deterministic PagesResult used when settings.run_mode == "simulate" (portal mock parity)."""
    src = _EXCEL_SAMPLE if _get(worker, "adapter") == "excel_parser_v1" else _DOC_SAMPLE
    return json.loads(json.dumps(src))


def sample_health(worker: Any) -> dict:
    """Deterministic WorkerHealth in simulate mode (mock: generic image workers are 'down')."""
    path = _get(worker, "health_path") or "/health"
    if _get(worker, "adapter") == "generic_pages_v1" and "Image" in str(_get(worker, "name", "")):
        return {"status": "unreachable", "checked_at": iso(utcnow()),
                "message": f"GET {_get(worker, 'base_url', '')}{path} → connection refused (simulated)"}
    return {"status": "healthy", "latency_ms": 41, "checked_at": iso(utcnow()), "message": f"GET {path} → 200 (simulated)"}


AdapterFn = Callable[..., dict]

__all__ = ["WorkerAdapter", "GenericPagesV1", "ExcelParserV1", "PaddleDocParseV1", "ADAPTERS", "register_adapter",
           "get_adapter", "invoke", "health", "sample_pages", "sample_health", "validate_pages", "html_table_rows"]
