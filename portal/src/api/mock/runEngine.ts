/**
 * Simulated run engine for the mock backend — ported from the prototype's `runTest()`.
 * Walks the graph from the intake, emitting the same `RunEvent` sequence a real backend sends over SSE.
 */
import type { Graph, GraphNode, OverlayLayer, RunEvent, RunResult, RunStep } from '../types';
import { CATALOG, INVOICE_SCHEMA, RECEIPT_SCHEMA, type Config, type SchemaField } from '../../features/builder/catalog';
import { insOf, mainNodes, modelOf, subsOf } from '../../features/builder/graph';
import { list } from '../../lib/util';
import { VALUES, type SampleKind } from './sampleData';

export interface EngineDoc {
  kind: SampleKind;
  docKind: string;
  filename: string;
}

/** Simulated output of a single step (prototype `mock(n)`). */
export function stepOutput(g: Graph, n: GraphNode, doc: EngineDoc): Record<string, unknown> {
  const c = n.config as Config;
  switch (n.type) {
    case 'read.ocr':
      return { pages: 1, characters: doc.kind === 'inv' ? 2184 : 612, mean_confidence: 0.97, worker: c.worker };
    case 'read.layout':
      return { tables: 1, key_value_pairs: doc.kind === 'inv' ? 11 : 5 };
    case 'read.sheet':
      return { sheets: 2, characters: 9412, worker: c.worker };
    case 'ai.classify':
      return { type: doc.docKind, confidence: 0.96, model: modelOf(g, n)?.config.model };
    case 'ai.extract':
    case 'ai.agent': {
      const m = modelOf(g, n);
      return {
        model: String(m?.config.provider ?? '') + ' · ' + String(m?.config.model ?? ''),
        fields: ((subsOf(g, n, 'parser')[0]?.config.schema as unknown[]) ?? []).length,
        tools_available: subsOf(g, n, 'tools').map((t) => t.name),
        knowledge: subsOf(g, n, 'knowledge').map((k) => k.config.collection),
        tokens: { input: 2310, output: 268 },
      };
    }
    case 'val.rules':
      return { passed: ((c.rules as unknown[]) ?? []).length, failed: 0 };
    case 'val.pii':
      return { masked: doc.kind === 'inv' ? 3 : 1, method: c.mode };
    case 'val.lookup':
      return { matched: true, record: 'V-00418' };
    case 'val.injection':
      return { verdict: 'clean' };
    case 'review.human':
      return { queue: c.queue, ticket: 'REV-1182', decision: 'approved' };
    default:
      return { status: 'ok' };
  }
}

export interface EngineState {
  layers: Set<OverlayLayer>;
  classification?: string;
  extractNode?: string;
  path: Record<string, string>;
  review: boolean;
  steps: RunStep[];
}

export interface EngineHooks {
  emit: (e: Omit<RunEvent, 'run_id' | 'at'>) => void;
  wait: (ms: number) => Promise<void>;
  cancelled: () => boolean;
}

export function schemaFor(g: Graph, st: EngineState, doc: EngineDoc): SchemaField[] {
  const ex = st.extractNode ? g.nodes.find((n) => n.id === st.extractNode) : undefined;
  const pr = ex && subsOf(g, ex, 'parser')[0];
  const s = pr?.config.schema as SchemaField[] | undefined;
  return s && s.length ? s : doc.kind === 'rcp' ? RECEIPT_SCHEMA : INVOICE_SCHEMA;
}

export async function execute(g: Graph, doc: EngineDoc, hooks: EngineHooks, rand: () => number = Math.random): Promise<EngineState> {
  const st: EngineState = { layers: new Set(), path: {}, review: false, steps: [] };
  const vals = VALUES[doc.kind];
  hooks.emit({ event: 'run.started', status: 'running' });
  const start = mainNodes(g).find((n) => !insOf(n));
  const q: (GraphNode | undefined)[] = [start];
  const seen = new Set<string>();
  while (q.length) {
    if (hooks.cancelled()) break;
    const n = q.shift();
    if (!n || seen.has(n.id)) continue;
    seen.add(n.id);
    const d = CATALOG[n.type];
    if (!d) continue;
    const startedAt = new Date().toISOString();
    hooks.emit({ event: 'step.started', node_id: n.id, step: { node_id: n.id, status: 'running', started_at: startedAt, attempt: 1 } });
    const ms = Math.round((d.ai ? 1100 : d.stage === 'read' ? 700 : 380) + rand() * 250);
    await hooks.wait(ms);
    if (n.type === 'read.ocr') st.layers.add('blocks');
    if (n.type === 'read.layout') st.layers.add('tables');
    if (n.type === 'ai.classify') st.classification = doc.docKind;
    if (n.type === 'ai.extract' || n.type === 'ai.agent') {
      st.layers.add('fields');
      st.extractNode = n.id;
    }
    if (n.type === 'val.pii') st.layers.add('redactions');
    if (n.type === 'review.human') st.review = true;
    let port: string | undefined;
    let output: Record<string, unknown>;
    if (n.type === 'route.type') port = list(n.config.paths).includes(doc.docKind) ? doc.docKind : 'other';
    if (n.type === 'route.confidence') {
      const sch = schemaFor(g, st, doc);
      const confs = sch.filter((f) => f.req && vals[f.name]).map((f) => vals[f.name]![1]);
      const m = confs.length ? Math.min(...confs) : 1;
      port = m >= Number(n.config.threshold) ? 'pass' : 'review';
      output = { lowest_required_confidence: m, threshold: n.config.threshold, output: port };
    } else output = { ...stepOutput(g, n, doc), ...(port ? { output: port } : {}) };
    if (port) st.path[n.id] = port;
    const step: RunStep = { node_id: n.id, status: 'completed', started_at: startedAt, duration_ms: ms, attempt: 1, output };
    if (port) step.output_port = port;
    if (d.ai) step.usage = { input_tokens: 2310, output_tokens: 268, cost_usd: 0.011 };
    st.steps.push(step);
    hooks.emit({ event: 'step.finished', node_id: n.id, step, layers: [...st.layers] });
    if (n.type === 'review.human') hooks.emit({ event: 'run.routed', node_id: n.id, status: 'review' });
    g.edges.filter((e) => !e.slot && e.from === n.id && (!port || e.from_port === port)).forEach((e) => q.push(g.nodes.find((x) => x.id === e.to)));
  }
  g.nodes.forEach((n) => {
    if (!st.steps.some((s) => s.node_id === n.id) && !CATALOG[n.type]?.sub) st.steps.push({ node_id: n.id, status: 'skipped' });
  });
  return st;
}

export function buildResult(runId: string, g: Graph, st: EngineState, doc: EngineDoc, threshold: number): RunResult {
  const vals = VALUES[doc.kind];
  const masked = st.layers.has('redactions');
  const res: RunResult = { run_id: runId, path: st.path, checks: [], overlays: { blocks: [], tables: [], redactions: [] } };
  if (st.classification) res.classification = { type: st.classification, confidence: 0.96 };
  if (st.layers.has('fields')) {
    res.fields = schemaFor(g, st, doc).map((f) => {
      const v = vals[f.name];
      return {
        name: f.name,
        type: f.type,
        value: v ? (f.pii && masked ? null : v[0]) : null,
        confidence: v ? v[1] : 0,
        required: !!f.req,
        pii: !!f.pii,
        masked: !!(f.pii && masked),
        page: 1,
      };
    });
  }
  const rules = g.nodes.find((n) => n.type === 'val.rules' && st.steps.some((s) => s.node_id === n.id && s.status === 'completed'));
  if (rules) res.checks = ((rules.config.rules as string[]) ?? []).map((r) => ({ name: r, passed: true, node_id: rules.id }));
  void threshold;
  return res;
}
