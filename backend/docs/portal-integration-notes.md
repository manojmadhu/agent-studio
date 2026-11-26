# Portal ⇄ backend integration notes (live mode)

Owner: Frontend engineer · Audience: BE1, BE2, DB engineer, QA, lead
Goal: `portal/` with `VITE_API_MODE=live`, `VITE_API_BASE_URL=http://localhost:8080/v1` works with **zero frontend changes**.
Ground truth = what the portal code does (cited `file:line`, paths relative to `portal/src/`), then the in-app mock
(`api/mock/backend.ts`, which the portal was tested against), then `api/openapi.yaml`. Items marked **⚠** are where the
portal depends on something the YAML does not say, or says differently.

Data exported from the portal (regenerate with the script in §8): `backend/app/data/`
`step_types.json` · `services.json` · `seed.json` · `sample_documents.json` · `mock_golden_responses.json` (79 recorded
mock responses incl. 4 SSE transcripts, use them as contract fixtures).

---

## 0. How the client behaves (applies to every call)

| Behaviour | Where | Backend consequence |
|---|---|---|
| One `openapi-fetch` client; base URL = `baseUrl` with trailing `/` stripped | `api/client.ts:30-34`, `api/config.ts:51` | Paths are exactly the YAML paths under `/v1`. |
| Success = `response.ok`; body parsed as JSON unless status 204 or `Content-Length: 0` | `client.ts:46-50`; openapi-fetch `dist/index.mjs:179-192` | A 2xx with a non-JSON, non-empty body throws. Empty 200/202 must send `Content-Length: 0`. |
| Error = any non-2xx → `ApiError(problem)` | `client.ts:48`, `api/errors.ts:20-29` | See §3. |
| Network/CORS failure → `TypeError` → toast "Could not reach the API…" | `errors.ts:36` | A CORS failure looks like "server down". |
| Queries retry once only when status ≥ 500 or network error; no refetch on focus; staleTime 15 s | `api/hooks.ts:39` | 4xx are final. |
| Lists: the portal reads **only `.data`** | `hooks.ts:45` (`data` helper), all list hooks `hooks.ts:48-68` | **Non-paginated list endpoints must still return `{ "data": [...] }`**, never a bare array. |
| A body is sent only when the call passes one; then `Content-Type: application/json` | openapi-fetch `index.mjs:50-65` | Endpoints called without body (see §1 "body: none") must not require one. |

## 1. Every operation the portal calls

`api/client.ts:59-151` defines `api.*`; the UI call sites are listed. "Reads" = response fields the UI actually uses.
Never called by the UI (defined only): `stepTypes`, `validatePipeline`, `getVersion`, `getRun`, `cancelRun`,
`claimReviewItem`; `/members`, `/documents`, `/runs/{id}/retry`, `/ingest` are not called at all.

### Identity, keys, settings, ops

| Call (client.ts) | Request | Reads | UI site |
|---|---|---|---|
| `GET /me` (:60) | — | `name`/`id`, `kind`, `role`, `workspace.name`, `workspace.environment` | `features/settings/SettingsPage.tsx:71,141` ("Test with GET /me") |
| `GET /api-keys` (:62) | no query (default limit) | `id,name,prefix,scopes[],last_used_at,expires_at` | `SettingsPage.tsx:234-252` |
| `POST /api-keys` (:63) | `{name, scopes[], expires_in_days?}` | `name`, **`secret`** | `SettingsPage.tsx:206` |
| `POST /api-keys/{id}/rotate` (:64) | `{grace_period_hours: 24}` | `name`, **`secret`** | `SettingsPage.tsx:210` |
| `DELETE /api-keys/{id}` (:65) | — | 204 | `SettingsPage.tsx:211` |
| `GET /settings` (:149) | — | `name, environment, default_region, retention_days` | `SettingsPage.tsx:158-190` |
| `PATCH /settings` (:150) | **the whole settings object as received**, incl. `sso`, `allowed_regions` | same | `SettingsPage.tsx:159` |
| `GET /metrics/overview?window=24h` (:144) | — | `documents, documents_change, straight_through_rate, straight_through_change, review_waiting, review_near_sla, median_ms, median_change_ms` | `features/pipelines/PipelinesPage.tsx:76-104` |
| `GET /audit-events?limit=20` (:145) | — | `id, action, resource_id, actor.name, at` | `SettingsPage.tsx:403-420` |
| `GET /webhooks` (:146) | — | `id, url, events[]` | `SettingsPage.tsx:346-359` |
| `POST /webhooks` (:147) | `{url, events[]}` | **`signing_secret`** (shown once) | `SettingsPage.tsx:331-333` |
| `DELETE /webhooks/{id}` (:148) | — | 204 | `SettingsPage.tsx:335` |

### Catalog

| Call | Notes |
|---|---|
| `GET /catalog/services` (:68) | Reads `id, name, category, auth_types[], settings_fields[] {key,label,type,required,default,options,monospace,hint}`; cached forever (`hooks.ts:57`). Drives the connection form (`features/connections/ConnectionsPage.tsx:141-165`). Serve `services.json` verbatim. |
| `GET /catalog/step-types` (:67) | **⚠ Not called by the portal** — the builder uses its compiled-in catalogue (`features/builder/catalog.ts:113-228`). Serve `step_types.json` verbatim anyway (contract) and use the same file for the server validator, so server and portal can never diverge. |

### Pipelines, drafts, versions, templates

| Call | Request | Reads | UI site |
|---|---|---|---|
| `GET /pipelines?limit=200` (:70) | filtering/search is client-side | `id,name,status,owner_team,live_version,draft_changes[],intake_type,step_count,ai_component_count,kpi.*,updated_at` | `PipelinesPage.tsx:35,157-196`, `app/CommandPalette.tsx`, review/runs pages (name lookup) |
| `GET /pipelines/{id}` (:71) | — | `name, status, owner_team, live_version, description` | `features/builder/BuilderPage.tsx:33,224-251` |
| `POST /pipelines` (:72) | `{name, intake_type}` (+ explicit `Idempotency-Key`) | `id` → navigates to builder | `PipelinesPage.tsx:252` |
| `PATCH /pipelines/{id}` (:73) | `{name?|description?|owner_team?}` | — | `BuilderPage.tsx:198` |
| `DELETE /pipelines/{id}` (:74) | — | 204 / 409 `pipeline_active` | `PipelinesPage.tsx:40` |
| `POST /pipelines/{id}/duplicate` (:75) | `{}` (portal never passes a name) | — | `PipelinesPage.tsx:39` |
| `POST /pipelines/{id}/activate` (:76) | **body: none** | `name, status` | `PipelinesPage.tsx:38`, `BuilderPage.tsx:208` |
| `POST /pipelines/{id}/pause` (:77) | **body: none** | `name, status` | same |
| `GET /pipelines/{id}/draft` (:78) | — | `graph`, **`etag` (header or body)**, `changes[]` | `BuilderPage.tsx:34,41,365,407,413` |
| `PUT /pipelines/{id}/draft` (:79) | header `If-Match: <etag>`; body `{graph, change}` | new `etag`, `changes[]` | `features/builder/autosave.ts:27-31` |
| `GET /pipelines/{id}/versions` (:82) | — | `version, release_note, published_by, published_at, live` | `BuilderPage.tsx:299-345` |
| `POST /pipelines/{id}/versions` (:83) | `{release_note, draft_etag}` | `version` | `BuilderPage.tsx:364` (then re-GETs the draft for the new etag/changes, :365-366) |
| `POST /pipelines/{id}/versions/{version}/restore` (:86) | **body: none** | `graph`, `etag` (header or body), `changes[]` | `BuilderPage.tsx:303-304` |
| `GET /templates` (:88) | — | `id, name, description, step_count, ai_component_count` | `PipelinesPage.tsx:181-202` |
| `POST /templates/{id}/instantiate` (:89) | `{}` | `id` → navigates | `PipelinesPage.tsx:187-190` |

### Runs, review

| Call | Request | Reads | UI site |
|---|---|---|---|
| `POST /pipelines/{id}/test-runs` (:91) | `{document_id: "doc_inv_sample"\|"doc_rcp_sample", version: "draft", deliver: false}` | `id` | `features/builder/runStore.ts:100` |
| `GET /runs/{id}/events` (SSE) | see §5 | event stream | `runStore.ts:104-166`, `api/sse.ts` |
| `GET /runs/{id}/result` (:97) | after the stream ends | `fields[] {name,value,confidence}` (errors swallowed) | `runStore.ts:169`, `features/builder/RunPanel.tsx:36-40` |
| `POST /pipelines/{id}/steps/{node_id}/test` (:93) | `{document_id}` | `output` | `BuilderPage.tsx:140-141` |
| `GET /runs?limit=200` (:95) | filters are client-side | `id, document.filename, pipeline_id, status, test, field_count, duration_ms, pipeline_version, started_at, error.detail` | `features/runs/RunsPage.tsx:13-95` |
| `GET /review-items` (:100) | **no query** (default limit 50) | `id, document.filename, pipeline_id, reason, reason_code, assignee, sla_due_at, created_at`; **the count is shown in the sidebar** | `features/review/ReviewQueuePage.tsx:12-58`, `app/Shell.tsx:33` |
| `GET /review-items/{id}` (:101) | — | `document.filename, pipeline_id, reason, result.fields[] {name,value,confidence}` | `features/review/ReviewWorkspace.tsx:16-90` |
| `POST /review-items/{id}/decision` (:103) | `{action: "approve"\|"reject", corrections?: {field: "string"}}` (corrections values are always strings) | **`document.filename`** | `ReviewWorkspace.tsx:24-33` |

### Components

| Call | Request body | Reads / notes | UI site |
|---|---|---|---|
| `GET /connections?limit=200` (:105) | — | `id,name,service,auth_type,status,secret_hint,settings.*,last_used_at,usage_count` | `ConnectionsPage.tsx`, builder field options `features/builder/FieldInput.tsx:162` (filters by `service`) |
| `POST /connections` (:106) | `{name, service, settings, auth: {type, …}}` — `auth` is built by `features/connections/authFields.ts:98-106` (empty optional fields dropped; `scopes` sent as string[]) | — | `ConnectionsPage.tsx:160` |
| `PATCH /connections/{id}` (:107) | `{auth: {type, …}}` (rotation only) | — | `ConnectionsPage.tsx:257` |
| `DELETE /connections/{id}?force=false\|true` (:108) | `force` always sent | 204; **any 409 → reads `problem.usages[]`** then offers "Delete anyway" (`force=true`) | `ConnectionsPage.tsx:326-331` |
| `POST /connections/{id}/test` (:109) | **body: none** | `ok, message` (status refreshed by re-listing) | `ConnectionsPage.tsx:351,459` |
| `GET /connections/{id}/usage` (:110) | — | `data[] {resource_type, resource_id, node_id, label}` | `ConnectionsPage.tsx:300-315` |
| `POST /connections/{id}/oauth/authorize` (:111) | `{}` | `authorization_url` (opened in a popup unless it contains `accounts.example.com`); re-lists connections **once** after 1.6 s | `ConnectionsPage.tsx:355-363` |
| `GET /providers` (:113) | — | `id,name,kind,enabled,base_url,connection_id,data_region,default_model,usage_count,models[] {id,display_name,enabled,embedding,context_window,supports_tools,supports_vision}` | `features/models/ModelsPage.tsx`, `FieldInput.tsx:229-254` |
| `POST /providers` (:114) | `{kind, name, base_url?, data_region, connection_id?, models: [{id, enabled:true}]}` | `name` | `ModelsPage.tsx:173-186` |
| `PATCH /providers/{id}` (:115) | `{name, base_url?, data_region (may be ""), connection_id (may be null), default_model (may be ""), models[]}` or `{enabled}` | `name, enabled` | `ModelsPage.tsx:31,232` |
| `DELETE /providers/{id}` (:116) | — | 409 → `usages[]` | `ModelsPage.tsx:145` |
| `POST /providers/{id}/test` (:117) | `{model}` | `ok, message, latency_ms` | `ModelsPage.tsx:33` |
| `POST /providers/{id}/models/sync` (:118) | **body: none** | `models.length` | `ModelsPage.tsx:32` |
| `GET /workers` (:120) | — | `id,name,kind,adapter,base_url,invoke_path,health_path,connection_id,timeout_s,max_concurrency,health{status,latency_ms,checked_at,message}` | `features/workers/WorkersPage.tsx` |
| `POST /workers` (:121) | `{name, kind, base_url, adapter, invoke_path?, health_path?, connection_id?, timeout_s?, max_concurrency?}` | `name` | `WorkersPage.tsx:249-260` |
| `PATCH /workers/{id}` (:122) | `{name, base_url, adapter, invoke_path, health_path, connection_id\|null, timeout_s, max_concurrency}` | — | `WorkersPage.tsx:124` |
| `DELETE /workers/{id}` (:123) | — | 409 → `usages[]` | `WorkersPage.tsx:140-144` |
| `POST /workers/{id}/health` (:124) | **body: none** | `status, message, latency_ms` | `WorkersPage.tsx:128,246` |
| `POST /workers/{id}/invoke` (:125) | `{document_id: "doc_inv_sample"\|"doc_rcp_sample"}` | `pages[] {blocks, tables}`, `worker_latency_ms` (rest pretty-printed) | `WorkersPage.tsx:110-133` |
| `GET /mcp-servers` (:127) | — | `id,name,transport,url,connection_id,headers,enabled,status,last_discovered_at,usage_count,tools[] {name,description,enabled,input_schema}` | `features/mcp/McpPage.tsx`, `FieldInput.tsx:197-206` |
| `POST /mcp-servers` (:128) | `{name, transport, url, connection_id?}` | — | `McpPage.tsx:219` |
| `PATCH /mcp-servers/{id}` (:129) | `{name,url,connection_id,headers}` or `{enabled}` or `{tools:[{name,enabled}]}` (partial tool list = merge by name) | `name, enabled` | `McpPage.tsx:50-52,218` |
| `DELETE /mcp-servers/{id}` (:130) | — | 409 → `usages[]` | `McpPage.tsx:72-76` |
| `POST /mcp-servers/{id}/discover` (:131) | **body: none** | `tools.length` | `McpPage.tsx:51` |
| `POST /mcp-servers/{id}/tools/{tool}/call` (:132) | `{arguments: {…}}` | `is_error, latency_ms, content[].text` | `McpPage.tsx:64-65` |
| `GET /knowledge-collections` (:135) | — | `name` (options for `ai.knowledge.collection`) | `FieldInput.tsx:152` |
| `GET /skills` (:136) | — | `id,name,description,version,versions[] {version,release_note,published_by,published_at},usage_count,instructions` | `features/skills/SkillsPage.tsx` |
| `POST /skills` (:137) | `{name, description, instructions}` | `name` | `SkillsPage.tsx:21` |
| `DELETE /skills/{id}` (:138) | — | 409 → `usages.length` | `SkillsPage.tsx:37-42` |
| `POST /skills/{id}/versions` (:139) | `{instructions, release_note}` | `name, version` | `SkillsPage.tsx:22` |
| `GET /policies` (:141) | — | `id,name,description,kind,enabled,scope{all_pipelines,pipeline_ids,owner_teams}` | `features/policies/PoliciesPage.tsx`, `features/builder/Inspector.tsx:98` |
| `PATCH /policies/{id}` (:142) | `{enabled}` | `name, enabled` | `PoliciesPage.tsx:24` |

The builder loads connections, providers, workers, MCP servers, skills, policies and collections on open
(`hooks.ts:84-96`); local validation waits for connections + providers (`hooks.ts:94`).

### ⚠ Contract gaps / differences (risky)

| # | Portal relies on | YAML says |
|---|---|---|
| G1 | SSE auth via **headers** (`Authorization`/`X-API-Key`) on a `fetch()` stream (`api/sse.ts:45-48`); `sse_token`/`?access_token=` never used | suggests `?access_token=` for EventSource |
| G2 | SSE dispatch on **`data.event`** inside the JSON; the `event:` line is ignored (`sse.ts:55-57`) | RunEvent has `event` (required) — OK, but must be inside `data` |
| G3 | Draft `etag` **in the body** as fallback when the `ETag` header is not readable (`client.ts:54`, `autosave.ts:28`, `BuilderPage.tsx:304,408,415`) | header only described |
| G4 | ETag string compared **byte-for-byte**; mock emits weak `W/"pl_ap-1"` (`backend.ts:202-205,479`) | — (RFC strong comparison would reject weak tags) |
| G5 | `428 precondition_required` when If-Match missing (`backend.ts:478`) | 428 not listed for `saveDraft` |
| G6 | `GET /review-items` returns **only open/claimed** items (`backend.ts:631`); sidebar shows `data.length` | no status filter defined |
| G7 | Review decision response includes `document` (`ReviewWorkspace.tsx:32`) | `ReviewItem` — OK, `document` is required there; just don't return a slim object |
| G8 | Test runs never create review items, and **continue past Human review** to the end, final status `review` (`runEngine.ts:103,120-121`, `backend.ts:321`) | "run paused at Human review resumes on decision" |
| G9 | Problem `code`s and step-validation `code`s not in YAML (§3, §9) | partial examples only |
| G10 | Node config keys: `ai.model.config.provider` (not `provider_id`), schema items `{name,type,req,pii}` (not `required`) (`catalog.ts:75-80,200`) | `docs/04 §7` example uses `provider_id` / `required` — **the doc is wrong** |
| G11 | `Template.ai_component_count` = number of AI **components** (sub-nodes) but `Pipeline.ai_component_count` = number of AI **steps** (`backend.ts:215` vs `:544`) | same name, no definition |
| G12 | Test documents are the fixed ids `doc_inv_sample`, `doc_rcp_sample` (`runStore.ts:22-25`); unknown ids fall back to the invoice (`backend.ts:291,583`). `POST /documents` is never called | expects uploaded documents |
| G13 | Result overlays/bboxes are **not used**; the viewer renders a built-in HTML invoice/receipt and measures DOM boxes by field name (`features/document/DocumentViewer.tsx:251-290`) | `RunResult.overlays` with bbox |
| G14 | `docs/04` lists `unknown_step_type`; portal emits **`unknown_type`** (`validate.ts:41`) | — |
| G15 | Lead's `app/services/catalog_data.py:step_types_by_id()` keys by `s["id"]` — StepType has **`type`**, not `id` → `KeyError` | — (lead to fix) |

## 2. Headers

| Header | Direction | Where | Rule |
|---|---|---|---|
| `Authorization: Bearer <token>` **or** `X-API-Key: <key>` | request | `api/config.ts:54-57`, middleware `client.ts:22-28`, SSE `sse.ts:45` | Only one is sent, chosen in Settings → API access. **No header at all if the credential is empty** (default on first load). |
| `Idempotency-Key: <uuid>` | request | `client.ts:25` on **every POST** (incl. activate, test, claim, decision, test-runs, discover…); `createPipeline` sets its own (`client.ts:72`) | Always unique per click (never replayed by the portal). Must be accepted on every POST path and must not be "required body/422". |
| `If-Match: <etag>` | request | `PUT /pipelines/{id}/draft` only (`client.ts:80`) | Exact string from the last GET/PUT/restore. Missing → 428 `precondition_required`; mismatch → 412 `etag_mismatch` (portal opens the conflict dialog on **status 412**, `autosave.ts:37`). |
| `ETag` | response | GET/PUT draft, restore (`backend.ts:471,490,538`) | Also put the same value in body `etag`. Expose it via CORS. Must change on every PUT, publish and restore (mock bumps `rev` on each, `backend.ts:483,521,535`). |
| `draft_etag` (body) | request | `POST /versions` (`BuilderPage.tsx:363-364`) | Mismatch → 412 `etag_mismatch`. |
| `Last-Event-ID` | request | `sse.ts:46` — **only if passed; `runStore.ts` never passes it today** | Support resume anyway (ids are integers from 1 per run). |
| `Accept: text/event-stream` | request | `sse.ts:45` | safelisted |
| `Content-Type: application/json` | request | any call with a body | triggers preflight |

CORS (portal origins `http://localhost:5173` dev, `http://localhost:4173` preview, plus `127.0.0.1` variants):

- Methods: `GET, POST, PUT, PATCH, DELETE` (+ `OPTIONS` preflight, including for the SSE GET — it carries `Authorization`/`X-API-Key`).
- `Access-Control-Allow-Headers` must cover: `Authorization, X-API-Key, Content-Type, Idempotency-Key, If-Match, Last-Event-ID, Accept`.
  Note: a literal `*` does **not** cover `Authorization` per the Fetch spec; list it explicitly (current `app/main.py ALLOW_HEADERS` does — keep it).
- `Access-Control-Expose-Headers`: **`ETag`** (required for the header path; body fallback exists), plus `Location, RateLimit-*, Retry-After` (not read today). The mock's replay header is `Idempotent-Replayed`; `main.py` exposes `Idempotency-Replayed` — the portal reads neither.
- `allow_credentials=false` is correct: the portal never sends cookies (`credentials` default).
- If QA loads the preview build inside the sandboxed iframe (`e2e/sandbox.py`), the Origin is the literal `null` — decide whether dev CORS allows it.
- Send `Cache-Control: no-store` on JSON (esp. drafts) so the browser never revalidates a draft behind the app's back.

## 3. Errors (problem+json)

Parsing: `api/errors.ts:19-29`. The body is used **only if it is an object containing `title`**; otherwise the portal
builds `{title: res.statusText || "Request failed (N)", status}` and every other field is lost. Toast text =
`title + ". " + detail` (`errors.ts:33-35`). Fields read: `title`, `detail`, `status`, `code` (`ApiError.code`),
`issues[]`, `usages[]`. (`app/core/errors.py` already always sets `title` — keep it for the RequestValidationError and
HTTPException handlers too.)

Status/code branches in the UI:

| Status / code | Portal reaction | Site |
|---|---|---|
| 412 (any code; mock `etag_mismatch`, extra `current_etag`) on PUT draft | conflict dialog (reload / overwrite) | `autosave.ts:37`, `BuilderPage.tsx:404-433` |
| 409 on DELETE connection/worker/provider/MCP (any code) | reads `problem.usages[]` → "in use" list; connections offer `?force=true` | `ConnectionsPage.tsx:331`, `WorkersPage.tsx:144`, `ModelsPage.tsx:145`, `McpPage.tsx:76` |
| 409 on DELETE skill | `usages.length` in toast | `SkillsPage.tsx:42` |
| any error with non-empty `issues[]` on test-run / publish | opens Problems tab; toast "Resolve N issues before testing" | `runStore.ts:178-180`, `BuilderPage.tsx:372` |
| `TypeError` (network/CORS) | "Could not reach the API…" | `errors.ts:36` |
| everything else | toast `title. detail` | `hooks.ts:40` |

Codes the mock emits (use the same so toasts/tests match): `not_found` 404 · `validation` 422 (generic body errors) ·
`bad_request` 400 · `precondition_required` 428 · `etag_mismatch` 412 · `pipeline_invalid` 422 (+`issues`, error-level
only, detail "Resolve N issue(s) before publishing|activating|testing") · `no_live_version` 422 · `pipeline_active` 409 ·
`connection_in_use` / `provider_in_use` / `worker_in_use` / `mcp_in_use` / `skill_in_use` 409 (+`usages`) ·
`already_claimed` 409 · `run_in_progress` 409 (result before finish) · `run_finished` 409 (cancel) · `mcp_disabled` 409 ·
`invalid_arguments` 422 · `worker_unreachable` 502 · `provider_unreachable` 502 · `file_too_large` 413 · `internal` 500.
Exact bodies: `mock_golden_responses.json` keys `DELETE /connections/con_anthropic?force=false`, `PUT /pipelines/pl_ap/draft`
(428) and `… #2` (412), `POST /pipelines/pl_xls/versions` (422), `DELETE /pipelines/pl_ap`, `POST /review-items/rv1/claim`.

## 4. Pagination

The portal reads `data` only and ignores `next_cursor` (`hooks.ts:45`). It never sends `cursor`. Limits sent:
`/pipelines?limit=200`, `/runs?limit=200`, `/connections?limit=200`, `/audit-events?limit=20`; `/api-keys` and
`/review-items` use the server default (50). Anything beyond one page is silently invisible — fine for the demo, keep
`MAX_LIMIT=200`. Order: pipelines in seed order (ap, exp, kyc, xls, then created ones); runs **newest first**
(`backend.ts:591`); review items in seed order.

## 5. SSE `/runs/{id}/events`

Client: `api/sse.ts:42-74`, consumer `features/builder/runStore.ts:104-166`.

- Request: `GET` with `Accept: text/event-stream` + auth header. Must return 2xx **and** a body, otherwise `ApiError`.
- Parser (`sse.ts:17-40`): `\r\n`/`\r` normalised; blank line ends a frame; `:` comment lines ignored (heartbeats OK);
  multiple `data:` lines joined with `\n`; `event:` and `id:` parsed but **not used**. Each frame's data is
  `JSON.parse`d; malformed frames are dropped silently.
- Termination: the client stops reading and cancels the stream at the first `data.event == "run.finished"`.
  If the server closes first, the client also proceeds. Then it calls `GET /runs/{id}/result` once.
- **Replay**: the client subscribes *after* `POST /test-runs` returns, so the server must replay all stored events of the
  run from the beginning (mock `backend.ts:332-359` replays everything with `_id > Last-Event-ID`, then streams live, and
  closes after `run.finished`). A finished run's stream = full replay + close.
- Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`; disable buffering (no GZip on this route,
  `X-Accel-Buffering: no`), flush each frame.

Wire format (mock, `backend.ts:343`):

```
id: 3
event: step.finished
data: {"event":"step.finished","node_id":"ap_n3","step":{"node_id":"ap_n3","status":"completed","started_at":"…","duration_ms":867,"attempt":1,"output":{"pages":1,"characters":2184,"mean_confidence":0.97,"worker":"wrk_paddle"}},"layers":["blocks"],"run_id":"run_48301","at":"…"}

```

| `data.event` | fields | portal use |
|---|---|---|
| `run.started` | `status:"running"`, `run_id`, `at` | — |
| `step.started` | `node_id`, `step {node_id, status:"running", started_at, attempt:1}` | node + its AI components light up (`runStore.ts:110-119`) |
| `step.finished` | `node_id`, `step {node_id, status:"completed", started_at, duration_ms, attempt, output{…}, output_port?, usage?}`, **`layers`** = cumulative list of `blocks|tables|fields|redactions` | node ok; `output` stored for the Output tab; only edges whose `from_port == output_port` (or all, if absent) animate; `layers` replaces the viewer's layer set; for `ai.classify` reads `output.type`; log "Name · 0.9 s → port" (`runStore.ts:120-141`) |
| `step.failed` | same as finished + `step.error.title` | node red, log uses `step.error.title` (mock never emits it) |
| `run.routed` | `node_id`, `status:"review"` | sets "routed to human review" (`runStore.ts:143-145`) |
| `run.finished` | `status: completed\|review\|failed\|cancelled` | non-visited nodes → skipped; log/toast (`runStore.ts:146-160`) |

Then `GET /runs/{id}/result` (`runStore.ts:169`; failures swallowed → the run panel silently falls back to the built-in
`VALUES`, `RunPanel.tsx:36-41` — **persist the result before emitting `run.finished`**). Rendered: `fields[]` by `name`
→ `value` (number → `toLocaleString(2 decimals)`), `confidence` (meter, "Below threshold" when < the graph's
`route.confidence` threshold or 0.85), PII masking is decided client-side from `layers` (`RunPanel.tsx:111`). The list of
rows comes from the extract node's parser schema, not from the result. `classification`, `checks`, `path`, `overlays`
are not rendered (classification label comes from the classify step output; "· 0.96" is hard-coded). Bbox units are
irrelevant to the portal today (G13); keep the YAML's page units for API clients.

Review workspace uses `result.fields[] {name, value, confidence}` from `GET /review-items/{id}` and renders the same
built-in paper chosen by `document.filename.startsWith('receipt')`; invoice number shown = filename without `.pdf`
(`ReviewWorkspace.tsx:37-39`). Field names must be the sample schema names for the overlay boxes to appear.

## 6. Mock run engine (port 1:1 for `run_mode=simulate`)

Source: `api/mock/runEngine.ts` (execute :77-127, step outputs :18-53, result :129-153) and `backend.ts:290-330`.

1. `POST /test-runs`: 404 if pipeline/version missing; validate the graph (draft or `version`) → 422 `pipeline_invalid`
   with error issues; else create `Run {id: "run_" + seq (mock starts 48301), pipeline_id, pipeline_version: "draft"|n,
   document:{id, filename, mime_type, pages:1}, status:"queued", test:true, started_at, finished_at:null,
   duration_ms:null, field_count:0}` → **202** with that body. Document = `SAMPLE_DOCS[document_id]` or the invoice.
2. After 30 ms: `status=running`, emit `run.started`.
3. BFS from the first main node with no input port (intake). Queue + visited set; skip unknown types; sub-nodes
   (AI components) are never visited (only non-slot edges are followed).
4. Per node: emit `step.started`; wait `ms = round((ai ? 1100 : stage=="read" ? 700 : 380) + rand*250)`; update state:
   `read.ocr`→layer `blocks`; `read.layout`→`tables`; `ai.classify`→classification = doc kind (`invoice`/`receipt`);
   `ai.extract`/`ai.agent`→layer `fields`, remember as extract node; `val.pii`→`redactions`; `review.human`→review=true.
5. Ports: `route.type` → doc kind if in `list(config.paths)` else `other`. `route.confidence` → schema = extract node's
   parser schema (else default schema for the doc kind); `m = min(confidence of required fields present in VALUES)`
   (1 if none); `pass` if `m >= threshold` else `review`; output `{lowest_required_confidence: m, threshold, output: port}`.
   Other nodes: output = `stepOutput()` (+`output: port` if a port). Record `path[node_id]=port`.
6. Emit `step.finished` with `step {node_id, status:"completed", started_at, duration_ms: ms, attempt:1, output,
   output_port?, usage? (AI steps: {input_tokens:2310, output_tokens:268, cost_usd:0.011})}` and cumulative `layers`.
   After `review.human` also emit `run.routed {node_id, status:"review"}` — **and keep going** downstream.
7. Enqueue targets of non-slot edges from the node where `!port || edge.from_port == port`
   (quirk: nodes without a port follow *all* outgoing edges, including an `error` port).
8. Cancellation is checked before each node. After the loop, every non-visited main node gets `{node_id, status:"skipped"}`
   in `RunDetail.steps` (no event).
9. Result (`buildResult`): `{run_id, path, checks: [], overlays:{blocks:[],tables:[],redactions:[]}}` +
   `classification {type, confidence:0.96}` if classified + `fields` if an extract ran: schema order, `value` from
   `VALUES[kind]` (`null` when absent, or when field is PII **and** `redactions` layer present → also `masked:true`),
   `confidence` (0 if absent), `required`, `pii`, `masked`, `page:1`. `checks` = rules of the first completed
   `val.rules` node, all `passed:true`, with `node_id`.
10. Final: `status = cancelled | review (if review.human visited) | completed`; `finished_at`, `duration_ms`,
    `field_count = len(fields)`; store result; **then** emit `run.finished {status}`.

Seed outcomes (see `mock_golden_responses.json` "SSE …" keys): `pl_ap`+invoice → … route.type→`invoice`, gate→`pass`
(min 0.88 ≥ 0.85), ERP, DB → `completed`; `pl_ap`+receipt → `receipt` → receipt extract → export → `completed`;
`pl_exp`+receipt → `completed` (card_last4 masked); `pl_kyc` → gate `pass` (schema fields not in VALUES) → `completed`.
No seed graph reaches Human review unless the user raises the gate threshold above 0.88 (or routes receipts through a gate:
`purchase_date` 0.64).

`stepOutput` per type (`runEngine.ts:18-53`): `read.ocr {pages:1, characters: inv?2184:612, mean_confidence:0.97, worker}` ·
`read.layout {tables:1, key_value_pairs: inv?11:5}` · `read.sheet {sheets:2, characters:9412, worker}` ·
`ai.classify {type, confidence:0.96, model}` · `ai.extract|ai.agent {model:"<provider> · <model>", fields:<schema len>,
tools_available:[names], knowledge:[collections], tokens:{input:2310, output:268}}` · `val.rules {passed:<n rules>, failed:0}` ·
`val.pii {masked: inv?3:1, method}` · `val.lookup {matched:true, record:"V-00418"}` · `val.injection {verdict:"clean"}` ·
`review.human {queue, ticket:"REV-1182", decision:"approved"}` · default `{status:"ok"}`.
Single-step test (`backend.ts:579-586`): `{node_id, status:"completed", started_at, duration_ms:420, attempt:1, output}`,
output = `stepOutput` (sub-nodes: `{component: name, config}`).

Review decision (`backend.ts:648-662`): `approve` → item `approved`, run `completed`; `reject` → `rejected`, run `failed`;
`return_to_queue` → `open`, assignee null; anything else 422. Constants for the engine are in `sample_documents.json.engine`.

## 7. Seed workspace (port from `seed.json`)

`seed.json` = `seed(now)` (`api/mock/seed.ts:203-389`) computed at `_meta.reference_now`; **shift every timestamp by
`real_now - reference_now`** so "x min ago" and SLA countdowns look like the mock. Graphs are already normalised
(`serializeGraph`). Mock workspace id is `ws_example` (backend uses `ws_default` — only `/me.workspace.name` is shown).

| Entity | Ids · key fields | Port from |
|---|---|---|
| Me / members | `usr_manoj` "Manoj M." admin (me, `sse_token`); `usr_sara` builder, `usr_lina` reviewer | `backend.ts:369-370` → `seed.json.me/members` |
| Settings | name "Example Trading", sandbox, UAE North, 2555 days, allowed `[UAE North, EU West]`, sso Azure AD | `seed.ts:387` |
| Pipelines | `pl_ap` active v14 (versions 14,13,12; 17 steps; 25 nodes), `pl_exp` active v6, `pl_kyc` paused v3, `pl_xls` draft (no versions, draft changes `["Created pipeline"]`); kpi per pipeline; `created_at` −120 d | `seed.ts:274-287`, graphs `seed.ts:55-171` |
| Drafts | graph = seed graph; `rev=1` → ETag `W/"<id>-1"`; `changes` = seed changes; `saved_at/by` = pipeline `updated_at/by` | `backend.ts:134-137` |
| Versions | every version stores **the same graph as the draft** | `seed.ts:233` |
| Node ids | deterministic `ap_n1…`, `exp_n…`, `kyc_n…`, `xls_n…`; edges `ap_e…` (shared counter) | `seed.ts:30-50` |
| Templates | `tpl_ap`, `tpl_exp`, `tpl_kyc`, `tpl_xls` (graph builders reused; instantiate = new pipeline, random ids, changes `["Created from template"]`, status draft, owner "Finance Operations") | `seed.ts:180-185`, `backend.ts:540-553` |
| Connections (13) | `con_gmail` gmail/oauth2_authorization_code, `con_wa` whatsapp/bearer, `con_anthropic`, `con_openai`, `con_azure`, `con_gemini` (api_key), `con_ollama` ollama/none **unverified**, `con_pg` postgresql/basic, `con_s3` aws_sigv4, `con_erp` http_api/oauth2_client_credentials, `con_qdrant` api_key, `con_mcp` mcp/bearer, `con_workers` document_worker/api_key **unverified**. No secrets — vault holds `{type, seeded:true}` | `seed.ts:288-302`, `backend.ts:131` |
| Providers (5) | `prv_anthropic` (default `claude-sonnet-5-5`), `prv_openai` (`gpt-5`), `prv_azure` (`gpt-prod`, UAE North), `prv_gemini` (`gemini-2.5-pro`), `prv_ollama` **disabled** | `seed.ts:303-314` |
| Workers (3) | `wrk_paddle` paddle_docparse_v1 `/parse` :8001, `wrk_excel` excel_parser_v1 `/to-text` :8000, `wrk_enhance` generic_pages_v1 `/preprocess` (mock health → unreachable) | `seed.ts:315-322`, `backend.ts:837-845` |
| MCP servers (3) | `mcp_vendors` (match_vendor, match_po*, get_vendor*), `mcp_docs` (search_documents*, get_document, save_extraction), `mcp_customers` sse **disabled** (find_customer). `*` = has `required` args | `seed.ts:323-338` |
| Skills (4) | `skl_invoice` v7, `skl_receipt` v3, `skl_identity` v2, `skl_bilingual` v4 | `seed.ts:339-345` |
| Policies (5) | `pol_pii`, `pol_inj`, `pol_schema`, `pol_region` (disabled), `pol_ret` | `seed.ts:346-352` |
| Knowledge (3) | `kc_vendor` vendor_policies, `kc_tax` tax_rules_uae, `kc_coa` chart_of_accounts (all on `con_qdrant`) | `seed.ts:353-357` |
| Review items (4) | `rv1` claimed by Sara (low_confidence total 0.71), `rv2` open (rule_failed), `rv3` claimed by Lina (pl_exp receipt), `rv4` open (no_master_match); run ids `run_48202/4/6/8` (not in runs list); results in `seed.json.review_results` | `seed.ts:358-363`, results `backend.ts:272-287` |
| Runs (8) | `run_48210…48217` (non-test), 48212 `review`, 48215 `failed` with `error {code: worker_timeout}`; no events/results stored (events → 404, result → 404) | `seed.ts:364-381` |
| API keys (2) | `key_7Hf2` "ERP ingest", `key_Q9sd` "Reporting" — prefixes only, no usable secrets → use `DI_BOOTSTRAP_API_KEY` for a working key | `seed.ts:382-385` |
| Webhooks (1) | `wh_1` → run.failed, review.created | `seed.ts:386` |
| Sample documents | `doc_inv_sample` INV-2026-0417.pdf (inv), `doc_rcp_sample` receipt_0918.jpg (rcp) — **must exist as documents** | `sampleData.ts:3-6` → `sample_documents.json` |

Derived fields computed on read (don't store): `Pipeline.draft_changes/intake_type/step_count/ai_component_count/
kpi.review_waiting` (`backend.ts:206-218`); `usage_count` on connections (any node `config` value equal to the id +
provider/worker/MCP/collection `connection_id`, `backend.ts:228-239`), providers (`ai.model` nodes with
`config.provider`), MCP (`config.server`), skills (`config.skill`) — all scanned over **drafts**; metrics
(`backend.ts:1005-1014`: documents = Σ kpi.documents_24h, STP weighted over active pipelines, fixed deltas 0.084/0.013/−400,
`median_ms` 6200, `review_near_sla = min(near,2)`).

## 8. Catalog data and the export

`/catalog/step-types` = `STEP_TYPES.map(toStepType)` (`catalog.ts:228,245-272`): 34 types. Notes: `outputs` is
`["out"]` for main steps, `["pass","review"]` for `route.confidence`, **absent** for `route.type` (dynamic: paths + `other`)
and for AI components; `required_slots` present whenever `slots` is (defaults to `["model"]`); `plugs_into` on
components; `default` included for every field. `/catalog/services` = `SERVICES` (`api/mock/services.ts:5-28`): 22 services.

Exports (serve/load verbatim — do not hand-edit; regenerate instead):

| File | Content |
|---|---|
| `step_types.json` | list of 34 `StepType` (the `data` array) |
| `services.json` | list of 22 `ServiceDefinition` |
| `sample_documents.json` | `documents[]`, `values` (`VALUES`), `schemas.inv/rcp`, `engine` constants, `step_defaults` (config defaults per type), `node_settings_default`, `slots`, `dynamic_outputs`, `primary_secret_field` (secret → `secret_hint`) |
| `seed.json` | §7 |
| `mock_golden_responses.json` | `{ "METHOD path": {request, status, headers, body} }` from a fresh mock + `SSE <pipeline> <doc>` transcripts (create, raw event text, result, run detail) |

Regenerate: script lives outside the repo at `/tmp/export/export.ts` (re-create from this description if gone): it imports
`portal/src/api/mock/{backend,seed,services,sampleData}.ts` and `features/builder/{catalog,graph}.ts`, bundles with the
portal's esbuild and runs on Node 22:
`cd portal && ./node_modules/.bin/esbuild /tmp/export/export.ts --bundle --platform=node --format=esm --outfile=/tmp/export/export.mjs && node /tmp/export/export.mjs ../backend/app/data`.

## 9. Validation rules (port `features/builder/validate.ts` 1:1)

Pre-processing the server must do first (the portal always validates a *deserialised* graph, `graph.ts:92-99`):
drop nodes of unknown type; fill missing `config` keys with `step_defaults[type]` and `settings` with
`{retries:2, timeout_s:60, on_error:"stop", notes:""}`; drop edges failing `edgeOK` (`graph.ts:54-60`: both ends exist;
slot edge needs component with `plugs_into == slot` and host whose `slots` contains it; flow edge needs
`from_port ∈ outsOf(from)` and target with an input). `outsOf` = `route.type`: `list(paths)+["other"]`;
`route.confidence`: `["pass","review"]`; components: `[]`; else `["out"]`; append `"error"` if `on_error=="error_output"`.
"Main" node = not a component; "has input" = not a component and `has_input`. `list(s)` = split on `,`, trim, drop empty.
Ctx = workspace connections (ids) and providers (`id, name, enabled`). Messages use the Unicode `—`, `→`, `“ ”`.

| # | Code | Level | Condition | Message | node_id / field |
|---|---|---|---|---|---|
| 1 | `missing_intake` | error | no main node without input | `Add an intake step to start the pipeline` | — (:35) |
| 2 | `missing_deliver` | warning | no main node with stage `deliver` | `No deliver step: results are not stored anywhere` | — (:36) |
| 3 | `unknown_type` | error | type not in catalog (then skip node) | `{name}: unknown step type {type}` | node (:41) |
| 4 | `required_field` | error | per field (all nodes incl. components), first hit of: required & value `""`/null/`[]` → `required`; `connection` field with value not in ctx → `connection was removed`; `number` field non-numeric → `must be a number` | `{name}: {field.label} — {reason}` | node, `field=key` (:18-22,47) |
| 5 | `unplugged_component` | warning | component without slot edge to a host | `{name} is not plugged into an AI step` | node (:50) |
| 6 | `missing_provider` | error | `ai.model` whose `config.provider` not in ctx | `{name}: choose a provider` | node (:53) |
| 7 | `provider_disabled` | error | provider found but `enabled=false` | `{name}: {provider.name} is disabled in Models` | node (:54) |
| 8 | `invalid_schema_field` | error | `ai.parser` schema empty | `{name}: add at least one field` | node (:58) |
| 9 | `invalid_schema_field` | error | any schema name not `^[a-z][a-z0-9_]*$` | `{name}: field names must be snake_case` | node (:59) |
| 10 | `no_incoming_connection` | error | main node with input and no flow edge into it | `{name} has no incoming connection` | node (:63) |
| 11 | `dangling_output` | warning | non-deliver main node, per output ≠ `error` with no edge from it | `{name} leads nowhere` (1 output) / `{name} → “{port}” leads nowhere` (>1) | node (:64-70) |
| 12 | `missing_model` | error | required slot `model` empty (`required_slots`; AI steps default `["model"]`) | `{name} needs a language model` | node (:72-75) |
| 13 | `missing_parser` | error | required slot `parser` empty (`ai.extract`) | `{name} needs an output schema` | node (:72-75) |
| 14 | `invalid_threshold` | error | `route.confidence` and not `0 < Number(threshold) <= 1` | `{name}: threshold must be between 0 and 1` | node, `field=threshold` (:76-78) |
| 15 | `loop` | error | cycle over flow edges (DFS from every main node) | `The pipeline contains a loop. Use retries instead of wiring back.` | — (:82-97) |

Order of output: 1, 2, then per node in graph order (3 | 4…, then 5–9 for components, or 10, 11, 12/13, 14 for main
nodes), then 15. `valid = no error-level issue`. Publish, activate (on the **live version's** graph) and test-runs reject
on any error (`backend.ts:456,511,574`). Tests: `features/builder/validate.test.ts` cases are a ready-made test list.

## 10. Top 10 things most likely to break live mode

1. **Draft ETag** — body `etag` missing/different from the header, ETag not changed on every PUT/publish/restore, or
   If-Match compared with RFC strong semantics against a weak `W/"…"` tag → endless 412 conflict dialog (§2, G3/G4).
2. **CORS preflight** — `Authorization`, `X-API-Key`, `Idempotency-Key`, `If-Match`, `Content-Type` not allowed, or
   OPTIONS rejected by auth middleware (incl. the SSE route) → every write shows "Could not reach the API".
3. **SSE** — `event` missing inside `data`, no replay from the first event (client subscribes after the 202), response
   buffered by GZip/proxy, or no `run.finished` → run panel spins forever (§5).
4. **Result race** — `run.finished` emitted before the result is stored → portal silently shows built-in values, masking bugs.
5. **Sample documents** — `doc_inv_sample` / `doc_rcp_sample` not seeded → test runs, step tests and worker invoke fail (G12).
6. **Body-less POSTs** — activate, pause, restore, connection test, worker health, MCP discover, models sync declared with a
   required body → 422 `invalid_request` (§1 "body: none").
7. **List shape / lists of all** — non-paginated lists returned as bare arrays, or `/review-items` returning decided items
   (sidebar count, queue) (§0, G6).
8. **Problem bodies** — any error without `title` loses `detail/issues/usages`; 409 without `usages` shows an empty
   "in use" dialog; review decision without `document` throws in `onSuccess` and is reported as a network error (§3, G7).
9. **Auth bootstrap** — the portal starts with **no credential** (no auth header) and persists mode/base URL/credential in
   `sessionStorage["dis.api.config"]`, which overrides `VITE_API_MODE` (`config.ts:23-35`). QA: clear it, then Settings →
   API access → X-API-Key = bootstrap key → Apply.
10. **Validator / catalog drift** — server not filling config defaults or not dropping invalid edges before validating,
    different codes/messages, or a catalog that is not `step_types.json` → publish blocked server-side while the builder
    shows "Ready to publish" (or the reverse) (§9).

Also watch: strict URL types rejecting `""`/`null` the portal sends in PATCH bodies (providers `data_region:""`,
`default_model:""`, `connection_id:null`); `PATCH /settings` receiving the full object incl. `sso`; `204` responses with a body.

## 11. Decisions for the backend team

- D1 Dev auth: allow anonymous admin in `DI_ENV=development`, or require the bootstrap key (portal shows 401 toasts until set)?
- D2 Test runs that hit Human review: keep mock behaviour (continue, final `review`, no review item) — recommended for
  parity — or pause and create a review item (portal still works, but the run panel only finishes when the stream ends).
- D3 ETag format: keep `W/"<id>-<rev>"` with exact-match comparison (recommended, matches fixtures) or strong tags.
- D4 Default filter for `GET /review-items` = open + claimed (needed by the portal; YAML has no status filter).
- D5 CORS `Origin: null` (sandboxed preview iframe) in development.
- D6 Lead: fix `catalog_data.step_types_by_id()` to key by `"type"` (G15); correct `docs/04 §7` (`provider`, `req`) and
  the `unknown_step_type` code (G10/G14).
