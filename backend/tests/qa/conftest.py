"""QA fixtures: black-box access to the in-process app through HTTP only, with the real auth path.

- `api`: wraps the root `raw_client` (no auth override). Every request goes through `api.req(...)`, which records
  the response (status, headers, body text) so a scenario can scan *everything it saw* (e.g. for leaked secrets)
  and validate each response against `api/openapi.yaml` (tests/contract/oas.py).
- Credentials: the bootstrap admin key `dik_test_admin` (as with `DI_BOOTSTRAP_API_KEY=dik_test_admin`), API keys
  created through `POST /v1/api-keys`, and HS256 dev bearer tokens (the dev stand-in for the OIDC IdP) for roles
  and for a second workspace.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.config import get_settings
from app.core.security import issue_dev_token

ADMIN_KEY = "dik_test_admin"


@dataclass
class Seen:
    method: str
    path: str
    status: int
    headers: dict
    text: str

    def json(self) -> Any:
        return json.loads(self.text) if self.text else None


@dataclass
class Api:
    client: Any
    seen: list[Seen] = field(default_factory=list)
    default_headers: dict = field(default_factory=dict)

    def req(self, method: str, path: str, *, auth: dict | None = None, **kw):  # noqa: ANN201
        headers = {**(auth if auth is not None else self.default_headers), **(kw.pop("headers", None) or {})}
        r = self.client.request(method, path if path.startswith("/v1") else "/v1" + path, headers=headers, **kw)
        self.seen.append(Seen(method, path, r.status_code, dict(r.headers), r.text))
        return r

    def get(self, path: str, **kw):  # noqa: ANN201
        return self.req("GET", path, **kw)

    def post(self, path: str, **kw):  # noqa: ANN201
        return self.req("POST", path, **kw)

    def put(self, path: str, **kw):  # noqa: ANN201
        return self.req("PUT", path, **kw)

    def patch(self, path: str, **kw):  # noqa: ANN201
        return self.req("PATCH", path, **kw)

    def delete(self, path: str, **kw):  # noqa: ANN201
        return self.req("DELETE", path, **kw)

    def contract_problems(self, allow: set[str] = frozenset()) -> list[str]:
        """Contract findings for every recorded JSON response (SSE and binary bodies are skipped)."""
        from tests.contract import oas

        out: list[str] = []
        for s in self.seen:
            ctype = s.headers.get("content-type", "")
            body = s.json() if s.text and "json" in ctype else None
            for p in oas.validate_response(s.method, s.path, s.status, ctype, body):
                if not any(a in p for a in allow):
                    out.append(p)
        return out


@pytest.fixture()
def api(raw_client, monkeypatch) -> Api:  # noqa: ANN001
    monkeypatch.setattr(get_settings(), "bootstrap_api_key", ADMIN_KEY)
    return Api(raw_client, default_headers={"X-API-Key": ADMIN_KEY})


def bearer(role: str, sub: str | None = None, ws: str = "ws_default", name: str | None = None) -> dict:
    sub = sub or f"usr_qa_{role}"
    tok = issue_dev_token(sub, ws=ws, role=role, email=f"{sub}@qa.example", name=name or sub)
    return {"Authorization": f"Bearer {tok}"}


def key_header(secret: str) -> dict:
    return {"X-API-Key": secret}
