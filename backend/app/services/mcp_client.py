"""Minimal MCP client (JSON-RPC 2.0) for the streamable HTTP and legacy HTTP+SSE transports (owned by BE1).

    from app.services import mcp_client
    tools = mcp_client.discover(server, auth)                  # initialize + tools/list → [McpTool dicts]
    out = mcp_client.call_tool(server, auth, "lookup_vendor", {"trn": "100…"})
    # → {"content": [...], "is_error": bool, "latency_ms": int}

``server`` is the ORM row or a dict with ``url, transport, headers``; ``auth`` is the decrypted AuthConfig of its
connection (bearer / api_key header / OAuth 2.0 client credentials …). ``stdio_gateway`` servers are reached
through their HTTP gateway URL with the streamable HTTP transport. Failures raise UpstreamError (502).
"""
from __future__ import annotations

import itertools
import json
import queue
import threading
import time
from typing import Any
from urllib.parse import urljoin

import httpx

from app.services.adapters.auth import apply_auth
from app.services.adapters.http import UpstreamError, client, get_transport, safe_url, send

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "docintel-studio", "version": "1.0.0"}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    v = obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    return default if v is None else v


def _sse_messages(text: str) -> list[dict]:
    """Parse an SSE body into its JSON `data:` payloads."""
    out, data = [], []
    for line in text.splitlines() + [""]:
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
        elif line == "" and data:
            try:
                out.append(json.loads("\n".join(data)))
            except ValueError:
                pass
            data = []
    return out


class McpClient:
    def __init__(self, url: str, *, transport_kind: str = "streamable_http", headers: dict | None = None,
                 auth: dict | None = None, timeout: float = 30.0, http_transport: httpx.BaseTransport | None = None) -> None:
        self.url = url
        self.kind = "streamable_http" if transport_kind == "stdio_gateway" else transport_kind
        self.headers = dict(headers or {})
        self.auth = auth or {"type": "none"}
        self.timeout = timeout
        self.http_transport = http_transport
        self.session_id: str | None = None
        self._ids = itertools.count(1)
        # legacy SSE transport state
        self._sse_endpoint: str | None = None
        self._sse_queue: queue.Queue = queue.Queue()
        self._sse_thread: threading.Thread | None = None
        self._sse_stop = threading.Event()
        self._sse_client: httpx.Client | None = None

    # ── streamable HTTP ──
    def _post(self, url: str, payload: dict) -> httpx.Response:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.session_id or payload.get("method") != "initialize":
            h["MCP-Protocol-Version"] = PROTOCOL_VERSION
        req = apply_auth({"method": "POST", "url": url, "headers": h, "content": json.dumps(payload).encode()},
                         self.auth, transport=self.http_transport)
        return send(req, timeout=self.timeout, transport=self.http_transport)

    def _rpc_http(self, method: str, params: dict | None, notify: bool = False) -> Any:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        rid = None
        if not notify:
            rid = next(self._ids)
            payload["id"] = rid
        resp = self._post(self.url, payload)
        if resp.headers.get("mcp-session-id"):
            self.session_id = resp.headers["mcp-session-id"]
        if notify:
            return None
        if resp.status_code >= 400:
            raise UpstreamError(f"MCP {method} → HTTP {resp.status_code} from {safe_url(self.url)}",
                                status_code=resp.status_code)
        ctype = resp.headers.get("content-type", "")
        msgs = _sse_messages(resp.text) if "text/event-stream" in ctype else [resp.json()]
        for m in msgs:
            if isinstance(m, dict) and m.get("id") == rid:
                return self._result(method, m)
        raise UpstreamError(f"MCP {method}: no response for request {rid}")

    @staticmethod
    def _result(method: str, msg: dict) -> Any:
        if "error" in msg:
            err = msg["error"] or {}
            raise UpstreamError(f"MCP {method} error {err.get('code')}: {str(err.get('message', ''))[:200]}",
                                code="mcp_error")
        return msg.get("result")

    # ── legacy HTTP+SSE ──
    def _sse_open(self) -> None:
        h = {"Accept": "text/event-stream", **self.headers}
        req = apply_auth({"method": "GET", "url": self.url, "headers": h}, self.auth, transport=self.http_transport)
        self._sse_req = req
        self._sse_client = client(timeout=self.timeout, transport=self.http_transport, verify=req.get("verify", True))
        ready = threading.Event()
        errors: list[Exception] = []

        def reader() -> None:
            try:
                with self._sse_client.stream("GET", req["url"], headers=req["headers"],
                                             timeout=httpx.Timeout(self.timeout, read=None)) as resp:
                    if resp.status_code >= 400:
                        raise UpstreamError(f"MCP SSE GET → HTTP {resp.status_code}", status_code=resp.status_code)
                    event, data = "message", []
                    for line in resp.iter_lines():
                        if self._sse_stop.is_set():
                            break
                        if line.startswith("event:"):
                            event = line[6:].strip()
                        elif line.startswith("data:"):
                            data.append(line[5:].lstrip())
                        elif line == "":
                            if data:
                                payload = "\n".join(data)
                                if event == "endpoint":
                                    self._sse_endpoint = urljoin(self.url, payload.strip())
                                    ready.set()
                                else:
                                    try:
                                        self._sse_queue.put(json.loads(payload))
                                    except ValueError:
                                        pass
                            event, data = "message", []
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                ready.set()

        self._sse_thread = threading.Thread(target=reader, daemon=True, name="mcp-sse")
        self._sse_thread.start()
        ready.wait(self.timeout)
        if not self._sse_endpoint:
            exc = errors[0] if errors else None
            detail = getattr(exc, "detail", None) or (f"{type(exc).__name__}: {exc}" if exc else "no endpoint event")
            raise UpstreamError(f"MCP SSE {safe_url(self.url)}: {detail}")

    def _rpc_sse(self, method: str, params: dict | None, notify: bool = False) -> Any:
        if not self._sse_endpoint:
            self._sse_open()
        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        rid = None
        if not notify:
            rid = next(self._ids)
            payload["id"] = rid
        resp = self._post(self._sse_endpoint, payload)  # type: ignore[arg-type]
        if resp.status_code >= 400:
            raise UpstreamError(f"MCP {method} → HTTP {resp.status_code}", status_code=resp.status_code)
        if notify:
            return None
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            try:
                m = self._sse_queue.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            if isinstance(m, dict) and m.get("id") == rid:
                return self._result(method, m)
        raise UpstreamError(f"MCP {method}: timed out waiting for the response")

    # ── public ──
    def rpc(self, method: str, params: dict | None = None, *, notify: bool = False) -> Any:
        if self.kind == "sse":
            return self._rpc_sse(method, params, notify)
        return self._rpc_http(method, params, notify)

    def initialize(self) -> dict:
        res = self.rpc("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                      "clientInfo": CLIENT_INFO}) or {}
        try:
            self.rpc("notifications/initialized", notify=True)
        except UpstreamError:
            pass
        return res

    def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        for _ in range(20):
            res = self.rpc("tools/list", {"cursor": cursor} if cursor else {}) or {}
            tools.extend(res.get("tools") or [])
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.rpc("tools/call", {"name": name, "arguments": arguments}) or {}

    def close(self) -> None:
        self._sse_stop.set()
        if self._sse_client is not None:
            try:
                self._sse_client.close()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self) -> "McpClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def _client_for(server: Any, auth: dict | None, transport: httpx.BaseTransport | None) -> McpClient:
    return McpClient(str(_get(server, "url", "")), transport_kind=str(_get(server, "transport", "streamable_http")),
                     headers=dict(_get(server, "headers", {}) or {}), auth=auth,
                     http_transport=get_transport(transport))


def discover(server: Any, auth: dict | None, *, transport: httpx.BaseTransport | None = None) -> list[dict]:
    """initialize + tools/list → list of McpTool dicts ({name, description, input_schema, enabled})."""
    with _client_for(server, auth, transport) as c:
        c.initialize()
        raw = c.list_tools()
    return [{"name": t.get("name"), "description": t.get("description") or "",
             "input_schema": t.get("inputSchema") or t.get("input_schema") or {"type": "object"}, "enabled": True}
            for t in raw if t.get("name")]


def call_tool(server: Any, auth: dict | None, name: str, arguments: dict, *,
              transport: httpx.BaseTransport | None = None) -> dict:
    """initialize + tools/call → {content, is_error, latency_ms}."""
    t0 = time.perf_counter()
    with _client_for(server, auth, transport) as c:
        c.initialize()
        res = c.call_tool(name, arguments)
    content = res.get("content")
    if content is None and "structuredContent" in res:
        content = [{"type": "text", "text": json.dumps(res["structuredContent"])}]
    return {"content": content or [], "is_error": bool(res.get("isError", False)),
            "latency_ms": int((time.perf_counter() - t0) * 1000)}


# ── argument validation against a tool's input_schema ──────────────────────────────────────────────

def validate_arguments(schema: dict | None, args: dict) -> list[str]:
    """Return human-readable problems (empty list = valid). Uses jsonschema_rs when installed, else a minimal
    validator covering required / type / enum / properties / additionalProperties=false."""
    schema = schema or {"type": "object"}
    try:
        import jsonschema_rs  # type: ignore[import-not-found]

        try:
            v = jsonschema_rs.validator_for(schema)
        except Exception:  # noqa: BLE001 — invalid schema from the server: fall back
            v = None
        if v is not None:
            out = []
            for e in v.iter_errors(args):
                path = "/".join(str(p) for p in getattr(e, "instance_path", []) or [])
                out.append(f"{path + ': ' if path else ''}{e.message}")
            return out
    except ImportError:
        pass
    return _mini_validate(schema, args, "")


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "object": dict, "array": list,
          "null": type(None)}


def _type_ok(t: Any, v: Any) -> bool:
    types = t if isinstance(t, list) else [t]
    for tt in types:
        py = _TYPES.get(tt)
        if py is None:
            return True
        if tt in ("integer", "number") and isinstance(v, bool):
            continue
        if isinstance(v, py):
            return True
    return False


def _mini_validate(schema: dict, value: Any, path: str) -> list[str]:
    errs: list[str] = []
    where = f"{path}: " if path else ""
    if "type" in schema and not _type_ok(schema["type"], value):
        return [f"{where}{json.dumps(value)[:40]} is not of type {schema['type']}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{where}{json.dumps(value)[:40]} is not one of {schema['enum']}")
    if isinstance(value, dict):
        for k in schema.get("required", []) or []:
            if k not in value or value[k] is None:
                errs.append(f"{where}\"{k}\" is a required property")
        props = schema.get("properties") or {}
        for k, v in value.items():
            if k in props and isinstance(props[k], dict):
                errs.extend(_mini_validate(props[k], v, f"{path}/{k}" if path else k))
            elif schema.get("additionalProperties") is False:
                errs.append(f"{where}Additional property \"{k}\" is not allowed")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            errs.extend(_mini_validate(schema["items"], item, f"{path}/{i}"))
    return errs


__all__ = ["McpClient", "discover", "call_tool", "validate_arguments"]
