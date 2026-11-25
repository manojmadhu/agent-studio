/**
 * In-app mock backend. Implements the OpenAPI operations as a `fetch`-compatible function so the portal's
 * typed client is identical in mock and live mode. No service workers, no MSW.
 *
 * Contract details honoured: write-only secrets (`secret_hint` only), 409 for deleting resources in use,
 * ETag / If-Match with 412 on drafts, Idempotency-Key replay on creates, problem+json errors,
 * cursor pagination, and an SSE stream for `/runs/{id}/events`.
 */
import type {
  ApiKey,
  AuthType,
  Connection,
  Draft,
  Graph,
  McpServer,
  Pipeline,
  Policy,
  Provider,
  ReviewItem,
  ReviewItemDetail,
  Run,
  RunDetail,
  RunEvent,
  RunResult,
  Skill,
  UsageRef,
  VersionSummary,
  Worker,
} from '../types';
import { CATALOG, INVOICE_SCHEMA, RECEIPT_SCHEMA, STEP_TYPES, toStepType } from '../../features/builder/catalog';
import { deserializeGraph, insOf, isSub, mainNodes, newNode, serializeGraph } from '../../features/builder/graph';
import { issues } from '../../features/builder/validate';
import { PRIMARY_SECRET } from '../../features/connections/authFields';
import { seed, TEMPLATES, type SeedPipeline, type SeedState } from './seed';
import { SERVICES } from './services';
import { SAMPLE_DOCS, VALUES } from './sampleData';
import { buildResult, execute, stepOutput, type EngineDoc } from './runEngine';

export interface MockOptions {
  /** artificial network latency in ms (default 80) */
  latency?: number;
  /** multiplier for simulated step durations (default 1) */
  speed?: number;
  now?: () => number;
}

type Body = Record<string, unknown>;
interface Ctx {
  method: string;
  path: string;
  params: string[];
  query: URLSearchParams;
  headers: Headers;
  body: Body;
  request: Request;
}
type Handler = (c: Ctx) => Response | Promise<Response>;

const JSON_H = { 'Content-Type': 'application/json' };
const json = (status: number, body: unknown, headers: Record<string, string> = {}): Response =>
  new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { ...JSON_H, ...headers } });
const noContent = () => new Response(null, { status: 204 });
export const problem = (status: number, title: string, code: string, detail?: string, extra: Body = {}): Response =>
  new Response(
    JSON.stringify({ type: `https://docs.docintel.example.com/errors/${code}`, title, status, code, ...(detail ? { detail } : {}), ...extra }),
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );
/** readOnly timestamps are server-owned; the mock is the server. */
const touch = (o: { readonly updated_at?: string }, at: string) => {
  (o as { updated_at?: string }).updated_at = at;
};
const rid = (p: string) => p + Math.random().toString(36).slice(2, 8);
const hintOf = (secret: string | undefined): string | null => (secret ? '••••' + secret.slice(-4) : null);
const page = <T>(items: T[], q: URLSearchParams) => {
  const limit = Math.min(200, Math.max(1, Number(q.get('limit') ?? 50)));
  const start = Number(q.get('cursor') ?? 0) || 0;
  const data = items.slice(start, start + limit);
  return { data, next_cursor: start + limit < items.length ? String(start + limit) : null };
};

interface DraftRec {
  graph: Graph;
  rev: number;
  changes: string[];
  saved_at: string;
  saved_by: string;
}
interface RunRec {
  run: Run;
  graph: Graph;
  doc: EngineDoc;
  events: (RunEvent & { _id: number })[];
  listeners: Set<() => void>;
  done: boolean;
  cancelled: boolean;
  result?: RunResult;
  steps: NonNullable<RunDetail['steps']>;
}

export class MockBackend {
  db!: SeedState;
  drafts = new Map<string, DraftRec>();
  versions = new Map<string, (VersionSummary & { graph: Graph })[]>();
  /** secret material by connection id — never serialised into responses */
  vault = new Map<string, Record<string, unknown>>();
  runs = new Map<string, RunRec>();
  idem = new Map<string, { status: number; body: string; headers: Record<string, string> }>();
  audit: { id: string; at: string; actor: { kind: 'user'; id: string; name: string }; action: string; resource_type: string; resource_id: string }[] = [];
  latency: number;
  speed: number;
  now: () => number;
  private routes: [string, RegExp, Handler][] = [];
  private seq = 48300;

  constructor(opts: MockOptions = {}) {
    this.latency = opts.latency ?? 80;
    this.speed = opts.speed ?? 1;
    this.now = opts.now ?? Date.now;
    this.reset();
    this.register();
  }

  reset(): void {
    this.db = seed(this.now());
    this.drafts.clear();
    this.versions.clear();
    this.vault.clear();
    this.runs.clear();
    this.idem.clear();
    for (const sp of this.db.pipelines) this.adoptPipeline(sp);
    for (const c of this.db.connections) this.vault.set(c.id, { type: c.auth_type, seeded: true });
  }

  private adoptPipeline(sp: SeedPipeline) {
    this.drafts.set(sp.pipeline.id, { graph: serializeGraph(sp.draft), rev: 1, changes: [...sp.changes], saved_at: sp.pipeline.updated_at ?? new Date(this.now()).toISOString(), saved_by: sp.pipeline.updated_by ?? 'Manoj M.' });
    this.versions.set(sp.pipeline.id, sp.versions);
  }

  /** `fetch`-compatible entry point. */
  fetch = async (input: Request | string | URL, init?: RequestInit): Promise<Response> => {
    const request = input instanceof Request ? input : new Request(String(input), init);
    if (this.latency) await new Promise((r) => setTimeout(r, this.latency));
    const url = new URL(request.url);
    const path = url.pathname.replace(/^.*?\/v1(?=\/)/, '');
    const method = request.method.toUpperCase();
    let body: Body = {};
    const ct = request.headers.get('content-type') ?? '';
    if (method !== 'GET' && method !== 'DELETE') {
      try {
        if (ct.includes('application/json')) {
          const t = await request.text();
          body = t ? (JSON.parse(t) as Body) : {};
        } else if (ct.includes('multipart/form-data')) {
          const fd = await request.formData();
          const f = fd.get('file');
          body = { file: f && typeof f === 'object' ? { name: (f as File).name, size: (f as File).size, type: (f as File).type } : null, purpose: fd.get('purpose') };
        }
      } catch {
        return problem(400, 'Malformed request body', 'bad_request');
      }
    }
    const idemKey = request.headers.get('Idempotency-Key');
    const ikey = idemKey && method === 'POST' ? `${path}|${idemKey}` : null;
    if (ikey && this.idem.has(ikey)) {
      const r = this.idem.get(ikey)!;
      return new Response(r.body, { status: r.status, headers: { ...r.headers, 'Idempotent-Replayed': 'true' } });
    }
    for (const [m, re, h] of this.routes) {
      if (m !== method) continue;
      const mt = re.exec(path);
      if (!mt) continue;
      try {
        const res = await h({ method, path, params: mt.slice(1).map(decodeURIComponent), query: url.searchParams, headers: request.headers, body, request });
        if (ikey && res.status < 300 && res.headers.get('content-type')?.includes('json')) {
          const text = await res.clone().text();
          this.idem.set(ikey, { status: res.status, body: text, headers: Object.fromEntries(res.headers.entries()) });
        }
        return res;
      } catch (e) {
        return problem(500, 'Mock backend error', 'internal', e instanceof Error ? e.message : String(e));
      }
    }
    return problem(404, 'Not found', 'not_found', `${method} ${path}`);
  };

  private on(method: string, pattern: string, h: Handler) {
    const re = new RegExp('^' + pattern.replace(/\{[^}]+\}/g, '([^/]+)') + '$');
    this.routes.push([method, re, h]);
  }

  private log(action: string, resource_type: string, resource_id: string) {
    this.audit.unshift({ id: rid('aud_'), at: new Date(this.now()).toISOString(), actor: { kind: 'user', id: 'usr_manoj', name: 'Manoj M.' }, action, resource_type, resource_id });
  }

  // ───────── helpers
  private ctx() {
    return { connections: this.db.connections, providers: this.db.providers.map((p) => ({ id: p.id, name: p.name, enabled: p.enabled })) };
  }
  private sp(id: string) {
    return this.db.pipelines.find((p) => p.pipeline.id === id);
  }
  private etag(id: string) {
    const d = this.drafts.get(id)!;
    return `W/"${id}-${d.rev}"`;
  }
  pipelineOut(sp: SeedPipeline): Pipeline {
    const d = this.drafts.get(sp.pipeline.id)!;
    const g = d.graph;
    const intake = mainNodes(g).find((n) => !insOf(n));
    return {
      ...sp.pipeline,
      draft_changes: [...d.changes],
      intake_type: intake?.type ?? '',
      step_count: mainNodes(g).length,
      ai_component_count: g.nodes.filter((n) => CATALOG[n.type]?.ai).length,
      kpi: { ...sp.pipeline.kpi, review_waiting: this.db.review.filter((r) => r.pipeline_id === sp.pipeline.id && (r.status === 'open' || r.status === 'claimed')).length },
    };
  }
  private draftOut(id: string): Draft {
    const d = this.drafts.get(id)!;
    return { pipeline_id: id, graph: d.graph, etag: this.etag(id), changes: [...d.changes], saved_at: d.saved_at, saved_by: d.saved_by, issues: issues(d.graph, this.ctx()) };
  }
  connOut(c: Connection & { _secret?: string }): Connection {
    const { _secret: _s, ...rest } = c;
    void _s;
    return { ...rest, usage_count: this.usages(c.id).length };
  }
  usages(connId: string): UsageRef[] {
    const out: UsageRef[] = [];
    for (const sp of this.db.pipelines) {
      const g = this.drafts.get(sp.pipeline.id)!.graph;
      for (const n of g.nodes) if (Object.values(n.config).includes(connId)) out.push({ resource_type: 'pipeline', resource_id: sp.pipeline.id, node_id: n.id, label: `${sp.pipeline.name} › ${n.name}` });
    }
    for (const p of this.db.providers) if (p.connection_id === connId) out.push({ resource_type: 'provider', resource_id: p.id, label: `Model provider · ${p.name}` });
    for (const w of this.db.workers) if (w.connection_id === connId) out.push({ resource_type: 'worker', resource_id: w.id, label: `Worker · ${w.name}` });
    for (const m of this.db.mcp) if (m.connection_id === connId) out.push({ resource_type: 'mcp_server', resource_id: m.id, label: `MCP server · ${m.name}` });
    for (const k of this.db.collections) if (k.connection_id === connId) out.push({ resource_type: 'knowledge_collection', resource_id: k.id, label: `Knowledge · ${k.name}` });
    return out;
  }
  private nodeUsages(pred: (n: Graph['nodes'][number]) => boolean): UsageRef[] {
    const out: UsageRef[] = [];
    for (const sp of this.db.pipelines) {
      const g = this.drafts.get(sp.pipeline.id)!.graph;
      for (const n of g.nodes) if (pred(n)) out.push({ resource_type: 'pipeline', resource_id: sp.pipeline.id, node_id: n.id, label: `${sp.pipeline.name} › ${n.name}` });
    }
    return out;
  }
  private provOut(p: Provider): Provider {
    return { ...p, usage_count: this.nodeUsages((n) => n.type === 'ai.model' && n.config.provider === p.id).length };
  }
  private workerOut(w: Worker): Worker {
    return w;
  }
  private mcpOut(m: McpServer): McpServer {
    return { ...m, usage_count: this.nodeUsages((n) => n.config.server === m.id).length };
  }
  private skillOut(k: Skill): Skill {
    return { ...k, usage_count: this.nodeUsages((n) => n.config.skill === k.id).length };
  }
  private newPipeline(name: string, graph: Graph, changes: string[], extra: Partial<Pipeline> = {}): SeedPipeline {
    const now = new Date(this.now()).toISOString();
    const sp: SeedPipeline = {
      pipeline: { id: rid('pl_'), name, description: '', status: 'draft', owner_team: 'Finance Operations', live_version: null, draft_changes: changes, kpi: { documents_24h: 0, straight_through_rate: 0, median_ms: 0, review_waiting: 0 }, created_at: now, updated_at: now, updated_by: 'Manoj M.', ...extra },
      draft: graph,
      changes,
      versions: [],
    };
    this.db.pipelines.push(sp);
    this.adoptPipeline(sp);
    return sp;
  }
  private reviewResult(r: ReviewItem): RunResult {
    const rcp = r.document.filename.startsWith('receipt');
    const sch = rcp ? RECEIPT_SCHEMA : INVOICE_SCHEMA;
    const vals = JSON.parse(JSON.stringify(VALUES[rcp ? 'rcp' : 'inv'])) as Record<string, [string | number, number]>;
    if (r.id === 'rv1') vals.total_amount![1] = 0.71;
    if (r.id === 'rv3') vals.purchase_date![1] = 0.64;
    if (r.id === 'rv2') vals.total_amount = [13120, 0.9];
    if (!rcp) vals.invoice_number![0] = r.document.filename.replace(/\.pdf$/, '');
    return {
      run_id: r.run_id,
      classification: { type: rcp ? 'receipt' : 'invoice', confidence: 0.96 },
      fields: sch.map((f) => ({ name: f.name, type: f.type, value: vals[f.name]?.[0] ?? null, confidence: vals[f.name]?.[1] ?? 0, required: !!f.req, pii: !!f.pii, page: 1 })),
      checks: r.reason_code === 'rule_failed' ? [{ name: 'Total = subtotal + VAT', passed: false, detail: '12,400.00 + 620.00 ≠ 13,120.00' }] : [],
      path: {},
    };
  }

  // ───────── run engine
  private startRun(sp: SeedPipeline, graph: Graph, documentId: string, version: number | 'draft'): Run {
    const sample = SAMPLE_DOCS[documentId] ?? SAMPLE_DOCS.doc_inv_sample!;
    const doc: EngineDoc = { kind: sample.kind, docKind: sample.docKind, filename: sample.filename };
    const id = 'run_' + ++this.seq;
    const run: Run = {
      id,
      pipeline_id: sp.pipeline.id,
      pipeline_version: version,
      document: { id: sample.id, filename: sample.filename, mime_type: sample.mime, pages: 1 },
      status: 'queued',
      test: true,
      started_at: new Date(this.now()).toISOString(),
      finished_at: null,
      duration_ms: null,
      field_count: 0,
    };
    const rec: RunRec = { run, graph, doc, events: [], listeners: new Set(), done: false, cancelled: false, steps: [] };
    this.runs.set(id, rec);
    this.db.runs.push(run);
    let evId = 0;
    const emit = (e: Omit<RunEvent, 'run_id' | 'at'>) => {
      rec.events.push({ ...e, run_id: id, at: new Date(this.now()).toISOString(), _id: ++evId } as RunEvent & { _id: number });
      rec.listeners.forEach((l) => l());
    };
    const t0 = this.now();
    const threshold = Number(graph.nodes.find((n) => n.type === 'route.confidence')?.config.threshold ?? 0.85);
    setTimeout(() => {
      run.status = 'running';
      void execute(graph, doc, { emit, wait: (ms) => new Promise((r) => setTimeout(r, ms * this.speed)), cancelled: () => rec.cancelled }).then((st) => {
        rec.steps = st.steps;
        rec.result = buildResult(id, graph, st, doc, threshold);
        run.status = rec.cancelled ? 'cancelled' : st.review ? 'review' : 'completed';
        run.finished_at = new Date(this.now()).toISOString();
        run.duration_ms = Math.max(1, this.now() - t0);
        run.field_count = rec.result.fields?.length ?? 0;
        rec.done = true;
        emit({ event: 'run.finished', status: run.status });
      });
    }, 30 * this.speed);
    return run;
  }

  private sse(rec: RunRec, lastId: number): Response {
    const enc = new TextEncoder();
    let sent = lastId;
    let cleanup = () => {};
    const stream = new ReadableStream<Uint8Array>({
      start: (controller) => {
        const flush = () => {
          for (const e of rec.events) {
            if (e._id <= sent) continue;
            sent = e._id;
            const { _id, ...ev } = e;
            controller.enqueue(enc.encode(`id: ${_id}\nevent: ${ev.event}\ndata: ${JSON.stringify(ev)}\n\n`));
            if (ev.event === 'run.finished') {
              cleanup();
              controller.close();
              return;
            }
          }
        };
        const l = () => flush();
        rec.listeners.add(l);
        cleanup = () => rec.listeners.delete(l);
        flush();
      },
      cancel: () => cleanup(),
    });
    return new Response(stream, { status: 200, headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' } });
  }

  // ───────── routes
  private register() {
    const on = this.on.bind(this);
    const db = () => this.db;
    const nowIso = () => new Date(this.now()).toISOString();
    const nf = (what = 'Resource') => problem(404, `${what} not found`, 'not_found');

    // identity
    on('GET', '/me', () => json(200, { kind: 'user', id: 'usr_manoj', name: 'Manoj M.', email: 'manoj@example.com', role: 'admin', scopes: ['admin'], workspace: { id: 'ws_example', name: db().settings.name ?? 'Example Trading', environment: db().settings.environment ?? 'sandbox', region: db().settings.default_region ?? 'UAE North' }, sse_token: 'sse_mock' }));
    on('GET', '/members', () => json(200, { data: [{ id: 'usr_manoj', name: 'Manoj M.', email: 'manoj@example.com', role: 'admin', status: 'active' }, { id: 'usr_sara', name: 'Sara K.', email: 'sara@example.com', role: 'builder', status: 'active' }, { id: 'usr_lina', name: 'Lina A.', email: 'lina@example.com', role: 'reviewer', status: 'active' }] }));
    on('GET', '/api-keys', (c) => json(200, page(db().apiKeys, c.query)));
    on('POST', '/api-keys', (c) => {
      const name = String(c.body.name ?? '').trim();
      const scopes = (c.body.scopes as string[]) ?? [];
      if (!name || !scopes.length) return problem(422, 'Validation failed', 'validation', 'Name and at least one scope are required');
      const secret = 'dik_live_' + Array.from({ length: 32 }, () => 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789'[Math.floor(Math.random() * 56)]).join('');
      const key: ApiKey = { id: rid('key_'), name, prefix: secret.slice(0, 13), scopes, expires_at: c.body.expires_in_days ? new Date(this.now() + Number(c.body.expires_in_days) * 864e5).toISOString() : null, last_used_at: null, created_by: 'Manoj M.', created_at: nowIso() };
      db().apiKeys.unshift(key);
      this.log('api_key.created', 'api_key', key.id);
      return json(201, { ...key, secret });
    });
    on('DELETE', '/api-keys/{id}', (c) => {
      const i = db().apiKeys.findIndex((k) => k.id === c.params[0]);
      if (i < 0) return nf('API key');
      db().apiKeys.splice(i, 1);
      this.log('api_key.revoked', 'api_key', c.params[0]!);
      return noContent();
    });
    on('POST', '/api-keys/{id}/rotate', (c) => {
      const k = db().apiKeys.find((x) => x.id === c.params[0]);
      if (!k) return nf('API key');
      const secret = 'dik_live_' + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
      k.prefix = secret.slice(0, 13);
      this.log('api_key.rotated', 'api_key', k.id);
      return json(200, { ...k, secret });
    });

    // catalog
    on('GET', '/catalog/step-types', () => json(200, { data: STEP_TYPES.map((t) => toStepType(CATALOG[t]!)) }));
    on('GET', '/catalog/services', () => json(200, { data: SERVICES }));

    // pipelines
    on('GET', '/pipelines', (c) => {
      const st = c.query.get('status');
      const q = (c.query.get('q') ?? '').toLowerCase();
      const items = db()
        .pipelines.filter((p) => (!st || p.pipeline.status === st) && (p.pipeline.name + p.pipeline.owner_team).toLowerCase().includes(q))
        .map((p) => this.pipelineOut(p));
      return json(200, page(items, c.query));
    });
    on('POST', '/pipelines', (c) => {
      const name = String(c.body.name ?? '').trim();
      const t = String(c.body.intake_type ?? '');
      if (!name) return problem(422, 'Validation failed', 'validation', 'Name is required');
      if (!CATALOG[t] || CATALOG[t]!.stage !== 'intake') return problem(422, 'Validation failed', 'validation', 'intake_type must be an intake step type');
      const g: Graph = { nodes: [newNode(t, 80, 200, {}, undefined, rid('n_'))], edges: [] };
      const sp = this.newPipeline(name, g, ['Created pipeline'], { description: String(c.body.description ?? ''), owner_team: String(c.body.owner_team ?? 'Finance Operations') });
      this.log('pipeline.created', 'pipeline', sp.pipeline.id);
      return json(201, this.pipelineOut(sp));
    });
    on('GET', '/pipelines/{id}', (c) => {
      const sp = this.sp(c.params[0]!);
      return sp ? json(200, this.pipelineOut(sp)) : nf('Pipeline');
    });
    on('PATCH', '/pipelines/{id}', (c) => {
      const sp = this.sp(c.params[0]!);
      if (!sp) return nf('Pipeline');
      for (const k of ['name', 'description', 'owner_team'] as const) if (typeof c.body[k] === 'string') sp.pipeline[k] = c.body[k] as string;
      touch(sp.pipeline, nowIso());
      const d = this.drafts.get(sp.pipeline.id)!;
      if (!d.changes.includes('Edited pipeline details')) d.changes.push('Edited pipeline details');
      return json(200, this.pipelineOut(sp));
    });
    on('DELETE', '/pipelines/{id}', (c) => {
      const i = db().pipelines.findIndex((p) => p.pipeline.id === c.params[0]);
      if (i < 0) return nf('Pipeline');
      if (db().pipelines[i]!.pipeline.status === 'active') return problem(409, 'Pipeline is active', 'pipeline_active', 'Pause the pipeline before deleting it.');
      db().pipelines.splice(i, 1);
      this.drafts.delete(c.params[0]!);
      this.log('pipeline.deleted', 'pipeline', c.params[0]!);
      return noContent();
    });
    on('POST', '/pipelines/{id}/duplicate', (c) => {
      const sp = this.sp(c.params[0]!);
      if (!sp) return nf('Pipeline');
      const g = JSON.parse(JSON.stringify(this.drafts.get(sp.pipeline.id)!.graph)) as Graph;
      const nsp = this.newPipeline(String(c.body.name ?? sp.pipeline.name + ' (copy)'), g, ['Duplicated'], { description: sp.pipeline.description ?? '', owner_team: sp.pipeline.owner_team });
      return json(201, this.pipelineOut(nsp));
    });
    on('POST', '/pipelines/{id}/activate', (c) => {
      const sp = this.sp(c.params[0]!);
      if (!sp) return nf('Pipeline');
      const vs = this.versions.get(sp.pipeline.id) ?? [];
      const live = vs.find((v) => v.version === sp.pipeline.live_version);
      if (!live) return problem(422, 'No published version', 'no_live_version', 'Publish a version before activating the pipeline.');
      const errs = issues(live.graph, this.ctx()).filter((i) => i.level === 'error');
      if (errs.length) return problem(422, 'Pipeline has blocking issues', 'pipeline_invalid', `Resolve ${errs.length} issue${errs.length > 1 ? 's' : ''} before activating`, { issues: errs });
      sp.pipeline.status = 'active';
      this.log('pipeline.activated', 'pipeline', sp.pipeline.id);
      return json(200, this.pipelineOut(sp));
    });
    on('POST', '/pipelines/{id}/pause', (c) => {
      const sp = this.sp(c.params[0]!);
      if (!sp) return nf('Pipeline');
      sp.pipeline.status = 'paused';
      this.log('pipeline.paused', 'pipeline', sp.pipeline.id);
      return json(200, this.pipelineOut(sp));
    });
    on('GET', '/pipelines/{id}/draft', (c) => {
      if (!this.drafts.has(c.params[0]!)) return nf('Pipeline');
      return json(200, this.draftOut(c.params[0]!), { ETag: this.etag(c.params[0]!) });
    });
    on('PUT', '/pipelines/{id}/draft', (c) => {
      const id = c.params[0]!;
      const d = this.drafts.get(id);
      if (!d) return nf('Pipeline');
      const im = c.headers.get('If-Match');
      if (!im) return problem(428, 'If-Match required', 'precondition_required', 'Send the draft ETag in If-Match.');
      if (im !== this.etag(id)) return problem(412, 'The draft was changed by someone else', 'etag_mismatch', `${d.saved_by} saved this draft ${d.saved_at}.`, { current_etag: this.etag(id) });
      const g = c.body.graph as Graph | undefined;
      if (!g || !Array.isArray(g.nodes) || !Array.isArray(g.edges)) return problem(422, 'Validation failed', 'validation', 'graph is required');
      d.graph = serializeGraph(deserializeGraph(g));
      d.rev++;
      const change = typeof c.body.change === 'string' ? c.body.change : '';
      if (change && !d.changes.includes(change)) d.changes.push(change);
      d.saved_at = nowIso();
      d.saved_by = 'Manoj M.';
      const sp = this.sp(id)!;
      touch(sp.pipeline, d.saved_at);
      return json(200, this.draftOut(id), { ETag: this.etag(id) });
    });
    on('POST', '/pipelines/{id}/validate', (c) => {
      const d = this.drafts.get(c.params[0]!);
      if (!d) return nf('Pipeline');
      const g = (c.body.nodes ? (c.body as unknown as Graph) : d.graph) as Graph;
      const iss = issues(deserializeGraph(g), this.ctx());
      return json(200, { valid: !iss.some((i) => i.level === 'error'), issues: iss });
    });
    on('GET', '/pipelines/{id}/versions', (c) => {
      const vs = this.versions.get(c.params[0]!);
      if (!vs) return nf('Pipeline');
      const live = this.sp(c.params[0]!)!.pipeline.live_version;
      return json(200, { data: vs.map(({ graph: _g, ...v }) => (void _g, { ...v, live: v.version === live })) });
    });
    on('POST', '/pipelines/{id}/versions', (c) => {
      const id = c.params[0]!;
      const sp = this.sp(id);
      const d = this.drafts.get(id);
      if (!sp || !d) return nf('Pipeline');
      if (c.body.draft_etag && c.body.draft_etag !== this.etag(id)) return problem(412, 'The draft changed since you reviewed it', 'etag_mismatch');
      const errs = issues(d.graph, this.ctx()).filter((i) => i.level === 'error');
      if (errs.length) return problem(422, 'Pipeline has blocking issues', 'pipeline_invalid', `Resolve ${errs.length} issue${errs.length > 1 ? 's' : ''} before publishing`, { issues: errs });
      const vs = this.versions.get(id)!;
      const v = Math.max(0, ...vs.map((x) => x.version)) + 1;
      const note = String(c.body.release_note ?? '').trim() || d.changes[0] || 'Published';
      const ver = { version: v, release_note: note, published_at: nowIso(), published_by: 'Manoj M.', live: true, graph: JSON.parse(JSON.stringify(d.graph)) as Graph };
      vs.forEach((x) => (x.live = false));
      vs.unshift(ver);
      sp.pipeline.live_version = v;
      d.changes = [];
      d.rev++;
      this.log('pipeline.published', 'pipeline', id);
      return json(201, ver);
    });
    on('GET', '/pipelines/{id}/versions/{v}', (c) => {
      const v = this.versions.get(c.params[0]!)?.find((x) => x.version === Number(c.params[1]));
      return v ? json(200, v) : nf('Version');
    });
    on('POST', '/pipelines/{id}/versions/{v}/restore', (c) => {
      const id = c.params[0]!;
      const v = this.versions.get(id)?.find((x) => x.version === Number(c.params[1]));
      const d = this.drafts.get(id);
      if (!v || !d) return nf('Version');
      d.graph = JSON.parse(JSON.stringify(v.graph)) as Graph;
      d.rev++;
      const ch = `Restored version ${v.version}`;
      if (!d.changes.includes(ch)) d.changes.push(ch);
      return json(200, this.draftOut(id), { ETag: this.etag(id) });
    });
    on('GET', '/templates', () =>
      json(200, {
        data: TEMPLATES.map((t) => {
          const g = t.graph('tpl');
          return { id: t.id, name: t.name, description: t.description, step_count: mainNodes(g).length, ai_component_count: g.nodes.filter((n) => isSub(n)).length };
        }),
      }),
    );
    on('POST', '/templates/{id}/instantiate', (c) => {
      const t = TEMPLATES.find((x) => x.id === c.params[0]);
      if (!t) return nf('Template');
      const sp = this.newPipeline(String(c.body.name ?? t.name), t.graph(rid('t')), ['Created from template']);
      return json(201, this.pipelineOut(sp));
    });

    // documents
    on('POST', '/documents', (c) => {
      const f = c.body.file as { name: string; size: number; type: string } | null;
      if (!f) return problem(422, 'Validation failed', 'validation', 'file is required');
      if (f.size > 50 * 1024 * 1024) return problem(413, 'File too large', 'file_too_large');
      return json(201, { id: rid('doc_'), filename: f.name, mime_type: f.type || 'application/octet-stream', size_bytes: f.size, pages: 1, uploaded_at: nowIso() });
    });
    on('GET', '/documents/{id}', (c) => {
      const s = SAMPLE_DOCS[c.params[0]!];
      return s ? json(200, { id: s.id, filename: s.filename, mime_type: s.mime, pages: 1 }) : nf('Document');
    });

    // runs
    on('POST', '/pipelines/{id}/test-runs', (c) => {
      const sp = this.sp(c.params[0]!);
      if (!sp) return nf('Pipeline');
      const version = (c.body.version as number | 'draft' | undefined) ?? 'draft';
      const graph = version === 'draft' ? this.drafts.get(sp.pipeline.id)!.graph : this.versions.get(sp.pipeline.id)?.find((v) => v.version === version)?.graph;
      if (!graph) return nf('Version');
      const errs = issues(graph, this.ctx()).filter((i) => i.level === 'error');
      if (errs.length) return problem(422, 'Pipeline has blocking issues', 'pipeline_invalid', `Resolve ${errs.length} issue${errs.length > 1 ? 's' : ''} before testing`, { issues: errs });
      const run = this.startRun(sp, JSON.parse(JSON.stringify(graph)) as Graph, String(c.body.document_id ?? 'doc_inv_sample'), version);
      return json(202, run);
    });
    on('POST', '/pipelines/{id}/steps/{node}/test', (c) => {
      const d = this.drafts.get(c.params[0]!);
      const n = d?.graph.nodes.find((x) => x.id === c.params[1]);
      if (!d || !n) return nf('Step');
      const s = SAMPLE_DOCS[String(c.body.document_id ?? 'doc_inv_sample')] ?? SAMPLE_DOCS.doc_inv_sample!;
      const output = isSub(n) ? { component: n.name, config: n.config } : stepOutput(d.graph, n, { kind: s.kind, docKind: s.docKind, filename: s.filename });
      return json(200, { node_id: n.id, status: 'completed', started_at: nowIso(), duration_ms: 420, attempt: 1, output });
    });
    on('GET', '/runs', (c) => {
      const pid = c.query.get('pipeline_id');
      const st = c.query.get('status');
      const test = c.query.get('test');
      const items = [...db().runs].reverse().filter((r) => (!pid || r.pipeline_id === pid) && (!st || r.status === st) && (test == null || String(r.test) === test));
      return json(200, page(items, c.query));
    });
    on('GET', '/runs/{id}', (c) => {
      const rec = this.runs.get(c.params[0]!);
      const run = rec?.run ?? db().runs.find((r) => r.id === c.params[0]);
      if (!run) return nf('Run');
      return json(200, { ...run, steps: rec?.steps ?? [] });
    });
    on('GET', '/runs/{id}/events', (c) => {
      const rec = this.runs.get(c.params[0]!);
      if (!rec) return nf('Run');
      return this.sse(rec, Number(c.headers.get('Last-Event-ID') ?? 0) || 0);
    });
    on('GET', '/runs/{id}/result', (c) => {
      const rec = this.runs.get(c.params[0]!);
      if (rec?.result) return json(200, rec.result);
      const rv = db().review.find((r) => r.run_id === c.params[0]);
      if (rv) return json(200, this.reviewResult(rv));
      if (rec) return problem(409, 'Run has not finished', 'run_in_progress');
      return nf('Run result');
    });
    on('POST', '/runs/{id}/cancel', (c) => {
      const rec = this.runs.get(c.params[0]!);
      if (!rec) return nf('Run');
      if (rec.done) return problem(409, 'Run already finished', 'run_finished');
      rec.cancelled = true;
      return new Response(null, { status: 202 });
    });
    on('POST', '/runs/{id}/retry', (c) => {
      const rec = this.runs.get(c.params[0]!);
      const sp = rec && this.sp(rec.run.pipeline_id);
      if (!rec || !sp) return nf('Run');
      return json(202, this.startRun(sp, rec.graph, rec.run.document?.id ?? 'doc_inv_sample', rec.run.pipeline_version ?? 'draft'));
    });

    // review
    on('GET', '/review-items', (c) => {
      const queue = c.query.get('queue');
      const who = c.query.get('assignee');
      const items = db().review.filter((r) => (r.status === 'open' || r.status === 'claimed') && (!queue || r.queue === queue) && (!who || (who === 'unassigned' ? !r.assignee : who === 'me' ? r.assignee === 'Manoj M.' : r.assignee === who)));
      return json(200, page(items, c.query));
    });
    on('GET', '/review-items/{id}', (c) => {
      const r = db().review.find((x) => x.id === c.params[0]);
      if (!r) return nf('Review item');
      const out: ReviewItemDetail = { ...r, result: this.reviewResult(r) };
      return json(200, out);
    });
    on('POST', '/review-items/{id}/claim', (c) => {
      const r = db().review.find((x) => x.id === c.params[0]);
      if (!r) return nf('Review item');
      if (r.assignee && r.assignee !== 'Manoj M.') return problem(409, 'Already claimed', 'already_claimed', `${r.assignee} is reviewing this document.`);
      r.assignee = 'Manoj M.';
      r.status = 'claimed';
      return json(200, r);
    });
    on('POST', '/review-items/{id}/decision', (c) => {
      const r = db().review.find((x) => x.id === c.params[0]);
      if (!r) return nf('Review item');
      const action = c.body.action;
      if (action === 'return_to_queue') {
        r.assignee = null;
        r.status = 'open';
      } else if (action === 'approve' || action === 'reject') {
        r.status = action === 'approve' ? 'approved' : 'rejected';
        const run = db().runs.find((x) => x.id === r.run_id);
        if (run) run.status = action === 'approve' ? 'completed' : 'failed';
      } else return problem(422, 'Validation failed', 'validation', 'action must be approve, reject or return_to_queue');
      this.log(`review.${String(action)}`, 'review_item', r.id);
      return json(200, r);
    });

    // connections
    on('GET', '/connections', (c) => {
      const svc = c.query.get('service');
      return json(200, page(db().connections.filter((x) => !svc || x.service === svc).map((x) => this.connOut(x)), c.query));
    });
    on('POST', '/connections', (c) => {
      const name = String(c.body.name ?? '').trim();
      const service = c.body.service as Connection['service'];
      const auth = c.body.auth as Record<string, unknown> | undefined;
      const svc = SERVICES.find((s) => s.id === service);
      if (!name) return problem(422, 'Validation failed', 'validation', 'Give the connection a name');
      if (!svc) return problem(422, 'Validation failed', 'validation', 'Unknown service');
      if (!auth || !svc.auth_types.includes(auth.type as AuthType)) return problem(422, 'Validation failed', 'validation', `${svc.name} supports ${svc.auth_types.join(', ')}`);
      const type = auth.type as AuthType;
      const pk = PRIMARY_SECRET[type];
      if (pk && !auth[pk]) return problem(422, 'Validation failed', 'validation', `auth.${pk} is required`);
      const conn: Connection = {
        id: rid('con_'),
        name,
        service,
        auth_type: type,
        settings: (c.body.settings as Record<string, unknown>) ?? {},
        secret_hint: pk ? hintOf(String(auth[pk])) : null,
        status: type === 'oauth2_authorization_code' ? 'pending_authorization' : 'unverified',
        last_tested_at: null,
        last_used_at: null,
        expires_at: null,
        usage_count: 0,
        created_at: nowIso(),
        updated_at: nowIso(),
      };
      this.vault.set(conn.id, { ...auth });
      db().connections.push(conn);
      this.log('connection.created', 'connection', conn.id);
      return json(201, this.connOut(conn));
    });
    on('GET', '/connections/{id}', (c) => {
      const x = db().connections.find((k) => k.id === c.params[0]);
      return x ? json(200, this.connOut(x)) : nf('Connection');
    });
    on('PATCH', '/connections/{id}', (c) => {
      const x = db().connections.find((k) => k.id === c.params[0]);
      if (!x) return nf('Connection');
      if (typeof c.body.name === 'string') x.name = c.body.name;
      if (c.body.settings && typeof c.body.settings === 'object') x.settings = { ...(x.settings ?? {}), ...(c.body.settings as Record<string, unknown>) };
      const auth = c.body.auth as Record<string, unknown> | undefined;
      if (auth) {
        const type = auth.type as AuthType;
        const svc = SERVICES.find((s) => s.id === x.service)!;
        if (!svc.auth_types.includes(type)) return problem(422, 'Validation failed', 'validation', `${svc.name} supports ${svc.auth_types.join(', ')}`);
        const pk = PRIMARY_SECRET[type];
        if (pk && !auth[pk]) return problem(422, 'Validation failed', 'validation', `auth.${pk} is required`);
        this.vault.set(x.id, { ...auth });
        x.auth_type = type;
        x.secret_hint = pk ? hintOf(String(auth[pk])) : null;
        x.status = type === 'oauth2_authorization_code' ? 'pending_authorization' : 'unverified';
        this.log('connection.rotated', 'connection', x.id);
      }
      touch(x, nowIso());
      return json(200, this.connOut(x));
    });
    on('DELETE', '/connections/{id}', (c) => {
      const i = db().connections.findIndex((k) => k.id === c.params[0]);
      if (i < 0) return nf('Connection');
      const us = this.usages(c.params[0]!);
      if (us.length && c.query.get('force') !== 'true') return problem(409, 'Connection is in use', 'connection_in_use', `Used by ${us.length} component${us.length > 1 ? 's' : ''}.`, { usages: us });
      db().connections.splice(i, 1);
      this.vault.delete(c.params[0]!);
      this.log('connection.deleted', 'connection', c.params[0]!);
      return noContent();
    });
    on('POST', '/connections/{id}/test', (c) => {
      const x = db().connections.find((k) => k.id === c.params[0]);
      if (!x) return nf('Connection');
      const ok = x.service !== 'ollama' && x.status !== 'pending_authorization';
      x.status = ok ? 'connected' : x.status === 'pending_authorization' ? x.status : 'failing';
      x.last_tested_at = nowIso();
      const latency = Math.round(60 + Math.random() * 180);
      const what: Record<string, string> = { gmail: 'Gmail profile', anthropic: 'List models', openai: 'List models', azure_openai: 'List deployments', google_gemini: 'List models', postgresql: 'SELECT 1', amazon_s3: 'HeadBucket', qdrant: 'GET /collections', http_api: 'GET /health', whatsapp_business: 'Phone number lookup', mcp: 'initialize' };
      return json(200, ok ? { ok: true, latency_ms: latency, message: `${what[x.service] ?? 'Authenticated request'} succeeded (simulated)` } : { ok: false, latency_ms: latency, message: x.status === 'pending_authorization' ? 'Sign-in has not been completed' : 'Connection refused: 10.0.4.12:11434 (simulated)' });
    });
    on('GET', '/connections/{id}/usage', (c) => {
      if (!db().connections.some((k) => k.id === c.params[0])) return nf('Connection');
      return json(200, { data: this.usages(c.params[0]!) });
    });
    on('POST', '/connections/{id}/oauth/authorize', (c) => {
      const x = db().connections.find((k) => k.id === c.params[0]);
      if (!x) return nf('Connection');
      const state = rid('st_');
      // Simulated consent: the mock "completes" the callback shortly after.
      setTimeout(() => {
        x.status = 'connected';
        x.last_tested_at = new Date(this.now()).toISOString();
      }, 1200 * this.speed);
      return json(200, { authorization_url: `https://accounts.example.com/o/oauth2/auth?client_id=mock&state=${state}`, state });
    });

    // providers
    on('GET', '/providers', () => json(200, { data: db().providers.map((p) => this.provOut(p)) }));
    on('POST', '/providers', (c) => {
      const b = c.body as Partial<Provider>;
      if (!b.kind || !b.name) return problem(422, 'Validation failed', 'validation', 'kind and name are required');
      const p: Provider = { id: rid('prv_'), kind: b.kind, name: b.name, enabled: true, base_url: b.base_url, connection_id: b.connection_id ?? null, data_region: b.data_region ?? 'Global', models: b.models ?? [], default_model: b.default_model ?? b.models?.[0]?.id, usage_count: 0 };
      db().providers.push(p);
      return json(201, this.provOut(p));
    });
    on('GET', '/providers/{id}', (c) => {
      const p = db().providers.find((x) => x.id === c.params[0]);
      return p ? json(200, this.provOut(p)) : nf('Provider');
    });
    on('PATCH', '/providers/{id}', (c) => {
      const p = db().providers.find((x) => x.id === c.params[0]);
      if (!p) return nf('Provider');
      Object.assign(p, c.body);
      this.log('provider.updated', 'provider', p.id);
      return json(200, this.provOut(p));
    });
    on('DELETE', '/providers/{id}', (c) => {
      const i = db().providers.findIndex((x) => x.id === c.params[0]);
      if (i < 0) return nf('Provider');
      const us = this.nodeUsages((n) => n.type === 'ai.model' && n.config.provider === c.params[0]);
      if (us.length) return problem(409, 'Provider is in use', 'provider_in_use', `Used by ${us.length} language model component${us.length > 1 ? 's' : ''}.`, { usages: us });
      db().providers.splice(i, 1);
      return noContent();
    });
    on('POST', '/providers/{id}/test', (c) => {
      const p = db().providers.find((x) => x.id === c.params[0]);
      if (!p) return nf('Provider');
      const conn = db().connections.find((x) => x.id === p.connection_id);
      const ok = !!conn && p.kind !== 'ollama';
      return json(200, { ok, latency_ms: Math.round(200 + Math.random() * 500), message: ok ? `${String(c.body.model ?? p.default_model)} answered a test prompt (simulated)` : conn ? 'Could not reach the server (simulated)' : 'No connection configured' });
    });
    on('POST', '/providers/{id}/models/sync', (c) => {
      const p = db().providers.find((x) => x.id === c.params[0]);
      if (!p) return nf('Provider');
      if (p.kind === 'ollama') return problem(502, 'Could not reach the provider', 'provider_unreachable', 'GET /api/tags failed (simulated)');
      const extra: Record<string, string> = { anthropic: 'claude-opus-5-5', openai: 'gpt-5-nano', google_gemini: 'gemini-2.5-flash-lite', azure_openai: 'gpt-embed-prod' };
      const add = extra[p.kind];
      if (add && !p.models.some((m) => m.id === add)) p.models.push({ id: add, display_name: add, enabled: true, supports_tools: true });
      return json(200, this.provOut(p));
    });

    // workers
    on('GET', '/workers', (c) => {
      const k = c.query.get('kind');
      return json(200, { data: db().workers.filter((w) => !k || w.kind === k).map((w) => this.workerOut(w)) });
    });
    on('POST', '/workers', (c) => {
      const b = c.body as Partial<Worker>;
      if (!b.name || !b.kind || !b.base_url || !b.adapter) return problem(422, 'Validation failed', 'validation', 'name, kind, base_url and adapter are required');
      const w: Worker = { id: rid('wrk_'), name: b.name, kind: b.kind, base_url: b.base_url, adapter: b.adapter, invoke_path: b.invoke_path || (b.adapter === 'excel_parser_v1' ? '/to-text' : '/invoke'), health_path: b.health_path || '/health', connection_id: b.connection_id ?? null, accepts: b.accepts ?? [], capabilities: b.capabilities ?? [], timeout_s: b.timeout_s ?? 60, max_concurrency: b.max_concurrency ?? 4, data_region: b.data_region, health: { status: 'unknown' } };
      db().workers.push(w);
      this.log('worker.registered', 'worker', w.id);
      return json(201, w);
    });
    on('GET', '/workers/{id}', (c) => {
      const w = db().workers.find((x) => x.id === c.params[0]);
      return w ? json(200, w) : nf('Worker');
    });
    on('PATCH', '/workers/{id}', (c) => {
      const w = db().workers.find((x) => x.id === c.params[0]);
      if (!w) return nf('Worker');
      Object.assign(w, c.body);
      return json(200, w);
    });
    on('DELETE', '/workers/{id}', (c) => {
      const i = db().workers.findIndex((x) => x.id === c.params[0]);
      if (i < 0) return nf('Worker');
      const us = this.nodeUsages((n) => n.config.worker === c.params[0]);
      if (us.length) return problem(409, 'Worker is in use', 'worker_in_use', `Used by ${us.length} step${us.length > 1 ? 's' : ''}.`, { usages: us });
      db().workers.splice(i, 1);
      return noContent();
    });
    on('POST', '/workers/{id}/health', (c) => {
      const w = db().workers.find((x) => x.id === c.params[0]);
      if (!w) return nf('Worker');
      const down = w.adapter === 'generic_pages_v1' && w.name.includes('Image');
      w.health = down
        ? { status: 'unreachable', checked_at: nowIso(), message: `GET ${w.base_url}${w.health_path ?? '/health'} → connection refused (simulated)` }
        : { status: 'healthy', latency_ms: Math.round(30 + Math.random() * 80), checked_at: nowIso(), message: `GET ${w.health_path ?? '/health'} → 200 (simulated)` };
      return json(200, w.health);
    });
    on('POST', '/workers/{id}/invoke', (c) => {
      const w = db().workers.find((x) => x.id === c.params[0]);
      if (!w) return nf('Worker');
      if (!c.body.document_id) return problem(422, 'Validation failed', 'validation', 'document_id is required');
      if (w.health?.status === 'unreachable') return problem(502, 'Worker unreachable', 'worker_unreachable', `POST ${w.base_url}${w.invoke_path} failed (simulated)`);
      if (w.adapter === 'excel_parser_v1')
        return json(200, {
          pages: [
            { n: 1, width: 1, height: 1, blocks: [{ text: 'Sheet "Summary": Revenue 4,820,000 · COGS 3,104,500 · Gross margin 35.6%', bbox: [0, 0, 1, 1] }] },
            { n: 2, width: 1, height: 1, blocks: [{ text: 'Sheet "By region": UAE 61% · KSA 27% · Oman 12%', bbox: [0, 0, 1, 1] }] },
          ],
          text: 'Summary …\nBy region …',
          worker_latency_ms: 212,
        });
      return json(200, {
        pages: [
          {
            n: 1,
            width: 595,
            height: 842,
            language: 'en',
            blocks: [
              { text: 'Gulf Office Supplies LLC', bbox: [48, 60, 260, 84], confidence: 0.98 },
              { text: 'TAX INVOICE', bbox: [400, 60, 547, 86], confidence: 0.99 },
              { text: 'Invoice no. INV-2026-0417', bbox: [380, 96, 547, 110], confidence: 0.99 },
            ],
            tables: [{ bbox: [48, 300, 547, 420], rows: [['Description', 'Qty', 'Unit price', 'Amount (AED)'], ['A4 copy paper, 80 gsm, box of 5', '40', '95.00', '3,800.00']] }],
            key_values: [{ key: 'TRN', value: '100354872600003', bbox: [48, 120, 230, 134] }],
          },
        ],
        text: 'Gulf Office Supplies LLC\nTAX INVOICE\n…',
        worker_latency_ms: 840,
      });
    });

    // MCP servers
    on('GET', '/mcp-servers', () => json(200, { data: db().mcp.map((m) => this.mcpOut(m)) }));
    on('POST', '/mcp-servers', (c) => {
      const b = c.body as Partial<McpServer>;
      if (!b.name || !b.url || !b.transport) return problem(422, 'Validation failed', 'validation', 'name, transport and url are required');
      const m: McpServer = { id: rid('mcp_'), name: b.name, transport: b.transport, url: b.url, connection_id: b.connection_id ?? null, headers: b.headers ?? {}, enabled: true, tools: [], status: 'unknown', last_discovered_at: null, usage_count: 0 };
      db().mcp.push(m);
      return json(201, this.mcpOut(m));
    });
    on('GET', '/mcp-servers/{id}', (c) => {
      const m = db().mcp.find((x) => x.id === c.params[0]);
      return m ? json(200, this.mcpOut(m)) : nf('MCP server');
    });
    on('PATCH', '/mcp-servers/{id}', (c) => {
      const m = db().mcp.find((x) => x.id === c.params[0]);
      if (!m) return nf('MCP server');
      const { tools, ...rest } = c.body as Partial<McpServer> & { tools?: { name: string; enabled: boolean }[] };
      Object.assign(m, rest);
      if (tools) for (const t of tools) {
        const mt = m.tools?.find((x) => x.name === t.name);
        if (mt) mt.enabled = t.enabled;
      }
      return json(200, this.mcpOut(m));
    });
    on('DELETE', '/mcp-servers/{id}', (c) => {
      const i = db().mcp.findIndex((x) => x.id === c.params[0]);
      if (i < 0) return nf('MCP server');
      const us = this.nodeUsages((n) => n.config.server === c.params[0]);
      if (us.length) return problem(409, 'MCP server is in use', 'mcp_in_use', `Used by ${us.length} step${us.length > 1 ? 's' : ''}.`, { usages: us });
      db().mcp.splice(i, 1);
      return noContent();
    });
    on('POST', '/mcp-servers/{id}/discover', (c) => {
      const m = db().mcp.find((x) => x.id === c.params[0]);
      if (!m) return nf('MCP server');
      if (!m.tools?.length)
        m.tools = [
          { name: 'ping', description: 'Health probe exposed by the server', enabled: true, input_schema: { type: 'object', properties: {} } },
          { name: 'search', description: 'Search records', enabled: true, input_schema: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'] } },
        ];
      m.status = 'connected';
      m.last_discovered_at = nowIso();
      return json(200, this.mcpOut(m));
    });
    on('POST', '/mcp-servers/{id}/tools/{tool}/call', (c) => {
      const m = db().mcp.find((x) => x.id === c.params[0]);
      const t = m?.tools?.find((x) => x.name === c.params[1]);
      if (!m || !t) return nf('Tool');
      if (!m.enabled) return problem(409, 'Server is disabled', 'mcp_disabled', 'Enable the server to call its tools.');
      const args = (c.body.arguments as Record<string, unknown>) ?? {};
      const req = ((t.input_schema as { required?: string[] } | undefined)?.required ?? []).filter((k) => args[k] == null || args[k] === '');
      if (req.length) return problem(422, 'Arguments do not match the input schema', 'invalid_arguments', `Missing: ${req.join(', ')}`);
      const sample: Record<string, unknown> = { match_vendor: { vendor_id: 'V-00418', name: 'Gulf Office Supplies LLC', trn: '100354872600003', score: 0.97 }, match_po: { po_number: args.po_number, status: 'open', remaining_aed: 13020 }, find_customer: { customer_id: 'C-22019', name: args.name ?? 'Example customer' } };
      return json(200, { content: [{ type: 'text', text: JSON.stringify(sample[t.name] ?? { ok: true, arguments: args }, null, 2) }], is_error: false, latency_ms: Math.round(80 + Math.random() * 200) });
    });

    // knowledge, skills, policies
    on('GET', '/knowledge-collections', () => json(200, { data: db().collections }));
    on('POST', '/knowledge-collections', (c) => {
      const b = c.body as Record<string, string>;
      if (!b.name || !/^[a-z][a-z0-9_]*$/.test(b.name)) return problem(422, 'Validation failed', 'validation', 'name must be snake_case');
      const k = { id: rid('kc_'), name: b.name, connection_id: b.connection_id ?? '', embedding_provider_id: b.embedding_provider_id, embedding_model: b.embedding_model, document_count: 0, chunk_count: 0, status: 'ready' as const };
      db().collections.push(k);
      return json(201, k);
    });
    on('POST', '/knowledge-collections/{id}/documents', () => new Response(null, { status: 202 }));
    on('GET', '/skills', () => json(200, { data: db().skills.map((k) => this.skillOut(k)) }));
    on('POST', '/skills', (c) => {
      const b = c.body as Record<string, string>;
      if (!b.name || !b.instructions) return problem(422, 'Validation failed', 'validation', 'name and instructions are required');
      const k: Skill = { id: rid('skl_'), name: b.name, description: b.description ?? '', instructions: b.instructions, version: 1, versions: [{ version: 1, release_note: 'Created', published_at: nowIso(), published_by: 'Manoj M.' }], usage_count: 0, created_at: nowIso(), updated_at: nowIso() };
      db().skills.push(k);
      return json(201, k);
    });
    on('GET', '/skills/{id}', (c) => {
      const k = db().skills.find((x) => x.id === c.params[0]);
      return k ? json(200, this.skillOut(k)) : nf('Skill');
    });
    on('PATCH', '/skills/{id}', (c) => {
      const k = db().skills.find((x) => x.id === c.params[0]);
      if (!k) return nf('Skill');
      Object.assign(k, c.body);
      return json(200, this.skillOut(k));
    });
    on('DELETE', '/skills/{id}', (c) => {
      const i = db().skills.findIndex((x) => x.id === c.params[0]);
      if (i < 0) return nf('Skill');
      const us = this.nodeUsages((n) => n.config.skill === c.params[0]);
      if (us.length) return problem(409, 'Skill is in use', 'skill_in_use', `Used by ${us.length} step${us.length > 1 ? 's' : ''}.`, { usages: us });
      db().skills.splice(i, 1);
      return noContent();
    });
    on('POST', '/skills/{id}/versions', (c) => {
      const k = db().skills.find((x) => x.id === c.params[0]);
      if (!k) return nf('Skill');
      if (!c.body.instructions) return problem(422, 'Validation failed', 'validation', 'instructions are required');
      k.version += 1;
      k.instructions = String(c.body.instructions);
      k.versions = [{ version: k.version, release_note: String(c.body.release_note ?? ''), published_at: nowIso(), published_by: 'Manoj M.' }, ...(k.versions ?? [])];
      return json(201, this.skillOut(k));
    });
    on('GET', '/policies', () => json(200, { data: db().policies }));
    on('POST', '/policies', (c) => {
      const b = c.body as Partial<Policy>;
      if (!b.name || !b.kind) return problem(422, 'Validation failed', 'validation', 'name and kind are required');
      const p: Policy = { id: rid('pol_'), name: b.name, description: b.description ?? '', kind: b.kind, enabled: b.enabled ?? true, scope: b.scope ?? { all_pipelines: true }, config: b.config ?? {} };
      db().policies.push(p);
      return json(201, p);
    });
    on('PATCH', '/policies/{id}', (c) => {
      const p = db().policies.find((x) => x.id === c.params[0]);
      if (!p) return nf('Policy');
      Object.assign(p, c.body);
      this.log('policy.updated', 'policy', p.id);
      return json(200, p);
    });
    on('DELETE', '/policies/{id}', (c) => {
      const i = db().policies.findIndex((x) => x.id === c.params[0]);
      if (i < 0) return nf('Policy');
      db().policies.splice(i, 1);
      return noContent();
    });

    // metrics, audit, webhooks, settings
    on('GET', '/metrics/overview', (c) => {
      const ps = db().pipelines.map((p) => p.pipeline);
      const act = ps.filter((p) => p.status === 'active');
      const docs = ps.reduce((a, p) => a + (p.kpi?.documents_24h ?? 0), 0);
      const actDocs = act.reduce((a, p) => a + (p.kpi?.documents_24h ?? 0), 0);
      const stp = actDocs ? act.reduce((a, p) => a + (p.kpi?.straight_through_rate ?? 0) * (p.kpi?.documents_24h ?? 0), 0) / actDocs : 0;
      const open = db().review.filter((r) => r.status === 'open' || r.status === 'claimed');
      const near = open.filter((r) => r.sla_due_at && Date.parse(r.sla_due_at) - this.now() < 3 * 36e5).length;
      return json(200, { window: c.query.get('window') ?? '24h', documents: docs, documents_change: 0.084, straight_through_rate: stp, straight_through_change: 0.013, review_waiting: open.length, review_near_sla: Math.min(near, 2), median_ms: 6200, median_change_ms: -400 });
    });
    on('GET', '/audit-events', (c) => json(200, page(this.audit, c.query)));
    on('GET', '/webhooks', () => json(200, { data: db().webhooks }));
    on('POST', '/webhooks', (c) => {
      const url = String(c.body.url ?? '');
      const events = (c.body.events as string[]) ?? [];
      if (!/^https?:\/\//.test(url) || !events.length) return problem(422, 'Validation failed', 'validation', 'A URL and at least one event are required');
      const w = { id: rid('wh_'), url, events: events as never, enabled: true, pipeline_ids: (c.body.pipeline_ids as string[]) ?? [] };
      db().webhooks.push(w);
      return json(201, { ...w, signing_secret: 'whsec_' + Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2) });
    });
    on('DELETE', '/webhooks/{id}', (c) => {
      const i = db().webhooks.findIndex((x) => x.id === c.params[0]);
      if (i < 0) return nf('Webhook');
      db().webhooks.splice(i, 1);
      return noContent();
    });
    on('GET', '/settings', () => json(200, db().settings));
    on('PATCH', '/settings', (c) => {
      Object.assign(db().settings, c.body);
      return json(200, db().settings);
    });
  }
}

let singleton: MockBackend | null = null;
export const mockBackend = (): MockBackend => (singleton ??= new MockBackend());
