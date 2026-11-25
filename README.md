# Agent Studio — Doc Intelligence Studio

Enterprise platform for building AI document and image processing pipelines: intake → prepare → read (OCR/layout/spreadsheet workers) → understand (AI classify / extract / agent with pluggable models, memory, tools, MCP servers, knowledge and output schemas) → validate (rules, PII, guardrails) → decide → human review → deliver.

## Repository layout

| Path | What it is |
|---|---|
| `portal/` | **Production React portal** (Vite, React 19, TypeScript, @xyflow/react). Runs against an in-app mock backend until the API exists. See `portal/README.md`. |
| `api/openapi.yaml` | Backend API contract (OpenAPI 3.1). The backend is a separate project, not built yet. |
| `docs/01-mvp-ui-architecture-and-changelog.md` | Product structure, builder behaviour, changelog |
| `docs/02-design-system.md` | Tokens, typography, builder visual language, layout |
| `docs/03-development-handoff.md` | Stack, module map, pipeline JSON, worker adapters, delivery plan |
| `docs/04-backend-api-specification.md` | Backend spec: auth (OIDC, API keys, HMAC), connections and auth types, components, runs, database outline |
| `app/index.html` | Approved single-file HTML prototype (v4.1), the visual reference |

## Run the portal

```bash
cd portal
npm install
npm run dev          # http://localhost:5173 (mock mode)
```

Quality gates: `npm run typecheck`, `npm run lint` (includes a case-sensitivity check for Windows/macOS), `npm test`, `npm run build`, and the browser smoke test `python3 e2e/smoke.py` against `npm run preview`.

Switch to a real backend with `VITE_API_MODE=live` and `VITE_API_BASE_URL=…/v1`, or in the portal under **Settings → API access**.
