"""Static catalogue data exported from the portal (single source of truth = portal catalog.ts / services.ts).

Files in app/data/ are produced by the frontend engineer's export script:
  step_types.json         → GET /catalog/step-types  (list[StepType])
  services.json           → GET /catalog/services    (list[ServiceDefinition])
  seed.json               → demo workspace seed (used by app.db.seed)
  sample_documents.json   → sample documents + extraction values used by the simulated run engine
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _load(name: str, default: Any) -> Any:
    p = DATA_DIR / name
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


@lru_cache
def step_types() -> list[dict]:
    return _load("step_types.json", [])


@lru_cache
def step_types_by_id() -> dict[str, dict]:
    return {s["type"]: s for s in step_types()}


@lru_cache
def services() -> list[dict]:
    return _load("services.json", [])


@lru_cache
def services_by_id() -> dict[str, dict]:
    return {s["id"]: s for s in services()}


@lru_cache
def seed_data() -> dict:
    return _load("seed.json", {})


@lru_cache
def sample_documents() -> Any:
    return _load("sample_documents.json", {})
