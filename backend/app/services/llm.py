"""Model provider clients behind one function (owned by BE1). Plain httpx, no SDKs.

    from app.services import llm
    out = llm.complete(db, provider, "claude-sonnet-5-5",
                       [{"role": "system", "content": "…"}, {"role": "user", "content": "…"}],
                       json_schema={"type": "object", ...}, temperature=0.1)
    # → {"text": str, "json": dict | None, "model": str, "usage": {"input_tokens", "output_tokens"},
    #    "latency_ms": int, "stop_reason": str | None, "provider_id": str, "kind": str}

``provider`` is the ORM row (or a dict) with ``id, kind, base_url, connection_id, workspace_id``; credentials and
non-secret settings (``endpoint``, ``api_version`` …) come from its connection. Failures raise
:class:`app.services.adapters.http.UpstreamError` (502 problem+json, ``code=upstream_error``).

Kinds: anthropic · openai · azure_openai · google_gemini · ollama · openai_compatible.
``test_provider`` returns a TestResult dict (never raises); ``list_models`` returns ModelInfo dicts.
Pass ``transport=httpx.MockTransport(...)`` (or ``adapters.http.set_transport``) in tests.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.core.errors import ApiError
from app.services.adapters.auth import apply_auth, client_credentials_token, connection_auth, secret_value
from app.services.adapters.http import UpstreamError, join_url, latency_of, raise_for_status, send

DEFAULT_BASE = {
    "anthropic": "https://api.anthropic.com",
    "openai": "https://api.openai.com/v1",
    "google_gemini": "https://generativelanguage.googleapis.com",
    "ollama": "http://localhost:11434",
}
ANTHROPIC_VERSION = "2023-06-01"
AZURE_API_VERSION = "2024-10-21"


def _get(obj: Any, key: str, default: Any = None) -> Any:
    v = obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    return default if v is None else v


class _Ctx:
    def __init__(self, db: Any, provider: Any) -> None:
        self.kind = str(_get(provider, "kind"))
        self.provider_id = str(_get(provider, "id", ""))
        ws = _get(provider, "workspace_id")
        if db is not None and _get(provider, "connection_id"):
            _, self.settings, self.auth = connection_auth(db, ws, _get(provider, "connection_id"))
        else:
            self.settings = dict(_get(provider, "settings", {}) or {})
            self.auth = dict(_get(provider, "auth", {}) or {"type": "none"})
        base = _get(provider, "base_url") or self.settings.get("base_url") or self.settings.get("endpoint") \
            or DEFAULT_BASE.get(self.kind, "")
        self.base = str(base).rstrip("/")
        if self.kind == "azure_openai" and not self.base:
            raise ApiError(422, "invalid_provider", "Azure OpenAI needs an endpoint (connection settings.endpoint)")

    # Native auth header conventions; fall back to generic apply_auth for other schemes.
    def authorise(self, req: dict, transport: httpx.BaseTransport | None) -> dict:
        key = secret_value(self.auth)
        t = self.auth.get("type")
        h = req.setdefault("headers", {})
        if self.kind == "anthropic" and key:
            h["x-api-key"] = key
            h["anthropic-version"] = ANTHROPIC_VERSION
            return req
        if self.kind == "azure_openai":
            if key:
                h["api-key"] = key
                return req
            if t == "azure_ad_client":
                h["Authorization"] = "Bearer " + client_credentials_token(self.auth, transport=transport)
                return req
        if self.kind == "google_gemini" and key:
            h["x-goog-api-key"] = key
            return req
        if self.kind in ("openai", "openai_compatible", "ollama") and key and t == "api_key" \
                and (self.auth.get("name") or "Authorization").lower() == "authorization" and not self.auth.get("prefix"):
            h["Authorization"] = "Bearer " + key
            return req
        if self.kind == "anthropic":
            h["anthropic-version"] = ANTHROPIC_VERSION
        return apply_auth(req, self.auth, settings=self.settings, transport=transport)

    @property
    def api_version(self) -> str:
        return str(self.settings.get("api_version") or AZURE_API_VERSION)

    def openai_base(self) -> str:
        b = self.base
        if self.kind == "openai_compatible" and not b.endswith("/v1") and "/v1/" not in b + "/":
            b = b + "/v1"
        return b


def _split_system(messages: list[dict]) -> tuple[str | None, list[dict]]:
    system = [str(m.get("content", "")) for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    return ("\n\n".join(system) if system else None), rest


def _parse_json(text: str) -> Any:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    try:
        return json.loads(t)
    except ValueError:
        start, end = t.find("{"), t.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(t[start:end + 1])
            except ValueError:
                return None
    return None


def complete(db: Any, provider: Any, model_id: str, messages: list[dict], *, json_schema: dict | None = None,
             temperature: float | None = None, max_tokens: int = 1024, timeout: float = 60.0,
             transport: httpx.BaseTransport | None = None) -> dict:
    """Run one chat completion. See module docstring for the return shape."""
    ctx = _Ctx(db, provider)
    system, convo = _split_system(messages)
    kind = ctx.kind
    t0 = time.perf_counter()

    if kind == "anthropic":
        body: dict[str, Any] = {"model": model_id, "max_tokens": max_tokens,
                                "messages": [{"role": m["role"], "content": m["content"]} for m in convo]}
        sys_text = system or ""
        if json_schema:
            sys_text += ("\n\n" if sys_text else "") + "Respond with only a JSON object matching this JSON Schema:\n" \
                + json.dumps(json_schema)
        if sys_text:
            body["system"] = sys_text
        if temperature is not None:
            body["temperature"] = temperature
        req = {"method": "POST", "url": join_url(ctx.base, "/v1/messages"), "json": body}
    elif kind in ("openai", "openai_compatible", "azure_openai"):
        body = {"messages": [{"role": m["role"], "content": m["content"]} for m in messages]}
        if temperature is not None:
            body["temperature"] = temperature
        body["max_tokens"] = max_tokens
        if json_schema:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "result", "schema": json_schema, "strict": False}}
        if kind == "azure_openai":
            url = join_url(ctx.base, f"/openai/deployments/{model_id}/chat/completions")
            req = {"method": "POST", "url": url, "params": {"api-version": ctx.api_version}, "json": body}
        else:
            body["model"] = model_id
            req = {"method": "POST", "url": join_url(ctx.openai_base(), "/chat/completions"), "json": body}
    elif kind == "google_gemini":
        contents = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": str(m["content"])}]}
                    for m in convo]
        body = {"contents": contents, "generationConfig": {"maxOutputTokens": max_tokens}}
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        if temperature is not None:
            body["generationConfig"]["temperature"] = temperature
        if json_schema:
            body["generationConfig"]["responseMimeType"] = "application/json"
            body["generationConfig"]["responseSchema"] = json_schema
        req = {"method": "POST", "url": join_url(ctx.base, f"/v1beta/models/{model_id}:generateContent"), "json": body}
    elif kind == "ollama":
        body = {"model": model_id, "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
                "stream": False, "options": {"num_predict": max_tokens}}
        if temperature is not None:
            body["options"]["temperature"] = temperature
        if json_schema:
            body["format"] = json_schema
        req = {"method": "POST", "url": join_url(ctx.base, "/api/chat"), "json": body}
    else:
        raise ApiError(422, "unsupported_provider", f"Provider kind {kind} is not supported")

    req.setdefault("headers", {})["Content-Type"] = "application/json"
    req = ctx.authorise(req, transport)
    resp = send(req, timeout=timeout, transport=transport)
    raise_for_status(resp, f"{kind} completion")
    try:
        data = resp.json()
    except ValueError as exc:
        raise UpstreamError(f"{kind} returned a non-JSON response") from exc

    text, usage, stop = "", {}, None
    try:
        if kind == "anthropic":
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            u = data.get("usage") or {}
            usage = {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens")}
            stop = data.get("stop_reason")
        elif kind in ("openai", "openai_compatible", "azure_openai"):
            ch = (data.get("choices") or [{}])[0]
            text = (ch.get("message") or {}).get("content") or ""
            u = data.get("usage") or {}
            usage = {"input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens")}
            stop = ch.get("finish_reason")
        elif kind == "google_gemini":
            cand = (data.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in ((cand.get("content") or {}).get("parts") or []))
            u = data.get("usageMetadata") or {}
            usage = {"input_tokens": u.get("promptTokenCount"), "output_tokens": u.get("candidatesTokenCount")}
            stop = cand.get("finishReason")
        elif kind == "ollama":
            text = (data.get("message") or {}).get("content") or ""
            usage = {"input_tokens": data.get("prompt_eval_count"), "output_tokens": data.get("eval_count")}
            stop = data.get("done_reason")
    except (AttributeError, TypeError, IndexError) as exc:
        raise UpstreamError(f"{kind} returned an unexpected response shape") from exc

    return {
        "text": text,
        "json": _parse_json(text) if json_schema else None,
        "model": data.get("model", model_id) if isinstance(data, dict) else model_id,
        "usage": usage,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "stop_reason": stop,
        "provider_id": ctx.provider_id,
        "kind": kind,
    }


def list_models(db: Any, provider: Any, *, transport: httpx.BaseTransport | None = None) -> list[dict]:
    """Model list from the provider API (Azure OpenAI: deployments). Raises UpstreamError."""
    ctx = _Ctx(db, provider)
    kind = ctx.kind
    if kind == "anthropic":
        req = {"method": "GET", "url": join_url(ctx.base, "/v1/models"), "params": {"limit": 100}}
    elif kind in ("openai", "openai_compatible"):
        req = {"method": "GET", "url": join_url(ctx.openai_base(), "/models")}
    elif kind == "azure_openai":
        req = {"method": "GET", "url": join_url(ctx.base, "/openai/deployments"), "params": {"api-version": "2022-12-01"}}
    elif kind == "google_gemini":
        req = {"method": "GET", "url": join_url(ctx.base, "/v1beta/models")}
    elif kind == "ollama":
        req = {"method": "GET", "url": join_url(ctx.base, "/api/tags")}
    else:
        raise ApiError(422, "unsupported_provider", f"Provider kind {kind} is not supported")
    req = ctx.authorise(req, transport)
    resp = send(req, timeout=20, transport=transport)
    raise_for_status(resp, f"{kind} model list")
    data = resp.json()
    out: list[dict] = []
    if kind == "anthropic":
        for m in data.get("data", []):
            out.append({"id": m["id"], "display_name": m.get("display_name") or m["id"], "enabled": True,
                        "supports_tools": True, "supports_vision": True})
    elif kind in ("openai", "openai_compatible"):
        for m in data.get("data", []):
            mid = m["id"]
            out.append({"id": mid, "display_name": mid, "enabled": True, "embedding": "embed" in mid})
    elif kind == "azure_openai":
        for m in data.get("data", data.get("value", [])):
            mid = m.get("id") or m.get("name")
            out.append({"id": mid, "display_name": mid, "enabled": True,
                        "embedding": "embed" in str(m.get("model", mid))})
    elif kind == "google_gemini":
        for m in data.get("models", []):
            mid = str(m.get("name", "")).removeprefix("models/")
            methods = m.get("supportedGenerationMethods") or []
            out.append({"id": mid, "display_name": m.get("displayName") or mid, "enabled": True,
                        "context_window": m.get("inputTokenLimit"), "embedding": "embedContent" in methods})
    elif kind == "ollama":
        for m in data.get("models", []):
            mid = m.get("name") or m.get("model")
            out.append({"id": mid, "display_name": mid, "enabled": True, "embedding": "embed" in str(mid)})
    return [{k: v for k, v in m.items() if v is not None} for m in out if m.get("id")]


def test_provider(db: Any, provider: Any, model: str | None = None, *,
                  transport: httpx.BaseTransport | None = None) -> dict:
    """Send a tiny prompt; returns TestResult {ok, latency_ms, message} (never raises)."""
    model = model or _get(provider, "default_model") or next(
        (m.get("id") for m in (_get(provider, "models", []) or []) if not m.get("embedding")), None)
    if not _get(provider, "connection_id") and _get(provider, "kind") != "ollama":
        return {"ok": False, "message": "No connection configured"}
    if not model:
        return {"ok": False, "message": "No model to test"}
    t0 = time.perf_counter()
    try:
        out = complete(db, provider, model, [{"role": "user", "content": "Reply with the single word OK."}],
                       max_tokens=5, temperature=0, timeout=20, transport=transport)
    except ApiError as exc:
        return {"ok": False, "latency_ms": int((time.perf_counter() - t0) * 1000), "message": exc.detail or exc.code}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "latency_ms": int((time.perf_counter() - t0) * 1000), "message": f"{type(exc).__name__}"}
    return {"ok": True, "latency_ms": out["latency_ms"], "message": f"{model} answered a test prompt",
            "details": {"model": out.get("model"), "usage": out.get("usage")}}


__all__ = ["complete", "list_models", "test_provider", "latency_of"]
