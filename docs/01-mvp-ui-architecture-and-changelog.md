# Doc Intelligence Studio — MVP UI

Live prototype: https://claude.ai/artifact/7dN5e5ASTapTFgYyFGqLDp
Source: `app/index.html` (single self-contained file, vanilla JS, IBM Carbon icon subset embedded).
Design system: `docs/02-design-system.md` · Development handoff: `docs/03-development-handoff.md` · Backend API: `docs/04-backend-api-specification.md` + `api/openapi.yaml`
Production portal (React): `portal/README.md`, `portal/CHANGELOG.md` · preview https://claude.ai/artifact/4gWHbabJCsvkt3JYJGMoVM
Current version: **Portal v1.1.0 · 2026-11-25 — builder panels hidden by default (+ to add steps, settings on click)**

## Product structure

| Area | Screen | Purpose |
|---|---|---|
| Operate | Pipelines | KPIs; table of pipelines (status, intake, volume, straight-through rate, latency, version, draft changes); filter, search, row menu (open, activate/pause, duplicate, delete); templates; new pipeline. |
| Operate | **Builder** | Drag-and-drop canvas (see below). |
| Operate | Review queue | Documents routed to people; detail view with editable fields, confidence, low-confidence fields highlighted on the page; approve/reject. |
| Operate | Runs | Processed documents with status, fields, duration, pipeline version. |
| Build | Models · Skills · MCP servers · Workers | Providers (Anthropic, OpenAI, Azure OpenAI, Gemini, Ollama) with enable, models, connection, region, usage; versioned skills; MCP servers and tools; workers with health check. |
| Govern | Policies · Connections | Workspace rules; write-only credentials (Gmail, WhatsApp, LLM keys, PostgreSQL, S3, Qdrant, HTTP). |
| Global | ⌘K, Settings | Command palette; API URL, environment, region. |

## Builder

Layout (portal v1.1): full-width **Canvas** (dotted grid, floating toolbar, minimap, run panel). The **Step list** (300 px, searchable, grouped by stage) and the **Settings panel** (384 px) are overlays, hidden by default. The step list opens from **+** (toolbar, or the large + on an empty canvas) and closes after a step is added or on an outside click. The settings panel opens when a step or connection is clicked and closes on an outside click, Escape or ×.

| Action | How |
|---|---|
| Add a step | Press **+** to open the step list, then drag a step onto the canvas; or click it (adds after the selected step and connects); or right-click canvas → Add step here; or drag from an output port to empty canvas → "Connect a new step". |
| Connect | Drag output port → step (snaps within ~30 px, targets highlighted); drag input port → step (reverse). |
| AI components | AI steps (Classify document, Extract fields, AI agent) have slots: **Model\***, Memory, Tools, Knowledge, **Output\*** (Extract). Fill by: + under a slot, drag from a slot to a component or to empty canvas (menu), drag a component's top diamond onto the step, drop a component from the palette onto the step, or click it in the palette (auto-plugs into the selected / first free AI step). |
| Delete | Step: hover toolbar, right-click, Configure ⋯ menu, Delete key. Connection: hover and press × at its midpoint, click and press Delete, right-click. |
| Other | Undo/redo (Ctrl+Z / Ctrl+Shift+Z / Ctrl+Y), duplicate (Ctrl+D), test a step, tidy layout, fit, zoom, minimap navigation, replace intake, unplug component. |
| Run panel | Run log (with per-step output) · Extracted data (values, confidence, PII masking) · Document (sample page with text-block, table, field and redaction overlays) · Problems (validation). |
| Publishing | Draft changes tracked; Versions dialog; Publish vN with release note; active switch. Publishing/activation blocked while errors exist. |

### Wire routing (v4.1)

Main-flow wires are cubic curves that always **leave an output port horizontally and enter an input port horizontally**. The handle length is computed from the horizontal gap and vertical distance (`handle()`): forward links use `max(48, gap/2) + min(0.18·Δy, 70)` capped at 220; backward or stacked links use `max(90, Δy/2.2 − gap/3)` capped at 280, giving a clean S-loop. The arrowhead is a separate 10 px triangle drawn at the input port (not an SVG marker), so it always points into the port, moves with the step while dragging, and takes the wire's state colour (selected, hovered, live run). The delete × sits on the true curve midpoint (t = 0.5). AI component wires are vertical dashed curves without arrowheads.

Validation: one intake; incoming connection for every non-intake step; required fields; connections exist; AI steps have a model (and Extract an output schema); provider enabled; schema field names snake_case; confidence 0–1; unplugged components and dead-end outputs warned; no loops.

## Data model

See `docs/03-development-handoff.md` §3 and the `Graph` schema in `api/openapi.yaml` (nodes + edges; AI components are nodes connected with `from_port: "plug"` + `slot` edges; node settings `retries`, `timeout_s`, `on_error`).

## Known limits

Prototype (`app/index.html`): sandbox only, simulated runs, localStorage state, no auth.
Portal v1.0.0: runs against an in-app mock backend until the API exists (state resets on reload); document viewer uses bundled sample pages (real page images + upload not wired); no UI yet for knowledge-collection create, members, policy create/edit; no automated accessibility audit. Details in `portal/README.md` → Known gaps.

## Changelog

- **Portal v1.1.0 · 2026-11-25** — Step list and settings panel hidden by default and shown as overlays: + opens the step list (large + on an empty canvas), clicking a step opens its settings, clicking outside closes them. Removed the left accent bar on the active navigation item. Empty minimap hidden. Browser test extended to 43 checks.
- **Portal v1.0.1 · 2026-11-25** — Fixed the Windows/macOS startup error (`Menu.tsx` vs `menu.ts` name clash on case-insensitive disks); added a case-sensitivity check to `npm run lint`.

- **Portal v1.0.0 · 2026-11-25** — Production React portal (Vite, React 19, TypeScript strict, @xyflow/react, zustand + zundo, TanStack Query, @carbon/icons-react, IBM Plex) built to the v4.1 design: all screens, full builder parity, run panel driven by SSE run events. Typed API client generated from `api/openapi.yaml`; `VITE_API_MODE=mock|live`, Bearer or X-API-Key auth; in-app mock backend enforcing write-only secrets, 409 delete-in-use, ETag/412 drafts, 422 validation, idempotency. Connection forms for 10 auth types; workers (Excel parser, Paddle DocParse adapters), providers, MCP servers, API keys and webhooks configurable through the API. Quality gates: 0 type errors, 0 lint issues, 78/78 unit tests, 34/34 browser checks + sandbox check, clean production build. Backend OpenAPI 3.1 spec added (`docs/04`, `api/openapi.yaml`).
- **v4.1 · 2026-11-25** — Fixed wire arrowheads when steps are moved above/below or behind each other (horizontal-entry routing, drawn arrowheads, true midpoint for delete). Enterprise visual refresh: navy navigation shell, cobalt brand (#1652c8), teal reserved for AI (#0e7490) with a teal edge and "AI" tag on AI steps instead of gradients, muted stage tints, IBM Plex Sans + Plex Mono, IBM Carbon icons (Apache 2.0) replacing Lucide, custom logo mark. Fixed a class-name collision (`.lg`) that pushed large header icons out of their tiles.
- **v4 · 2026-11-25** — Builder returned to drag-and-drop canvas inside the v3 design system: palette, AI component slots, wires with midpoint delete, context menus, snapping, undo/redo, tidy, minimap, run panel with document overlays.
- **v3 · 2026-11-25** — Enterprise design system, stage-list composer (superseded), review queue, versioning, KPIs, ⌘K. 3D three.js/Blender trial dropped.
- **v2.1 · 2026-11-24** — Agent connection fixes on the v2 canvas.
- **v2 · 2026-11-23** — Processes list, AI models, credentials, agent slots, deletion, tabbed inspector.
- **v1 · 2026-11-23** — First MVP canvas builder.
