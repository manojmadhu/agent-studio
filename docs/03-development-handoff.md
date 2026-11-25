# Doc Intelligence Studio — Development handoff (from MVP UI v4.1)

The prototype in `app/index.html` is the approved UX. This document turns it into a build plan. Visual spec: `docs/02-design-system.md`. Full backend contract: `docs/04-backend-api-specification.md` and `api/openapi.yaml` (supersedes the summary table in §4). The React portal built from this plan is in `portal/`.

## 1. Recommended stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React 19 + TypeScript + Vite | Team-standard, typed domain model |
| Canvas | **React Flow (@xyflow/react, MIT)** with custom node/edge components | Handles pan/zoom, ports, selection, minimap, virtualisation; we keep our own node visuals |
| UI primitives | Radix UI + CSS variables from the token sheet (or Tailwind configured with the same tokens) | Accessible menus, dialogs, tabs, switches |
| State | Zustand (editor state, undo/redo via `zundo`) + TanStack Query (server state) | Simple, testable |
| Forms | React Hook Form + Zod schemas generated from the step catalogue | Field validation identical to the prototype rules |
| Icons / fonts | `@carbon/icons-react` (Apache 2.0); IBM Plex Sans + IBM Plex Mono (OFL), self-hosted | Enterprise icon set, no CDN dependency |
| Backend | FastAPI (Python) orchestration API + worker adapters; PostgreSQL; Redis queue; object storage for documents | Existing workers are Python/FastAPI |
| Runs | Durable execution (Temporal, or Celery/RQ to start) with SSE to the UI | Long-running, retryable steps |
| Secrets | Cloud KMS-backed vault (e.g., Azure Key Vault / AWS Secrets Manager / HashiCorp Vault) | Write-only secrets, rotation |
| Auth | OIDC SSO, workspace RBAC (Admin, Builder, Reviewer, Viewer) | Enterprise requirement |

## 2. Frontend module map

```
src/
  app/            shell (navy sidebar, topbar, ⌘K, routing)
  design/         tokens.css, primitives (Button, Badge, Tile, Menu, Dialog…)
  features/
    pipelines/    list, KPIs, templates
    builder/      Canvas, StepNode, AiComponentNode, FlowEdge, AiEdge, Palette, Inspector, RunPanel, Minimap
      catalog.ts  step + component definitions (stage, icon, fields, outputs, slots)
      validate.ts issues() — port the prototype rules 1:1
      routing.ts  handle() + arrowhead geometry — port from the prototype
    review/       queue, ReviewWorkspace, DocumentViewer (shared)
    runs/ models/ skills/ mcp/ workers/ policies/ connections/ settings/
  api/            typed client generated from OpenAPI
```

`DocumentViewer` (page render + overlays for blocks, tables, fields, redactions) is shared by the builder run panel and the review workspace; in production it renders PDF/image pages (pdf.js) with bounding boxes returned by workers.

**Edges in React Flow:** implement `FlowEdge` as a custom edge that uses the prototype's `handle()` routing (horizontal exit/entry, handle length from gap and vertical distance) and renders the arrowhead as its own path at the target handle, coloured by edge state. Don't rely on SVG `marker-end` (it does not follow state colours and was the source of the v4 arrow bug).

## 3. Pipeline definition (stored per version)

```json
{
  "id": "p_ap", "name": "Accounts payable · supplier invoices", "status": "active",
  "version": 14,
  "nodes": [
    { "id": "n1", "type": "intake.email", "name": "Email inbox", "x": 40, "y": 420,
      "config": { "conn": "c_gmail", "filter": "in:inbox has:attachment" },
      "set": { "retries": 2, "timeout": 60, "onError": "Stop the run" } },
    { "id": "n7", "type": "ai.extract", "config": { "skill": "Invoice field extraction", "instructions": "…" } },
    { "id": "n20", "type": "ai.model", "config": { "provider": "Anthropic", "model": "claude-sonnet-5-5", "temperature": 0.1 } },
    { "id": "n24", "type": "ai.parser", "config": { "schema": [ { "name": "total_amount", "type": "number", "req": 1 } ] } }
  ],
  "edges": [
    { "id": "e1", "from": "n1", "fromPort": "out", "to": "n2" },
    { "id": "e9", "from": "n6", "fromPort": "invoice", "to": "n7" },
    { "id": "e30", "from": "n20", "fromPort": "plug", "to": "n7", "slot": "model" }
  ]
}
```

(The API uses `from_port` and node `settings {retries, timeout_s, on_error}`; see the `Graph` schema in `api/openapi.yaml`.)

Rules: one intake; main edges `fromPort` ∈ outputs of the source (`out`, route names, `pass`/`review`, `error`); slot edges carry `slot` ∈ {model, memory, tools, knowledge, parser}; a component plugs into one step; single-item slots: model, memory, parser.

Step catalogue (type ids are stable API contract): `intake.email|upload|whatsapp|api|storage`, `prep.enhance|split`, `read.ocr|layout|sheet`, `ai.classify|extract|agent`, `val.rules|pii|lookup|injection`, `route.type|confidence`, `review.human`, `out.erp|db|email|whatsapp|export`; components `ai.model|memory|knowledge|parser`, `tool.mcp|worker|calc|search|http`.

## 4. API contract (v1, summary)

| Method | Path | Purpose |
|---|---|---|
| GET/POST | /pipelines | List / create |
| GET/PUT | /pipelines/{id}/draft | Load / autosave draft (nodes, edges) |
| POST | /pipelines/{id}/versions | Publish draft → version N+1 with release note |
| GET | /pipelines/{id}/versions | Version history; POST …/{v}/restore |
| POST | /pipelines/{id}/activate · /pause | Server re-validates |
| POST | /pipelines/{id}/test-runs | Run draft on an uploaded/sample document |
| GET | /runs/{id}/events | SSE: `step.started`, `step.finished` (output, ms), `run.finished` |
| GET | /runs, /runs/{id} | History and detail (with document, fields, bounding boxes) |
| GET/POST | /review-items, /review-items/{id}/decision | Queue; approve/reject with corrected fields |
| GET/POST | /providers | Model providers, enablement, default connections |
| GET/POST | /connections; POST /connections/{id}/test | Secrets write-only; responses return `secret_hint` only |
| GET/POST | /workers; POST /workers/{id}/health | Worker registry |
| GET/POST | /mcp-servers; POST …/{id}/discover | MCP registry and tool discovery |
| GET/POST | /skills, /policies | Versioned skills; workspace policies |

Validation runs on both sides; the server is authoritative for publish/activate.

## 5. Worker adapters

| Worker | Status | Contract |
|---|---|---|
| Excel Parser Service (`excel_parser_service.py`) | Found | `POST /to-text` (multipart xlsx) → `{text_repr, sheet_names, total_cells, char_count}`; `GET /health` |
| Paddle DocParse (`paddle-docparse.zip`) | To confirm | Needs routes; must return text blocks with page + bounding boxes and tables for overlays |
| Image enhancement | Planned | `POST /preprocess` |

Adapter output standard: `{pages:[{n, width, height, blocks:[{text, bbox, confidence}], tables:[…]}]}` so the DocumentViewer can draw overlays from any worker.

## 6. Delivery plan

1. **Foundation (2 wks):** repo, tokens + primitives, shell, auth stub, OpenAPI client, CI.
2. **Builder (3–4 wks):** React Flow canvas with custom StepNode/AiComponentNode/edges, palette, inspector forms from catalogue, validation, undo/redo, draft autosave.
3. **Runtime (3–4 wks):** orchestration API, worker adapters (Excel, Paddle), test runs with SSE, run panel + DocumentViewer.
4. **Operate & govern (2–3 wks):** pipelines list/KPIs, versions/publish, review queue, runs, providers, connections vault, MCP, skills, policies.
5. **Hardening (2 wks):** RBAC, audit log, data residency enforcement, load tests, accessibility audit (WCAG 2.1 AA).

Acceptance for the builder: every gesture listed in `docs/01-mvp-ui-architecture-and-changelog.md` (Builder table) works with mouse, touch and keyboard; validation messages match the prototype; arrowheads stay attached to the input port when steps are moved above, below or behind each other.
