"""Startup/shutdown hooks called by app.main's lifespan (owned by BE2).

startup: create the run queue (in-process thread pool) and recover runs orphaned by a previous process
(queued → re-enqueued; running with no event for 10 min → failed ``engine_error``). Webhook dispatchers owned by
BE1 can hook in through ``app.services.webhooks.start/stop`` if present.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI

from app.config import get_settings
from app.services import run_engine

log = logging.getLogger("docintel.lifecycle")


def startup(app: FastAPI) -> None:
    workers = int(os.environ.get("DI_RUN_WORKERS", "8"))
    run_engine.set_queue(run_engine.ThreadPoolRunQueue(max_workers=workers))
    if get_settings().env != "test":
        try:
            n = run_engine.recover_orphans()
            if n:
                log.info("recovered %d orphaned run(s)", n)
        except Exception:  # noqa: BLE001 - DB not migrated yet etc.
            log.warning("run recovery skipped", exc_info=True)
    try:
        from app.services import webhooks

        if hasattr(webhooks, "start"):
            webhooks.start(app)
    except ImportError:
        pass


def shutdown(app: FastAPI) -> None:
    try:
        from app.services import webhooks

        if hasattr(webhooks, "stop"):
            webhooks.stop(app)
    except ImportError:
        pass
    q = run_engine.get_queue()
    if hasattr(q, "wait_idle"):
        q.wait_idle(float(os.environ.get("DI_SHUTDOWN_GRACE_S", "10")))  # let in-flight steps finish
    q.shutdown(wait=False)
    run_engine.set_queue(None)
