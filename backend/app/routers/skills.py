"""Skills (BE1): versioned instruction packs for AI steps. Mirrors the portal mock."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import conflict
from app.core.security import AuthContext, get_auth, require_scope
from app.core.time import iso, utcnow
from app.db.models import Skill, SkillVersion
from app.db.session import get_db
from app.routers._be1 import not_found, no_content, node_usages, parse, validation
from app.services import audit

router = APIRouter(tags=["Skills"])


class SkillIn(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None


class VersionIn(BaseModel):
    instructions: str | None = None
    release_note: str | None = None


def skill_usages(db: Session, ws: str, sid: str) -> list[dict]:
    return node_usages(db, ws, lambda n: (n.get("config") or {}).get("skill") == sid)


def skill_out(db: Session, k: Skill) -> dict:
    versions = db.scalars(select(SkillVersion).where(SkillVersion.skill_id == k.id)
                          .order_by(SkillVersion.version.desc())).all()
    return {"id": k.id, "name": k.name, "description": k.description or "", "instructions": k.instructions,
            "version": k.version,
            "versions": [{"version": v.version, "release_note": v.release_note or "", "published_at": iso(v.published_at),
                          "published_by": v.published_by} for v in versions],
            "usage_count": len(skill_usages(db, k.workspace_id, k.id)),
            "created_at": iso(k.created_at), "updated_at": iso(k.updated_at)}


def _get(db: Session, ws: str, sid: str) -> Skill:
    k = db.scalar(select(Skill).where(Skill.workspace_id == ws, Skill.id == sid))
    if k is None:
        raise not_found("Skill", sid)
    return k


@router.get("/skills")
def list_skills(auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(Skill).where(Skill.workspace_id == auth.workspace_id).order_by(Skill.created_at, Skill.id)).all()
    return {"data": [skill_out(db, k) for k in rows]}


@router.post("/skills", status_code=201)
def create_skill(body: dict | None = Body(None), auth: AuthContext = Depends(require_scope("pipelines:write")),
                 db: Session = Depends(get_db)) -> dict:
    req = parse(SkillIn, body)
    if not (req.name or "").strip() or not (req.instructions or "").strip():
        raise validation("name and instructions are required")
    now = utcnow()
    who = auth.name or auth.principal_id
    k = Skill(workspace_id=auth.workspace_id, name=req.name.strip(), description=req.description or "",
              instructions=req.instructions, version=1, updated_by=who, created_at=now, updated_at=now)
    db.add(k)
    db.flush()
    db.add(SkillVersion(skill_id=k.id, workspace_id=k.workspace_id, version=1, name=k.name, description=k.description,
                        instructions=k.instructions, release_note="Created", published_at=now, published_by=who))
    audit.record(db, auth, "skill.created", "skill", k.id, summary=f"Created skill {k.name}")
    db.commit()
    return skill_out(db, k)


@router.get("/skills/{id}")
def get_skill(id: str, auth: AuthContext = Depends(get_auth), db: Session = Depends(get_db)) -> dict:  # noqa: A002
    return skill_out(db, _get(db, auth.workspace_id, id))


@router.patch("/skills/{id}")
def update_skill(id: str, body: dict | None = Body(None),  # noqa: A002
                 auth: AuthContext = Depends(require_scope("pipelines:write")), db: Session = Depends(get_db)) -> dict:
    req = parse(SkillIn, body)
    k = _get(db, auth.workspace_id, id)
    sent = req.model_fields_set
    if "name" in sent and req.name is not None:
        if not req.name.strip():
            raise validation("name is required")
        k.name = req.name.strip()
    if "description" in sent:
        k.description = req.description or ""
    if "instructions" in sent and req.instructions is not None:
        if not req.instructions.strip():
            raise validation("instructions are required")
        k.instructions = req.instructions
    k.updated_by = auth.name or auth.principal_id
    k.updated_at = utcnow()
    audit.record(db, auth, "skill.updated", "skill", k.id, summary=f"Updated skill {k.name}",
                 details={"fields": sorted(sent)})
    db.commit()
    return skill_out(db, k)


@router.delete("/skills/{id}", status_code=204)
def delete_skill(id: str, auth: AuthContext = Depends(require_scope("pipelines:write")),  # noqa: A002
                 db: Session = Depends(get_db)):
    k = _get(db, auth.workspace_id, id)
    us = skill_usages(db, auth.workspace_id, k.id)
    if us:
        raise conflict("skill_in_use", title="Skill is in use", detail=f"Used by {len(us)} step{'s' if len(us) > 1 else ''}.", usages=us)
    db.execute(delete(SkillVersion).where(SkillVersion.skill_id == k.id))
    audit.record(db, auth, "skill.deleted", "skill", k.id, summary=f"Deleted skill {k.name}")
    db.delete(k)
    db.commit()
    return no_content()


@router.post("/skills/{id}/versions", status_code=201)
def publish_skill_version(id: str, body: dict | None = Body(None),  # noqa: A002
                          auth: AuthContext = Depends(require_scope("pipelines:write")),
                          db: Session = Depends(get_db)) -> dict:
    req = parse(VersionIn, body)
    if not (req.instructions or "").strip():
        raise validation("instructions are required")
    k = _get(db, auth.workspace_id, id)
    now = utcnow()
    who = auth.name or auth.principal_id
    k.version += 1
    k.instructions = req.instructions
    k.updated_by, k.updated_at = who, now
    db.add(SkillVersion(skill_id=k.id, workspace_id=k.workspace_id, version=k.version, name=k.name,
                        description=k.description, instructions=k.instructions, release_note=req.release_note or "",
                        published_at=now, published_by=who))
    audit.record(db, auth, "skill.published", "skill", k.id, summary=f"Published {k.name} v{k.version}",
                 details={"version": k.version})
    db.commit()
    return skill_out(db, k)
