# BE1 — platform & components (auth, middleware, secrets, adapters, providers, MCP, ops)

Owner: Backend engineer 1. The module docstrings hold the details. This page lists the entry points and the decisions.

## Interfaces other modules use
| Function | Signature / behaviour |
|---|---|
| Ingest HMAC | `app.core.security.verify_ingest_hmac(request, body: bytes, secret: str) -> None`. It checks `X-DI-Signature` (hex, an optional `sha256=` prefix is allowed) = HMAC-SHA256(secret, `X-DI-Timestamp + "." + body`) within ±300 s. On failure it raises 401 `invalid_signature` or `signature_expired`. `hmac_context(ws, pipeline_id)` → AuthContext(kind="hmac", scopes {ingest:write}). `sign_payload(secret, ts, body)` |
| Tokens | `issue_dev_token(sub, ws=, role=, email=, name=, ttl_s=)` (HS256, secret_key) · `issue_sse_token(auth)` (10 min, only accepted on `GET /v1/runs/{id}/events?access_token=`) |
| Webhooks | `app.services.webhooks.emit(db, workspace_id, event_type, payload) -> list[delivery_id]`. Delivery starts after `db.commit()` in a background thread and is signed with `X-DI-Signature: sha256=…` + `X-DI-Timestamp`. Retries: 1 s, 5 s, 30 s |
| Worker adapters | `app.services.adapters.invoke(worker, auth, data, content_type, filename, *, options=None, settings=None) -> PagesResult dict` (raises UpstreamError 502) · `health(worker, auth) -> WorkerHealth` · `sample_pages(worker)` (simulate mode) |
| Credentials | `app.services.adapters.auth.connection_auth(db, ws, connection_id) -> (conn, settings, auth)` · `apply_auth(req_dict, auth, settings=)` |
| LLM | `app.services.llm.complete(db, provider, model_id, messages, *, json_schema=None, temperature=None, max_tokens=1024) -> {text, json, model, usage{input_tokens,output_tokens}, latency_ms, stop_reason}` |
| MCP | `app.services.mcp_client.discover(server, auth)` · `call_tool(server, auth, name, args) -> {content, is_error, latency_ms}` · `validate_arguments(schema, args)` |
| Secrets | `app.services.secret_store.put/get/rotate/delete(db, …)` (Fernet, `local:sec_…` refs) · `hint(v) -> "••••Rk3w"` |
| Audit | `app.services.audit.record(db, auth, action, resource_type, resource_id, summary=, details=)`. Secret-looking keys are dropped. The client IP comes from the middleware |

## Decisions
- **run_mode=simulate** (the default): connection, provider, worker and MCP test endpoints return the portal mock's deterministic results. **run_mode=live** makes real calls. Every outbound failure becomes `TestResult{ok:false}` or a 502 problem.
- **Body validation errors** in BE1 routers use the mock's code `validation` (422). Query-parameter errors still use the lead's `invalid_request` handler.
- **Scopes:** reads need any authenticated principal (audit events need `admin`). Connections writes need `connections:write`. Providers, workers, MCP servers, knowledge and skills writes need `pipelines:write`. Policies, webhooks, settings, API keys and members need `admin`.
- **Idempotency** (POST, PUT and PATCH with JSON bodies) is scoped by the credential. A replay carries `Idempotency-Replayed: true`. Reusing a key with a different body returns 422 `idempotency_key_reused`. A key whose first request is still running returns 409 `idempotency_in_progress`. Stored statuses: 2xx, 400, 404, 422.
- **Rate limits:** `RateLimit-*` headers are always sent. A 429 is returned only when `DI_RATE_LIMIT_ENFORCE=true` or in production.
- `DELETE /connections/{id}?force=true` clears `connection_id` on providers, workers, MCP servers and knowledge collections, and sets those collections to `status: failed` (API shows `connection_id: ""`). This relies on `knowledge_collections.connection_id` being nullable (migration `0002_kc_connection_nullable`, which drops and recreates the FK and index because SQL Server cannot alter a column that a foreign key references).
- `settings.dev_anonymous_admin` (outside production): a request with no credential becomes `usr_admin` of `ws_default`.

## CLI
`python -m app.cli create-api-key --workspace ws_default --name "Portal dev" --scopes admin` · `python -m app.cli dev-token --role builder`

## Known gaps
- Knowledge indexing only records documents and estimates chunk counts. It does not embed or upsert to the vector store.
- AWS `role_arn` assumption is not implemented.
- The Key Vault, AWS Secrets Manager and HashiCorp Vault backends are stubs.
- OIDC users must already exist as members.
- The rate limiter is in-memory and per process.
- Webhook retries are not durable across restarts: rows stay `pending`, and there is no sweeper.
