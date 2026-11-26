# BE2 — pipelines & runs (implementation notes)

Owner: Backend engineer 2. Scope: routers `pipelines` (Pipelines/Versions/Templates), `documents`, `runs`, `ingest`,
`review`; services `validator`, `storage`, `event_bus`, `run_engine` (+ `run_engine_live`), `lifecycle`.
Tests: `tests/be2/` (`DI_TEST_DATABASE_URL=…/docintel_test_be2 DI_RUN_STEP_DELAY_MS=0 pytest tests/be2`).

## Endpoints (34/34 operations of the tags)
Pipelines 8 · Versions 7 · Templates 2 · Documents 4 · Runs 8 (incl. test-runs, step test, SSE) · Ingest 1 · Review 4.
Behaviour follows `portal/src/api/mock/backend.ts`; asserted against `app/data/mock_golden_responses.json`.

## Decisions / deviations
- ETag `W/"<pipeline>-<rev>"`, exact comparison, bumped on save, publish and restore; header + body `etag`.
- Draft save and `validate` normalise the graph like the portal (`serializeGraph∘deserializeGraph`): unknown node
  types and invalid edges are dropped, config defaults and settings filled. Validator = 1:1 port of `validate.ts`
  (`unknown_type`) + server-only `data_residency` (enabled policy `allowed_regions` vs provider/worker `data_region`).
- Pipelines are soft-deleted (`archived_at`): runs/review items keep their FK ("runs are retained for audit").
- Lists: pipelines in creation order, runs newest first (keyset), review items most-urgent SLA first (open+claimed
  by default; `?status=` extension).
- Sample documents `doc_inv_sample`/`doc_rcp_sample` are seeded rows in `ws_default` and virtual in other
  workspaces (bytes synthesised: a real one-page PDF / JPEG). Unknown `document_id` on test-runs → 404 (mock falls back).
- Run engine: simulate mode = `runEngine.ts` event-for-event (verified against the 4 golden SSE transcripts);
  sleep = mock duration × `run_step_delay_ms`/350. Test runs never pause at Human review (finish `review`, no item);
  production runs (ingest) pause, create a review item and resume on decision (approve → walk continues with
  corrections applied; reject → `failed`, `review_rejected`). Seeded review runs without engine state get the
  mock's direct status change.
- SSE: full replay from seq 1 (or `Last-Event-ID` header / `?last_event_id=`), `: keep-alive` every 15 s,
  `Cache-Control: no-cache`, `X-Accel-Buffering: no`, closes after `run.finished`; terminal runs with no events
  (seeded history) get one synthetic `run.finished`. Results are committed with the `run.finished` event.
- Live mode: workers via `adapters.invoke`, models via `llm.complete`; `retries`/`timeout_s`/`on_error` honoured.
  Deliver steps are simulated unless `deliver: true`; only `out.export` has a real delivery (object store) —
  others fail with `delivery_not_supported`. MCP/tool calls inside AI steps are not executed yet.
- Page images: PDFs rendered with pypdfium2, images converted to PNG; redaction boxes drawn from recorded
  `overlays.redactions` bboxes (simulated runs record none → `X-DI-Redacted: false`). `redacted=false` needs
  `pipelines:write` and is audited.
- Ingest: multipart (contract) + JSON `{content_base64|url}` extension; API key `ingest:write` or HMAC with the
  pipeline secret (`ingest_secret_ref`, fallback derived from `secret_key`); own Idempotency-Key handling keyed on
  file content (BE1's middleware skips multipart).
- Startup recovers orphaned runs (queued → re-enqueue, running w/o events for 10 min → failed); shutdown waits
  up to `DI_SHUTDOWN_GRACE_S` (10) for in-flight steps.
