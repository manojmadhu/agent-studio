# 04 · Backend API specification (build later)

**Status:** specification only — the backend is a separate project and is not built yet.
**Machine-readable contract:** `api/openapi.yaml` (OpenAPI 3.1, validated with `openapi-spec-validator` — 66 paths, 95 operations, 97 schemas, zero unresolved `$ref`s, no duplicate `operationId`s).
**Consumers:** the Doc Intelligence Studio portal (React, `portal/`) and external systems (ingest, webhooks).

---

## 1. Project boundary

| | Frontend `doc-intel-portal` | Backend `doc-intel-api` (to build) |
|---|---|---|
| Repo | separate | separate |
| Runtime | Vite + React SPA, static hosting | Python 3.12 · FastAPI · Uvicorn |
| State | none durable (server is source of truth) | PostgreSQL 16 (metadata, graphs, runs, audit) |
| Queue / workers | — | Redis 7 + Celery/Arq (run orchestration), S3-compatible object store for documents |
| Secrets | never sees secret values | Vault / Azure Key Vault / AWS Secrets Manager via `SecretStore` interface |
| Contract | types generated from `openapi.yaml` (`openapi-typescript`) | routes implemented to `openapi.yaml`; contract tests with Schemathesis |

The portal runs against an in-app **mock adapter** (`VITE_API_MODE=mock`) until the backend exists, and switches to `VITE_API_MODE=live` + `VITE_API_BASE_URL` with no code changes.

## 2. Conventions

- Base path `/v1`; JSON everywhere; errors are `application/problem+json` (RFC 9457) with a stable `code` and, for graph errors, an `issues[]` list.
- Workspace scoping comes from the token/API key — never from the URL.
- Cursor pagination: `?limit=1..200&cursor=` → `{ data: [...], next_cursor }`.
- `Idempotency-Key` header on creates and ingest (24 h replay window).
- Drafts use `ETag` + `If-Match` (412 on conflict) so two builders cannot overwrite each other.
- IDs are prefixed (`pl_`, `run_`, `doc_`, `con_`, `prv_`, `wrk_`, `mcp_`, `key_`).
- Rate-limit headers: `RateLimit-Limit/Remaining/Reset`.

## 3. Authentication & authorization

| Caller | Mechanism | Header |
|---|---|---|
| Portal users | OIDC (Azure AD, Okta, Google) access token | `Authorization: Bearer <jwt>` |
| Services / scripts | Workspace API key with scopes, optional IP allow-list, expiry, rotation with grace period | `X-API-Key: dik_live_…` |
| Ingest webhooks | API key with `ingest:write` **or** HMAC-SHA256 over `timestamp + "." + body` (5-minute window) | `X-DI-Signature`, `X-DI-Timestamp` |
| Outbound webhooks | Backend signs every delivery the same way | `X-DI-Signature` |
| Run event stream (SSE) | short-lived `sse_token` from `GET /me` | `?access_token=` |

Roles: `admin` (everything), `builder` (pipelines, components, connections), `reviewer` (review queue, runs read), `viewer` (read-only). API-key scopes: `pipelines:read|write`, `runs:read|write`, `ingest:write`, `review:write`, `connections:write`, `admin`.

## 4. How every component connects — Connections

Every external component authenticates through a **Connection**: OCR/layout/image workers, AI model providers, MCP servers, vector stores, mailboxes, WhatsApp, databases, ERP/HTTP APIs.

```
Component (Provider | Worker | MCP server | Knowledge collection | pipeline step)
   └── connection_id ──► Connection { service, auth_type, settings, secret_hint, status }
                                └── secret material ──► SecretStore (vault) — never returned
```

Supported `auth.type` (discriminated union `AuthConfig`):

| type | Typical use | Write-only fields |
|---|---|---|
| `api_key` | Anthropic, OpenAI, Gemini, OCR workers (`placement` header/query, header `name`, optional `prefix`) | `key` |
| `bearer` | MCP servers, internal APIs | `token` |
| `basic` | SFTP, legacy ERP | `password` |
| `oauth2_client_credentials` | ERP APIs, MCP with OAuth | `client_secret` |
| `oauth2_authorization_code` | Gmail, Microsoft 365 mail (popup via `/connections/{id}/oauth/authorize`) | `client_secret` (+ refresh token held server-side) |
| `azure_ad_client` | Azure OpenAI, Azure AI Search, Blob | `client_secret` |
| `aws_sigv4` | S3, Bedrock-hosted workers | `secret_access_key`, `session_token` |
| `hmac` | signed webhooks to/from partners | `secret` |
| `mtls` | on-prem workers in a private network | `client_key_pem` |
| `none` | local dev workers | — |

Rules: secrets are write-only (`writeOnly: true`), responses carry only `secret_hint` (`••••Rk3w`); changing secrets = sending a full `auth` object (rotation, audited); `POST /connections/{id}/test` performs a harmless authenticated call; deleting a connection in use returns 409 with `usages[]` unless `force=true`. `GET /catalog/services` tells the portal which auth types and settings fields each service supports, so the connection form is data-driven.

### Component examples

**OCR worker (e.g., Paddle DocParse)**
```http
POST /v1/connections
{ "name": "Paddle OCR key", "service": "document_worker",
  "auth": { "type": "api_key", "key": "…", "placement": "header", "name": "x-api-key" } }

POST /v1/workers
{ "name": "Paddle DocParse", "kind": "ocr", "base_url": "https://ocr.internal:8443",
  "adapter": "paddle_docparse_v1", "connection_id": "con_4Pq9",
  "accepts": ["application/pdf","image/png","image/tiff"], "capabilities": ["text","layout","tables"] }

POST /v1/workers/wrk_paddle/health      → { "status": "healthy", "latency_ms": 41 }
POST /v1/workers/wrk_paddle/invoke      { "document_id": "doc_8f21" } → PagesResult
```

**Excel parser worker (existing service)** — `adapter: excel_parser_v1`, `invoke_path: /to-text`, `health_path: /health`. The adapter maps `{text_repr, sheet_names, total_cells, char_count}` to one text block per sheet.

**AI model provider**
```http
POST /v1/connections { "name": "Azure OpenAI – UAE", "service": "azure_openai",
  "settings": { "endpoint": "https://acme-uae.openai.azure.com", "api_version": "2025-04-01-preview" },
  "auth": { "type": "api_key", "key": "…", "name": "api-key" } }
POST /v1/providers { "kind": "azure_openai", "name": "Azure OpenAI", "connection_id": "con_…",
  "data_region": "UAE North", "models": [{ "id": "gpt-4o-prod", "supports_tools": true }] }
POST /v1/providers/{id}/test            → { "ok": true, "latency_ms": 380 }
POST /v1/providers/{id}/models/sync     (lists deployments / models from the provider)
```

**MCP server**
```http
POST /v1/connections { "name": "Vendor MCP token", "service": "mcp", "auth": { "type": "bearer", "token": "…" } }
POST /v1/mcp-servers { "name": "Vendor master", "transport": "streamable_http",
  "url": "https://mcp.acme.internal/vendors", "connection_id": "con_…" }
POST /v1/mcp-servers/{id}/discover      (initialize + tools/list, stores tools)
PATCH /v1/mcp-servers/{id} { "tools": [{ "name": "delete_vendor", "enabled": false }] }
POST /v1/mcp-servers/{id}/tools/lookup_vendor/call { "arguments": { "trn": "100…" } }
```

**Mail / messaging intake** — Gmail uses `oauth2_authorization_code` (`/oauth/authorize` → popup → `/oauth/callback`); WhatsApp Business uses `bearer` with `settings.phone_number_id`.

## 5. Worker output contract `docintel.pages.v1`

Every worker adapter returns `PagesResult`:
```json
{ "pages": [ { "n": 1, "width": 612, "height": 792, "language": "en",
    "blocks": [ { "text": "INVOICE", "bbox": [40,32,180,58], "confidence": 0.99 } ],
    "tables": [ { "bbox": [40,300,570,520], "rows": [["Item","Qty","Amount"]] } ],
    "key_values": [ { "key": "TRN", "value": "100…", "bbox": [ … ] } ] } ],
  "text": "…", "worker_latency_ms": 812 }
```
New workers either return this directly (`generic_pages_v1`) or get an adapter class in the backend (`excel_parser_v1`, `paddle_docparse_v1` — Paddle routes still to be confirmed from its source).

## 6. Resource map

| Area | Endpoints |
|---|---|
| Identity | `GET /me` · `/api-keys` CRUD + `rotate` · `/members` |
| Catalog | `GET /catalog/step-types` (palette + field specs) · `GET /catalog/services` |
| Pipelines | `/pipelines` CRUD · `duplicate` · `activate` · `pause` |
| Versions | `GET/PUT /pipelines/{id}/draft` (ETag) · `validate` · `/versions` list/publish · `restore` · `/templates` + `instantiate` |
| Documents | `POST /documents` (multipart, 50 MB) · page images (redacted by default) |
| Runs | `test-runs` · single-step `test` · `/runs` list/detail · **SSE** `/runs/{id}/events` · `result` (fields, checks, overlays) · `cancel` · `retry` |
| Ingest | `POST /ingest/{pipeline_id}` (API key or HMAC) |
| Review | `/review-items` list/detail · `claim` · `decision` (approve with corrections / reject / return) |
| Components | `/connections` · `/providers` · `/workers` · `/mcp-servers` · `/knowledge-collections` · `/skills` (versioned) · `/policies` |
| Ops | `/metrics/overview` · `/audit-events` · `/webhooks` · `/settings` |

## 7. Pipeline graph (what the builder saves)

```json
{ "nodes": [ { "id": "n3", "type": "ai.extract", "name": "Extract fields", "x": 620, "y": 140,
               "config": { "schema": [ { "name": "total_amount", "type": "number", "required": true } ] },
               "settings": { "retries": 2, "timeout_s": 60, "on_error": "stop" } },
             { "id": "c1", "type": "ai.model", "name": "Model", "x": 600, "y": 330,
               "config": { "provider_id": "prv_anthropic", "model": "claude-sonnet-5-5", "temperature": 0.1 } } ],
  "edges": [ { "id": "e2", "from": "n2", "from_port": "out", "to": "n3" },
             { "id": "s1", "from": "c1", "from_port": "plug", "to": "n3", "slot": "model" } ] }
```
Server-side validation (`ValidationIssue.code`): `missing_intake`, `no_incoming_connection`, `required_field`, `missing_model`, `missing_parser`, `invalid_schema_field`, `provider_disabled`, `loop`, `unknown_step_type`. Publishing and activation are blocked by any `error`.

## 8. Run lifecycle

`queued → running → (review) → completed | failed | cancelled`.
SSE events: `run.started`, `step.started`, `step.finished`, `step.failed`, `run.routed`, `run.finished` (resumable with `Last-Event-ID`). Test runs simulate deliver steps unless `deliver: true`. A run paused at Human review resumes on `POST /review-items/{id}/decision`.

## 9. Database outline (PostgreSQL)

`workspaces`, `members`, `api_keys (hash, prefix, scopes)`, `connections (secret_ref, secret_hint)`, `providers`, `provider_models`, `workers`, `mcp_servers`, `mcp_tools`, `knowledge_collections`, `skills`, `skill_versions`, `policies`, `pipelines`, `pipeline_drafts (graph jsonb, etag)`, `pipeline_versions (graph jsonb, immutable)`, `documents (object_key, sha256, retention_until)`, `runs`, `run_steps`, `run_results (jsonb)`, `review_items`, `webhooks`, `webhook_deliveries`, `audit_events (append-only)`. All tables carry `workspace_id` with row-level security.

## 10. Non-functional requirements

- Secrets encrypted at rest in the vault; DB holds only references; secret values never logged.
- PII redaction policy applied before documents or fields leave the platform; page images default to redacted.
- Data residency: providers/workers carry `data_region`; the `data_residency` policy blocks steps outside `allowed_regions` at validation time.
- Audit every write (`audit_events`), including connection rotation and review decisions.
- Targets: p95 control-plane API < 300 ms; SSE first event < 1 s; 99.9 % availability.

## 11. Build-later checklist (backend)

1. Scaffold FastAPI from `openapi.yaml` (e.g., `fastapi-code-generator`), keep the YAML as the source of truth.
2. Auth middleware (OIDC JWKS + API-key hash lookup + HMAC ingest).
3. `SecretStore` interface + vault implementation; Connections + `test`.
4. Catalog, pipelines, drafts/versions, server-side validator (port of the portal rules).
5. Worker adapters (`generic_pages_v1`, `excel_parser_v1`, `paddle_docparse_v1`), provider clients, MCP client.
6. Run orchestrator (queue, retries/timeout/on_error, SSE fan-out), review resume.
7. Metrics, audit, webhooks.
8. Contract tests: Schemathesis against `openapi.yaml`; the portal's live mode as an end-to-end smoke test.
