# QA test report: doc-intel-api

Date: 2026-11-26 · Author: QA · Scope: `backend/` (95 operations of `api/openapi.yaml`) and the portal in live mode.
QA made **no application code changes** (no one-line fixes either). New QA files: `tests/qa/**`, `tests/contract/**`
and this report.

## 1. Summary

| Suite | Cases | Passed | Failed | Skipped / xfail | Notes |
|---|---:|---:|---:|---:|---|
| `tests/db` (DB engineer) | 96 | 96 | 0 | 0 | includes the 67 parametrised cases of `test_mssql_ddl.py` |
| `tests/be1` (BE1) | 112 | 112 | 0 | 0 | |
| `tests/be2` (BE2) | 80 | 80 | 0 | 0 | |
| `tests/qa` (QA scenarios, black-box) | 11 | 11 | 0 | 0 | QA-01 … QA-11 |
| `tests/contract` (golden replay) | 5 | 5 | 0 | 0 | 1 HTTP replay of 74 recorded responses + 4 SSE transcripts |
| `tests/contract/test_robustness.py` | 20 | 20 | 0 | 0 | regression tests for D-01 … D-06 (all fixed, see § 4) |
| **pytest total** | **324** | **324** | **0** | **0** | 34 s, `DI_RUN_STEP_DELAY_MS=0`, PostgreSQL 16 (re-run after the defect fixes) |
| Schemathesis 4 (CLI, uvicorn :8084) | 4 695 generated, 95/95 ops | — | 163 unique failures in 75 ops | — | 17 ops return 500 (D-01…D-04); 143 undocumented statuses (contract gaps C-01…C-03); 1 SSE false positive (C-04); 0 response-schema violations, 0 content-type violations |
| Portal live smoke (`e2e/smoke.py`) | 43 | 42 | 1 | 0 | the only failure is the known intentional 409 that shows as a console error (I-10) |
| MSSQL offline checks | 4 | 4 | 0 | — | runtime on SQL Server **not verified** (§ 5) |

Existing suites before QA files were added: 288 passed (db 96, be1 112, be2 80), 0 failed, 0 skipped.

Commands:

```bash
cd backend && . .venv/bin/activate
export DI_TEST_DATABASE_URL=postgresql+psycopg://di:di@localhost:5432/docintel_test_qa DI_RUN_STEP_DELAY_MS=0
pytest tests -q                                                     # all suites
PYTHONPATH=$PWD pytest tests -q -p tests.contract.op_coverage_plugin  # + operation coverage → /tmp/qa_op_coverage.json
tests/contract/run_schemathesis.sh                                  # against uvicorn :8084 (see the script header)
```

## 2. Environment

| Item | Version |
|---|---|
| OS | Linux 6.18 (container), no Docker daemon, no SQL Server, no ODBC Driver 18 |
| Python / FastAPI / Starlette / Pydantic | 3.13.18 / 0.165.0 / 1.10.0 / 2.14.4 |
| SQLAlchemy / Alembic / psycopg / pyodbc | 2.1.9 / 1.20.1 / 3.3.9 / 5.5.0 (installed, no driver) |
| pytest / Hypothesis / Schemathesis / jsonschema-rs | 9.2.0 / 6.170.17 / 4.34.2 / 0.63.1 |
| Database | PostgreSQL 16.15, DB `docintel_test_qa`, reset with `python -m app.db.reset` before each server run |
| Node / npm / Vite | 22.23.5 / 10.9.9 / 7.3.8 |
| Browser | Chrome for Testing 153.0.8010.12 via Playwright 1.63.0 |
| Backend config | `DI_RUN_MODE=simulate`, `DI_BOOTSTRAP_API_KEY=dik_test_admin`, `DI_RUN_STEP_DELAY_MS=0` (contract run), default delay for the smoke |

## 3. Test case catalogue

### 3.1 QA functional scenarios (`tests/qa/test_scenarios.py`)

All scenarios go through HTTP only (in-process ASGI `TestClient`, **no auth override**). Credentials: the bootstrap key
`dik_test_admin`, keys created with `POST /v1/api-keys`, and HS256 dev bearer tokens (the dev stand-in for the IdP)
for roles and for a second workspace. Every response a scenario sees is recorded and validated against the contract
(schema + `application/problem+json` on errors). Undocumented error statuses are tolerated as contract gap C-01.

| ID | Area | Scenario | Expected | Result |
|---|---|---|---|---|
| QA-01 | Builder | create pipeline → GET draft (ETag header = body) → PUT with If-Match → stale If-Match → no If-Match → validate invalid graph → publish/activate refused → PUT valid graph → validate → publish v1 → versions list/get → activate → pause | 201; new ETag per save; 412 `etag_mismatch` with `current_etag`; 428 `precondition_required`; `valid:false` + issues `{level,message}`; 422 on publish/activate; v1 201; `active` → `paused` | Pass |
| QA-02 | Runs / SSE | test run of `pl_ap` on `doc_inv_sample`, stream `/events` to completion, resume with `Last-Event-ID`, `/result`, SSE without credentials | 202; `run.started` … `run.finished`; ids 1..n contiguous; every event has `run_id`, `at`; resume returns only the tail; result has fields; 401 problem | Pass |
| QA-03 | Ingest / review | gate raised to 0.9 + publish; ingest with an `ingest:write` API key; ingest with HMAC (valid / bad / expired / missing); key without scope; paused pipeline; run pauses at review; reviewer A claims, reviewer B claims; A approves with `total_amount` correction; second decision | 202 / 202 / 401 `invalid_signature` / 401 `signature_expired` / 401 / 403 / 409; run `review`; item `open` → `claimed`; B gets 409; decision 200 `approved`; second decision 409; run `completed`; result `total_amount = 13020` | Pass |
| QA-04 | Connections | create (`api_key` secret), get, list, test, rotate via PATCH `auth`, use from a provider, delete → 409 `connection_in_use` with `usages`, `force=true` → 204, provider `connection_id` cleared; I-1 regression: `con_qdrant` force delete; audit list. Scan **every** response body and header for both raw secrets | hint `••••` + last 4, changes on rotation; 409 lists the provider; 204; 404 afterwards; `connection_id` null/""; neither secret appears anywhere | Pass |
| QA-05 | API keys | create `pipelines:read` key (secret once, not in list); read OK, write 403; rotate with grace 0 → old 401, new OK; rotate with grace 1 h → previous stays valid; revoke → 204, both secrets 401; unknown key 401 | as described | Pass |
| QA-06 | Roles | viewer: reads OK, `/me` role viewer; creates/edits pipelines, connections, review claim/decision, API keys → 403. Reviewer: draft PUT, PATCH pipeline, publish, delete → 403; review decision → 200 | as described | Pass |
| QA-07 | Isolation | admin of `ws_qa_other` reads/writes 15 `ws_default` ids (pipeline, draft, versions, run, result, events, review item, connection, usage, provider, worker, MCP server, skill), PATCH, force DELETE, claim, test run, ingest; lists | 404 problem for every id; no foreign ids in lists; the PATCH did not change the pipeline. Sample documents are virtual in every workspace (BE2 decision), so the page image may return 200 | Pass |
| QA-08 | Pagination | 7 extra pipelines; walk `/pipelines` (2), `/runs` (3), `/connections` (4), `/audit-events` (3), `/api-keys` (1) by `next_cursor`; bad cursor; `limit=0` | walk = full list, same order, no duplicates; 400 `invalid_cursor` on the keyset lists; 422 | Pass (pipelines/review-items ignore a bad cursor → D-06) |
| QA-09 | Idempotency | POST /pipelines twice with one `Idempotency-Key`; same key, other body; other principal, same key | same id + `Idempotency-Replayed: true`; 422 `idempotency_key_reused`; one row; other principal gets its own pipeline | Pass |
| QA-10 | Upload | `max_upload_mb=1`, upload 1 MB + 1 byte; small PDF | 413 `file_too_large` problem; 201 then GET 200 | Pass |
| QA-11 | CORS | preflight from `http://localhost:5173` for PUT draft with `if-match, content-type, x-api-key, idempotency-key`; actual GET; foreign origin | 200, origin echoed, methods/headers allowed; `Access-Control-Expose-Headers` contains `ETag`; foreign origin not allowed | Pass |

### 3.2 Contract checks

| ID | Check | Expected | Result |
|---|---|---|---|
| CT-01 | Schemathesis 4.34.2, `api/openapi.yaml`, uvicorn :8084, `X-API-Key: dik_test_admin`, `--max-examples 10 --seed 4242`, phases examples+coverage+fuzzing, checks `not_a_server_error, status_code_conformance, content_type_conformance, response_schema_conformance`. **No operation excluded** (destructive calls hit the QA DB only). Stateful phase not run (budget) | no 5xx; every status documented; problem+json on errors; responses match schemas | **Fail**: 95/95 ops tested, 4 695 cases. 17 ops → 500 (D-01…D-04); 2 ops → 502 by design (simulated upstream failure, C-03); 143 undocumented statuses (C-01…C-03); 1 SSE finding = contract formulation (C-04). **0** response-schema and **0** content-type violations |
| CT-02 | `test_golden_responses.py::test_golden_http_responses_status_and_shape`: replay all 74 recorded requests of `mock_golden_responses.json` in recorded order (GETs, validate, step test, invoke/health/test, MCP call, 409/412/422/428 cases, draft PUTs, publish, test-run) | same status and content type; no key of the mock missing in live (all array items); no type change; live response valid against the contract | Pass. Contract findings only C-01 (MCP call 422) and C-02 (428) |
| CT-03 | `test_golden_sse_transcripts` × 4 (`pl_ap`×2 docs, `pl_exp`, `pl_kyc`) | same create shape, identical (event, node_id, status) sequence, result shape | Pass |
| CT-04 | `test_robustness.py` (20 cases) | 4xx problem, never 500 | Pass after the fixes (originally xfail: defects D-01…D-06 reproduced) |

### 3.3 MSSQL checks (§ 5) and portal smoke

| ID | Check | Result |
|---|---|---|
| MS-01 | `alembic upgrade head --sql` with an `mssql+pyodbc` URL | Pass: 947 lines, 0001 + 0002 rendered |
| MS-02 | Compile `CreateTable` / `CreateIndex` for all 31 tables / 70 indexes with the mssql dialect | Pass |
| MS-03 | `tests/db/test_mssql_ddl.py` | 67 passed |
| MS-04 | Scan of the generated T-SQL for risky constructs | Pass, with notes (§ 5) |
| SM-01 | Portal `VITE_API_MODE=live` build + preview :4173 against backend :8080 (`DI_DEV_ANONYMOUS_ADMIN=true`, reset DB), `e2e/smoke.py` (run from a copy in /tmp so `portal/e2e/shots` is untouched) | 42/43. FAIL "no console errors" = the intended 409 of the delete-in-use step (known I-10). Backend log: 74×200, 2×201, 1×202, 1×409, no 5xx |

## 4. Defects

Severity: High = blocks a portal flow or leaks data · Medium = 500 on reachable input, wrong state · Low = inconsistency.
No High defects were found. **Status (2026-11-26, after the QA run): D-01 … D-07 fixed by the lead**, verified by
re-running the full suite (324 passed) with the strict-xfail markers removed:

- D-01 / D-02 / D-03: a `sqlalchemy.exc.DataError` (or a driver `ValueError`, e.g. NUL on PostgreSQL) now maps to
  422 `validation` problem+json in `app/core/errors.py`; the request's transaction is rolled back. SQL Server raises
  DataError (SQLSTATE 22001/22003) for truncation and overflow, so the mapping is the same there. D-03 still diverges by
  database (SQL Server stores NUL characters PostgreSQL rejects); input-level NUL rejection remains a follow-up.
- D-04: `validator.normalize_graph` drops nodes whose `id`/`type` is not a string (like unknown step types) and ignores
  non-object `config`/`settings` — no more 500.
- D-05: malformed JSON returns 400 `bad_request` in every router.
- D-06: `paginate_list` returns 400 `invalid_cursor` for an unknown or stale cursor.
- D-07: `migrations/sql/{mssql,postgresql}_0002_kc_connection_nullable.sql` added (offline-generated).

The table below is the original finding as reported.

| ID | Sev. | Owner | Steps to reproduce | Expected | Actual |
|---|---|---|---|---|---|
| D-01 | Medium | BE1 (providers, workers, policies, connections, skills), BE2 (pipelines, templates) | `PATCH /v1/pipelines/pl_ap {"name":"a"*300}`; likewise PATCH providers/workers/policies/connections/skills `name`, `POST /pipelines` with a 300-char `owner_team`, `POST /pipelines/{id}/duplicate` and `/templates/{id}/instantiate` with a long `name`, `POST /providers`/`/policies` long `name`, `POST /skills/{id}/versions` `release_note` > 1000 | 422 `validation` | 500 `internal_error` (`StringDataRightTruncation`, VARCHAR(200)/(1000)). Same on SQL Server (error 2628). Fix: `max_length` on request models = column length |
| D-02 | Medium | BE2 (versions, test-runs), BE1 (workers, provider models) | `GET /v1/pipelines/pl_ap/versions/2147483648`; `POST …/versions/2147483648/restore`; `POST /pipelines/pl_ap/test-runs {"document_id":"doc_inv_sample","version":2147483648}`; huge integers in `POST /workers` / provider `models[].context_window` | 404 / 422 | 500 (`NumericValueOutOfRange`). Fix: `le=2**31-1` on int params/fields |
| D-03 | Low | BE1 (connections, policies) / DB (types) | `POST /v1/connections {"name":"a\u0000b","service":"redis","auth":{"type":"none"}}` or a `\u0000` inside `settings` | 422 | 500 on PostgreSQL (`UntranslatableCharacter`: JSONB/text reject NUL). SQL Server would store it, so behaviour differs by DB. Fix: reject NUL in validation |
| D-04 | Medium | BE2 (validator) | `POST /v1/pipelines/pl_kyc/validate {"nodes":[{"id":"n1","type":{"x":1},…}],"edges":[]}`, or the same graph in `PUT /pipelines/pl_kyc/draft` with a valid If-Match | 422 (or a `unknown_type` issue) | 500: `TypeError: unhashable type` in `app/services/validator.py:215 normalize_graph`. The portal always sends strings, so no portal impact, but the public API crashes |
| D-05 | Low | BE1 / Lead (errors) | send `{not json` with `Content-Type: application/json` to `POST /v1/members` and to `POST /v1/pipelines` | one code | BE1 routers: 400 `http_error` "There was an error parsing the body"; BE2 routers: 400 `bad_request` |
| D-06 | Low | Lead (`app/core/pagination.paginate_list`) | `GET /v1/pipelines?limit=5` → `next_cursor` = a deletable pipeline; delete it; `GET /v1/pipelines?limit=5&cursor=<it>`. Also `?cursor=garbage` on `/pipelines` and `/review-items` | 400 `invalid_cursor` (as the keyset lists do) or the next page | 200 with page 1 again: a client walking the list gets duplicates and can loop forever |
| D-07 | Low | DB engineer | apply `migrations/sql/mssql_initial.sql` (or `postgresql_initial.sql`) as a DBA would | schema at head | only 0001; there is no `*_0002_kc_connection_nullable.sql`, so `knowledge_collections.connection_id` stays NOT NULL and the I-1 force delete of a vector store fails until `alembic upgrade head` is run. Fix: add the 0002 DBA file or regenerate the copies at head |

Not defects (by design, recorded for completeness): 502 `worker_unreachable` for `wrk_enhance` and 502 `provider_unreachable`
on `models/sync` of a new Ollama provider are the mock's simulated upstream failures; the 409 console error in the smoke (I-10).

### Contract gaps (to be fixed in `api/openapi.yaml` by the lead; no backend change needed)

| ID | Finding | Evidence |
|---|---|---|
| C-01 | Error statuses are missing on most operations: 404 on 51 ops, 422 on 39, 400 on 27, 401 is documented only on `/me`, 403 nowhere. Add a shared `default` (or 400/401/403/404/422) `Problem` response to every operation | Schemathesis ST103 (143 cases); golden: `POST /mcp-servers/{id}/tools/{tool}/call` 422 |
| C-02 | 428 `precondition_required` on `PUT /pipelines/{id}/draft` (portal relies on it, G5) | Schemathesis, golden |
| C-03 | Other undocumented codes: 409 on `POST /members` (`member_exists`), `DELETE /members/{id}` (`last_admin`), `POST /review-items/{id}/decision` (`review_decided`); 412 on `POST /pipelines/{id}/versions` (stale `draft_etag`); 415 on `POST /documents`; 401 on `POST /ingest/{id}`; 502 on worker invoke, model sync and the test endpoints | Schemathesis |
| C-04 | `GET /runs/{id}/events` declares `text/event-stream: schema: RunEvent`. OAS 3.2-aware tools validate the SSE frame (`{event, data, id}`) against it, hence "run_id is a required property" although every `data` JSON has `run_id` (checked byte-wise). Use OAS 3.2 `itemSchema` with `data: {contentMediaType: application/json, contentSchema: RunEvent}` | Schemathesis ST108 |
| C-05 | `TestRunCreate.deliver` is broken YAML: the unquoted description `When false, deliver steps are simulated` inside a flow mapping becomes the property key `"deliver steps are simulated": null`. Quote the description | `yaml.safe_load` of the contract |
| C-06 | The server is stricter than the contract (Schemathesis "schema validation mismatch" for `POST /api-keys`, `/connections`, `/pipelines`, `/pipelines/{id}/test-runs`): scopes, `intake_type`, service/auth combinations and `document_id` existence are not enumerated in the YAML | Schemathesis warning |
| C-07 | No operation provisions, reads or rotates a pipeline's ingest HMAC secret; QA had to derive it out of band (`ingest_secret()`). Integrators need an endpoint (e.g. `POST /pipelines/{id}/ingest-secret/rotate`) | QA-03 |
| C-08 | Problem `code` values and validation issue codes are not enumerated (G9/G14, carried) | portal-integration-notes § 1 |

## 5. MSSQL verification

Done here (no SQL Server instance, no Docker, no ODBC driver):

1. **Offline migration:** `DI_DATABASE_URL='mssql+pyodbc://u:p@h/db?driver=ODBC+Driver+18+for+SQL+Server' alembic upgrade head --sql`
   renders 0001 + 0002 (947 lines, one transaction, `GO` batches). 0002 drops FK + index, `ALTER COLUMN … NULL`,
   recreates both; correct order for SQL Server.
2. **Dialect compile:** all 31 tables and 70 indexes compile with `sqlalchemy.dialects.mssql` (plus `alembic_version`).
3. **`tests/db/test_mssql_ddl.py`:** 67 passed.
4. **T-SQL scan** (offline script, metadata compile and the DBA copy `migrations/sql/mssql_initial.sql`, which has the same 32 tables):
   - NVARCHAR(MAX)/VARCHAR(MAX): 53 columns (JSON and long text), **none** in a PK, unique, FK or index.
   - Index key width: no key above 900 bytes.
   - Cascades: 52 FKs, **no** `ON DELETE/UPDATE` actions, so no multiple-cascade-path errors (children are deleted in code).
   - Reserved words: `idempotency_keys.key` and `runs.trigger` are T-SQL reserved; SQLAlchemy quotes them (`[key]`, `[trigger]`), and so must any hand-written SQL.
   - Booleans: `BIT` columns have no server defaults (the app always sets them); no `true/false`/`BOOLEAN` tokens.
   - Timestamps: only `DATETIMEOFFSET`, defaults `SYSDATETIMEOFFSET()`.
   - No PostgreSQL-only tokens (`JSONB`, `SERIAL`, `now()`, `::`, `TEXT`, `ILIKE`).
   - No unique constraint or unique index on a nullable column.
   - Keyset pagination is expanded to `OR/AND` (no row-value comparison, which SQL Server does not support).

**Runtime execution on SQL Server is NOT verified.** Remaining risk (medium) until the suite runs against a real
instance (`docker compose up -d mssql mssql-init`, then `DI_TEST_DATABASE_URL=mssql+pyodbc://… pytest tests`):

- the pyodbc/ODBC 18 connection, `fast_executemany=True` together with NVARCHAR(MAX) JSON parameters (a known source of
  truncation/memory errors in pyodbc), DATETIMEOFFSET round-trips and timezone awareness;
- concurrency of the run-engine threads + SSE polling + review decisions under `READ COMMITTED` (needs
  `READ_COMMITTED_SNAPSHOT ON`, otherwise blocking/deadlocks), and the idempotency-key insert race;
- case-insensitive collation (rule 7) on ids, emails and names in uniqueness checks;
- VARCHAR id parameters bound as NVARCHAR (implicit conversion → index scans) if `setinputsizes` is not effective;
- error translation: D-01/D-02 produce different driver errors on SQL Server (2628 / 8115); both surface as `DataError` and now map to 422, still to be confirmed on a real instance;
- D-03 diverges: SQL Server accepts NUL characters that PostgreSQL rejects;
- ~~the DBA copy stops at 0001 (D-07)~~ fixed: `mssql_0002_kc_connection_nullable.sql`.

## 6. Coverage of the 95 operations

Measured with `tests/contract/op_coverage_plugin.py` (wraps `httpx.Client.send` during the whole pytest session and
maps every request to a contract operation):

- **pytest (all suites): 95 / 95 operations exercised**, 0 uncovered.
- QA + contract tests alone: 71 / 95.
- Schemathesis: 95 / 95 operations tested.
- Thin coverage (a single pytest test): `DELETE /mcp-servers/{id}`, `DELETE /members/{id}`, `DELETE /policies/{id}`,
  `DELETE /skills/{id}`, `DELETE /webhooks/{id}`, `PATCH /mcp-servers/{id}`, `PATCH /members/{id}`, `PATCH /settings`,
  `POST /knowledge-collections`, `POST /knowledge-collections/{id}/documents`, `POST /mcp-servers`, `POST /runs/{id}/retry`,
  `POST /webhooks`, `POST /workers`.
- Portal smoke (live): 78 backend requests, every portal screen.

## 7. Known gaps carried from the engineers' docs

- **BE1:** knowledge indexing does not embed/upsert; AWS `role_arn` not implemented; Key Vault/AWS SM/Vault secret
  backends are stubs; OIDC users must already be members; in-memory per-process rate limiter; webhook retries not durable.
- **BE2:** MCP/tool calls inside AI steps are not executed in live mode; only `out.export` really delivers; simulated runs
  record no redaction boxes (`X-DI-Redacted: false`).
- **DB:** row-level security (PG RLS / SQL Server security policies) not implemented, isolation is in the app (QA-07 verified it);
  MSSQL runtime untested (§ 5).
- **Integration report:** I-2 seed name ("Example Trading" now matches the mock), I-3 equal `created_at` ordering, I-4
  review order by SLA, I-5 computed metrics, I-6 extra runs, I-7 `Origin: null` (dev flag), I-9 portal query
  invalidation after a credential change (portal follow-up), I-10 the smoke's console check, I-11 benign `ERR_ABORTED`.
  I-1 (force delete of a vector store) and I-8 (reset) are fixed and covered (QA-04, `app.db.reset`).
- **Contract notes:** G1 (SSE via headers), G3/G4 (ETag in the body, weak tags), G6 (`?status=` extension), G8 (test
  runs never pause), G10–G13 are behaviours the backend follows; the YAML should catch up (C-01…C-08).

## 8. Recommendation

**GO for committing** the backend and the QA suites. All 304 tests passed at QA time (324 after the defect fixes) against PostgreSQL, the golden replay and the
portal live smoke match the mock, secrets never leak, roles and workspace isolation hold, and there are no High defects.

Conditions, before a production deployment (not before the commit):

1. ~~Fix D-01, D-02 and D-04, then remove the strict-xfail markers.~~ Done after the QA run (all of D-01 … D-07).
2. Lead updates the contract for C-01…C-05 (and C-07 if integrators use HMAC ingest).
3. Run the full suite on a real SQL Server 2019+ instance before the first MSSQL deployment (§ 5), (the 0002 DBA script, D-07, is done).
