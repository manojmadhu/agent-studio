"""Run engine (owned by BE2): queued → running → (review) → completed | failed | cancelled.

Executor: in-process ``ThreadPoolExecutor`` behind the ``RunQueue`` interface (swap for Celery/Arq + Redis later).
Every event is persisted to ``run_events`` with a per-run monotonically increasing ``seq`` (the SSE ``id:``) and
committed before the in-process ``event_bus`` is notified; ``run_steps`` and ``run_results`` are persisted too.

``settings.run_mode == "simulate"`` mirrors ``portal/src/api/mock/runEngine.ts`` + ``backend.ts#startRun`` exactly:
same walk (BFS from the intake, ports decide the branch, ``seen`` set), same event sequence and payload fields
(``run.started`` → ``step.started``/``step.finished`` (+ ``run.routed`` after Human review) … → ``run.finished``),
same simulated outputs, durations (``(ai 1100 | read 700 | other 380) + rand·250`` ms), usage, layers, path,
skipped steps and RunResult. Real sleeping is ``duration × run_step_delay_ms / 350`` (350 = the mock's pace, so the
default animates exactly like the mock; tests set 0).

``"live"``: read steps call BE1's ``adapters.invoke`` on the configured worker, AI steps call ``llm.complete`` on the
plugged model's provider; ``settings.retries`` / ``timeout_s`` / ``on_error`` (stop | continue | error_output) are
honoured and failures surface as ``step.failed`` with a problem ``error``. Deliver steps never call external targets
in test runs unless ``deliver: true``; there is no delivery adapter yet except ``out.export`` (writes JSON to the
object store), so a real delivery to ERP/DB/email/WhatsApp fails with ``delivery_not_supported``.

Human review: *test* runs behave like the mock (the step completes, ``run.routed`` is emitted, the walk continues
and the run finishes with status ``review``; no queue item). *Production* runs (ingest/retry of one) pause at the
Human review step: ``step.started`` → ``run.routed`` (status review) and a ``review_items`` row; the SSE stream stays
open. ``POST /review-items/{id}/decision`` resumes: approve → ``step.finished`` for the review step (corrections
applied to the extracted values) and the walk continues; reject → the run finishes ``failed``.
"""
from __future__ import annotations

import copy
import logging
import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any, Callable, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.ids import new_id
from app.core.time import ensure_aware, iso, utcnow
from app.services import catalog_data, storage
from app.services import validator as V
from app.services.event_bus import bus

log = logging.getLogger("docintel.runs")

TERMINAL = {"completed", "failed", "cancelled"}
UNDEF = object()  # JS `undefined` (dropped by JSON.stringify in objects, null in arrays)


# ───────────────────────── queue ─────────────────────────

class RunQueue(Protocol):
    def submit(self, fn: Callable[..., Any], *args: Any) -> None: ...
    def shutdown(self, wait: bool = True) -> None: ...


class ThreadPoolRunQueue:
    def __init__(self, max_workers: int = 8) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="run-engine")
        self._closed = False
        self._inflight = 0
        self._cv = threading.Condition()

    def submit(self, fn: Callable[..., Any], *args: Any) -> None:
        if self._closed:
            raise RuntimeError("run queue is shut down")
        with self._cv:
            self._inflight += 1
        self._pool.submit(self._run, fn, *args)

    def _run(self, fn: Callable[..., Any], *args: Any) -> None:
        try:
            _guard(fn, *args)
        finally:
            with self._cv:
                self._inflight -= 1
                self._cv.notify_all()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until no task is executing (tests; graceful shutdown)."""
        with self._cv:
            return self._cv.wait_for(lambda: self._inflight == 0, timeout)

    def shutdown(self, wait: bool = True) -> None:
        self._closed = True
        self._pool.shutdown(wait=wait, cancel_futures=not wait)


def _guard(fn: Callable[..., Any], *args: Any) -> None:
    try:
        fn(*args)
    except Exception:  # noqa: BLE001
        log.exception("run engine task failed")


_queue: RunQueue | None = None
_qlock = threading.Lock()


def get_queue() -> RunQueue:
    global _queue
    with _qlock:
        if _queue is None:
            _queue = ThreadPoolRunQueue()
        return _queue


def set_queue(q: RunQueue | None) -> None:
    global _queue
    with _qlock:
        _queue = q


def enqueue(run_id: str) -> None:
    """Call after the run row is committed."""
    get_queue().submit(execute_run, run_id)


def enqueue_resume(run_id: str, review_item_id: str) -> None:
    get_queue().submit(resume_run, run_id, review_item_id)


# ───────────────────────── sample data (sample_documents.json) ─────────────────────────

def _sim() -> dict:
    return catalog_data.sample_documents() or {}


def _engine_const() -> dict:
    return _sim().get("engine") or {}


def sample_values(kind: str) -> dict[str, list]:
    return copy.deepcopy((_sim().get("values") or {}).get(kind) or {})


def default_schema(kind: str) -> list[dict]:
    return copy.deepcopy((_sim().get("schemas") or {}).get("rcp" if kind == "rcp" else "inv") or [])


@dataclass
class EngineDoc:
    kind: str  # inv | rcp
    doc_kind: str  # invoice | receipt
    filename: str


def engine_doc_for(doc: Any) -> EngineDoc:
    """Sample docs carry their kind; uploaded docs are receipts when the name says so, invoices otherwise (the mock
    treats every unknown document as the invoice sample)."""
    if storage.is_sample(doc):
        return EngineDoc(doc.kind, doc.doc_kind, doc.filename)
    sample = storage.sample_document(doc.id)
    if sample:  # seeded row for a sample id
        return EngineDoc(sample.get("kind") or "inv", sample.get("doc_kind") or "invoice", doc.filename)
    name = (doc.filename or "").lower()
    if "receipt" in name or "rcp" in name:
        return EngineDoc("rcp", "receipt", doc.filename)
    return EngineDoc("inv", "invoice", doc.filename)


# ───────────────────────── JS-compatible object helpers ─────────────────────────

def _js(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not UNDEF}


def _jsarr(xs: list) -> list:
    return [None if x is UNDEF else x for x in xs]


def _g(c: dict | None, key: str) -> Any:
    if not isinstance(c, dict) or key not in c:
        return UNDEF
    return c[key]


def _nullish(v: Any) -> bool:
    return v is None or v is UNDEF


def _length(v: Any) -> int:
    if isinstance(v, (list, str)):
        return len(v)
    return 0


# ───────────────────────── engine state ─────────────────────────

@dataclass
class EngineState:
    layers: list[str] = field(default_factory=list)
    classification: str | None = None
    extract_node: str | None = None
    path: dict[str, str] = field(default_factory=dict)
    review: bool = False
    steps: list[dict] = field(default_factory=list)
    queue: list[str | None] = field(default_factory=list)
    seen: list[str] = field(default_factory=list)
    values: dict[str, list] | None = None  # live extraction {name: [value, confidence]}; None = sample values
    overlays: dict[str, list] = field(default_factory=lambda: {"blocks": [], "tables": [], "redactions": []})
    checks: list[dict] | None = None  # live rule results
    text: str = ""
    key_values: list[dict] = field(default_factory=list)
    tables: int | None = None
    paused_at: str | None = None
    cancelled: bool = False
    failed: dict | None = None  # Problem when a step stopped the run

    def add_layer(self, layer: str) -> None:
        if layer not in self.layers:
            self.layers.append(layer)

    def to_json(self) -> dict:
        d = asdict(self)
        d["text"] = d["text"][:100_000]
        return d

    @classmethod
    def from_json(cls, d: dict | None) -> "EngineState":
        st = cls()
        for k, v in (d or {}).items():
            if hasattr(st, k):
                setattr(st, k, v)
        return st


class StepError(Exception):
    def __init__(self, problem: dict, retryable: bool = True) -> None:
        super().__init__(problem.get("detail") or problem.get("title"))
        self.problem = problem
        self.retryable = retryable


def problem(status: int, code: str, title: str, detail: str | None = None) -> dict:
    p = {"type": "https://docs.docintel.example.com/errors/" + code.replace("_", "-"), "title": title,
         "status": status, "code": code}
    if detail:
        p["detail"] = detail
    return p


# ───────────────────────── simulated outputs (runEngine.ts) ─────────────────────────

def step_output(g: dict, n: dict, doc: EngineDoc, cat: dict | None = None) -> dict:
    """Prototype `mock(n)` / runEngine.ts `stepOutput`."""
    c = n.get("config") or {}
    t = n.get("type")
    inv = doc.kind == "inv"
    if t == "read.ocr":
        return _js({"pages": 1, "characters": 2184 if inv else 612, "mean_confidence": 0.97, "worker": _g(c, "worker")})
    if t == "read.layout":
        return {"tables": 1, "key_value_pairs": 11 if inv else 5}
    if t == "read.sheet":
        return _js({"sheets": 2, "characters": 9412, "worker": _g(c, "worker")})
    if t == "ai.classify":
        m = V.model_of(g, n)
        return _js({"type": doc.doc_kind, "confidence": 0.96, "model": _g(m.get("config"), "model") if m else UNDEF})
    if t in ("ai.extract", "ai.agent"):
        m = V.model_of(g, n)
        mc = (m or {}).get("config") or {}
        prov = "" if _nullish(_g(mc, "provider")) else V.js_string(mc["provider"])
        model = "" if _nullish(_g(mc, "model")) else V.js_string(mc["model"])
        parsers = V.subs_of(g, n, "parser")
        schema = (parsers[0].get("config") or {}).get("schema") if parsers else None
        return {
            "model": prov + " · " + model,
            "fields": _length(schema),
            "tools_available": [x.get("name") for x in V.subs_of(g, n, "tools")],
            "knowledge": _jsarr([_g(k.get("config"), "collection") for k in V.subs_of(g, n, "knowledge")]),
            "tokens": {"input": 2310, "output": 268},
        }
    if t == "val.rules":
        return {"passed": _length(c.get("rules") or []), "failed": 0}
    if t == "val.pii":
        return _js({"masked": 3 if inv else 1, "method": _g(c, "mode")})
    if t == "val.lookup":
        return {"matched": True, "record": "V-00418"}
    if t == "val.injection":
        return {"verdict": "clean"}
    if t == "review.human":
        return _js({"queue": _g(c, "queue"), "ticket": _engine_const().get("review_ticket", "REV-1182"),
                    "decision": "approved"})
    return {"status": "ok"}


def schema_for(g: dict, st: EngineState, doc: EngineDoc) -> list[dict]:
    ex = V.node_by_id(g, st.extract_node) if st.extract_node else None
    pr = V.subs_of(g, ex, "parser") if ex else []
    s = (pr[0].get("config") or {}).get("schema") if pr else None
    return s if isinstance(s, list) and s else default_schema(doc.kind)


def _values(st: EngineState, doc: EngineDoc) -> dict[str, list]:
    return st.values if st.values is not None else sample_values(doc.kind)


def _duration_ms(d: dict, rand: Callable[[], float]) -> int:
    k = _engine_const().get("step_duration_ms") or {}
    base = k.get("ai", 1100) if d.get("ai") else (k.get("read", 700) if d.get("stage") == "read" else k.get("other", 380))
    return int(math.floor(base + rand() * k.get("random_extra_max", 250) + 0.5))


def _pace() -> float:
    return max(0.0, float(get_settings().run_step_delay_ms)) / 350.0


def build_result(run_id: str, g: dict, st: EngineState, doc: EngineDoc) -> dict:
    """runEngine.ts `buildResult` (+ live checks/overlays when present)."""
    vals = _values(st, doc)
    masked = "redactions" in st.layers
    res: dict[str, Any] = {"run_id": run_id, "path": dict(st.path), "checks": [],
                           "overlays": copy.deepcopy(st.overlays) or {"blocks": [], "tables": [], "redactions": []}}
    if st.classification:
        res["classification"] = {"type": st.classification,
                                 "confidence": _engine_const().get("classification_confidence", 0.96)}
    if "fields" in st.layers:
        fields = []
        for f in schema_for(g, st, doc):
            v = vals.get(f.get("name"))
            pii = bool(f.get("pii"))
            item = {"name": f.get("name"), "type": f.get("type"),
                    "value": (None if (pii and masked) else v[0]) if v else None,
                    "confidence": v[1] if v else 0, "required": bool(f.get("req")), "pii": pii,
                    "masked": bool(pii and masked), "page": 1}
            if v and len(v) > 2 and isinstance(v[2], list) and len(v[2]) == 4:
                item["bbox"] = v[2]
            fields.append(item)
        res["fields"] = fields
    done = {s["node_id"] for s in st.steps if s.get("status") == "completed"}
    rules = next((n for n in g.get("nodes", []) if n.get("type") == "val.rules" and n.get("id") in done), None)
    if st.checks is not None:
        res["checks"] = st.checks
    elif rules:
        res["checks"] = [{"name": r, "passed": True, "node_id": rules["id"]}
                         for r in ((rules.get("config") or {}).get("rules") or [])]
    return res


# ───────────────────────── creating runs ─────────────────────────

def create_run(db: Session, *, workspace_id: str, pipeline_id: str, graph: dict, version: int | None, doc: Any,
               test: bool, deliver: bool, trigger: str, input: dict | None = None, created_by: str | None = None,
               retry_of: str | None = None) -> Any:
    """Insert a queued run (flush only). Call `enqueue(run.id)` after committing."""
    from app.db.models import Run

    inp = dict(input or {})
    if storage.is_sample(doc):
        inp["sample_document_id"] = doc.id
    now = utcnow()
    run = Run(id=new_id("run"), workspace_id=workspace_id, pipeline_id=pipeline_id, pipeline_version=version,
              document_id=None if storage.is_sample(doc) else doc.id, status="queued", test=test, deliver=deliver,
              trigger=trigger, graph=copy.deepcopy(graph), input=inp, started_at=now, finished_at=None,
              duration_ms=None, field_count=0, retry_of=retry_of, created_by=created_by)
    db.add(run)
    db.flush()
    return run


def run_document(db: Session, run: Any) -> Any:
    sid = (run.input or {}).get("sample_document_id")
    if sid:
        return storage.get_document(db, run.workspace_id, sid)
    if run.document_id:
        from app.db.models import Document

        d = db.get(Document, run.document_id)
        if d is not None:
            return d
    return storage.get_document(db, run.workspace_id, "doc_inv_sample")


def run_out(db: Session, run: Any, doc: Any = None) -> dict:
    """Run schema (field order as the mock)."""
    doc = doc if doc is not None else _safe_doc(db, run)
    out: dict[str, Any] = {
        "id": run.id, "pipeline_id": run.pipeline_id,
        "pipeline_version": run.pipeline_version if run.pipeline_version is not None else "draft",
    }
    if doc is not None:
        d = storage.document_out(doc)
        if storage.is_sample(doc) or storage.sample_document(doc.id):
            d = {k: d[k] for k in ("id", "filename", "mime_type", "pages") if k in d}
        out["document"] = d
    out.update({"status": run.status, "test": bool(run.test), "started_at": iso(run.started_at or run.created_at),
                "finished_at": iso(run.finished_at) if run.finished_at else None, "duration_ms": run.duration_ms,
                "field_count": run.field_count or 0})
    if run.error:
        out["error"] = run.error
    return out


def _safe_doc(db: Session, run: Any) -> Any:
    try:
        return run_document(db, run)
    except Exception:  # noqa: BLE001 - deleted documents: render what the run remembers
        snap = (run.input or {}).get("document")
        if snap:
            return storage.SampleDocument(**{k: snap.get(k) for k in ("id", "filename", "mime_type")},
                                          pages=snap.get("pages") or 1, is_sample=False)
        return None


def step_out(s: Any) -> dict:
    if s.status == "skipped":
        return {"node_id": s.node_id, "status": "skipped"}
    d: dict[str, Any] = {"node_id": s.node_id, "status": s.status}
    if s.started_at:
        d["started_at"] = iso(s.started_at)
    if s.duration_ms is not None:
        d["duration_ms"] = s.duration_ms
    d["attempt"] = s.attempt
    if s.output is not None:
        d["output"] = s.output
    if s.output_port:
        d["output_port"] = s.output_port
    if s.usage:
        d["usage"] = s.usage
    if s.error:
        d["error"] = s.error
    return d


def run_steps_out(db: Session, run: Any) -> list[dict]:
    """Latest attempt per node, in execution order (completed/failed first, then skipped — like the mock)."""
    from app.db.models import RunStep

    rows = db.scalars(select(RunStep).where(RunStep.workspace_id == run.workspace_id, RunStep.run_id == run.id)
                      .order_by(RunStep.seq, RunStep.attempt)).all()
    latest: dict[str, Any] = {}
    order: list[str] = []
    for r in rows:
        if r.node_id not in latest:
            order.append(r.node_id)
        latest[r.node_id] = r
    return [step_out(latest[nid]) for nid in order]


def result_out(rr: Any) -> dict:
    out: dict[str, Any] = {"run_id": rr.run_id, "path": rr.path or {}, "checks": rr.checks or [],
                           "overlays": rr.overlays or {"blocks": [], "tables": [], "redactions": []}}
    if rr.classification:
        out["classification"] = rr.classification
    if rr.fields is not None and (rr.fields or (rr.overlays or {}).get("_has_fields")):
        out["fields"] = rr.fields
    if rr.corrections:
        out["corrections"] = rr.corrections
    out["overlays"] = {k: v for k, v in out["overlays"].items() if not k.startswith("_")}
    return out


# ───────────────────────── execution context ─────────────────────────

class _Exec:
    """One execution (or resumption) of a run in a worker thread, with its own session."""

    def __init__(self, db: Session, run: Any) -> None:
        self.db = db
        self.run = run
        self.run_id = run.id
        self.ws = run.workspace_id
        self.settings = get_settings()
        self.graph: dict = run.graph or {"nodes": [], "edges": []}
        self.cat = V.catalog()
        self.doc_row = run_document(db, run)
        self.doc = engine_doc_for(self.doc_row)
        from app.db.models import RunEvent

        self.seq = int(db.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run.id)) or 0)
        self.step_seq = 0
        self.rand: Callable[[], float] = random.random
        self.live = self.settings.run_mode == "live"

    # events -----------------------------------------------------------------------------------------
    def emit(self, payload: dict, *, commit: bool = True) -> None:
        from app.db.models import RunEvent

        now = utcnow()
        self.seq += 1
        body = {**payload, "run_id": self.run_id, "at": iso(now)}
        self.db.add(RunEvent(id=new_id("evt"), workspace_id=self.ws, run_id=self.run_id, seq=self.seq,
                             event=payload["event"], node_id=payload.get("node_id"), payload=body, created_at=now))
        if commit:
            self.db.commit()
            bus.publish(self.run_id, self.seq)

    def commit(self) -> None:
        self.db.commit()
        bus.publish(self.run_id, self.seq)

    def wait(self, ms: float) -> None:
        secs = ms * _pace() / 1000.0
        if secs > 0:
            time.sleep(secs)

    def cancelled(self) -> bool:
        from app.db.models import Run

        self.db.expire_all()
        return bool(self.db.scalar(select(Run.cancel_requested).where(Run.id == self.run_id)))

    # steps ------------------------------------------------------------------------------------------
    def save_step(self, step: dict, *, error: dict | None = None) -> None:
        from app.db.models import RunStep

        attempt = int(step.get("attempt") or 1)
        row = self.db.scalar(select(RunStep).where(RunStep.run_id == self.run_id, RunStep.node_id == step["node_id"],
                                                   RunStep.attempt == attempt))
        if row is None:
            self.step_seq = max(self.step_seq, self._max_step_seq()) + 1
            row = RunStep(id=new_id("rst"), workspace_id=self.ws, run_id=self.run_id, node_id=step["node_id"],
                          attempt=attempt, seq=self.step_seq)
            self.db.add(row)
        row.status = step["status"]
        if step.get("started_at"):
            from datetime import datetime

            row.started_at = ensure_aware(datetime.fromisoformat(step["started_at"].replace("Z", "+00:00")))
        row.duration_ms = step.get("duration_ms")
        row.output = step.get("output")
        row.output_port = step.get("output_port")
        row.usage = step.get("usage")
        row.error = error or step.get("error")
        if step["status"] in ("completed", "failed", "skipped"):
            row.finished_at = utcnow()

    def _max_step_seq(self) -> int:
        from app.db.models import RunStep

        return int(self.db.scalar(select(func.max(RunStep.seq)).where(RunStep.run_id == self.run_id)) or 0)

    def save_state(self, st: EngineState) -> None:
        inp = dict(self.run.input or {})
        inp["_engine"] = st.to_json()
        self.run.input = inp

    # walk -------------------------------------------------------------------------------------------
    def walk(self, st: EngineState) -> str:
        """BFS walk (runEngine.ts `execute` loop). Returns 'done' | 'paused' | 'failed'."""
        g, cat = self.graph, self.cat
        while st.queue:
            if self.cancelled():
                st.cancelled = True
                break
            nid = st.queue.pop(0)
            n = V.node_by_id(g, nid) if nid else None
            if not n or n["id"] in st.seen:
                continue
            st.seen.append(n["id"])
            d = cat.get(n.get("type"))
            if not d:
                continue
            outcome = self.run_step(n, d, st)
            if outcome == "paused":
                self.save_state(st)
                return "paused"
            if outcome == "failed":
                return "failed"
        return "done"

    def successors(self, n: dict, port: str | None) -> list[str]:
        return [e["to"] for e in self.graph.get("edges", [])
                if not e.get("slot") and e.get("from") == n["id"] and (not port or e.get("from_port") == port)]

    def run_step(self, n: dict, d: dict, st: EngineState) -> str:
        started = iso(utcnow())
        settings = {**V.SETTINGS_DEFAULT, **(n.get("settings") or {})}
        max_attempts = 1 + max(0, min(5, int(V.js_number(settings.get("retries")) or 0))) if self.live else 1
        attempt = 1
        self.emit({"event": "step.started", "node_id": n["id"],
                   "step": {"node_id": n["id"], "status": "running", "started_at": started, "attempt": attempt}},
                  commit=False)
        self.save_step({"node_id": n["id"], "status": "running", "started_at": started, "attempt": attempt})
        self.commit()

        # production runs pause at Human review
        if n.get("type") == "review.human" and not self.run.test:
            return self.pause_for_review(n, st, started)

        while True:
            t0 = time.monotonic()
            ms = _duration_ms(d, self.rand)
            try:
                if self.live:
                    output, port, usage = self.live_step(n, d, st, settings)
                    ms = max(1, int((time.monotonic() - t0) * 1000))
                else:
                    self.wait(ms)
                    output, port, usage = self.sim_step(n, d, st)
                break
            except StepError as exc:
                dur = max(1, int((time.monotonic() - t0) * 1000))
                if exc.retryable and attempt < max_attempts:
                    self.save_step({"node_id": n["id"], "status": "failed", "started_at": started, "attempt": attempt,
                                    "duration_ms": dur}, error=exc.problem)
                    attempt += 1
                    started = iso(utcnow())
                    self.emit({"event": "step.started", "node_id": n["id"],
                               "step": {"node_id": n["id"], "status": "running", "started_at": started,
                                        "attempt": attempt}}, commit=False)
                    self.save_step({"node_id": n["id"], "status": "running", "started_at": started, "attempt": attempt})
                    self.commit()
                    time.sleep(min(2.0, 0.2 * attempt) * _pace())
                    continue
                return self.step_failed(n, st, started, attempt, dur, exc.problem, settings.get("on_error"))

        if port:
            st.path[n["id"]] = port
        step = {"node_id": n["id"], "status": "completed", "started_at": started, "duration_ms": ms,
                "attempt": attempt, "output": output}
        if port:
            step["output_port"] = port
        if usage:
            step["usage"] = usage
        st.steps.append(step)
        self.save_step(step)
        self.emit({"event": "step.finished", "node_id": n["id"], "step": step, "layers": list(st.layers)}, commit=False)
        if n.get("type") == "review.human":
            self.emit({"event": "run.routed", "node_id": n["id"], "status": "review"}, commit=False)
        self.commit()
        st.queue.extend(self.successors(n, port))
        return "ok"

    def step_failed(self, n: dict, st: EngineState, started: str, attempt: int, dur: int, prob: dict,
                    on_error: str | None) -> str:
        step = {"node_id": n["id"], "status": "failed", "started_at": started, "duration_ms": dur, "attempt": attempt,
                "error": prob}
        st.steps.append(step)
        self.save_step(step, error=prob)
        self.emit({"event": "step.failed", "node_id": n["id"], "step": step, "layers": list(st.layers)}, commit=False)
        self.commit()
        if on_error == "continue":
            st.queue.extend(self.successors(n, "out" if "out" in V.outs_of(n, self.cat) else None))
            return "ok"
        if on_error == "error_output":
            st.queue.extend(self.successors(n, "error"))
            return "ok"
        st.failed = {**prob, "detail": f"{n.get('name')}: {prob.get('detail') or prob.get('title')}"}
        return "failed"

    # simulated step (runEngine.ts) ------------------------------------------------------------------
    def sim_step(self, n: dict, d: dict, st: EngineState) -> tuple[dict, str | None, dict | None]:
        g, doc, t = self.graph, self.doc, n.get("type")
        self._apply_layers(n, st)
        port: str | None = None
        c = n.get("config") or {}
        if t == "route.type":
            port = doc.doc_kind if doc.doc_kind in V.js_list(c.get("paths")) else "other"
        if t == "route.confidence":
            output, port = self._confidence_gate(n, st)
        else:
            output = {**self._sim_or_deliver_output(n, d), **({"output": port} if port else {})}
        usage = dict(_engine_const().get("ai_usage") or {"input_tokens": 2310, "output_tokens": 268, "cost_usd": 0.011}) \
            if d.get("ai") else None
        return output, port, usage

    def _sim_or_deliver_output(self, n: dict, d: dict) -> dict:
        return step_output(self.graph, n, self.doc, self.cat)

    def _apply_layers(self, n: dict, st: EngineState) -> None:
        t = n.get("type")
        if t == "read.ocr":
            st.add_layer("blocks")
        if t == "read.layout":
            st.add_layer("tables")
        if t == "ai.classify" and not self.live:
            st.classification = self.doc.doc_kind
        if t in ("ai.extract", "ai.agent"):
            st.add_layer("fields")
            st.extract_node = n["id"]
        if t == "val.pii":
            st.add_layer("redactions")
        if t == "review.human":
            st.review = True

    def _confidence_gate(self, n: dict, st: EngineState) -> tuple[dict, str]:
        vals = _values(st, self.doc)
        sch = schema_for(self.graph, st, self.doc)
        confs = [vals[f["name"]][1] for f in sch if f.get("req") and vals.get(f.get("name"))]
        m: Any = min(confs) if confs else 1
        c = n.get("config") or {}
        thr = V.js_number(c.get("threshold")) if "threshold" in c else math.nan
        port = "pass" if m >= thr else "review"
        return _js({"lowest_required_confidence": m, "threshold": _g(c, "threshold"), "output": port}), port

    # live step ---------------------------------------------------------------------------------------
    def live_step(self, n: dict, d: dict, st: EngineState, settings: dict) -> tuple[dict, str | None, dict | None]:
        from app.services import run_engine_live  # noqa: PLC0415

        return run_engine_live.execute(self, n, d, st, settings)

    # review -----------------------------------------------------------------------------------------
    def pause_for_review(self, n: dict, st: EngineState, started: str) -> str:
        from app.db.models import ReviewItem

        self._apply_layers(n, st)
        c = n.get("config") or {}
        reason, code = self.review_reason(st)
        sla_h = V.js_number(c.get("sla", 4))
        sla_h = 4 if math.isnan(sla_h) else sla_h
        doc_id = None if storage.is_sample(self.doc_row) else self.doc_row.id
        rv = ReviewItem(id=new_id("review_item"), workspace_id=self.ws, run_id=self.run_id,
                        pipeline_id=self.run.pipeline_id, document_id=doc_id, node_id=n["id"], reason=reason,
                        reason_code=code, queue=str(c.get("queue") or "Accounts payable"), assignee=None,
                        sla_due_at=utcnow() + timedelta(hours=sla_h), status="open")
        self.db.add(rv)
        st.paused_at = n["id"]
        st.review = True
        self.run.status = "review"
        self.save_state(st)
        self.save_result(st, partial=True)
        self.emit({"event": "run.routed", "node_id": n["id"], "status": "review"}, commit=False)
        _webhook(self.db, self.ws, "review.created", {"review_item_id": rv.id, "run_id": self.run_id,
                                                      "pipeline_id": self.run.pipeline_id, "queue": rv.queue,
                                                      "reason": reason})
        self.commit()
        return "paused"

    def review_reason(self, st: EngineState) -> tuple[str, str]:
        gate = next((nid for nid, p in st.path.items()
                     if p == "review" and (V.node_by_id(self.graph, nid) or {}).get("type") == "route.confidence"), None)
        if gate:
            vals = _values(st, self.doc)
            sch = schema_for(self.graph, st, self.doc)
            req = [(f["name"], vals[f["name"]][1]) for f in sch if f.get("req") and vals.get(f.get("name"))]
            if req:
                name, conf = min(req, key=lambda x: x[1])
                return f"Low confidence · {name} {conf:g}", "low_confidence"
        if st.checks and any(not c.get("passed") for c in st.checks):
            bad = next(c for c in st.checks if not c.get("passed"))
            return f"Rule failed · {bad.get('name')}", "rule_failed"
        return "Routed to human review", "manual"

    # finishing --------------------------------------------------------------------------------------
    def save_result(self, st: EngineState, partial: bool = False) -> dict:
        from app.db.models import RunResult

        res = build_result(self.run_id, self.graph, st, self.doc)
        rr = self.db.get(RunResult, self.run_id)
        if rr is None:
            rr = RunResult(run_id=self.run_id, workspace_id=self.ws)
            self.db.add(rr)
        rr.classification = res.get("classification")
        rr.fields = res.get("fields") or []
        rr.checks = res.get("checks") or []
        rr.path = res.get("path") or {}
        ov = dict(res.get("overlays") or {})
        ov["_has_fields"] = "fields" in res
        rr.overlays = ov
        return res

    def finish(self, st: EngineState) -> None:
        # skipped steps (every unexecuted, non-component node)
        executed = {s["node_id"] for s in st.steps}
        for n in self.graph.get("nodes", []):
            if n.get("id") not in executed and not V.is_sub(n, self.cat):
                st.steps.append({"node_id": n["id"], "status": "skipped"})
                self.save_step({"node_id": n["id"], "status": "skipped", "attempt": 1})
        res = self.save_result(st)
        run = self.run
        if st.cancelled:
            run.status = "cancelled"
        elif st.failed:
            run.status = "failed"
            run.error = st.failed
        else:
            run.status = "review" if st.review and run.test else "completed"
        now = utcnow()
        run.finished_at = now
        t0 = ensure_aware(run.started_at or run.created_at) or now
        run.duration_ms = max(1, int((now - t0).total_seconds() * 1000))
        run.field_count = len(res.get("fields") or [])
        inp = dict(run.input or {})
        inp.pop("_engine", None)
        run.input = inp
        self.emit({"event": "run.finished", "status": run.status}, commit=False)
        if not run.test and run.status in ("completed", "failed"):
            _webhook(self.db, self.ws, f"run.{run.status}", {"run_id": run.id, "pipeline_id": run.pipeline_id,
                                                             "status": run.status, "field_count": run.field_count})
        self.commit()


def _webhook(db: Session, workspace_id: str, event_type: str, payload: dict) -> None:
    try:
        from app.services import webhooks  # BE1
    except ImportError:
        return
    try:
        webhooks.emit(db, workspace_id, event_type, payload)
    except Exception:  # noqa: BLE001 - delivery problems never break a run
        log.exception("webhook emit failed for %s", event_type)


# ───────────────────────── entry points (worker threads) ─────────────────────────

def _session() -> Session:
    from app.db.session import session_factory

    return session_factory()()


def execute_run(run_id: str) -> None:
    from app.db.models import Run

    with _session() as db:
        run = db.get(Run, run_id)
        if run is None or run.status != "queued":
            return
        ex = _Exec(db, run)
        try:
            ex.wait(float(_engine_const().get("queued_delay_ms", 30)))
            run.status = "running"
            ex.emit({"event": "run.started", "status": "running"})
            st = EngineState()
            start = next((n for n in V.main_nodes(ex.graph, ex.cat) if not V.ins_of(n, ex.cat)), None)
            st.queue = [start["id"] if start else None]
            outcome = ex.walk(st)
            if outcome == "paused":
                return
            ex.finish(st)
        except Exception as exc:  # noqa: BLE001
            log.exception("run %s crashed", run_id)
            _crash(db, run_id, exc)


def resume_run(run_id: str, review_item_id: str) -> None:
    """Continue a production run after a review decision (approve → walk on; reject → failed)."""
    from app.db.models import ReviewItem, Run

    with _session() as db:
        run = db.get(Run, run_id)
        rv = db.get(ReviewItem, review_item_id)
        if run is None or rv is None or run.status != "review":
            return
        ex = _Exec(db, run)
        try:
            st = EngineState.from_json((run.input or {}).get("_engine"))
            node = V.node_by_id(ex.graph, st.paused_at or rv.node_id)
            if node is None:
                raise RuntimeError("paused node is missing from the run graph")
            step_row = _latest_step(db, run_id, node["id"])
            started = iso(step_row.started_at) if step_row is not None and step_row.started_at else iso(utcnow())
            approved = rv.decision == "approve"
            if approved and rv.corrections:
                vals = _values(st, ex.doc)
                for k, v in rv.corrections.items():
                    vals[k] = [v, 1.0]
                st.values = vals
            output = _js({"queue": _g(node.get("config"), "queue"), "ticket": rv.id,
                          "decision": "approved" if approved else "rejected",
                          "corrections": rv.corrections if rv.corrections else UNDEF,
                          "reviewer": rv.decided_by or UNDEF})
            dur = max(1, int(((rv.decided_at or utcnow()) - (ensure_aware(step_row.started_at) if step_row is not None
                                                                 and step_row.started_at else utcnow()))
                              .total_seconds() * 1000))
            step = {"node_id": node["id"], "status": "completed", "started_at": started, "duration_ms": dur,
                    "attempt": 1, "output": output}
            st.steps.append(step)
            st.paused_at = None
            run.status = "running"
            ex.save_step(step)
            ex.emit({"event": "step.finished", "node_id": node["id"], "step": step, "layers": list(st.layers)},
                    commit=False)
            ex.commit()
            if not approved:
                st.failed = problem(422, "review_rejected", "Rejected in review",
                                    rv.comment or f"{rv.decided_by or 'A reviewer'} rejected the document")
                ex.finish(st)
                return
            st.queue.extend(ex.successors(node, None))
            outcome = ex.walk(st)
            if outcome == "paused":
                return
            ex.finish(st)
        except Exception as exc:  # noqa: BLE001
            log.exception("resume of run %s crashed", run_id)
            _crash(db, run_id, exc)


def _latest_step(db: Session, run_id: str, node_id: str) -> Any:
    from app.db.models import RunStep

    return db.scalars(select(RunStep).where(RunStep.run_id == run_id, RunStep.node_id == node_id)
                      .order_by(RunStep.attempt.desc())).first()


def _crash(db: Session, run_id: str, exc: Exception) -> None:
    """Never leave a run (and its SSE readers) hanging."""
    from app.db.models import Run, RunEvent

    try:
        db.rollback()
        run = db.get(Run, run_id)
        if run is None or run.status in TERMINAL:
            return
        seq = int(db.scalar(select(func.max(RunEvent.seq)).where(RunEvent.run_id == run_id)) or 0) + 1
        now = utcnow()
        run.status = "failed"
        run.error = problem(500, "engine_error", "Run engine error", str(exc)[:500])
        run.finished_at = now
        run.duration_ms = max(1, int((now - (ensure_aware(run.started_at) or now)).total_seconds() * 1000))
        body = {"event": "run.finished", "status": "failed", "run_id": run_id, "at": iso(now)}
        db.add(RunEvent(id=new_id("evt"), workspace_id=run.workspace_id, run_id=run_id, seq=seq, event="run.finished",
                        payload=body, created_at=now))
        db.commit()
        bus.publish(run_id, seq)
    except Exception:  # noqa: BLE001
        log.exception("could not mark run %s failed", run_id)


def cancel_paused(db: Session, run: Any) -> None:
    """Cancel a run that is waiting in review (no worker thread owns it). Caller commits."""
    from app.db.models import ReviewItem

    ex = _Exec(db, run)
    st = EngineState.from_json((run.input or {}).get("_engine"))
    st.cancelled = True
    for rv in db.scalars(select(ReviewItem).where(ReviewItem.run_id == run.id,
                                                  ReviewItem.status.in_(["open", "claimed"]))).all():
        rv.status = "rejected"
        rv.comment = (rv.comment or "") or "Run cancelled"
    ex.finish(st)


def recover_orphans(stale_after_s: int = 600) -> int:
    """Startup: re-enqueue queued runs; fail 'running' runs with no event for `stale_after_s` (server restarted)."""
    from app.db.models import Run, RunEvent

    n = 0
    with _session() as db:
        cutoff = utcnow() - timedelta(seconds=stale_after_s)
        for run in db.scalars(select(Run).where(Run.status.in_(["queued", "running"]))).all():
            last = db.scalar(select(func.max(RunEvent.created_at)).where(RunEvent.run_id == run.id))
            ref = ensure_aware(last or run.updated_at or run.created_at)
            if ref and ref > cutoff:
                continue
            if run.status == "queued":
                db.commit()
                enqueue(run.id)
            else:
                _crash(db, run.id, RuntimeError("The server restarted while this run was in progress"))
            n += 1
    return n
