"""Knowledge collections (BE1). Indexing is recorded synchronously (document rows + chunk estimate); real
chunk/embed/upsert into the vector store is a documented gap (see backend README / BE1 report)."""
from __future__ import annotations

import re

from fastapi import APIRouter, Body, Depends, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import conflict
from app.core.security import AuthContext, get_auth, require_scope
from app.core.time import utcnow
from app.db.models import Connection, Document, KnowledgeCollection, KnowledgeDocument, Provider
from app.db.session import get_db
from app.routers._be1 import not_found, parse, validation
from app.services import audit

router = APIRouter(tags=["Knowledge"])

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
VECTOR_SERVICES = {"qdrant", "pgvector", "azure_ai_search"}


class CollectionIn(BaseModel):
    name: str | None = None
    connection_id: str | None = None
    embedding_provider_id: str | None = None
    embedding_model: str | None = None
    chunk_size: int | None = 800


class AddDocs(BaseModel):
    document_ids: list[str] | None = None


def collection_out(k: KnowledgeCollection) -> dict:
    out = {"id": k.id, "name": k.name, "connection_id": k.connection_id or "", "embedding_provider_id": k.embedding_provider_id,
           "embedding_model": k.embedding_model, "document_count": k.document_count, "chunk_count": k.chunk_count,
           "status": k.status}
    return {a: b for a, b in out.items() if b is not None}


@router.get("/knowledge-collections")
def list_collections(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(KnowledgeCollection).where(KnowledgeCollection.workspace_id == auth.workspace_id)
                      .order_by(KnowledgeCollection.created_at, KnowledgeCollection.id)).all()
    return {"data": [collection_out(k) for k in rows]}


@router.post("/knowledge-collections", status_code=201)
def create_collection(body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("pipelines:write")),
                      db: Session = Depends(get_db)) -> dict:
    req = parse(CollectionIn, body)
    ws = auth.workspace_id
    if not req.name or not NAME_RE.match(req.name):
        raise validation("name must be snake_case")
    if not req.connection_id or not req.embedding_provider_id or not req.embedding_model:
        raise validation("connection_id, embedding_provider_id and embedding_model are required")
    conn = db.scalar(select(Connection).where(Connection.workspace_id == ws, Connection.id == req.connection_id))
    if conn is None:
        raise validation(f"Connection {req.connection_id} does not exist")
    if conn.service not in VECTOR_SERVICES:
        raise validation("connection_id must be a vector store connection (Qdrant, pgvector, Azure AI Search)")
    if db.scalar(select(Provider.id).where(Provider.workspace_id == ws, Provider.id == req.embedding_provider_id)) is None:
        raise validation(f"Provider {req.embedding_provider_id} does not exist")
    if db.scalar(select(func.count()).select_from(KnowledgeCollection).where(
            KnowledgeCollection.workspace_id == ws, KnowledgeCollection.name == req.name)):
        raise conflict("collection_exists", f"A collection named {req.name} already exists")
    if req.chunk_size is not None and not 50 <= req.chunk_size <= 8000:
        raise validation("chunk_size must be between 50 and 8000")
    now = utcnow()
    k = KnowledgeCollection(workspace_id=ws, name=req.name, connection_id=conn.id,
                            embedding_provider_id=req.embedding_provider_id, embedding_model=req.embedding_model,
                            chunk_size=req.chunk_size or 800, document_count=0, chunk_count=0, status="ready",
                            created_at=now, updated_at=now)
    db.add(k)
    db.flush()
    audit.record(db, auth, "knowledge_collection.created", "knowledge_collection", k.id,
                 summary=f"Created collection {k.name}")
    db.commit()
    return collection_out(k)


@router.post("/knowledge-collections/{id}/documents", status_code=202)
def add_collection_documents(id: str, body: dict | None = Body(None),  # noqa: A002
                             auth: AuthContext = Depends(require_scope("pipelines:write")),
                             db: Session = Depends(get_db)) -> Response:
    from app.services import storage

    req = parse(AddDocs, body)
    ws = auth.workspace_id
    k = db.scalar(select(KnowledgeCollection).where(KnowledgeCollection.workspace_id == ws, KnowledgeCollection.id == id))
    if k is None:
        raise not_found("Knowledge collection", id)
    if not req.document_ids:
        raise validation("document_ids must contain at least one document")
    docs = [storage.get_document(db, ws, d) for d in dict.fromkeys(req.document_ids)]  # 404 on unknown ids
    added = 0
    for d in docs:
        exists = db.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.collection_id == k.id,
                                                              KnowledgeDocument.filename == d.filename))
        if exists:
            continue
        size = getattr(d, "size_bytes", None) or 4000
        chunks = max(1, int(size / 6 / (k.chunk_size or 800)))  # ≈ 6 bytes per character of extracted text
        db.add(KnowledgeDocument(workspace_id=ws, collection_id=k.id,
                                 document_id=d.id if db.get(Document, d.id) is not None else None,
                                 filename=d.filename, mime_type=d.mime_type, size_bytes=getattr(d, "size_bytes", None),
                                 chunk_count=chunks, status="ready"))
        k.document_count += 1
        k.chunk_count += chunks
        added += 1
    k.status = "ready"
    k.updated_at = utcnow()
    audit.record(db, auth, "knowledge_collection.documents_added", "knowledge_collection", k.id,
                 summary=f"Added {added} document(s)", details={"document_ids": list(req.document_ids)})
    db.commit()
    return Response(status_code=202)
