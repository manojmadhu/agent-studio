"""In-process run event bus (owned by BE2).

The durable log is the `run_events` table (seq 1..n per run = SSE `id:`). The bus only *wakes up* SSE readers in
the same process when the engine has committed a new event, so streams don't have to poll hard. Readers always
re-read the table, so a missed notification (another process, a race) costs at most one `wait` timeout.

Swap for Redis pub/sub (channel ``run:<id>``) when the engine moves to Celery/Arq workers; the interface stays:

    bus.publish(run_id, seq)                      # after commit
    bus.wait(run_id, after_seq, timeout) -> bool  # blocking; True if something newer was published
"""
from __future__ import annotations

import threading
import time


class EventBus:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._latest: dict[str, int] = {}

    def publish(self, run_id: str, seq: int) -> None:
        with self._cond:
            if seq > self._latest.get(run_id, 0):
                self._latest[run_id] = seq
            self._cond.notify_all()

    def latest(self, run_id: str) -> int:
        with self._cond:
            return self._latest.get(run_id, 0)

    def wait(self, run_id: str, after_seq: int, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._latest.get(run_id, 0) <= after_seq:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
            return True

    def forget(self, run_id: str) -> None:
        with self._cond:
            self._latest.pop(run_id, None)


bus = EventBus()
