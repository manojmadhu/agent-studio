"""AI model providers (BE1). Mirrors the portal mock; live calls go through app.services.llm."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, conflict
from app.core.security import AuthContext, get_auth, require_scope
from app.core.time import iso, utcnow
from app.db.models import Connection, KnowledgeCollection, Provider, ProviderModel
from app.db.session import get_db
from app.routers._be1 import not_found, no_content, node_usages, parse, simulated, validation
from app.schemas.generated import ModelInfo
from app.services import audit, llm
from app.services.adapters.http import UpstreamError

router = APIRouter(tags=["Providers"])

KINDS = ("anthropic", "openai", "azure_openai", "google_gemini", "ollama", "openai_compatible")
SYNC_EXTRA = {"anthropic": "claude-opus-5-5", "openai": "gpt-5-nano", "google_gemini": "gemini-2.5-flash-lite",
              "azure_openai": "gpt-embed-prod"}


class ProviderIn(BaseModel):
    kind: str | None = None
    name: str | None = None
    enabled: bool | None = None
    base_url: str | None = None
    connection_id: str | None = None
    data_region: str | None = None
    models: list[ModelInfo] | None = None
    default_model: str | None = None
    limits: dict[str, Any] | None = None


def _models(db: Session, p: Provider) -> list[ProviderModel]:
    return list(db.scalars(select(ProviderModel).where(ProviderModel.provider_id == p.id)
                           .order_by(ProviderModel.position, ProviderModel.model_id)).all())


def model_out(m: ProviderModel) -> dict:
    out = {"id": m.model_id, "display_name": m.display_name, "context_window": m.context_window,
           "supports_tools": m.supports_tools, "supports_vision": m.supports_vision, "embedding": m.embedding,
           "enabled": m.enabled}
    return {k: v for k, v in out.items() if v is not None}


def provider_usages(db: Session, ws: str, pid: str) -> list[dict]:
    return node_usages(db, ws, lambda n: n.get("type") == "ai.model" and (n.get("config") or {}).get("provider") == pid)


def provider_out(db: Session, p: Provider, usage_count: int | None = None) -> dict:
    out = {"id": p.id, "kind": p.kind, "name": p.name, "enabled": p.enabled, "base_url": p.base_url,
           "connection_id": p.connection_id, "data_region": p.data_region,
           "models": [model_out(m) for m in _models(db, p)], "default_model": p.default_model, "limits": p.limits,
           "usage_count": usage_count if usage_count is not None else len(provider_usages(db, p.workspace_id, p.id)),
           "created_at": iso(p.created_at), "updated_at": iso(p.updated_at)}
    return {k: v for k, v in out.items() if v is not None or k == "connection_id"}


def get_provider_row(db: Session, ws: str, pid: str) -> Provider:
    p = db.scalar(select(Provider).where(Provider.workspace_id == ws, Provider.id == pid))
    if p is None:
        raise not_found("Provider", pid)
    return p


def provider_with_models(db: Session, p: Provider) -> dict:
    """Dict view used by llm.* (models included)."""
    return {"id": p.id, "workspace_id": p.workspace_id, "kind": p.kind, "base_url": p.base_url,
            "connection_id": p.connection_id, "default_model": p.default_model,
            "models": [model_out(m) for m in _models(db, p)]}


def _check_connection(db: Session, ws: str, cid: str | None) -> None:
    if cid and db.scalar(select(Connection.id).where(Connection.workspace_id == ws, Connection.id == cid)) is None:
        raise validation(f"Connection {cid} does not exist")


def _set_models(db: Session, p: Provider, models: list[dict]) -> None:
    db.execute(delete(ProviderModel).where(ProviderModel.provider_id == p.id))
    seen: set[str] = set()
    for i, m in enumerate(models):
        mid = str(m.get("id") or "").strip()
        if not mid or mid.lower() in seen:
            continue
        seen.add(mid.lower())
        db.add(ProviderModel(provider_id=p.id, workspace_id=p.workspace_id, model_id=mid, position=i,
                             display_name=m.get("display_name"), context_window=m.get("context_window"),
                             supports_tools=bool(m.get("supports_tools")), supports_vision=bool(m.get("supports_vision")),
                             embedding=bool(m.get("embedding")), enabled=m.get("enabled", True) is not False))
    db.flush()


def _clean_url(v: str | None) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    if not v.startswith(("http://", "https://")):
        raise validation("base_url must be an http(s) URL")
    return v.rstrip("/")


@router.get("/providers")
def list_providers(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Provider).where(Provider.workspace_id == auth.workspace_id)
                      .order_by(Provider.created_at, Provider.id)).all()
    return {"data": [provider_out(db, p) for p in rows]}


@router.post("/providers", status_code=201)
def create_provider(body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("pipelines:write")),
                    db: Session = Depends(get_db)) -> dict:
    req = parse(ProviderIn, body)
    if not req.kind or not (req.name or "").strip():
        raise validation("kind and name are required")
    if req.kind not in KINDS:
        raise validation(f"kind must be one of {', '.join(KINDS)}")
    _check_connection(db, auth.workspace_id, req.connection_id)
    models = [m.model_dump(exclude_none=True) for m in req.models or []]
    now = utcnow()
    p = Provider(workspace_id=auth.workspace_id, kind=req.kind, name=req.name.strip(), enabled=True,
                 base_url=_clean_url(req.base_url), connection_id=req.connection_id or None,
                 data_region=req.data_region or "Global",
                 default_model=req.default_model or (models[0]["id"] if models else None), limits=req.limits,
                 created_at=now, updated_at=now)
    db.add(p)
    db.flush()
    _set_models(db, p, models)
    audit.record(db, auth, "provider.created", "provider", p.id, summary=f"Added provider {p.name}",
                 details={"kind": p.kind})
    db.commit()
    return provider_out(db, p, 0)


@router.get("/providers/{id}")
def get_provider(id: str, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:  # noqa: A002
    return provider_out(db, get_provider_row(db, auth.workspace_id, id))


@router.patch("/providers/{id}")
def update_provider(id: str, body: dict | None = Body(None),  # noqa: A002
                    auth: AuthContext = Depends(require_scope("pipelines:write")), db: Session = Depends(get_db)) -> dict:
    req = parse(ProviderIn, body)
    sent = req.model_fields_set
    p = get_provider_row(db, auth.workspace_id, id)
    if "kind" in sent and req.kind and req.kind != p.kind:
        raise validation("kind cannot be changed")
    if "name" in sent and req.name is not None:
        if not req.name.strip():
            raise validation("name is required")
        p.name = req.name.strip()
    if "enabled" in sent and req.enabled is not None:
        p.enabled = req.enabled
    if "base_url" in sent:
        p.base_url = _clean_url(req.base_url)
    if "connection_id" in sent:
        _check_connection(db, auth.workspace_id, req.connection_id)
        p.connection_id = req.connection_id or None
    if "data_region" in sent:
        p.data_region = req.data_region or None
    if "default_model" in sent:
        p.default_model = req.default_model or None
    if "limits" in sent:
        p.limits = req.limits
    if "models" in sent and req.models is not None:
        _set_models(db, p, [m.model_dump(exclude_none=True) for m in req.models])
    p.updated_at = utcnow()
    audit.record(db, auth, "provider.updated", "provider", p.id, summary=f"Updated provider {p.name}",
                 details={"fields": sorted(sent)})
    db.commit()
    return provider_out(db, p)


@router.delete("/providers/{id}", status_code=204)
def delete_provider(id: str, auth: AuthContext = Depends(require_scope("pipelines:write")),  # noqa: A002
                    db: Session = Depends(get_db)):
    p = get_provider_row(db, auth.workspace_id, id)
    us = provider_usages(db, auth.workspace_id, p.id)
    for k in db.scalars(select(KnowledgeCollection).where(KnowledgeCollection.workspace_id == auth.workspace_id,
                                                          KnowledgeCollection.embedding_provider_id == p.id)).all():
        us.append({"resource_type": "knowledge_collection", "resource_id": k.id, "label": f"Knowledge · {k.name}"})
    if us:
        n = len(us)
        raise conflict("provider_in_use", title="Provider is in use", detail=f"Used by {n} language model component{'s' if n > 1 else ''}.", usages=us)
    db.execute(delete(ProviderModel).where(ProviderModel.provider_id == p.id))
    audit.record(db, auth, "provider.deleted", "provider", p.id, summary=f"Deleted provider {p.name}")
    db.delete(p)
    db.commit()
    return no_content()


class TestBody(BaseModel):
    model: str | None = None


@router.post("/providers/{id}/test")
def test_provider(id: str, body: dict | None = Body(None),  # noqa: A002
                  auth: AuthContext = Depends(require_scope("pipelines:write")), db: Session = Depends(get_db)) -> dict:
    req = parse(TestBody, body)
    p = get_provider_row(db, auth.workspace_id, id)
    model = req.model or p.default_model
    if simulated():
        conn = db.scalar(select(Connection).where(Connection.workspace_id == auth.workspace_id,
                                                  Connection.id == p.connection_id)) if p.connection_id else None
        ok = conn is not None and p.kind != "ollama"
        msg = (f"{model} answered a test prompt (simulated)" if ok
               else "Could not reach the server (simulated)" if conn else "No connection configured")
        return {"ok": ok, "latency_ms": 200 + sum(map(ord, p.id)) % 500, "message": msg}
    return llm.test_provider(db, provider_with_models(db, p), model)


@router.post("/providers/{id}/models/sync")
def sync_provider_models(id: str, auth: AuthContext = Depends(require_scope("pipelines:write")),  # noqa: A002
                         db: Session = Depends(get_db)) -> dict:
    p = get_provider_row(db, auth.workspace_id, id)
    current = [model_out(m) for m in _models(db, p)]
    if simulated():
        if p.kind == "ollama":
            raise ApiError(502, "provider_unreachable", "GET /api/tags failed (simulated)",
                           title="Could not reach the provider")
        add = SYNC_EXTRA.get(p.kind)
        if add and not any(m["id"] == add for m in current):
            current.append({"id": add, "display_name": add, "enabled": True, "supports_tools": True})
        merged = current
    else:
        try:
            remote = llm.list_models(db, provider_with_models(db, p))
        except UpstreamError as exc:
            raise ApiError(502, "provider_unreachable", exc.detail, title="Could not reach the provider") from exc
        known = {m["id"]: m for m in current}
        merged = list(current)
        for m in remote:
            if m["id"] in known:
                known[m["id"]].update({k: v for k, v in m.items() if k not in ("enabled",)})
            else:
                merged.append(m)
    _set_models(db, p, merged)
    p.updated_at = utcnow()
    audit.record(db, auth, "provider.models_synced", "provider", p.id, summary=f"Synced models ({len(merged)})")
    db.commit()
    return provider_out(db, p)


__all__ = ["router", "provider_out", "get_provider_row", "provider_with_models"]
