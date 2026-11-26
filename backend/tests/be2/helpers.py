"""BE2 test helpers: SSE parsing exactly like portal/src/api/sse.ts, golden fixtures, waiting for runs."""
from __future__ import annotations

import json
import time
from typing import Any

from app.services import catalog_data


def golden(key: str) -> Any:
    return catalog_data._load("mock_golden_responses.json", {})[key]


def parse_sse(text: str) -> list[dict]:
    """Port of createSseParser: frames of {id, event, data}; ':' comments ignored; blank line ends a frame."""
    frames: list[dict] = []
    cur: dict = {}
    buf = text.replace("\r\n", "\n").replace("\r", "\n")
    for line in buf.split("\n"):
        if line == "":
            if cur.get("data") is not None:
                frames.append({"id": cur.get("id"), "event": cur.get("event"), "data": cur["data"]})
            cur = {}
            continue
        if line.startswith(":"):
            continue
        c = line.find(":")
        field = line if c < 0 else line[:c]
        value = "" if c < 0 else line[c + 1:]
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            cur["data"] = value if cur.get("data") is None else cur["data"] + "\n" + value
        elif field in ("event", "id"):
            cur[field] = value
    return frames


def stream_events(client, run_id: str, headers: dict | None = None, params: dict | None = None) -> list[dict]:
    """Read /runs/{id}/events until the server closes (after run.finished); return parsed frames with JSON data."""
    with client.stream("GET", f"/v1/runs/{run_id}/events", headers={"Accept": "text/event-stream", **(headers or {})},
                       params=params) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers.get("cache-control") == "no-cache"
        assert r.headers.get("x-accel-buffering") == "no"
        text = "".join(r.iter_text())
    out = []
    for f in parse_sse(text):
        d = json.loads(f["data"])
        out.append({**f, "json": d})
    return out


def golden_events(key: str) -> list[dict]:
    return [{**f, "json": json.loads(f["data"])} for f in parse_sse(golden(key)["events_text"])]


VOLATILE_STEP = ("started_at", "duration_ms")


def normalise_event(e: dict) -> dict:
    """Event payload minus run-specific/timing fields (run_id, at, started_at, duration_ms)."""
    d = {k: v for k, v in e.items() if k not in ("run_id", "at")}
    if "step" in d:
        d["step"] = {k: v for k, v in d["step"].items() if k not in VOLATILE_STEP}
    return d


def wait_status(client, run_id: str, statuses: set[str], timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = client.get(f"/v1/runs/{run_id}").json()
        if last.get("status") in statuses:
            return last
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach {statuses}: {last}")


def draft(client, pid: str) -> tuple[dict, str]:
    r = client.get(f"/v1/pipelines/{pid}/draft")
    assert r.status_code == 200
    assert r.headers["etag"] == r.json()["etag"]
    return r.json(), r.headers["etag"]


def set_threshold(client, pid: str, threshold: float) -> str:
    """Raise/lower the confidence gate of a draft; returns the new etag."""
    d, etag = draft(client, pid)
    g = d["graph"]
    for n in g["nodes"]:
        if n["type"] == "route.confidence":
            n["config"]["threshold"] = threshold
    r = client.put(f"/v1/pipelines/{pid}/draft", json={"graph": g, "change": f"Gate {threshold}"},
                   headers={"If-Match": etag})
    assert r.status_code == 200, r.json()
    return r.headers["etag"]
