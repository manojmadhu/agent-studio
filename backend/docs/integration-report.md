# Portal live-mode integration report

Date: 2026-11-26 · Author: Frontend engineer · Contract notes: `docs/portal-integration-notes.md`

## Setup used

- DB `postgresql+psycopg://di:di@localhost:5432/docintel_test_qa`, reset before every pass:
  `drop schema public cascade; create schema public;` + `alembic upgrade head`. The lead's
  `alembic downgrade base && alembic upgrade head` failed with `DuplicateTable: idempotency_keys`: the DB held tables
  but no `alembic_version` row (created earlier by a `create_all`), so `downgrade base` dropped nothing (see I-8).
- Backend: `uvicorn app.main:app --port 8080` with `DI_BOOTSTRAP_API_KEY=dik_test_admin` and
  `DI_DEV_ANONYMOUS_ADMIN=true`, or `false` for the credential pass. Seeded on startup.
- Portal: `VITE_API_MODE=live VITE_API_BASE_URL=http://localhost:8080/v1` build, `vite preview` on :4173, Chrome via Playwright.
- No portal code was changed. **No backend fixes were made** (no `# FE-integration:` edits).

## Results

| Pass | Result |
|---|---|
| `e2e/smoke.py` (anonymous admin) | **42/43**. The one failure ("no console errors during the whole session") is Chrome logging the **expected** 409 from the delete-in-use check as `Failed to load resource`. The mock never hits the network, so it can't produce this. It is a mock-only assumption in the test, not a backend failure. |
| Manual flows, anonymous admin (`/tmp/qa/flows.py anon`) | All product flows pass. The remaining failures were bugs in my own script (selectors), each re-checked by hand or with curl. |
| Manual flows, **X-API-Key `dik_test_admin` set in Settings → API access**, anonymous **disabled** | **41/42**. The only failure is the provider-switch selector in the script; `PATCH /providers/prv_ollama {enabled}` works with curl. Every request after Apply carries `X-API-Key`, including the SSE stream (unauthenticated SSE → 401). |
| Contract diff: every GET in `app/data/mock_golden_responses.json` vs live (shape + values) | All 200, same shapes. Value differences are listed below as I-3 to I-6. |
| Error and mutation replay (409/412/422/428 cases, validate, step test, worker invoke/health, connection/provider tests, MCP call) | Same status, `code`, `title`, `detail`, `usages[]`, `issues[]` and `application/problem+json` as the mock. |
| SSE byte-level check (`/runs/{id}/events`) | Same event order and fields as the mock (`event` inside `data`, `id:` lines, cumulative `layers`, `output_port`). Timing is close to the mock (about 9 s for `pl_ap`). Replay after finish and `Last-Event-ID` resume work. The result is stored before `run.finished`. With the gate threshold raised to 0.9 the run emits `run.routed`, finishes as `review` and creates no review item (same as the mock). |

Flows verified end to end in the browser, in both auth modes:

- **Pipelines list and KPIs:** load correctly.
- **Builder (`pl_ap`):**
  - A field edit autosaves via `PUT` + `If-Match`.
  - A second edit chains the new ETag (`W/"pl_ap-2"` → `-3`) with no conflict dialog.
  - Draft changes survive a reload.
  - Publish v15 works and the Versions dialog lists it.
  - Pause/activate work.
- **Test run:** streams to "Finished", shows the `→ invoice` and `→ pass` ports, and the Extracted data tab has 10 rows with IBAN masked. The single-step test works.
- **Review:** `rv2` opens, an approve with a correction is sent, the app returns to the queue and the count drops 4 → 3.
- **Connections:**
  - Create shows only the hint (`••••WXYZ`). Test works.
  - Rotating the secret updates the hint, and no secret appears in the DOM.
  - Delete in use returns 409 with the usage list; "Delete anyway" (`force=true`) then deletes.
  - Deleting an unused connection works, and so does starting the OAuth sign-in.
- **Models:** test prompt, sync and save all work.
- **Workers:** health check works, and the test invoke returns a PagesResult. Save works.
- **MCP:** discover returns 3 tools and the tool call returns a result. Save works.
- **Settings:**
  - API keys: create (secret shown once), rotate and revoke all work.
  - Webhooks: create shows the signing secret; delete works.
  - The audit list shows the new writes.
- **⌘K palette:** opens a pipeline.

Screenshots: `portal/e2e/shots/live_*.png`.

## Issues

| # | Severity | Owner | Endpoint / area | Expected (mock) | Actual (live) | Suggested fix |
|---|---|---|---|---|---|---|
| I-1 | **Medium** | BE1 (components) | `DELETE /connections/con_qdrant?force=true` (a connection used by knowledge collections) | 204: force delete always succeeds | 409 `connection_in_use`, detail "Knowledge collections need a vector store connection…", `usages` = the 3 collections. The portal's delete dialog catches the 409 and shows the same "in use" list again. The reason (`detail`) is **never shown**, so the user is stuck with no explanation. | Either make the force delete work like the mock (null the collection's `connection_id` and set the collection status to `failed`), or keep the rule but use a distinct code (e.g. `connection_required_by_collection`). Then raise it in the lead's notes so the portal can toast the `detail` (a portal change, coming later). |
| I-2 | Low | BE1 (identity/ops) | seed: `/me`, `/settings` | workspace "Example Trading", user "Manoj M." (`app/data/seed.json`) | "Acme Finance", user "Admin" (`usr_admin`) | Cosmetic. Use `seed.json.settings.name` and `me` for the dev principal if parity with the mock demo matters. Note the 412 detail still says "Manoj M. saved this draft" (seeded `saved_by`). |
| I-3 | Low | DB / BE1 (connections) | `GET /connections?limit=200` order | seed order (`con_gmail, con_wa, con_anthropic…`) | reverse id order (`con_workers, con_wa, con_s3…`): all seeded rows share one `created_at`, so the `(created_at, id) DESC` keyset falls back to id | Seed with staggered `created_at` (seed index × 1 ms), or order lists ascending. The same applies to policies (`pol_region` before `pol_schema`). |
| I-4 | Low | BE2 (review) | `GET /review-items` order | `rv1, rv2, rv3, rv4` | `rv2, rv3, rv1, rv4` (by SLA due) | Acceptable, arguably better. Only note it if QA compares against mock screenshots. |
| I-5 | Low | BE1 (ops) | `GET /metrics/overview` | `documents_change 0.084`, `straight_through_change 0.013`, `median_change_ms -400`, `median_ms 6200` | `0.0 / 0.0 / 0 / 6335` (computed) | Fine, it's real computation; the KPI cards show "0.0% vs previous day". Seed previous-window runs if the demo needs trend arrows. |
| I-6 | Info | BE2 (runs) | `GET /runs` | 8 seed runs | 12 (adds the 4 review-linked runs `run_48202/4/6/8`); `GET /runs/run_48210/events` returns a synthesized `run.finished` where the mock returned 404 | Better than the mock; no action. |
| I-7 | Low | Lead (CORS config) | requests with `Origin: null` (preview build in the sandboxed iframe, `e2e/sandbox.py`) | n/a | no `Access-Control-Allow-Origin`, so blocked | Decide on D5. If needed, allow `null` in development only. |
| I-8 | Low | DB engineer | `alembic downgrade base && alembic upgrade head` on `docintel_test_qa` | clean reset | `DuplicateTable idempotency_keys`: tables existed without an `alembic_version` row (a test `create_all` or a `drop_all` that didn't finish) | Make `tests/conftest.py` drop what it creates, or document `drop schema public cascade` as the reset step. |
| I-9 | Info | Portal (frontend, follow-up) | after setting a credential in Settings → API access | n/a in mock (no auth) | queries that got 401 before Apply (`/settings`, `/api-keys`, `/webhooks`, `/audit-events`, `/review-items`) keep their error until the page is reopened | Portal: invalidate all queries when the credential changes (`onApiConfig` → `queryClient.invalidateQueries()`). Live-only, so per the rules it is not changed now. |
| I-10 | Info | QA / test | `e2e/smoke.py` "no console errors" | mock: no network, so no console noise | the intended 409 shows as a console error | In live mode, ignore `Failed to load resource … 409` for the delete-in-use step. |
| I-11 | Info | — | Chrome `requestfailed net::ERR_ABORTED` | — | reported by Playwright for every 204 DELETE and for the SSE GET after `run.finished` (the client cancels the reader) | Benign. The raw 204s (`curl -v`) are clean (no body, CORS headers present) and the UI shows success. |

No issue blocks live mode: every portal flow works against the real backend with no frontend changes.
