"""MCP servers (BE1): CRUD, discovery (initialize + tools/list), tool enable/disable, test calls with argument
validation against the tool's input_schema. Mirrors the portal mock; live calls via app.services.mcp_client."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, conflict
from app.core.security import AuthContext, get_auth, require_scope
from app.core.time import iso, utcnow
from app.db.models import Connection, McpServer, McpTool
from app.db.session import get_db
from app.routers._be1 import not_found, no_content, node_usages, parse, simulated, validation
from app.services import audit, mcp_client
from app.services.adapters.auth import connection_auth
from app.services.adapters.http import UpstreamError

router = APIRouter(tags=["MCP servers"])

TRANSPORTS = ("streamable_http", "sse", "stdio_gateway")
SECRET_HEADERS = {"authorization", "x-api-key", "api-key", "proxy-authorization", "cookie"}
SIM_TOOLS = [
    {"name": "ping", "description": "Health probe exposed by the server", "enabled": True,
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "search", "description": "Search records", "enabled": True,
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]


class ToolToggle(BaseModel):
    name: str
    enabled: bool


class McpIn(BaseModel):
    name: str | None = None
    transport: str | None = None
    url: str | None = None
    connection_id: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None
    tools: list[ToolToggle] | None = None


def _tools(db: Session, m: McpServer) -> list[McpTool]:
    return list(db.scalars(select(McpTool).where(McpTool.server_id == m.id).order_by(McpTool.position, McpTool.name)).all())


def tool_out(t: McpTool) -> dict:
    return {"name": t.name, "description": t.description or "", "input_schema": t.input_schema or {"type": "object"},
            "enabled": t.enabled}


def mcp_usages(db: Session, ws: str, sid: str) -> list[dict]:
    return node_usages(db, ws, lambda n: (n.get("config") or {}).get("server") == sid)


def mcp_out(db: Session, m: McpServer) -> dict:
    return {"id": m.id, "name": m.name, "transport": m.transport, "url": m.url, "connection_id": m.connection_id,
            "headers": dict(m.headers or {}), "enabled": m.enabled, "tools": [tool_out(t) for t in _tools(db, m)],
            "status": m.status or "unknown", "last_discovered_at": iso(m.last_discovered_at),
            "usage_count": len(mcp_usages(db, m.workspace_id, m.id)), "created_at": iso(m.created_at),
            "updated_at": iso(m.updated_at)}


def get_mcp_row(db: Session, ws: str, sid: str) -> McpServer:
    m = db.scalar(select(McpServer).where(McpServer.workspace_id == ws, McpServer.id == sid))
    if m is None:
        raise not_found("MCP server", sid)
    return m


def _headers(h: dict[str, str] | None) -> dict[str, str]:
    h = h or {}
    bad = [k for k in h if k.lower() in SECRET_HEADERS]
    if bad:
        raise validation(f"headers must not carry credentials ({', '.join(bad)}); use a connection")
    return h


def _check_connection(db: Session, ws: str, cid: str | None) -> None:
    if cid and db.scalar(select(Connection.id).where(Connection.workspace_id == ws, Connection.id == cid)) is None:
        raise validation(f"Connection {cid} does not exist")


def _replace_tools(db: Session, m: McpServer, tools: list[dict]) -> None:
    db.execute(delete(McpTool).where(McpTool.server_id == m.id))
    seen: set[str] = set()
    for i, t in enumerate(tools):
        if not t.get("name") or t["name"].lower() in seen:
            continue
        seen.add(t["name"].lower())
        db.add(McpTool(server_id=m.id, workspace_id=m.workspace_id, name=t["name"], position=i,
                       description=t.get("description"), input_schema=t.get("input_schema"),
                       enabled=t.get("enabled", True) is not False))
    db.flush()


@router.get("/mcp-servers")
def list_mcp_servers(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(McpServer).where(McpServer.workspace_id == auth.workspace_id)
                      .order_by(McpServer.created_at, McpServer.id)).all()
    return {"data": [mcp_out(db, m) for m in rows]}


@router.post("/mcp-servers", status_code=201)
def create_mcp_server(body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("pipelines:write")),
                      db: Session = Depends(get_db)) -> dict:
    req = parse(McpIn, body)
    if not (req.name or "").strip() or not (req.url or "").strip() or not req.transport:
        raise validation("name, transport and url are required")
    if req.transport not in TRANSPORTS:
        raise validation(f"transport must be one of {', '.join(TRANSPORTS)}")
    _check_connection(db, auth.workspace_id, req.connection_id)
    now = utcnow()
    m = McpServer(workspace_id=auth.workspace_id, name=req.name.strip(), transport=req.transport, url=req.url.strip(),
                  connection_id=req.connection_id or None, headers=_headers(req.headers), enabled=True,
                  status="unknown", created_at=now, updated_at=now)
    db.add(m)
    db.flush()
    audit.record(db, auth, "mcp_server.created", "mcp_server", m.id, summary=f"Added MCP server {m.name}",
                 details={"transport": m.transport})
    db.commit()
    return mcp_out(db, m)


@router.get("/mcp-servers/{id}")
def get_mcp_server(id: str, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:  # noqa: A002
    return mcp_out(db, get_mcp_row(db, auth.workspace_id, id))


@router.patch("/mcp-servers/{id}")
def update_mcp_server(id: str, body: dict | None = Body(None),  # noqa: A002
                      auth: AuthContext = Depends(require_scope("pipelines:write")), db: Session = Depends(get_db)) -> dict:
    req = parse(McpIn, body)
    sent = req.model_fields_set
    m = get_mcp_row(db, auth.workspace_id, id)
    if "name" in sent and req.name is not None:
        if not req.name.strip():
            raise validation("name is required")
        m.name = req.name.strip()
    if "url" in sent and req.url is not None:
        if not req.url.strip():
            raise validation("url is required")
        m.url = req.url.strip()
    if "transport" in sent and req.transport:
        if req.transport not in TRANSPORTS:
            raise validation(f"transport must be one of {', '.join(TRANSPORTS)}")
        m.transport = req.transport
    if "connection_id" in sent:
        _check_connection(db, auth.workspace_id, req.connection_id)
        m.connection_id = req.connection_id or None
    if "headers" in sent:
        m.headers = _headers(req.headers)
    if "enabled" in sent and req.enabled is not None:
        m.enabled = req.enabled
    if req.tools:
        by_name = {t.name: t for t in _tools(db, m)}
        for t in req.tools:
            if t.name in by_name:
                by_name[t.name].enabled = t.enabled
    m.updated_at = utcnow()
    audit.record(db, auth, "mcp_server.updated", "mcp_server", m.id, summary=f"Updated MCP server {m.name}",
                 details={"fields": sorted(sent),
                          "tools": [{"name": t.name, "enabled": t.enabled} for t in req.tools or []] or None})
    db.commit()
    return mcp_out(db, m)


@router.delete("/mcp-servers/{id}", status_code=204)
def delete_mcp_server(id: str, auth: AuthContext = Depends(require_scope("pipelines:write")),  # noqa: A002
                      db: Session = Depends(get_db)):
    m = get_mcp_row(db, auth.workspace_id, id)
    us = mcp_usages(db, auth.workspace_id, m.id)
    if us:
        raise conflict("mcp_in_use", title="MCP server is in use", detail=f"Used by {len(us)} step{'s' if len(us) > 1 else ''}.", usages=us)
    db.execute(delete(McpTool).where(McpTool.server_id == m.id))
    audit.record(db, auth, "mcp_server.deleted", "mcp_server", m.id, summary=f"Deleted MCP server {m.name}")
    db.delete(m)
    db.commit()
    return no_content()


@router.post("/mcp-servers/{id}/discover")
def discover_mcp_tools(id: str, auth: AuthContext = Depends(require_scope("pipelines:write")),  # noqa: A002
                       db: Session = Depends(get_db)) -> dict:
    m = get_mcp_row(db, auth.workspace_id, id)
    if simulated():
        if not _tools(db, m):
            _replace_tools(db, m, [dict(t) for t in SIM_TOOLS])
    else:
        _, _, creds = connection_auth(db, auth.workspace_id, m.connection_id)
        try:
            found = mcp_client.discover(m, creds)
        except UpstreamError as exc:
            m.status, m.status_message = "failing", (exc.detail or "")[:1000]
            db.commit()
            raise ApiError(502, "mcp_unreachable", exc.detail, title="Could not reach the MCP server") from exc
        enabled = {t.name: t.enabled for t in _tools(db, m)}
        for t in found:
            t["enabled"] = enabled.get(t["name"], True)
        _replace_tools(db, m, found)
    m.status, m.status_message, m.last_discovered_at = "connected", None, utcnow()
    audit.record(db, auth, "mcp_server.discovered", "mcp_server", m.id,
                 summary=f"Discovered {len(_tools(db, m))} tools")
    db.commit()
    return mcp_out(db, m)


class CallBody(BaseModel):
    arguments: dict[str, Any] | None = None


def _sim_result(tool: str, args: dict) -> dict:
    sample = {"match_vendor": {"vendor_id": "V-00418", "name": "Gulf Office Supplies LLC", "trn": "100354872600003",
                               "score": 0.97},
              "match_po": {"po_number": args.get("po_number"), "status": "open", "remaining_aed": 13020},
              "find_customer": {"customer_id": "C-22019", "name": args.get("name") or "Example customer"}}
    return {"content": [{"type": "text", "text": json.dumps(sample.get(tool, {"ok": True, "arguments": args}), indent=2)}],
            "is_error": False, "latency_ms": 80 + len(tool) * 7}


@router.post("/mcp-servers/{id}/tools/{tool}/call")
def call_mcp_tool(id: str, tool: str, body: dict | None = Body(None),  # noqa: A002
                  auth: AuthContext = Depends(require_scope("pipelines:write")), db: Session = Depends(get_db)) -> dict:
    req = parse(CallBody, body)
    m = get_mcp_row(db, auth.workspace_id, id)
    t = next((x for x in _tools(db, m) if x.name == tool), None)
    if t is None:
        raise not_found("Tool", tool)
    if not m.enabled:
        raise conflict("mcp_disabled", "Enable the server to call its tools.", title="Server is disabled")
    if not t.enabled:
        raise conflict("tool_disabled", f"Enable the tool {tool} to call it.", title="Tool is disabled")
    args = req.arguments or {}
    schema = t.input_schema or {"type": "object"}
    missing = [k for k in schema.get("required", []) or [] if args.get(k) is None or args.get(k) == ""]
    if missing:
        raise ApiError(422, "invalid_arguments", f"Missing: {', '.join(missing)}",
                       title="Arguments do not match the input schema")
    problems = mcp_client.validate_arguments(schema, args)
    if problems:
        raise ApiError(422, "invalid_arguments", "; ".join(problems[:5]), title="Arguments do not match the input schema")
    if simulated():
        out = _sim_result(tool, args)
    else:
        _, _, creds = connection_auth(db, auth.workspace_id, m.connection_id)
        out = mcp_client.call_tool(m, creds, tool, args)
    audit.record(db, auth, "mcp_tool.called", "mcp_server", m.id, summary=f"Test call {tool}",
                 details={"tool": tool, "is_error": out.get("is_error")})
    db.commit()
    return out


__all__ = ["router", "mcp_out", "get_mcp_row"]
