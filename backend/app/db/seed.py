"""Demo workspace seed (ws_default, named after seed.json settings.name, sandbox) — mirrors the portal's mock backend.

Source: `app/data/seed.json`, exported from `portal/src/api/mock/seed.ts` by the frontend engineer and read via
`app.services.catalog_data.seed_data()`. Portal ids are kept (pl_ap, con_gmail, rv1, run_48210, …) so deep
links work. All timestamps in seed.json were computed at `_meta.reference_now`; they are shifted by
(now - reference_now) so "x min ago" labels look like the mock.

Secrets: the mock has no secret material. Connections that need one get a *placeholder* credential stored
through `app.services.secret_store.put(db, workspace_id, payload)` when that module exists (BE1), otherwise
`secret_ref = "seed:placeholder"` (to be treated as "no usable secret"). Plain-text secrets never reach a table.

Usage:
    seed_if_empty(db)   # no-op when workspace ws_default exists (app startup, tests)
    seed(db)            # unconditional insert of the demo data (caller guarantees an empty workspace)
    python -m app.db.seed   # seed the DI_DATABASE_URL database if empty
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.time import utcnow
from app.db import models as m

log = logging.getLogger("docintel.seed")

WORKSPACE_ID = "ws_default"
WORKSPACE_NAME = "Acme Finance"  # fallback when seed.json has no settings.name
ADMIN_ID = "usr_admin"
ADMIN_FALLBACK = {"name": "Admin", "email": "admin@acme.example"}  # used when seed.json has no `me`
PLACEHOLDER_REF = "seed:placeholder"

AI_STEP_PREFIXES = ("ai.", "tool.")


# ───────────────────────── helpers ─────────────────────────

def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


class _Clock:
    """Parses seed timestamps and shifts them so they are relative to the real now."""

    def __init__(self, reference_now: str | None, now: datetime) -> None:
        ref = self.parse_raw(reference_now) if reference_now else now
        self.delta = now - ref
        self.now = now

    @staticmethod
    def parse_raw(v: str) -> datetime:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def __call__(self, v: str | None) -> datetime | None:
        if not v:
            return None
        return self.parse_raw(v) + self.delta


_ASC_WINDOW = 1000  # seconds; seed arrays are far shorter


def _staggered(clock: "_Clock", value: str | None, index: int, *, desc: bool = False) -> datetime:
    """created_at for the index-th row of a seed array, rows 1 s apart, so each list comes back in the mock's
    array order under the ordering its router uses:
      desc=True  → earliest-listed newest, for keyset lists (created_at DESC, id DESC): connections (paginate);
      desc=False → earliest-listed oldest, for lists ordered (created_at, id) ASC: pipelines, templates,
                   policies, providers, workers, MCP servers, knowledge, skills, members, webhooks.
    Every value is <= the base timestamp (never in the future), so rows created later by the API sort after
    (ASC) / before (DESC) the seed. Rows with distinct real timestamps (api keys, runs) keep their order."""
    base = clock(value) or clock.now
    return base - timedelta(seconds=index if desc else _ASC_WINDOW - index)


def _ai_count(graph: dict) -> int:
    return sum(1 for n in (graph or {}).get("nodes", []) if str(n.get("type", "")).startswith(AI_STEP_PREFIXES))


def _store_secret(db: Session, workspace_id: str, payload: dict[str, Any]) -> str:
    """Store a credential via BE1's SecretStore if available; returns a secret_ref."""
    try:
        from app.services import secret_store  # type: ignore[attr-defined]
    except ImportError:
        return PLACEHOLDER_REF
    put = getattr(secret_store, "put", None)
    if put is None:
        return PLACEHOLDER_REF
    try:
        ref = put(db, workspace_id, payload)
        return str(ref) if ref else PLACEHOLDER_REF
    except Exception:  # noqa: BLE001 — seeding must not fail because the secret store isn't ready
        log.warning("secret_store.put failed during seeding; using placeholder secret_ref", exc_info=True)
        return PLACEHOLDER_REF


def _placeholder_auth(auth_type: str, conn_id: str) -> dict[str, Any] | None:
    """Non-functional demo credential shaped like the AuthConfig for `auth_type` (None for no-auth)."""
    fake = f"demo-placeholder-{conn_id}"
    return {
        "none": None,
        "api_key": {"type": "api_key", "key": fake},
        "bearer": {"type": "bearer", "token": fake},
        "basic": {"type": "basic", "username": "demo", "password": fake},
        "oauth2_client_credentials": {"type": "oauth2_client_credentials", "token_url": "https://example.invalid/token",
                                      "client_id": "demo", "client_secret": fake},
        "oauth2_authorization_code": {"type": "oauth2_authorization_code", "client_id": "demo", "client_secret": fake},
        "hmac": {"type": "hmac", "secret": fake},
        "aws_sigv4": {"type": "aws_sigv4", "access_key_id": "AKIADEMO", "secret_access_key": fake, "region": "me-central-1"},
        "azure_ad_client": {"type": "azure_ad_client", "tenant_id": "demo", "client_id": "demo", "client_secret": fake},
    }.get(auth_type, {"type": auth_type, "secret": fake})


# ───────────────────────── public API ─────────────────────────

def seed_if_empty(db: Session) -> bool:
    """Seed the demo workspace unless ws_default already exists. Returns True when it seeded."""
    if db.get(m.Workspace, WORKSPACE_ID) is not None:
        return False
    seed(db)
    return True


def seed(db: Session, *, now: datetime | None = None, data: dict | None = None) -> None:
    """Insert the demo workspace and commit."""
    from app.services.catalog_data import seed_data

    data = data if data is not None else seed_data()
    now = now or utcnow()
    clock = _Clock((data.get("_meta") or {}).get("reference_now"), now)
    ws = WORKSPACE_ID
    settings_doc = data.get("settings") or {}

    db.add(m.Workspace(id=ws, name=settings_doc.get("name") or WORKSPACE_NAME, environment=settings_doc.get("environment", "sandbox"),
                       region=settings_doc.get("default_region")))
    db.flush()
    db.add(m.WorkspaceSettings(workspace_id=ws, default_region=settings_doc.get("default_region"),
                               retention_days=settings_doc.get("retention_days"),
                               allowed_regions=settings_doc.get("allowed_regions") or [],
                               sso=settings_doc.get("sso")))

    _seed_members(db, ws, data.get("me") or {}, data.get("members") or [], clock)
    _seed_api_keys(db, ws, data.get("api_keys") or [], clock)
    if not data:
        log.warning("app/data/seed.json not found — seeded only the workspace, admin member and bootstrap key")
        db.commit()
        return

    _seed_connections(db, ws, data.get("connections") or [], clock)
    db.flush()
    _seed_components(db, ws, data, clock)
    db.flush()
    _seed_pipelines(db, ws, data.get("pipelines") or [], data.get("templates") or [], clock)
    db.flush()
    _seed_runs(db, ws, data, clock)
    for i, w in enumerate(data.get("webhooks") or []):
        db.add(m.Webhook(id=w["id"], workspace_id=ws, url=w["url"], events=list(w.get("events") or []),
                         enabled=w.get("enabled", True), pipeline_ids=list(w.get("pipeline_ids") or []),
                         secret_ref=_store_secret(db, ws, {"type": "hmac", "secret": f"demo-placeholder-{w['id']}"}),
                         secret_hint="••••demo", created_at=_staggered(clock, w.get("created_at"), i)))
    db.commit()
    log.info("Seeded demo workspace %s", ws)


# ───────────────────────── sections ─────────────────────────

def admin_member(data: dict) -> dict:
    """usr_admin carries the mock principal's (`me`) name and email; id stays usr_admin."""
    me = data.get("me") if "me" in data else data
    return {"id": ADMIN_ID, "name": (me or {}).get("name") or ADMIN_FALLBACK["name"],
            "email": (me or {}).get("email") or ADMIN_FALLBACK["email"], "role": "admin"}


def _seed_members(db: Session, ws: str, me: dict, members: Iterable[dict], clock: "_Clock") -> None:
    admin = admin_member({"me": me})
    seen = {admin["email"].lower()}
    db.add(m.Member(workspace_id=ws, status="active", created_at=_staggered(clock, None, 0), **admin))
    i = 1
    for u in members:
        # The mock's own principal (usr_manoj) is represented by usr_admin (same email) — skip duplicates.
        if u["email"].lower() in seen or u["id"] in (ADMIN_ID, me.get("id")):
            continue
        seen.add(u["email"].lower())
        db.add(m.Member(id=u["id"], workspace_id=ws, name=u.get("name"), email=u["email"],
                        role=u.get("role", "viewer"), status=u.get("status", "active"),
                        created_at=_staggered(clock, None, i)))
        i += 1


def _seed_api_keys(db: Session, ws: str, keys: Iterable[dict], clock: _Clock) -> None:
    import secrets as _secrets

    for i, k in enumerate(keys):
        # Demo keys have no known secret: store the hash of a random value so they can never authenticate.
        db.add(m.ApiKey(id=k["id"], workspace_id=ws, name=k["name"], prefix=k["prefix"],
                        key_hash=_sha256(_secrets.token_urlsafe(32)), scopes=list(k.get("scopes") or []),
                        expires_at=clock(k.get("expires_at")), last_used_at=clock(k.get("last_used_at")),
                        created_by=k.get("created_by"), created_at=_staggered(clock, k.get("created_at"), i, desc=True)))
    bootstrap = get_settings().bootstrap_api_key
    if bootstrap:
        prefix = bootstrap[:13] if bootstrap.startswith("dik_") else bootstrap[:8]
        db.add(m.ApiKey(id="key_bootstrap", workspace_id=ws, name="Bootstrap admin key", prefix=prefix,
                        key_hash=_sha256(bootstrap), scopes=["admin"], created_by="system"))


def _seed_connections(db: Session, ws: str, conns: Iterable[dict], clock: _Clock) -> None:
    for i, c in enumerate(conns):
        auth = _placeholder_auth(c["auth_type"], c["id"])
        ref = _store_secret(db, ws, auth) if auth else None
        db.add(m.Connection(
            id=c["id"], workspace_id=ws, name=c["name"], service=c["service"], auth_type=c["auth_type"],
            settings=c.get("settings") or {}, secret_ref=ref, secret_hint=c.get("secret_hint"),
            status=c.get("status", "unverified"), last_tested_at=clock(c.get("last_tested_at")),
            last_used_at=clock(c.get("last_used_at")), expires_at=clock(c.get("expires_at")),
            created_at=_staggered(clock, c.get("created_at"), i, desc=True), updated_at=clock(c.get("updated_at")) or clock.now,
        ))


def _seed_components(db: Session, ws: str, data: dict, clock: _Clock) -> None:
    for i, p in enumerate(data.get("providers") or []):
        db.add(m.Provider(id=p["id"], workspace_id=ws, kind=p["kind"], name=p["name"], enabled=p.get("enabled", True),
                          base_url=p.get("base_url"), connection_id=p.get("connection_id"),
                          data_region=p.get("data_region"), default_model=p.get("default_model"),
                          limits=p.get("limits"), created_at=_staggered(clock, p.get("created_at"), i)))
        db.flush()
        for i, mi in enumerate(p.get("models") or []):
            db.add(m.ProviderModel(provider_id=p["id"], workspace_id=ws, model_id=mi["id"], position=i,
                                   display_name=mi.get("display_name"), context_window=mi.get("context_window"),
                                   supports_tools=bool(mi.get("supports_tools")),
                                   supports_vision=bool(mi.get("supports_vision")),
                                   embedding=bool(mi.get("embedding")), enabled=mi.get("enabled", True)))
    for i, w in enumerate(data.get("workers") or []):
        db.add(m.Worker(id=w["id"], workspace_id=ws, name=w["name"], kind=w["kind"], base_url=w["base_url"],
                        adapter=w["adapter"], invoke_path=w.get("invoke_path"), health_path=w.get("health_path"),
                        connection_id=w.get("connection_id"), accepts=list(w.get("accepts") or []),
                        capabilities=list(w.get("capabilities") or []), timeout_s=w.get("timeout_s"),
                        max_concurrency=w.get("max_concurrency"), data_region=w.get("data_region"),
                        health=w.get("health"), created_at=_staggered(clock, w.get("created_at"), i)))
    for i, s in enumerate(data.get("mcp_servers") or []):
        db.add(m.McpServer(id=s["id"], workspace_id=ws, name=s["name"], transport=s["transport"], url=s["url"],
                           connection_id=s.get("connection_id"), headers=s.get("headers"),
                           enabled=s.get("enabled", True), status=s.get("status", "unknown"),
                           last_discovered_at=clock(s.get("last_discovered_at")),
                           created_at=_staggered(clock, s.get("created_at"), i)))
        db.flush()
        for i, t in enumerate(s.get("tools") or []):
            db.add(m.McpTool(server_id=s["id"], workspace_id=ws, name=t["name"], position=i,
                             description=t.get("description"), input_schema=t.get("input_schema"),
                             enabled=t.get("enabled", True)))
    for i, k in enumerate(data.get("knowledge_collections") or []):
        db.add(m.KnowledgeCollection(id=k["id"], workspace_id=ws, name=k["name"], connection_id=k["connection_id"],
                                     embedding_provider_id=k.get("embedding_provider_id"),
                                     embedding_model=k.get("embedding_model"),
                                     document_count=k.get("document_count", 0), chunk_count=k.get("chunk_count", 0),
                                     status=k.get("status", "ready"),
                                     created_at=_staggered(clock, k.get("created_at"), i)))
    for i, s in enumerate(data.get("skills") or []):
        db.add(m.Skill(id=s["id"], workspace_id=ws, name=s["name"], description=s.get("description"),
                       instructions=s.get("instructions") or "", version=s.get("version", 1),
                       created_at=_staggered(clock, s.get("created_at"), i)))
        db.flush()
        for v in s.get("versions") or []:
            db.add(m.SkillVersion(skill_id=s["id"], workspace_id=ws, version=v["version"], name=s["name"],
                                  description=s.get("description"), instructions=s.get("instructions") or "",
                                  release_note=v.get("release_note"), published_at=clock(v.get("published_at")),
                                  published_by=v.get("published_by")))
    for i, p in enumerate(data.get("policies") or []):
        db.add(m.Policy(id=p["id"], workspace_id=ws, name=p["name"], description=p.get("description"), kind=p["kind"],
                        enabled=p.get("enabled", True), scope=p.get("scope") or {}, config=p.get("config"),
                        created_at=_staggered(clock, p.get("created_at"), i)))


def _seed_pipelines(db: Session, ws: str, pipelines: Iterable[dict], templates: Iterable[dict], clock: _Clock) -> None:
    for i, entry in enumerate(pipelines):
        p, draft = entry["pipeline"], entry.get("draft") or {}
        graph = draft.get("graph") or {"nodes": [], "edges": []}
        intake = next((n["type"] for n in graph.get("nodes", []) if str(n.get("type", "")).startswith("intake.")), None)
        db.add(m.Pipeline(
            id=p["id"], workspace_id=ws, name=p["name"], description=p.get("description"),
            status=p.get("status", "draft"), active=p.get("status") == "active", owner_team=p.get("owner_team"),
            live_version=p.get("live_version"), intake_type=p.get("intake_type") or intake, kpi=p.get("kpi"),
            updated_by=p.get("updated_by"), created_by=p.get("updated_by"),
            created_at=_staggered(clock, p.get("created_at"), i), updated_at=clock(p.get("updated_at")) or clock.now,
        ))
        db.flush()
        rev = draft.get("rev", 1)
        db.add(m.PipelineDraft(pipeline_id=p["id"], workspace_id=ws, graph=graph,
                               etag=f'W/"{p["id"]}-{rev}"', changes=list(draft.get("changes") or []),
                               saved_by=draft.get("saved_by"), base_version=p.get("live_version"),
                               updated_at=clock(draft.get("saved_at")) or clock.now))
        for v in entry.get("versions") or []:
            db.add(m.PipelineVersion(id=f"plv_{p['id']}_{v['version']}", workspace_id=ws, pipeline_id=p["id"],
                                     version=v["version"], graph=v.get("graph") or graph,
                                     release_note=v.get("release_note"), published_at=clock(v.get("published_at")),
                                     published_by=v.get("published_by"),
                                     created_at=clock(v.get("published_at")) or clock.now))
    for i, t in enumerate(templates):
        g = t.get("graph") or {"nodes": [], "edges": []}
        intake = next((n["type"] for n in g.get("nodes", []) if str(n.get("type", "")).startswith("intake.")), None)
        db.add(m.Template(id=t["id"], workspace_id=ws, name=t["name"], description=t.get("description"),
                          intake_type=intake, graph=g, step_count=t.get("step_count", len(g.get("nodes", []))),
                          ai_component_count=t.get("ai_component_count", _ai_count(g)),
                          created_at=_staggered(clock, t.get("created_at"), i)))


def _add_document(db: Session, ws: str, doc: dict, clock: _Clock, seen: set[str], pipeline_id: str | None) -> str:
    if doc["id"] not in seen:
        seen.add(doc["id"])
        db.add(m.Document(id=doc["id"], workspace_id=ws, filename=doc["filename"], mime_type=doc["mime_type"],
                          size_bytes=doc.get("size_bytes"), pages=doc.get("pages", 1), sha256=doc.get("sha256"),
                          source="sample", pipeline_id=pipeline_id, redacted=True,
                          retention_until=clock(doc.get("retention_until")),
                          created_at=clock(doc.get("uploaded_at")) or clock.now))
    return doc["id"]


def _sample_documents() -> list[dict]:
    from app.services.catalog_data import sample_documents

    return list((sample_documents() or {}).get("documents") or [])


def _seed_sample_documents(db: Session, ws: str, clock: _Clock, seen: set[str]) -> None:
    """doc_inv_sample / doc_rcp_sample — the portal always test-runs with these. Bytes are stored through
    BE2's app.services.storage (synthesised deterministic PDF/JPEG) when available; otherwise rows only
    (storage.read_bytes synthesises them on demand)."""
    for sd in _sample_documents():
        if sd["id"] in seen:
            continue
        seen.add(sd["id"])
        row = m.Document(id=sd["id"], workspace_id=ws, filename=sd["filename"], mime_type=sd["mime_type"],
                         pages=int(sd.get("pages") or 1), source="sample", redacted=True,
                         meta={"kind": sd.get("kind"), "doc_kind": sd.get("doc_kind")}, created_at=clock.now)
        try:
            from app.services import storage  # BE2

            data = storage.synth_sample_bytes(sd["id"], sd["filename"], sd["mime_type"])
            key = f"{ws}/{sd['id']}/{storage._safe_name(sd['filename'])}"  # same layout as save_document
            storage.get_store().put(key, data, sd["mime_type"])
            row.object_key, row.size_bytes, row.sha256 = key, len(data), hashlib.sha256(data).hexdigest()
        except Exception:  # noqa: BLE001 — storage absent/not ready: keep the row, bytes are synthesised later
            log.info("Sample document %s seeded without stored bytes", sd["id"], exc_info=True)
        db.add(row)


def _seed_runs(db: Session, ws: str, data: dict, clock: _Clock) -> None:
    docs: set[str] = set()
    _seed_sample_documents(db, ws, clock, docs)
    live = {e["pipeline"]["id"]: e["pipeline"].get("live_version") for e in data.get("pipelines") or []}
    run_ids: set[str] = set()
    for r in data.get("runs") or []:
        doc_id = _add_document(db, ws, r["document"], clock, docs, r["pipeline_id"]) if r.get("document") else None
        started = clock(r.get("started_at"))
        pv = r.get("pipeline_version")
        db.add(m.Run(id=r["id"], workspace_id=ws, pipeline_id=r["pipeline_id"],
                     pipeline_version=pv if isinstance(pv, int) else None, document_id=doc_id, status=r["status"],
                     test=bool(r.get("test")), deliver=not r.get("test"), trigger="ingest", started_at=started,
                     finished_at=clock(r.get("finished_at")), duration_ms=r.get("duration_ms"),
                     field_count=r.get("field_count", 0), error=r.get("error"), created_at=started or clock.now))
        run_ids.add(r["id"])
    db.flush()
    results = data.get("review_results") or {}
    member_ids = {u.get("name"): u["id"] for u in data.get("members") or [] if u.get("name")}
    member_ids[admin_member(data)["name"]] = ADMIN_ID
    for rv in data.get("review_items") or []:
        doc_id = _add_document(db, ws, rv["document"], clock, docs, rv["pipeline_id"]) if rv.get("document") else None
        created = clock(rv.get("created_at")) or clock.now
        res = results.get(rv["id"]) or {}
        if rv["run_id"] not in run_ids:  # review items point at runs that are paused in review
            db.add(m.Run(id=rv["run_id"], workspace_id=ws, pipeline_id=rv["pipeline_id"],
                         pipeline_version=live.get(rv["pipeline_id"]), document_id=doc_id, status="review",
                         test=False, deliver=True, trigger="ingest", started_at=created,
                         field_count=len(res.get("fields") or []), created_at=created))
            run_ids.add(rv["run_id"])
            db.flush()
        if res:
            db.add(m.RunResult(run_id=rv["run_id"], workspace_id=ws, classification=res.get("classification"),
                               fields=res.get("fields") or [], checks=res.get("checks") or [], path=res.get("path"),
                               overlays=res.get("overlays")))
        db.add(m.ReviewItem(id=rv["id"], workspace_id=ws, run_id=rv["run_id"], pipeline_id=rv["pipeline_id"],
                            document_id=doc_id, reason=rv["reason"], reason_code=rv.get("reason_code"),
                            queue=rv["queue"], assignee=rv.get("assignee"),
                            assignee_id=member_ids.get(rv.get("assignee")), sla_due_at=clock(rv.get("sla_due_at")),
                            status=rv.get("status", "open"), created_at=created))


if __name__ == "__main__":  # pragma: no cover
    from app.db.session import session_factory

    logging.basicConfig(level=logging.INFO)
    with session_factory()() as _db:
        print("seeded" if seed_if_empty(_db) else "already seeded")
