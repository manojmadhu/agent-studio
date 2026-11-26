# Backend team charter — doc-intel-api

The backend lives in `backend/` of the same repository as the portal (`manojmadhu/agent-studio`).
Contract: `api/openapi.yaml` (OpenAPI 3.1) is the **source of truth**. The portal (`portal/`) must work in
`VITE_API_MODE=live` against `http://localhost:8080/v1` with **no frontend changes**. Where the portal's behaviour
and the YAML disagree, read `backend/docs/portal-integration-notes.md` and raise it with the lead.

## Team & ownership

| Role | Owns (only edit these; ask the lead for anything else) |
|---|---|
| Lead | `app/main.py`, `app/config.py`, `app/core/{errors,ids,time,pagination}.py`, `app/services/catalog_data.py`, this file |
| DB engineer | `app/db/**` (base, types, session, models, seed), `migrations/**`, `alembic.ini`, `tests/conftest.py`, `tests/db/**`, `docs/database.md`, `docker-compose.yml` |
| Backend engineer 1 (platform & components) | `app/core/security.py`, `app/core/middleware.py` (idempotency, rate-limit headers), `app/services/{audit,secret_store,adapters,llm,mcp_client,webhooks,oauth}*`, routers `identity, catalog, connections, providers, workers, mcp_servers, knowledge, skills, policies, ops`, tests `tests/be1/**` |
| Backend engineer 2 (pipelines & runs) | routers `pipelines, documents, runs, ingest, review`, `app/services/{validator,storage,run_engine,event_bus,lifecycle}*`, tests `tests/be2/**` |
| Frontend engineer | `backend/docs/portal-integration-notes.md`, `backend/app/data/*.json` (exports from the portal), live-mode verification of `portal/` |
| QA | `tests/qa/**`, `tests/contract/**`, `docs/test-report.md` |

Public interfaces you may import from other owners: `app.core.security` (`AuthContext`, `get_auth`,
`require_scope`, `require_role`, `DEFAULT_WORKSPACE_ID`), `app.services.audit.record`, `app.db.session.get_db`,
`app.db.models.*`, `app.core.errors.*`, `app.core.pagination.paginate`, `app.core.ids.new_id`,
`app.services.catalog_data.*`. Do not change another owner's public signatures; add, don't break.

## Stack

Python 3.12+ · FastAPI · SQLAlchemy 2 (sync ORM, `Mapped[...]`) · Alembic · Pydantic v2 · pytest.
Request/response models: `app/schemas/generated.py` is **generated** from the contract
(`datamodel-codegen`, see README) — import from it, never edit it. Add hand-written schemas in your own module if needed.

## Database portability rules (PostgreSQL 16 **and** Microsoft SQL Server 2019+)

1. Only SQLAlchemy Core/ORM constructs — no raw SQL, no `postgresql.*`/`mssql.*` imports outside `app/db/types.py`.
2. JSON documents (graphs, configs, results) use `app.db.types.JSONType` (JSONB on PG, NVARCHAR(MAX) on MSSQL).
   Never filter/query inside JSON in SQL — load and filter in Python.
3. Timestamps use `app.db.types.UTCDateTime` (timestamptz on PG, DATETIMEOFFSET on MSSQL); always aware UTC.
4. Every indexed/unique/FK string column has an explicit length (MSSQL can't index NVARCHAR(MAX)). IDs: `String(40)`.
5. No `ON DELETE CASCADE` chains that create multiple cascade paths (MSSQL rejects them) — delete children in code.
6. Unique constraints on nullable columns: avoid (MSSQL treats NULLs as equal) — use a non-null column or check in code.
7. Case-insensitive collation on MSSQL: never rely on case to distinguish values. `new_id()` is lowercase.
8. Booleans → `Boolean`; enums → `String(32)` + validation in Pydantic (no native ENUM types).
9. Pagination via `app.core.pagination.paginate` (keyset on `created_at, id`).
10. Every tenant table has `workspace_id String(40) NOT NULL` + index; **every query filters by `auth.workspace_id`**.

## API conventions (from the contract)

- Prefix `/v1` (routers are mounted with it — declare paths without `/v1`).
- Errors: raise `ApiError(status, code, detail, **extra)` → `application/problem+json` with `code`, `issues[]`, `usages[]`.
- IDs: `new_id("con")`. Timestamps: RFC 3339 UTC (`app.core.time.iso`).
- Secrets are write-only: never return, log or audit secret material; responses carry `secret_hint` (`••••` + last 4).
- Drafts: `ETag` response header + `If-Match` required on PUT (428 without, 412 `etag_mismatch` on conflict).
- Idempotency-Key on POST creates is handled centrally by BE1's middleware (24 h replay window).
- Every write calls `audit.record(...)` in the same transaction. Commit explicitly in the route (`db.commit()`).
- Respect role/scope with `Depends(require_scope(...))` on every route.

## Tests

- `tests/conftest.py` (DB engineer) provides: `engine`, `db` (session), `client` (TestClient with the demo workspace
  seeded and `get_auth` overridden to an admin of `ws_default`), `raw_client` (no auth override),
  `auth_as(role|scopes)` helper. Test DB = env `DI_TEST_DATABASE_URL`.
- Use your own test database so parallel work doesn't collide:
  DB engineer `docintel_test_db`, BE1 `docintel_test_be1`, BE2 `docintel_test_be2`, QA `docintel_test_qa`
  (all exist on `postgresql+psycopg://di:di@localhost:5432/…`).
- Run: `cd backend && . .venv/bin/activate && DI_TEST_DATABASE_URL=postgresql+psycopg://di:di@localhost:5432/docintel_test_be1 pytest tests/be1 -q`

## Don'ts

- Don't commit to git (the lead commits). Don't touch `portal/` (frontend engineer only). Don't edit generated files.
- Don't start long-running servers and leave them running; use ports 8081 (BE1), 8082 (BE2), 8083 (DB), 8084 (QA) if you need one, and stop it.
