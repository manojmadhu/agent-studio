"""QA helpers around the contract `api/openapi.yaml`: operation matching and response-schema validation.

`match(method, path)` maps a concrete request path (with or without the `/v1` prefix) to the contract operation
key "METHOD /template". `validate_response(method, path, status, content_type, body)` returns a list of problems:
an undocumented status, a wrong content type (errors must be application/problem+json), or JSON-Schema violations.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any

import yaml

CONTRACT = Path(__file__).resolve().parents[3] / "api" / "openapi.yaml"
METHODS = ("get", "post", "put", "patch", "delete")


@functools.lru_cache
def spec() -> dict:
    return yaml.safe_load(CONTRACT.read_text())


@functools.lru_cache
def operations() -> list[tuple[str, str, re.Pattern, dict]]:
    out = []
    for tmpl, item in spec()["paths"].items():
        rx = re.compile("^" + re.sub(r"\{[^/}]+\}", "[^/]+", tmpl) + "/?$")
        for m, op in item.items():
            if m in METHODS:
                out.append((m.upper(), tmpl, rx, op))
    # literal segments win over templated ones (e.g. /pipelines/{id}/versions vs /pipelines/{id}/{x})
    out.sort(key=lambda o: o[1].count("{"))
    return out


def all_operation_keys() -> list[str]:
    return [f"{m} {t}" for m, t, _, _ in operations()]


def match(method: str, path: str) -> str | None:
    p = path.split("?", 1)[0]
    if p.startswith("/v1"):
        p = p[3:] or "/"
    for m, tmpl, rx, _ in operations():
        if m == method.upper() and rx.match(p):
            return f"{m} {tmpl}"
    return None


def _resolve(node: Any) -> Any:
    while isinstance(node, dict) and "$ref" in node and len(node) == 1:
        cur: Any = spec()
        for part in node["$ref"].lstrip("#/").split("/"):
            cur = cur[part]
        node = cur
    return node


@functools.lru_cache(maxsize=512)
def _validator(schema_json: str):  # noqa: ANN202
    import jsonschema_rs

    schema = json.loads(schema_json)
    schema = {**schema, "components": spec()["components"]} if isinstance(schema, dict) else schema
    return jsonschema_rs.Draft202012Validator(schema)


def validate_response(method: str, path: str, status: int, content_type: str | None, body: Any) -> list[str]:
    key = match(method, path)
    if key is None:
        return [f"no contract operation for {method} {path}"]
    m, tmpl = key.split(" ", 1)
    op = spec()["paths"][tmpl][m.lower()]
    responses = op.get("responses", {})
    resp = responses.get(str(status)) or responses.get(f"{str(status)[0]}XX") or responses.get("default")
    problems: list[str] = []
    ctype = (content_type or "").split(";")[0].strip()
    if status >= 400 and ctype != "application/problem+json":
        problems.append(f"{key} {status}: error content-type is {ctype!r}, expected application/problem+json")
    if resp is None:
        problems.append(f"{key}: status {status} not documented")
        return problems
    content = _resolve(resp).get("content") or {}
    if not content or status == 204 or ctype not in content or ctype == "text/event-stream":
        return problems
    schema = content[ctype].get("schema")
    if schema is None or not ctype.endswith("json"):
        return problems
    errs = list(_validator(json.dumps(schema)).iter_errors(body))
    for e in errs[:5]:
        problems.append(f"{key} {status}: schema violation at {'/'.join(map(str, e.instance_path))}: {e.message[:200]}")
    return problems
