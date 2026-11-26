"""BE1 test helpers (uses the DB engineer's tests/conftest.py fixtures: client, raw_client, auth_as, db)."""
from __future__ import annotations

from datetime import timedelta
from typing import Callable

import httpx
import pytest

from app.config import get_settings
from app.core.security import api_key_prefix, hash_api_key, new_api_key_secret
from app.core.time import utcnow
from app.services.adapters import http as http_mod
from app.services.adapters.auth import clear_token_cache


@pytest.fixture()
def live_mode(monkeypatch):
    """run_mode=live so component endpoints call the (mocked) network."""
    monkeypatch.setattr(get_settings(), "run_mode", "live")
    yield


class Router:
    """httpx.MockTransport with a list of (method, url-substring) → handler and a request log."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, str, Callable[[httpx.Request], httpx.Response]]] = []
        self.requests: list[httpx.Request] = []

    def add(self, method: str, url_part: str, handler) -> None:  # noqa: ANN001
        if not callable(handler):
            resp = handler
            handler = lambda req, _r=resp: _r() if callable(_r) else _r  # noqa: E731
        self.routes.append((method.upper(), url_part, handler))

    def json(self, method: str, url_part: str, body, status: int = 200, headers=None) -> None:  # noqa: ANN001
        self.add(method, url_part, lambda req: httpx.Response(status, json=body, headers=headers))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        for m, part, h in reversed(self.routes):
            if m in (request.method, "*") and part in str(request.url):
                return h(request)
        raise httpx.ConnectError("no route (sandbox)", request=request)


@pytest.fixture()
def net():
    r = Router()
    http_mod.set_transport(httpx.MockTransport(r))
    clear_token_cache()
    yield r
    http_mod.set_transport(None)
    clear_token_cache()


@pytest.fixture()
def make_key(db):
    """make_key(scopes=["admin"], **fields) → secret of a fresh API key in ws_default."""
    from app.db.models import ApiKey

    def _make(scopes=("admin",), **fields) -> str:  # noqa: ANN001, ANN003
        secret = new_api_key_secret(live=False)
        now = utcnow()
        k = ApiKey(workspace_id=fields.pop("workspace_id", "ws_default"), name=fields.pop("name", "test"),
                   prefix=api_key_prefix(secret), key_hash=hash_api_key(secret), scopes=list(scopes),
                   created_at=now - timedelta(seconds=1), updated_at=now, **fields)
        db.add(k)
        db.commit()
        return secret

    return _make
