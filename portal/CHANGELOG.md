# Changelog

## 1.1.0 — 2026-11-25

### Changed
- Builder side panels are hidden by default; the canvas uses the full width.
  - **Step list:** opens from the **+** button in the canvas toolbar (or the + in the middle of an empty canvas). It closes
    once a step is added, on its × button, on Escape, or on a click outside it.
  - **Settings panel:** opens when a step or connection is clicked (or via the header **Settings** button for pipeline
    settings). It closes on a click outside it, on Escape, on its ×, or when the selected step is deleted.
  - Both panels slide over the canvas instead of taking fixed columns. Pressing inside the step list keeps the current
    selection, so clicking a step adds it after the selected one.
- Empty canvas shows a large **+** ("Add your first step") instead of a drag hint; the empty minimap is hidden.
- Navigation: the active item no longer has a left accent bar; it is shown with a background highlight only.
- Canvas hint text updated: "Press + to add steps · drag from a port to connect · click a step to configure it".

### Tests
- `e2e/smoke.py` now has 43 checks, including: panels hidden on open, the active nav item has no left bar, clicking a
  step shows settings, the empty canvas + opens the step list, adding a step closes it, and an outside click hides both.

## 1.0.1 — 2026-11-25

### Fixed
- `npm run dev` / `npm run build` failed on Windows (and default macOS) with
  `No matching export in "src/ui/menu.ts" for import "Menu"`. The folder `src/ui` held both `Menu.tsx` (the menu component)
  and `menu.ts` (its store). On a case-insensitive file system `import { Menu } from '../ui/Menu'` resolved to `menu.ts`.
  The store is renamed to `src/ui/menuStore.ts` and all imports updated. The 1.0.0 checks ran only on Linux, which is
  case-sensitive, so they did not catch it.

### Added
- `scripts/check-case.mjs` (`npm run check:case`, also run by `npm run lint`): fails if two files in a folder differ only
  by case or extension, or if a relative import's casing differs from the file on disk.
- `forceConsistentCasingInFileNames: true` set explicitly in `tsconfig.json`.

## 1.0.0 — 2026-11-25

First production build of the Doc Intelligence Studio portal (React 19 + TypeScript), at parity with the approved
prototype v4.1.

### Added
- App shell: navy sidebar (Operate · Build · Govern), top bar with breadcrumbs, ⌘K command palette, Sandbox/Live
  indicator; compact 64px rail in the builder and review workspace and below 1100 px; bottom tabs below 760 px;
  light/dark via `prefers-color-scheme`; reduced motion.
- Pipelines: KPIs from `/metrics/overview`, status filter, search, row menu (open, activate/pause, duplicate, delete),
  templates, new pipeline.
- Builder on @xyflow/react with the prototype look: StepNode/AiComponentNode, FlowEdge with `handle()` routing and drawn
  arrowheads, dashed teal AiEdge, midpoint × delete, palette drag/click add, port connections with node-level snapping,
  drag-to-empty “Connect a new step”, AI slots (Model*/Memory/Tools/Knowledge/Output*) with drop-onto-step, slot “+”
  menus, plug-diamond and slot drags, context menus, 8px snapping, undo/redo (zundo), duplicate, test step, tidy,
  fit/zoom, minimap, catalogue-generated Configure forms + Settings/Output tabs, validation (port of `issues()`),
  draft autosave with ETag/If-Match and conflict dialog, Versions (restore), Publish vN with release note, active switch.
- Run panel: Run log, Extracted data (confidence meters, threshold, PII masking), Document with overlays, Problems —
  driven by `RunEvent`s over SSE.
- Review queue and review workspace (editable fields with confidence, document overlays, approve/reject with
  corrections).
- Runs (filters, CSV export), Models/providers, Skills (versions), MCP servers (discover, tool toggles, call tool),
  Workers (adapters, health, test invoke), Policies, Connections (data-driven auth forms, test, sign-in, rotate, usage,
  409 delete-in-use), Settings (API access, workspace, API keys, webhooks, audit).
- Typed API layer generated from `api/openapi.yaml` (openapi-typescript + openapi-fetch), problem+json errors → toasts,
  Idempotency-Key on creates, configurable Bearer / X-API-Key auth.
- In-app mock backend implementing the contract (write-only secrets, 409/412/422, idempotency, SSE) with seed data ported
  from the prototype.
- Tests: 78 Vitest tests; Playwright smoke test `e2e/smoke.py` with screenshots; `e2e/sandbox.py` for the sandboxed
  preview host.
- Preview-safe production build: relative paths, classic deferred script, single stylesheet, inlined woff2 fonts.

### Changed (relative to the prototype)
- Resource references in step config are ids (`con_*`, `wrk_*`, `mcp_*`, `skl_*`, `prv_*`) as in the API.
- Node settings use the API names (`timeout_s`, `on_error: stop|continue|error_output`).
- Activation validates the published (live) version, as specified by the API.

### Fixed (relative to the prototype)
- Switch rows in forms overlapped their label; they now lay out with flex.
- The required “*” under AI slots wrapped onto its own line.
- The top-bar search label wrapped between 1000 and 1180 px.
