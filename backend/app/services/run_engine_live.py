"""Live step execution for the run engine (``settings.run_mode == "live"``), owned by BE2.

Read steps call BE1's worker adapters, AI steps call BE1's ``llm.complete`` on the plugged model's provider.
Everything else (prepare, routing, lookups, injection screen) runs the same deterministic logic as simulate mode,
except business rules (evaluated on the extracted values) and PII redaction (masks PII fields that have values).
Failures raise ``StepError`` with a problem dict; the engine applies retries / on_error.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Any

from sqlalchemy import select

from app.core.errors import ApiError
from app.services import storage
from app.services import validator as V
from app.services.run_engine import (
    EngineState, StepError, _g, _js, _values, problem, schema_for, step_output, UNDEF,
)

DELIVER_SIMULATED = {"status": "ok", "simulated": True}


def _err(exc: Exception, code: str = "step_failed", title: str = "Step failed") -> StepError:
    if isinstance(exc, ApiError):
        p = problem(exc.status, exc.code, exc.title, exc.detail)
        return StepError(p, retryable=exc.status >= 500 or exc.status == 429)
    return StepError(problem(500, code, title, str(exc)[:500]))


def _timeout(settings: dict, default: float = 60.0) -> float:
    t = V.js_number(settings.get("timeout_s"))
    return default if math.isnan(t) or t <= 0 else float(t)


def execute(ex: Any, n: dict, d: dict, st: EngineState, settings: dict) -> tuple[dict, str | None, dict | None]:
    t = n.get("type")
    c = n.get("config") or {}
    ex._apply_layers(n, st)
    if t in ("read.ocr", "read.sheet"):
        return _read(ex, n, c, st, settings), None, None
    if t == "read.layout":
        if st.tables is None:
            out = step_output(ex.graph, n, ex.doc)
        else:
            out = {"tables": st.tables, "key_value_pairs": len(st.key_values)}
        return out, None, None
    if t == "ai.classify":
        return _classify(ex, n, c, st, settings)
    if t in ("ai.extract", "ai.agent"):
        return _extract(ex, n, c, st, settings)
    if t == "route.type":
        kind = st.classification or ex.doc.doc_kind
        port = kind if kind in V.js_list(c.get("paths")) else "other"
        return {"status": "ok", "output": port}, port, None
    if t == "route.confidence":
        out, port = ex._confidence_gate(n, st)
        return out, port, None
    if t == "val.rules":
        return _rules(ex, n, c, st), None, None
    if t == "val.pii":
        vals = _values(st, ex.doc)
        pii = [f["name"] for f in schema_for(ex.graph, st, ex.doc) if f.get("pii") and vals.get(f.get("name"))]
        return _js({"masked": len(pii), "method": _g(c, "mode"), "fields": pii}), None, None
    if d.get("stage") == "deliver":
        return _deliver(ex, n, c, st), None, None
    return step_output(ex.graph, n, ex.doc), None, None


# ── read ────────────────────────────────────────────────────────────────────────────────────────

def _read(ex: Any, n: dict, c: dict, st: EngineState, settings: dict) -> dict:
    from app.db.models import Worker
    from app.services import adapters
    from app.services.adapters.auth import connection_auth

    wid = c.get("worker")
    w = ex.db.scalar(select(Worker).where(Worker.workspace_id == ex.ws, Worker.id == wid)) if wid else None
    if w is None:
        raise StepError(problem(422, "worker_not_found", "Worker not found", f"Worker {wid} is not registered"),
                        retryable=False)
    worker = {k: getattr(w, k) for k in ("id", "name", "kind", "base_url", "adapter", "invoke_path", "health_path",
                                         "connection_id", "accepts", "capabilities", "data_region")}
    worker["timeout_s"] = int(_timeout(settings, float(w.timeout_s or 60)))
    try:
        data, mime, filename = storage.read_bytes(ex.db, ex.ws, ex.doc_row.id)
        _, conn_settings, auth = connection_auth(ex.db, ex.ws, w.connection_id)
        options = {k: v for k, v in c.items() if k in ("lang", "mode", "max")}
        res = adapters.invoke(worker, auth, data, mime, filename, options=options, settings=conn_settings)
    except StepError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _err(exc, "worker_failed", "Worker call failed") from exc
    pages = res.get("pages") or []
    text = res.get("text") or "\n".join(b.get("text", "") for p in pages for b in (p.get("blocks") or []))
    st.text = (st.text + "\n" + text).strip() if st.text else text
    confs = [b["confidence"] for p in pages for b in (p.get("blocks") or []) if isinstance(b.get("confidence"), (int, float))]
    st.overlays.setdefault("blocks", []).extend({"page": p.get("n", i + 1), "bbox": b["bbox"]}
                                                for i, p in enumerate(pages) for b in (p.get("blocks") or []) if b.get("bbox"))
    st.overlays.setdefault("tables", []).extend({"page": p.get("n", i + 1), "bbox": t["bbox"]}
                                                for i, p in enumerate(pages) for t in (p.get("tables") or []) if t.get("bbox"))
    st.key_values = [{**kv, "page": p.get("n", i + 1)} for i, p in enumerate(pages) for kv in (p.get("key_values") or [])]
    st.tables = sum(len(p.get("tables") or []) for p in pages)
    out: dict[str, Any] = {"pages": len(pages), "characters": len(text)}
    if n.get("type") == "read.sheet":
        out = {"sheets": len(pages), "characters": len(text)}
    else:
        out["mean_confidence"] = round(sum(confs) / len(confs), 2) if confs else None
    out["worker"] = wid
    if res.get("worker_latency_ms") is not None:
        out["latency_ms"] = res["worker_latency_ms"]
    return out


# ── AI ──────────────────────────────────────────────────────────────────────────────────────────

def _model(ex: Any, n: dict) -> tuple[Any, dict]:
    from app.db.models import Provider

    m = V.model_of(ex.graph, n)
    if m is None:
        raise StepError(problem(422, "missing_model", "No language model", f"{n.get('name')} needs a language model"),
                        retryable=False)
    mc = m.get("config") or {}
    pid = mc.get("provider") or mc.get("provider_id")
    p = ex.db.scalar(select(Provider).where(Provider.workspace_id == ex.ws, Provider.id == pid)) if pid else None
    if p is None or not p.enabled:
        raise StepError(problem(422, "provider_disabled" if p is not None else "missing_provider",
                                "Provider unavailable", f"Provider {pid} is missing or disabled"), retryable=False)
    return p, mc


def _complete(ex: Any, n: dict, messages: list[dict], schema: dict, settings: dict) -> tuple[dict, dict, str]:
    from app.services import llm

    p, mc = _model(ex, n)
    model = str(mc.get("model") or p.default_model or "")
    temp = V.js_number(mc.get("temperature"))
    max_tokens = V.js_number(mc.get("maxTokens"))
    try:
        out = llm.complete(ex.db, p, model, messages, json_schema=schema,
                           temperature=None if math.isnan(temp) else temp,
                           max_tokens=2048 if math.isnan(max_tokens) else int(max_tokens), timeout=_timeout(settings))
    except Exception as exc:  # noqa: BLE001
        raise _err(exc, "model_failed", "Model call failed") from exc
    u = out.get("usage") or {}
    usage = {"input_tokens": int(u.get("input_tokens") or 0), "output_tokens": int(u.get("output_tokens") or 0)}
    if u.get("cost_usd") is not None:
        usage["cost_usd"] = u["cost_usd"]
    data = out.get("json")
    if not isinstance(data, dict):
        try:
            data = json.loads(out.get("text") or "")
        except ValueError as exc:
            raise StepError(problem(502, "invalid_model_output", "Model returned invalid JSON",
                                    (out.get("text") or "")[:300])) from exc
    return data, usage, f"{p.id} · {model}"


def _doc_text(st: EngineState, limit: int = 60_000) -> str:
    return st.text[:limit] if st.text else "(no text was extracted from the document)"


def _classify(ex: Any, n: dict, c: dict, st: EngineState, settings: dict) -> tuple[dict, None, dict]:
    classes = V.js_list(c.get("classes")) or ["invoice", "receipt"]
    schema = {"type": "object", "required": ["type", "confidence"],
              "properties": {"type": {"type": "string", "enum": [*classes, "other"]},
                             "confidence": {"type": "number", "minimum": 0, "maximum": 1}}}
    msgs = [{"role": "system", "content": "Classify the document into exactly one of: " + ", ".join(classes)
             + ". Anything else is \"other\". Return JSON {type, confidence}."},
            {"role": "user", "content": _doc_text(st, 20_000)}]
    data, usage, model = _complete(ex, n, msgs, schema, settings)
    kind = str(data.get("type") or "other")
    if kind not in classes:
        kind = "other"
    st.classification = kind
    conf = data.get("confidence")
    return {"type": kind, "confidence": conf if isinstance(conf, (int, float)) else None,
            "model": model.split(" · ", 1)[-1]}, None, usage


def _skill_text(ex: Any, skill_id: Any) -> str:
    if not skill_id or skill_id == "none":
        return ""
    try:
        from app.db.models import Skill

        s = ex.db.scalar(select(Skill).where(Skill.workspace_id == ex.ws, Skill.id == skill_id))
        return (getattr(s, "instructions", None) or "") if s else ""
    except Exception:  # noqa: BLE001
        return ""


_JSON_TYPES = {"string": "string", "number": "number", "date": "string", "boolean": "boolean", "array": "array"}


def _extract(ex: Any, n: dict, c: dict, st: EngineState, settings: dict) -> tuple[dict, None, dict]:
    fields = schema_for(ex.graph, st, ex.doc)
    props = {f["name"]: {"type": "object", "properties": {
        "value": {"type": [_JSON_TYPES.get(f.get("type"), "string"), "null"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1}}} for f in fields}
    schema = {"type": "object", "properties": props}
    system = "\n\n".join(x for x in [
        _skill_text(ex, c.get("skill")), str(c.get("instructions") or ""),
        "Return a JSON object with one key per field: {\"<field>\": {\"value\": …, \"confidence\": 0..1}}. "
        "Use null when a field is not in the document. Dates as YYYY-MM-DD."] if x)
    kvs = "\n".join(f"{kv.get('key')}: {kv.get('value')}" for kv in st.key_values)
    user = _doc_text(st) + (f"\n\nKey-value pairs:\n{kvs}" if kvs else "") + \
        "\n\nFields: " + ", ".join(f"{f['name']} ({f.get('type')})" for f in fields)
    data, usage, model = _complete(ex, n, [{"role": "system", "content": system}, {"role": "user", "content": user}],
                                   schema, settings)
    vals: dict[str, list] = {}
    for f in fields:
        v = data.get(f["name"])
        if isinstance(v, dict):
            value, conf = v.get("value"), v.get("confidence")
        else:
            value, conf = v, None
        if value is None or value == "":
            continue
        vals[f["name"]] = [value, float(conf) if isinstance(conf, (int, float)) else 0.5]
    st.values = vals
    return {"model": model, "fields": len(fields),
            "tools_available": [x.get("name") for x in V.subs_of(ex.graph, n, "tools")],
            "knowledge": [(k.get("config") or {}).get("collection") for k in V.subs_of(ex.graph, n, "knowledge")],
            "tokens": {"input": usage["input_tokens"], "output": usage["output_tokens"]}}, None, usage


# ── validate / deliver ──────────────────────────────────────────────────────────────────────────

def _num(v: Any) -> float | None:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _rules(ex: Any, n: dict, c: dict, st: EngineState) -> dict:
    vals = {k: v[0] for k, v in _values(st, ex.doc).items()}
    checks = []
    for r in (c.get("rules") or []):
        passed, detail = True, None
        if r == "Total = subtotal + VAT":
            t, s, v = _num(vals.get("total_amount")), _num(vals.get("subtotal")), _num(vals.get("vat_amount"))
            if None not in (t, s, v):
                passed = abs(t - (s + v)) < 0.01
                if not passed:
                    detail = f"{s:,.2f} + {v:,.2f} ≠ {t:,.2f}"
        elif r == "Due date after invoice date":
            a, b = _date(vals.get("invoice_date")), _date(vals.get("due_date"))
            if a and b:
                passed = b >= a
                detail = None if passed else f"{b} is before {a}"
        elif r == "TRN has 15 digits":
            trn = re.sub(r"\D", "", str(vals.get("vendor_trn") or ""))
            passed = len(trn) == 15 if trn else True
            detail = None if passed else f"TRN has {len(trn)} digits"
        elif r == "No duplicate invoice number":
            passed = not _duplicate_invoice(ex, vals.get("invoice_number"))
            detail = None if passed else "Another run already extracted this invoice number"
        ch = {"name": r, "passed": passed, "node_id": n["id"]}
        if detail:
            ch["detail"] = detail
        checks.append(ch)
    st.checks = (st.checks or []) + checks
    failed = [x for x in checks if not x["passed"]]
    if failed and c.get("onFail") == "Stop the run":
        raise StepError(problem(422, "rule_failed", "Business rule failed", failed[0]["name"]), retryable=False)
    return {"passed": len(checks) - len(failed), "failed": len(failed)}


def _duplicate_invoice(ex: Any, number: Any) -> bool:
    if not number:
        return False
    from app.db.models import Run, RunResult

    rows = ex.db.scalars(select(RunResult).join(Run, Run.id == RunResult.run_id).where(
        RunResult.workspace_id == ex.ws, Run.pipeline_id == ex.run.pipeline_id, Run.test.is_(False),
        RunResult.run_id != ex.run_id).limit(500)).all()
    for rr in rows:  # rule 2: filter JSON in Python
        for f in rr.fields or []:
            if f.get("name") == "invoice_number" and f.get("value") == number:
                return True
    return False


def _deliver(ex: Any, n: dict, c: dict, st: EngineState) -> dict:
    if not ex.run.deliver:
        return dict(DELIVER_SIMULATED)
    if n.get("type") == "out.export":
        from app.services.run_engine import build_result

        res = build_result(ex.run_id, ex.graph, st, ex.doc)
        fmt = str(c.get("format") or "JSON").upper()
        path = str(c.get("path") or "exports/{{run.id}}.json").replace("{{run.id}}", ex.run_id)
        if fmt == "CSV":
            rows = ["name,value,confidence"] + [f"{f['name']},{json.dumps(f.get('value'))},{f.get('confidence')}"
                                                for f in res.get("fields") or []]
            body = ("\n".join(rows) + "\n").encode()
        else:
            body = json.dumps(res, ensure_ascii=False, indent=2).encode()
        key = f"{ex.ws}/exports/{re.sub(r'[^A-Za-z0-9._/-]+', '_', path).lstrip('/')}"
        storage.get_store().put(key, body, "text/csv" if fmt == "CSV" else "application/json")
        return {"status": "ok", "object_key": key, "bytes": len(body)}
    raise StepError(problem(501, "delivery_not_supported", "Delivery not supported",
                            f"No delivery adapter for {n.get('type')} yet; run with deliver=false"), retryable=False)


__all__ = ["execute", "UNDEF"]
