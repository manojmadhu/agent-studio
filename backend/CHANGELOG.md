# Changelog — doc-intel-api

## 1.0.0 — 2026-11-26

First release of the backend for Doc Intelligence Studio, implementing `api/openapi.yaml`.

### Added
- **All 95 contract operations**:
  - Identity, API keys, Members, Catalog, Connections, Providers, Workers, MCP servers, Knowledge, Skills, Policies,
    Metrics, Audit, Webhooks and Settings (61 operations, backend engineer 1).
  - Pipelines, Versions, Templates, Documents, Runs, Ingest and Review (34 operations, backend engineer 2).
- **Database portability**:
  - The same SQLAlchemy models and Alembic migrations run on PostgreSQL 16 and SQL Server 2019+.
  - Portable `JSONType`, `UTCDateTime` and `LongText` types.
  - 31 tables.
  - Offline DDL for DBAs in `migrations/sql/`.
  - `docker-compose.yml` for both engines (DB engineer).
- **Auth**:
  - Workspace API keys (sha256-hashed, scopes, expiry, IP allow-list, rotation with a grace period).
  - OIDC bearer tokens via JWKS.
  - Dev HS256 tokens.
  - Short-lived `sse_token`.
  - HMAC-signed ingest.
  - Anonymous dev admin (`DI_DEV_ANONYMOUS_ADMIN`; development only).
- **Cross-cutting**: problem+json errors with the portal mock's codes, `Idempotency-Key` replay (24 h),
  `RateLimit-*` headers, keyset pagination, weak draft ETags with `If-Match` (412/428), an audit trail on every write,
  signed outbound webhooks, and CORS for the portal.
- **Secrets**: a write-only, Fernet-encrypted local `SecretStore`. Responses carry only `secret_hint`. Vault backends
  are stubbed behind the interface.
- **Components**: worker adapters (`generic_pages_v1`, `excel_parser_v1`, `paddle_docparse_v1`), model provider clients
  (Anthropic, OpenAI, Azure OpenAI, Gemini, Ollama, OpenAI-compatible) and an MCP client (streamable HTTP and SSE).
  Every outbound call is guarded and testable with an injected transport.
- **Runs**:
  - A server-side validator that is a 1:1 port of the portal's `validate.ts`.
  - An in-process run engine:
    - `simulate` mode is event-for-event identical to the portal mock;
    - `live` mode calls the real workers and models, with retries, timeouts and `on_error` honoured.
  - An SSE event stream with `Last-Event-ID` resume.
  - Human review that pauses and resumes the run.
- **Demo data**: the seed workspace, catalogue and sample documents are exported from the portal (`app/data/*.json`),
  so the portal's deep links and demo data work in live mode.
- **Tests**: 324 pytest tests:
  - db 96, be1 112, be2 80;
  - QA scenarios 11;
  - golden replay of 74 mock responses and 4 SSE transcripts;
  - 20 robustness regressions.
  - Schemathesis contract run: 95 of 95 operations, 0 response-schema violations.

### Fixed during integration and QA
- I-1: force-deleting a connection that knowledge collections use now succeeds; migration `0002` makes
  `knowledge_collections.connection_id` nullable.
- I-2 and I-3: seed names and list order now match the mock. I-8: there is a clean reset (`python -m app.db.reset`).
- D-01 to D-04: values that are too long, out of range, contain NUL, or are malformed graph nodes now return 422 or are
  dropped instead of causing a 500.
- D-05: malformed JSON returns 400 `bad_request` everywhere.
- D-06: a stale cursor returns 400 `invalid_cursor`.
- D-07: incremental DBA SQL for migration 0002 is added.

### Known gaps
See `docs/test-report.md` §7 and the engineers' docs. Summary:
- SQL Server is verified offline only.
- Knowledge collections don't embed or index documents yet.
- The queue is in-process; Redis/Celery comes later.
- Contract gaps C-01 to C-08 still need updates to the YAML.
