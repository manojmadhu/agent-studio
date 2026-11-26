"""Outbound webhooks (owned by BE1).

    from app.services import webhooks
    webhooks.emit(db, auth.workspace_id, "run.completed", {"run_id": run.id, "pipeline_id": run.pipeline_id, ...})
    db.commit()     # deliveries start only after the caller's transaction commits

``emit`` selects enabled subscriptions of the workspace whose ``events`` contain ``event_type`` and whose
``pipeline_ids`` is empty or contains ``payload["pipeline_id"]``, inserts one ``webhook_deliveries`` row each
(status ``pending``) and returns the delivery ids. After commit a background thread POSTs::

    {"id": "<event_id>", "type": "run.completed", "created_at": "…", "workspace_id": "…", "data": {payload}}

with headers ``X-DI-Event``, ``X-DI-Delivery``, ``X-DI-Timestamp`` (Unix seconds) and
``X-DI-Signature: sha256=<hex HMAC-SHA256(signing_secret, timestamp + "." + body)>``. Non-2xx / network errors are
retried (``RETRY_DELAYS_S``) and the row ends ``succeeded`` or ``failed``. If the session is rolled back nothing is
sent. Contract event names: run.completed, run.failed, review.created, review.decided, pipeline.published,
connection.failing.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

import httpx
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.ids import new_id
from app.core.time import iso, utcnow
from app.core.security import sign_payload

log = logging.getLogger("docintel.webhooks")

EVENTS = ["run.completed", "run.failed", "review.created", "review.decided", "pipeline.published",
          "connection.failing"]
RETRY_DELAYS_S: list[float] = [1.0, 5.0, 30.0]   # attempts = 1 + len(RETRY_DELAYS_S); tests set [] or [0]
TIMEOUT_S = 10.0
SYNC_DELIVERY = False      # tests: deliver inline in the committing thread instead of a background thread
_transport: httpx.BaseTransport | None = None
_threads: list[threading.Thread] = []
_PENDING_KEY = "di_pending_webhooks"


def set_transport(t: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = t


def emit(db: Session, workspace_id: str, event_type: str, payload: dict[str, Any]) -> list[str]:
    """Record deliveries for matching subscriptions; delivery happens after `db.commit()`. Never raises."""
    from app.db.models import Webhook, WebhookDelivery

    try:
        subs = db.scalars(select(Webhook).where(Webhook.workspace_id == workspace_id, Webhook.enabled.is_(True))).all()
    except Exception:  # noqa: BLE001
        log.warning("webhooks.emit: could not load subscriptions", exc_info=True)
        return []
    pid = payload.get("pipeline_id")
    event_id = new_id("evt")
    ids: list[str] = []
    for w in subs:
        if event_type not in (w.events or []):
            continue
        if w.pipeline_ids and pid and pid not in w.pipeline_ids:
            continue
        if w.pipeline_ids and not pid:
            continue
        d = WebhookDelivery(id=new_id("whd"), workspace_id=workspace_id, webhook_id=w.id, event=event_type,
                            event_id=event_id, payload=payload, status="pending", attempt=0, next_attempt_at=utcnow())
        db.add(d)
        ids.append(d.id)
    if ids:
        pending = db.info.setdefault(_PENDING_KEY, [])
        pending.extend(ids)
        if not event.contains(db, "after_commit", _after_commit):
            event.listen(db, "after_commit", _after_commit)
            event.listen(db, "after_rollback", _after_rollback)
    return ids


def _after_rollback(session: Session) -> None:
    session.info.pop(_PENDING_KEY, None)


def _after_commit(session: Session) -> None:
    ids = session.info.pop(_PENDING_KEY, None)
    if not ids:
        return
    if SYNC_DELIVERY:
        for i in ids:
            deliver(i)
        return
    t = threading.Thread(target=_deliver_many, args=(list(ids),), daemon=True, name="webhook-delivery")
    _threads.append(t)
    t.start()


def _deliver_many(ids: list[str]) -> None:
    for i in ids:
        try:
            deliver(i)
        except Exception:  # noqa: BLE001
            log.exception("webhook delivery %s crashed", i)


def wait_for_deliveries(timeout: float = 10.0) -> None:
    """Join background delivery threads (tests / shutdown)."""
    deadline = time.monotonic() + timeout
    while _threads:
        t = _threads.pop()
        t.join(max(0.0, deadline - time.monotonic()))


def build_request(webhook: Any, delivery: Any, secret: str | None, *, timestamp: int | None = None) -> dict:
    body = json.dumps({"id": delivery.event_id, "type": delivery.event, "created_at": iso(delivery.created_at or utcnow()),
                       "workspace_id": delivery.workspace_id, "data": delivery.payload},
                      separators=(",", ":"), default=str).encode()
    ts = str(timestamp or int(time.time()))
    headers = {"Content-Type": "application/json", "User-Agent": "DocIntel-Webhooks/1.0", "X-DI-Event": delivery.event,
               "X-DI-Delivery": delivery.id, "X-DI-Timestamp": ts}
    if secret:
        headers["X-DI-Signature"] = "sha256=" + sign_payload(secret, ts, body)
    return {"method": "POST", "url": webhook.url, "headers": headers, "content": body}


def deliver(delivery_id: str) -> str:
    """Attempt one delivery with retries; returns the final status. Uses its own sessions."""
    from app.db.models import Webhook, WebhookDelivery
    from app.db.session import session_factory
    from app.services.adapters.http import UpstreamError, send
    from app.services.secret_store import store_for_ref

    attempts = 1 + len(RETRY_DELAYS_S)
    for n in range(attempts):
        with session_factory()() as db:
            d = db.get(WebhookDelivery, delivery_id)
            if d is None or d.status == "succeeded":
                return d.status if d else "missing"
            w = db.get(Webhook, d.webhook_id)
            if w is None or not w.enabled:
                d.status, d.last_error = "failed", "Subscription deleted or disabled"
                db.commit()
                return d.status
            secret = None
            if w.secret_ref:
                try:
                    secret = store_for_ref(w.secret_ref).get(db, w.secret_ref).get("secret")
                except Exception:  # noqa: BLE001
                    secret = None
            req = build_request(w, d, secret)
            d.attempt = (d.attempt or 0) + 1
            try:
                resp = send(req, timeout=TIMEOUT_S, transport=_transport)
                d.response_status = resp.status_code
                d.response_excerpt = resp.text[:500]
                ok = 200 <= resp.status_code < 300
                d.last_error = None if ok else f"HTTP {resp.status_code}"
            except UpstreamError as exc:
                ok = False
                d.last_error = (exc.detail or "delivery failed")[:2000]
            if ok:
                d.status, d.delivered_at, d.next_attempt_at = "succeeded", utcnow(), None
                db.commit()
                return d.status
            last = n == attempts - 1
            d.status = "failed" if last else "pending"
            delay = 0.0 if last else RETRY_DELAYS_S[n]
            d.next_attempt_at = None if last else utcnow()
            db.commit()
        if not last and delay:
            time.sleep(delay)
    return "failed"


__all__ = ["emit", "deliver", "build_request", "wait_for_deliveries", "set_transport", "EVENTS", "RETRY_DELAYS_S"]
