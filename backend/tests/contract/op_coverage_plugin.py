"""pytest plugin (QA): records every in-process HTTP request and maps it to the 95 contract operations.

Use: `pytest tests -p tests.contract.op_coverage_plugin`. Writes `$QA_COVERAGE_OUT` (default
/tmp/qa_op_coverage.json): {operation: [test nodeids…]} plus the list of operations no test exercised.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict

import httpx
import pytest

_hits: dict[str, set[str]] = defaultdict(set)
_unmatched: set[str] = set()
_current = {"node": "?"}
_orig_send = httpx.Client.send


def _send(self, request, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
    from tests.contract import oas

    key = oas.match(request.method, request.url.path)
    if key:
        _hits[key].add(_current["node"])
    elif request.url.path.startswith("/v1"):
        _unmatched.add(f"{request.method} {request.url.path}")
    return _orig_send(self, request, *args, **kwargs)


def pytest_configure(config):  # noqa: ANN001, ANN201, ARG001
    httpx.Client.send = _send


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):  # noqa: ANN001, ANN201
    _current["node"] = item.nodeid
    yield


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001, ANN201, ARG001
    from tests.contract import oas

    httpx.Client.send = _orig_send
    ops = oas.all_operation_keys()
    out = {
        "total": len(ops),
        "covered": sorted(k for k in ops if _hits.get(k)),
        "uncovered": sorted(k for k in ops if not _hits.get(k)),
        "by_operation": {k: sorted(_hits.get(k, ())) for k in ops},
        "unmatched_requests": sorted(_unmatched)[:200],
    }
    with open(os.environ.get("QA_COVERAGE_OUT", "/tmp/qa_op_coverage.json"), "w") as f:
        json.dump(out, f, indent=1)
