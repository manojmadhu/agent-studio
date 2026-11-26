# Database layer — doc-intel-api

Owner: DB engineer. Code: `app/db/` (types, base, session, models, seed, reset), `migrations/`, `alembic.ini`,
`tests/conftest.py`, `tests/db/`, `docker-compose.yml`.

One set of SQLAlchemy 2 models and one Alembic migration chain serve **PostgreSQL 16** and **Microsoft SQL Server
2019+**. PostgreSQL is the primary, fully tested engine. **SQL Server was verified in this environment only by DDL
compilation and offline (`--sql`) migration rendering. No SQL Server instance was available (no Docker daemon).**
Before the first MSSQL deployment, run `alembic upgrade head` and the test suite against a real instance
(`docker compose up -d mssql mssql-init`, then `DI_TEST_DATABASE_URL=mssql+pyodbc://… pytest tests`).

## 1. Tables (ERD summary)

Every tenant table has `workspace_id VARCHAR(40) NOT NULL` + index + FK → `workspaces.id` (no cascade). IDs are
`String(40)` with prefixes from `app.core.ids` (`IdMixin` fills `new_id(prefix)` by default; explicit ids, such as
the portal's seeded ones, are kept). Timestamps are `created_at` / `updated_at` (`UTCDateTime`, python default
`utcnow()`, server default `UtcNow()`). "JSON" means `JSONType` (a whole document, never queried in SQL).

| Table | Purpose | Key columns / notes |
|---|---|---|
| `workspaces` | Tenant root | `name`, `environment` (sandbox/production), `region`, `disabled` |
| `workspace_settings` | 1:1 settings (PK `workspace_id`) | `default_region`, `retention_days`, `allowed_regions` JSON, `sso` JSON, `extra` JSON. API `WorkspaceSettings` = `workspaces.{name,environment}` + this row |
| `members` | Users of a workspace | `email` (unique per workspace), `role`, `status`, `last_seen_at`, `external_subject` (OIDC, indexed, not unique) |
| `api_keys` | API keys | `prefix` (shown), `key_hash` = hex sha256 (unique), `scopes` JSON, `allowed_ips` JSON, `expires_at`, `revoked_at`, `last_used_at/_ip`, rotation grace: `previous_key_hash` + `previous_expires_at` |
| `idempotency_keys` | Idempotency-Key replay (BE1 middleware) | unique (`scope_hash`, `key`), `method`, `path`, `request_hash`, `state`, `status_code`, `response_body`, `response_headers` JSON, `expires_at` (indexed). Internal: `workspace_id` nullable, no FK |
| `secrets` | Local SecretStore ciphertext | `purpose`, `ciphertext` (Fernet, never plaintext), `version`, `key_id` |
| `connections` | Credentials + settings for services | `service`, `auth_type`, `settings` JSON (non-secret), `secret_ref`, `secret_hint`, `status`, `status_message`, `last_tested_at`, `last_used_at`, `expires_at` |
| `oauth_states` | Pending OAuth2 auth-code flows | `state_hash` (unique), `connection_id`, `code_verifier` (PKCE), `redirect_uri`, `expires_at`, `used_at` |
| `providers` | Model providers | `kind`, `enabled`, `base_url`, `connection_id`, `data_region`, `default_model`, `limits` JSON |
| `provider_models` | `Provider.models[]` | PK (`provider_id`, `model_id`), `position`, `display_name`, `context_window`, `supports_tools/vision`, `embedding`, `enabled` |
| `workers` | Document/image workers | `kind`, `base_url`, `adapter`, `invoke_path`, `health_path`, `connection_id`, `accepts`/`capabilities` JSON, `timeout_s`, `max_concurrency`, `data_region`, `health` JSON (WorkerHealth) |
| `mcp_servers` | MCP servers | `transport`, `url`, `connection_id`, `headers` JSON (non-secret), `enabled`, `status`, `last_discovered_at` |
| `mcp_tools` | `McpServer.tools[]` | PK (`server_id`, `name`), `position`, `description`, `input_schema` JSON, `enabled` |
| `knowledge_collections` | Vector collections | `connection_id`, `embedding_provider_id`, `embedding_model`, `chunk_size`, `document_count`, `chunk_count`, `status` |
| `knowledge_documents` | Files added to a collection | `collection_id`, `document_id`, `filename`, `status`, `chunk_count` |
| `skills` | Skill head | `description`, `instructions`, `version` (latest) |
| `skill_versions` | Immutable `Skill.versions[]` | PK (`skill_id`, `version`), `instructions`, `release_note`, `published_at/_by` |
| `policies` | Guardrails | `kind`, `enabled`, `scope` JSON, `config` JSON |
| `pipelines` | Pipeline head | `status` (active/paused/draft), `active` (accepting intake), `live_version`, `owner_team`, `intake_type`, `kpi` JSON (cached snapshot), `ingest_secret_ref`, `updated_by`, `archived_at` |
| `pipeline_drafts` | 1:1 editable draft (PK `pipeline_id`) | `graph` JSON, `etag` (full header value, e.g. `W/"pl_ap-1"`), `changes` JSON, `issues` JSON, `saved_by`; `saved_at` = synonym of `updated_at` |
| `pipeline_versions` | Immutable published graphs | unique (`pipeline_id`, `version`), `graph` JSON, `release_note`, `published_at/_by`. `live` is derived |
| `templates` | Pipeline templates (per workspace) | `graph` JSON, `intake_type`, `step_count`, `ai_component_count` |
| `documents` | Uploaded/ingested files | `filename`, `mime_type`, `size_bytes`, `pages`, `sha256`, `object_key`, `source`, `metadata` JSON (attribute `meta`), `redacted`, `retention_until`; `uploaded_at` = synonym of `created_at` |
| `runs` | Executions | `pipeline_id`, `pipeline_version` (NULL = draft → API `"draft"`), `document_id`, `status`, `test`, `deliver`, `trigger`, `graph` JSON (executed snapshot), `input` JSON, `started_at`, `finished_at`, `duration_ms`, `field_count`, `error` JSON (Problem), `retry_of`, `cancel_requested` |
| `run_steps` | `RunDetail.steps[]` | unique (`run_id`, `node_id`, `attempt`), `seq`, `status`, timings, `output_port`, `output`/`usage`/`error` JSON |
| `run_events` | Append-only SSE log | unique (`run_id`, `seq`). `seq` is the SSE `id:`, and resume = `seq > Last-Event-ID`. `event`, `node_id`, `payload` JSON (the wire RunEvent); `at` = synonym of `created_at` |
| `run_results` | `RunResult` (PK `run_id`) | `classification`, `fields`, `checks`, `path`, `overlays`, `corrections` (all JSON) |
| `review_items` | Human review queue | `run_id`, `pipeline_id`, `document_id`, `node_id`, `reason`, `reason_code`, `queue`, `assignee` (display), `assignee_id`, `sla_due_at`, `status`, `decision`, `corrections` JSON, `comment`, `decided_by/_at` |
| `webhooks` | Subscriptions | `url`, `events` JSON, `enabled`, `pipeline_ids` JSON, `secret_ref` (signing secret), `secret_hint` |
| `webhook_deliveries` | Outbox/attempts | `webhook_id`, `event`, `event_id`, `payload` JSON, `status`, `attempt`, `next_attempt_at` (index with status), `response_status`, `last_error`, `delivered_at` |
| `audit_events` | Append-only audit trail | `actor` JSON {kind,id,name}, `actor_id`, `action`, `resource_type`, `resource_id`, `summary`, `ip`, `details` JSON; `at` = synonym of `created_at` |

**Derived API fields (not stored, computed in code):** `usage_count` (connections, providers, MCP servers,
skills: computed by scanning graphs), `Pipeline.draft_changes` (= `pipeline_drafts.changes`),
`Pipeline.step_count` / `ai_component_count` (from the graph), `VersionSummary.live`, `Document.uploaded_at`,
`AuditEvent.at`, `Draft.saved_at` (synonyms), `KnowledgeCollection` counts are stored.

**Indexes:** `(workspace_id, created_at)` on every listed resource (keyset pagination), plus `status`,
`pipeline_id`, `run_id`, `(workspace_id, status|queue)` on runs and review items, `(status, next_attempt_at)` on
deliveries, `(resource_type, resource_id)` on audit, `(workspace_id, sha256)` on documents.

## 2. Portable types (`app/db/types.py`)

| Type | PostgreSQL | SQL Server | Notes |
|---|---|---|---|
| `JSONType` | `JSONB` | `NVARCHAR(max)` + json serialisation | `None` → SQL NULL everywhere. **Mutation is not tracked**: reassign the whole value (`draft.graph = new`) or call `flag_modified(obj, "graph")` |
| `UTCDateTime` | `TIMESTAMP WITH TIME ZONE` | `DATETIMEOFFSET` | Always returns aware UTC. Naive input is assumed UTC |
| `LongText` | `TEXT` | `NVARCHAR(max)` | Never `NTEXT` (what `UnicodeText` renders when the server version is unknown) |
| `Unicode(n)` | `VARCHAR(n)` | `NVARCHAR(n)` | Used for human text (names, descriptions, filenames, reasons) so Arabic etc. survives on MSSQL |
| `String(n)` | `VARCHAR(n)` | `VARCHAR(n)` | IDs, enums, hashes, URLs (ASCII) |
| `Boolean` | `BOOLEAN` | `BIT` | |
| `UtcNow()` | `now()` | `SYSDATETIMEOFFSET()` | `server_default` |

## 3. Switching database

```bash
# PostgreSQL (default)
export DI_DATABASE_URL=postgresql+psycopg://di:di@localhost:5432/docintel
# SQL Server (pyodbc + Microsoft ODBC Driver 18)
export DI_DATABASE_URL='mssql+pyodbc://sa:DocIntel_Dev_2026!@localhost:1433/docintel?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes'
```

Local servers: `docker compose up -d postgres` or `docker compose up -d mssql mssql-init` (creates `docintel` and the
test DBs, enables `READ_COMMITTED_SNAPSHOT`).

**ODBC Driver 18** (needed by `pyodbc` for MSSQL; not installed in this environment):

```bash
# Debian/Ubuntu
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | sudo gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg
curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list | sudo tee /etc/apt/sources.list.d/mssql-release.list
sudo apt-get update && sudo ACCEPT_EULA=Y apt-get install -y msodbcsql18 unixodbc-dev
# macOS: brew tap microsoft/mssql-release https://github.com/Microsoft/homebrew-mssql-release && brew install msodbcsql18
```

Driver 18 encrypts by default. For self-signed dev servers add `TrustServerCertificate=yes`. In production use a
trusted certificate and `Encrypt=yes`.

Engine settings (`app/db/session.py: make_engine`): `pool_pre_ping`, `isolation_level="READ COMMITTED"`,
`pool_size = max_overflow = DI_DB_POOL_SIZE`, and `fast_executemany=True` for `mssql+pyodbc`. On SQL Server, enable
`READ_COMMITTED_SNAPSHOT` on the database so readers (SSE polling, lists) don't block on the run engine's writes.
PostgreSQL behaves like this by default.

## 4. Sessions

- Routes: `db: Session = Depends(get_db)`. Commit explicitly. Uncommitted work is rolled back when the request ends.
- Background code (run engine, webhook dispatcher, startup): **never import `SessionLocal`**. Use
  `from app.db.session import session_factory, session_scope`. Then either
  `with session_scope() as db: …` (commit/rollback/close), or `with session_factory()() as db: …`.
  Tests redirect it with `bind_session_factory(factory)`, and `bind_session_factory(None)` restores the default.
- `make_engine(url, **kw)` builds an engine with the same defaults, for tests and tools.

## 5. Migrations

```bash
cd backend && . .venv/bin/activate
alembic upgrade head                      # uses DI_DATABASE_URL (or -x url=…)
alembic downgrade -1
alembic revision --autogenerate -m "add foo"   # run against PostgreSQL
alembic check                             # models vs DB drift
DI_DATABASE_URL='mssql+pyodbc://u:p@h/db?driver=ODBC+Driver+18+for+SQL+Server' alembic upgrade head --sql > mssql.sql
```

After every autogenerate, **hand-review the revision for MSSQL** (checklist in `migrations/script.py.mako`):

- Replace `server_default=sa.text('now()')` with `server_default=dbt.UtcNow()`.
- Make sure types render as `dbt.JSONType()`, `dbt.UTCDateTime()` or `dbt.LongText()` (env.py `render_item` does this), with no `postgresql.*` imports.
- Give every indexed, unique or FK string column a length. Use no `ondelete="CASCADE"`, no `sa.Enum`, and no unique constraints on nullable columns.
- Keep `ALTER COLUMN` changes simple. SQL Server needs explicit `existing_type`/`existing_nullable`, and you must drop and recreate indexes and default constraints around type changes.

### Resetting a database (I-8)

Chosen recipe: **drop every table, then rebuild through Alembic**. It is portable and needs no raw SQL:

```bash
DI_DATABASE_URL=… python -m app.db.reset               # drop all tables (reflected, incl. alembic_version) → alembic upgrade head
DI_DATABASE_URL=… python -m app.db.reset --create-all  # drop all → metadata.create_all → alembic stamp head
```

`app.db.reset` refuses to run when `DI_ENV=production`. For a DB that was only ever built by migrations,
`alembic downgrade base && alembic upgrade head` also works. A DB built with a bare `create_all` (no
`alembic_version`) fails that with `DuplicateTable`. This was the I-8 failure, and `python -m app.db.reset` fixes it.
Manual fallback: on PostgreSQL run `DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION di;`, and on SQL Server drop and
recreate the database. Then run `alembic upgrade head`.

`tests/conftest.py` uses `rebuild(engine, create_all=True)` once per session. The test DB always carries an
`alembic_version` row equal to head, so `alembic upgrade head` / `downgrade base` keep working on it
(`tests/db/test_migrations.py::test_test_db_is_stamped_at_head`).

Then regenerate the DBA copies `migrations/sql/postgresql_initial.sql` / `mssql_initial.sql` (or add a new
`*_<rev>.sql`). `tests/db/test_migrations.py` runs upgrade → downgrade → upgrade + drift check on PostgreSQL
in a throw-away schema. `tests/db/test_mssql_ddl.py` compiles every table and index for MSSQL, renders the offline
T-SQL migration, and enforces the portability rules below.

## 6. Portability rules (summary of TEAM.md)

1. ORM/Core only, with no raw SQL. Dialect types live only in `app/db/types.py`.
2. JSON via `JSONType`. Never filter inside JSON in SQL.
3. Timestamps via `UTCDateTime`, always aware UTC.
4. Explicit lengths on indexed, unique or FK strings (`ID_LEN=40`, `NAME_LEN=200`, …). Index keys must stay ≤ 1700 bytes (NVARCHAR = 2 B/char).
5. No cascade chains. Delete children in code, in FK order.
6. No unique constraints on nullable columns.
7. Case-insensitive MSSQL collation: don't let values differ only by case.
8. `Boolean` and `String(32)` enums only.
9. Paginate with `app.core.pagination.paginate` (keyset on `created_at, id`).
10. Every query filters by `workspace_id`.
11. Constraint names come from the naming convention in `app/db/base.py` and stay ≤ 63 chars (tested).

## 7. Seed (`app/db/seed.py`)

- `seed_if_empty(db)` does nothing when workspace `ws_default` exists. The app calls it on startup when `DI_SEED_ON_STARTUP=true`.
- `seed(db)` inserts the demo workspace **ws_default** and commits. The workspace is named after `seed.json`
  `settings.name` ("Example Trading"). The data comes from `app/data/seed.json`, exported from the portal mock.
- It keeps the portal's ids (`pl_ap`, `con_gmail`, `rv1`, `run_48210`, `tpl_ap`, …). Timestamps are shifted by
  `utcnow() - _meta.reference_now`. Draft ETags are `W/"<pipeline_id>-<rev>"`.
- **List order (I-3):** seeded `created_at` values are staggered 1 s apart in seed-array order, so every list
  matches the mock's order (checked against `mock_golden_responses.json` in `tests/db/test_seed.py`). The
  direction follows each router's ordering. Keyset lists using (created_at DESC, id DESC), such as connections, get the
  earliest-listed row newest. Lists ordered (created_at, id) ASC (pipelines, templates, policies, providers, workers,
  MCP servers, knowledge, skills, members, webhooks) get it oldest. All values are ≤ now, so rows created later through
  the API sort after or before the seed as expected. API keys and runs keep their real timestamps.
- **Admin (I-2):** member `usr_admin` (role admin) carries the mock principal's (`me`) name and email ("Manoj M.",
  manoj@example.com). The mock's `usr_manoj` is therefore not seeded separately. It is followed by `usr_sara`
  and `usr_lina`. `usr_admin` is the principal used by `dev_anonymous_admin` and by the test fixtures.
- It seeds the sample documents `doc_inv_sample` / `doc_rcp_sample`. Their bytes are synthesised and stored through
  `app.services.storage` when available. Otherwise only the rows are created. Run and review documents are seeded as rows only.
- Review items reference runs that are paused in review. Those runs are created with `status="review"` and their
  `run_results`.
- **Secrets:** the mock has none. Each connection that needs one gets a non-functional placeholder credential
  stored via `app.services.secret_store.put(db, workspace_id, payload) -> secret_ref` when that module exists.
  Otherwise it gets `secret_ref = "seed:placeholder"` (treat this as "no usable secret"). `secret_hint` comes from the mock.
  Plaintext never reaches a table.
- API keys from the mock get the hash of a random value, so they can never authenticate.
  `DI_BOOTSTRAP_API_KEY` is seeded as `key_bootstrap` with scope `admin`. Only its sha256 and prefix are stored.
- CLI: `python -m app.db.seed`.

## 8. Tests (`tests/conftest.py`)

`DI_TEST_DATABASE_URL` (default `postgresql+psycopg://di:di@localhost:5432/docintel_test_db`) is also exported as
`DI_DATABASE_URL` before the app is imported. `DI_STORAGE_DIR` defaults to a temp dir.

- `engine` (session scope): drop every table, run `create_all` and `alembic stamp head` (`app.db.reset.rebuild`), then seed. It also binds `session_factory()` and patches `SessionLocal`.
- `clean_db`, `db`, `client` (admin of ws_default, lifespan runs), `raw_client` (separate app instance with no auth override), and `auth_as(role=…, scopes=…, **fields)`.
- **Isolation: "reset when dirty".** A cursor hook flags any INSERT/UPDATE/DELETE. The next test that uses the DB
  then deletes all rows (reverse FK order, which is portable) and re-seeds, in about 0.1 s. Read-only tests cost nothing. Tests may commit
  freely, and background threads see the same data.

## 9. Row-level isolation

Isolation is enforced **in the application**: every tenant table carries `workspace_id`, and every query filters by
`auth.workspace_id` (rule 10). Cross-workspace FKs are not prevented by the DB, so routes must check that referenced
ids belong to the caller's workspace.

Future hardening (defence in depth, not implemented):

- **PostgreSQL RLS:** `ALTER TABLE … ENABLE ROW LEVEL SECURITY` + a policy
  `USING (workspace_id = current_setting('app.workspace_id'))`, with `SET LOCAL app.workspace_id = …` issued per
  transaction from `get_db` (a session `after_begin` event). The app role must not own the tables, or must use `FORCE ROW LEVEL SECURITY`.
- **SQL Server:** `CREATE SECURITY POLICY` with an inline predicate function comparing `workspace_id` to
  `SESSION_CONTEXT(N'workspace_id')`, set via `sp_set_session_context` per connection checkout.

Both would live in a dialect-conditional migration (`op.get_context().dialect.name`).
