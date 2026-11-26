# doc-intel-api — Doc Intelligence Studio backend

FastAPI implementation of the contract in [`../api/openapi.yaml`](../api/openapi.yaml): all 95 operations.
It serves the portal (`../portal`, live mode) and external integrations (ingest, webhooks). It runs on **PostgreSQL 16**
or **Microsoft SQL Server 2019+** from the same models and Alembic migrations.

## Quick start (PostgreSQL)

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
docker compose up -d postgres                      # or any PostgreSQL 16
export DI_DATABASE_URL=postgresql+psycopg://di:di@localhost:5432/docintel
alembic upgrade head
DI_DEV_ANONYMOUS_ADMIN=true DI_BOOTSTRAP_API_KEY=dik_dev_admin uvicorn app.main:app --port 8080 --reload
```

The demo workspace `ws_default` is seeded on first start. The seed is the same workspace the portal's mock backend
uses, so deep links like `#builder-pl_ap` work. Start the portal against the backend like this:

```bash
cd ../portal && VITE_API_MODE=live VITE_API_BASE_URL=http://localhost:8080/v1 npm run dev
```

You can also switch the portal at runtime under **Settings → API access**. With `DI_DEV_ANONYMOUS_ADMIN=true`, a
request without a credential acts as the seeded admin; this is for development only and is ignored when
`DI_ENV=production`. Otherwise use `X-API-Key: dik_dev_admin`. To create more keys:
`python -m app.cli create-api-key --workspace ws_default --name "CI" --scopes admin`.

## SQL Server

```bash
docker compose up -d mssql mssql-init
# The ODBC Driver 18 for SQL Server must be installed on the host (msodbcsql18)
export DI_DATABASE_URL='mssql+pyodbc://sa:DocIntel_Dev_2026!@localhost:1433/docintel?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes'
alembic upgrade head && uvicorn app.main:app --port 8080
```

Enable `READ_COMMITTED_SNAPSHOT` on the database; the compose init script does this. Ready-made DDL for DBAs is in
`migrations/sql/{postgresql,mssql}_initial.sql`. See [`docs/database.md`](docs/database.md) for the portability rules.

## Configuration (`DI_*` environment variables, see `app/config.py`)

| Variable | Default | Purpose |
|---|---|---|
| `DI_DATABASE_URL` | local PostgreSQL | `postgresql+psycopg://…` or `mssql+pyodbc://…` |
| `DI_ENV` | `development` | `production` disables dev auth, anonymous admin and the null CORS origin |
| `DI_SECRET_KEY` | dev value | Master key for the local secret store (Fernet) and for signed tokens. **Must be set in production.** |
| `DI_CORS_ORIGINS` | portal dev and preview ports | Comma-separated list |
| `DI_CORS_ALLOW_NULL_ORIGIN` | `false` | Allows `Origin: null` (sandboxed preview iframe) outside production |
| `DI_BOOTSTRAP_API_KEY` | — | Seeds an admin API key (only its hash is stored) |
| `DI_DEV_AUTH` / `DI_DEV_ANONYMOUS_ADMIN` | `true` / `false` | HS256 dev bearer tokens / no-credential admin (development only) |
| `DI_OIDC_ISSUER`, `DI_OIDC_AUDIENCE`, `DI_OIDC_JWKS_URL` | — | OIDC bearer verification (Azure AD / Okta / Google) |
| `DI_RUN_MODE` | `simulate` | `simulate` mirrors the portal mock engine; `live` calls the real workers, model providers and MCP servers |
| `DI_RUN_STEP_DELAY_MS` | `350` | Pacing of simulated steps (`0` in tests) |
| `DI_STORAGE_DIR`, `DI_MAX_UPLOAD_MB` | `./var/storage`, `50` | Local object store for documents |

## Layout

```
app/
  main.py, config.py, cli.py
  core/        errors (problem+json), ids, time, pagination (keyset), security (API key, OIDC, dev JWT,
               sse_token, HMAC ingest), middleware (Idempotency-Key, RateLimit-*)
  db/          types (portable JSON/UTC types), base, session, models/, seed
  schemas/     generated.py — generated from api/openapi.yaml, never edit
  routers/     identity, catalog, connections, providers, workers, mcp_servers, knowledge, skills, policies, ops,
               pipelines, documents, runs, ingest, review
  services/    secret_store, audit, adapters/ (generic_pages_v1, excel_parser_v1, paddle_docparse_v1), llm,
               mcp_client, webhooks, validator (port of the portal's validate.ts), storage, run_engine, event_bus, lifecycle
  data/        catalogue, seed and sample documents exported from the portal (single source of truth)
migrations/    Alembic (PostgreSQL + MSSQL), sql/ offline DDL
tests/         db/, be1/, be2/, qa/, contract/
docs/          TEAM.md, database.md, be1-platform.md, be2-pipelines-and-runs.md, portal-integration-notes.md,
               integration-report.md, test-report.md
```

Regenerate the schemas after a contract change:

```bash
datamodel-codegen --input ../api/openapi.yaml --input-file-type openapi --output app/schemas/generated.py \
  --output-model-type pydantic_v2.BaseModel --target-python-version 3.12 --use-annotated --field-constraints \
  --use-standard-collections --use-union-operator --enum-field-as-literal all --collapse-root-models --use-double-quotes
```

## Tests

```bash
DI_TEST_DATABASE_URL=postgresql+psycopg://di:di@localhost:5432/docintel_test DI_RUN_STEP_DELAY_MS=0 pytest -q
```

The QA results are in [`docs/test-report.md`](docs/test-report.md).
