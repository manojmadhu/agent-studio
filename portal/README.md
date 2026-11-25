# Doc Intelligence Studio — portal

Production React portal for **Doc Intelligence Studio**: build document/image pipelines on a canvas (intake → prepare →
read → understand (AI) → validate → decide → deliver), plug AI components (models, memory, tools, knowledge, output
schemas) into AI steps, test-run them on sample documents, review low-confidence results, and configure every component
(connections, model providers, workers, MCP servers, skills, policies, API keys) through the backend API.

The approved prototype (v4.1, `ref/src5.html`) is the visual and behavioural source of truth; the backend contract is
`api/openapi.yaml` (OpenAPI 3.1). The backend does not exist yet, so the portal ships with an **in-app mock backend** that
implements the same operations.

## Quick start

```bash
npm install            # Node 22. .npmrc sets legacy-peer-deps (see Decisions)
npm run dev            # http://localhost:5173  (mock mode by default)
npm run build          # typecheck + production build → dist/ (relative asset paths)
npm run preview        # serve dist on http://localhost:4173
```

| Script | What it does |
| --- | --- |
| `npm run gen:api` | Regenerates `src/api/schema.d.ts` from `api/openapi.yaml` (openapi-typescript, `--default-non-nullable false`). Never edit the generated file. |
| `npm run typecheck` | `tsc --noEmit` (strict) |
| `npm run lint` | ESLint (typescript-eslint + react-hooks) + case-sensitivity check (`scripts/check-case.mjs`) so the tree also works on Windows/macOS |
| `npm test` | Vitest (jsdom) — unit, API-layer, mock-backend and component tests |
| `npm run build` | `tsc --noEmit` then `vite build` |
| `npm run e2e` | Playwright browser smoke test against `npm run preview` (see below) |
| `python3 e2e/sandbox.py` | Loads the preview build inside a `sandbox="allow-scripts"` iframe and checks it runs |

### Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `VITE_API_MODE` | `mock` | `mock` = in-app mock backend, `live` = call `VITE_API_BASE_URL` |
| `VITE_API_BASE_URL` | `http://localhost:8080/v1` | Backend base URL including `/v1` |

Both can be changed at runtime in **Settings → API access** (mode, base URL, `Authorization: Bearer` or `X-API-Key`).
The credential is kept only in memory and `sessionStorage` (wrapped in try/catch because sandboxed hosts can throw) and is
never logged or rendered back.

## Architecture / module map

```
api/openapi.yaml               backend contract (copied from ref/, source of truth for types)
src/
  main.tsx                     fonts (@fontsource IBM Plex), CSS, mount
  app/
    App.tsx                    QueryClient + react-router memory router; route table
    hashSync.ts                memory history ⇄ plain hash (#pipelines, #builder-pl_ap, #review-rv1)
    Shell.tsx, nav.ts          navy sidebar (Operate · Build · Govern), top bar, compact rail, ⌘K
    CommandPalette.tsx         ⌘K palette (pages, pipelines, actions)
    crumbs.ts                  breadcrumb store
  api/
    schema.d.ts                GENERATED (npm run gen:api)
    types.ts                   friendly aliases of generated types
    config.ts                  mode / base URL / credential (memory + sessionStorage)
    client.ts                  openapi-fetch typed client; `api.*` = every operation the portal uses
    errors.ts                  problem+json → ApiError, toast messages
    sse.ts                     /runs/{id}/events via fetch streaming (auth headers, Last-Event-ID)
    hooks.ts                   TanStack Query hooks, mutation helper (invalidate + error toasts)
    mock/                      in-app mock backend (no MSW, no service worker)
      backend.ts               MockBackend.fetch(Request) — routes, ETags, 409/412/422, idempotency, SSE stream
      seed.ts                  seed workspace ported from the prototype (pipelines, connections, providers, …)
      services.ts              /catalog/services data (auth schemes + settings fields per service)
      runEngine.ts             simulated executor → RunEvent sequence + RunResult
      sampleData.ts            sample documents and extraction values
  features/
    builder/                   the pipeline builder
      catalog.ts               step catalogue (stable type IDs, FieldDefs, slots) ⇄ OpenAPI StepType
      validate.ts              1:1 port of the prototype issues()
      graph.ts                 graph helpers, ports, serialisation to the API Graph shape
      geometry.ts              handle() routing, curves, drawn arrowheads, bezier midpoint
      layout.ts                free-spot placement, tidy layout, bounds
      ops.ts                   pure editing operations + connection targeting/snapping rules
      store.ts                 zustand editor store + zundo undo/redo (drag = 1 step, typing coalesced)
      autosave.ts              debounced PUT draft with If-Match; 412 → conflict dialog
      runStore.ts              test-run state driven by RunEvents (identical in mock and live)
      panels.ts                step list + settings panel visibility (hidden by default, outside-click to close)
      Canvas.tsx               @xyflow/react canvas (custom nodes/edges, menus, DnD, wheel, fit, tidy)
      StepNode.tsx             StepNode (248×68) + AiComponentNode (208×48)
      FlowEdge.tsx             FlowEdge (handle() routing + drawn arrowhead) + AiEdge (dashed teal)
      MiniMap.tsx, Palette.tsx, Inspector.tsx, FieldInput.tsx, RunPanel.tsx, BuilderPage.tsx
    document/DocumentViewer.tsx sample page + overlays (blocks, tables, fields, redactions)
    pipelines/ review/ runs/ models/ skills/ mcp/ workers/ policies/ connections/ settings/
  ui/                          Icon (Carbon), Dialog, Menu (+ menuStore), Toasts, small primitives
  styles/app.css               prototype CSS ported verbatim (tokens in :root, light/dark)
  styles/flow.css              React Flow integration + additions (drawers, forms) — tokens only
e2e/smoke.py                   Playwright smoke test; screenshots in e2e/shots/
e2e/sandbox.py                 the build inside a sandboxed iframe (opaque origin)
```

### Graph model

The builder edits the OpenAPI `Graph` directly: `nodes[] {id,type,name,x,y,config,settings}` and
`edges[] {id,from,from_port,to,slot?}`. AI component edges use `from_port: "plug"` plus `slot`
(`model|memory|tools|knowledge|parser`). `serializeGraph()` normalises positions, settings defaults and drops invalid
edges before every save. Resource references inside `config` are ids (`con_*`, `wrk_*`, `mcp_*`, `skl_*`, `prv_*`).

### Mock vs live

* One typed client for both modes. In mock mode the openapi-fetch transport is `MockBackend.fetch`, so every request goes
  through the same code path (headers, Idempotency-Key, problem+json parsing) as in live mode.
* The mock honours the contract: write-only secrets (`secret_hint` only — secret material lives in a private vault map),
  `409 connection_in_use` with `usages[]` (and `?force=true`), draft `ETag`/`If-Match` with `412 etag_mismatch` and
  `428` without If-Match, `422 pipeline_invalid` with `issues[]` for publish/activate/test runs, idempotent replays,
  cursor pagination, and a real `text/event-stream` body for `/runs/{id}/events`.
* Test runs: the mock run engine walks the graph like the prototype and emits `run.started → step.started/step.finished
  (… output_port, layers) → run.routed → run.finished` with timers. The run panel only consumes those events, so it behaves
  identically against a real backend.
* Mock state is in memory; reloading the page resets the sandbox workspace.

### How components connect via the API

Every external component authenticates through a **Connection** (`/connections`):

* **Connections** page — data-driven form: services and their allowed auth schemes come from `/catalog/services`;
  each scheme renders the fields of its `AuthConfig` member (`api_key`, `bearer`, `basic`, `oauth2_client_credentials`,
  `oauth2_authorization_code`, `hmac`, `mtls`, `aws_sigv4`, `azure_ad_client`, `none`). Secrets are password/textarea
  inputs, cleared after submit, never displayed again (only `secret_hint`). Test connection, Sign in (OAuth authorize),
  rotate secret (PATCH with a full `auth`), usage list, delete with 409 → “Delete anyway”.
* **Models** — provider kind, base URL, data region, connection (filtered to the provider's service), models (enable,
  default), sync from provider, test prompt, enable switch, delete (409 when used).
* **Workers** — base URL, adapter (`generic_pages_v1`, `excel_parser_v1` → `/to-text`, `paddle_docparse_v1`), invoke and
  health paths, connection, timeout, concurrency; health check; test invoke on a sample document showing the normalised
  `PagesResult`.
* **MCP servers** — transport, URL, connection, extra headers; discover tools; enable/disable tools; call a tool with JSON
  arguments (validated against its input schema by the backend).
* **Settings** — API access (mode/base URL/credential), workspace settings, API keys (secret shown once on create and
  rotate, revoke), webhooks (signing secret shown once), recent audit events.
* Builder fields of type `connection`, `worker`, `mcp_server`, `mcp_tools`, `skill`, `provider`, `model`,
  `provider_connection` list live data from these endpoints.

## Testing

* `npm test` — 78 tests: `handle()` routing + arrowhead geometry, every `validate.ts` rule, catalogue integrity,
  serialisation to the API Graph, connection targeting/snapping, editor store (add/connect/delete/undo/redo/slot plug,
  drag = one undo step, typing coalesced), mock API (secrets never returned, rotation, 409 delete-in-use, 412 ETag,
  422 publish, idempotency, SSE run with results, workers/MCP/API keys/providers), live client (auth headers,
  Idempotency-Key, If-Match, problem+json), plain-hash routing, and component render tests.
* `e2e/smoke.py` — run `npm run build && npm run preview` in one terminal, then `python3 e2e/smoke.py`
  (Python Playwright + Chrome at `/usr/bin/google-chrome`). Loads every screen with zero console errors, builds a pipeline
  (palette drag, connect, plug a model, slot menu, delete an edge with the midpoint ×, undo/redo, autosave), runs a test
  run to completion with extracted data and overlays, creates a connection and verifies only the hint is shown, checks the
  409 usage dialog and ⌘K. Screenshots are written to `e2e/shots/`.

## Decisions

* **Builder panels (1.1)** — the step list and the settings panel are overlays, hidden until needed: + opens the step
  list, selecting a step or connection opens its settings, and a press outside either panel (menus, dialogs and toasts
  excluded) closes it. Visibility lives in `features/builder/panels.ts`; settings visibility follows the editor selection.

* **Router** — react-router with `createMemoryRouter`; the location is mirrored to a plain hash (`#builder-pl_ap`)
  with `history.pushState` in try/catch, because the preview host disallows `#/path` hashes. Back/forward work via
  `popstate`/`hashchange`. No service workers anywhere.
* **Preview-safe build** — `base: './'`, a single classic `defer` script (IIFE, no `type="module"`, no
  `crossorigin`), one stylesheet and inlined woff2 fonts (latin subsets). In a sandboxed iframe without
  `allow-same-origin` the document origin is opaque, so module scripts, modulepreload and font fetches are CORS requests that
  a static host usually blocks; the classic build loads anyway (verified by `e2e/sandbox.py`). Web Storage access is
  wrapped in try/catch, and the app keeps working when it throws.
* **Mock as a fetch transport** instead of MSW: no service worker (forbidden in the preview host) and identical client code
  in both modes.
* **SSE through fetch streaming** rather than `EventSource`, so `Authorization`/`X-API-Key` headers can be sent; the parser
  supports multi-line data, comments and `Last-Event-ID`.
* **Undo/redo** with zundo on `{nodes, edges}` only. A node drag records one entry per gesture; keystrokes in the same field
  within 1.5 s coalesce; selection and run state are never recorded.
* **React Flow** renders the prototype's own look: custom nodes/edges/minimap, prototype CSS classes, default library
  styles not imported (only `base.css`). Edge geometry is computed from graph positions (not measured handle boxes) so the
  `handle()` routing and arrowheads match the prototype exactly. Node-level snapping (prototype `targetAt`) complements React
  Flow's handle snapping: dropping anywhere near a compatible step connects to it; dropping on empty canvas opens
  “Connect a new step” (or the slot menu from an AI slot).
* **Catalogue** is local (`catalog.ts`) for instant rendering and type safety; the mock serves the same definitions from
  `/catalog/step-types` via `toStepType()`, and a test asserts the shape. Resource fields store ids (API shape) instead of the
  prototype's display names.
* **Validation** runs client-side on every edit (same rules as the server) and the server re-validates on publish,
  activate and test run.
* **Activation** follows the API: it validates the live version, so an unpublished pipeline reports “Publish a version
  before activating”. Publishing flushes the autosave first and sends `draft_etag`.
* **Prototype fixes**: switch rows in forms now lay out with flex (the label and switch overlapped in the prototype); the
  required “*” under AI slots stays inline; search button text no longer wraps at 1000–1180 px.
* **Cross-platform** — Windows and macOS file systems are case-insensitive; `npm run lint` fails if two files differ only by case/extension or an import's casing differs from the file. Never name a module and a component the same apart from case (e.g. `Menu.tsx` + `menu.ts`).
* **npm** — npm 10.9's arborist crashes resolving the vitest/jsdom optional peer set (`Cannot read properties of null
  (reading 'edgesOut')`), so `.npmrc` sets `legacy-peer-deps=true`. Versions are pinned exactly. TypeScript is 5.9 because
  openapi-typescript 7 and typescript-eslint 8 do not support TS 7 yet; `@xyflow/react` 12 and react-router 7 are the
  latest majors with stable, known APIs.

## Known gaps

* The document viewer renders the bundled sample invoice/receipt and measures overlay boxes from the DOM. Rendering real
  page rasters from `/documents/{id}/pages/{n}/image` with `bbox` overlays (live mode) is not implemented, and test runs
  use the sample documents only (`POST /documents` upload is not wired into the UI).
* Knowledge collections are read-only (used by the Knowledge component); members management, policy create/edit
  (only enable/disable), template management and audit filtering have no UI yet.
* The ETag conflict dialog is covered by API tests but not by the browser smoke test.
* Accessibility was built in (labels, focus rings, keyboard, reduced motion, AA tokens) but no automated axe audit was run.
