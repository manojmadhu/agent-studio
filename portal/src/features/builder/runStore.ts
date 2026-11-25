/**
 * Test-run state for the builder's run panel and canvas highlighting.
 * Driven entirely by `RunEvent`s from `/runs/{id}/events` (SSE) — identical in mock and live mode.
 */
import { create } from 'zustand';
import type { Graph, OverlayLayer, RunEvent, RunResult } from '../../api/types';
import { api } from '../../api/client';
import { streamRunEvents } from '../../api/sse';
import { errorMessage, isApiError } from '../../api/errors';
import { toast } from '../../ui/toast';
import { CATALOG } from './catalog';
import { nodeById, subsOf } from './graph';

export type StepState = 'run' | 'ok' | 'skip' | 'fail';
export type RunTab = 'log' | 'data' | 'doc' | 'problems';
export interface LogLine {
  t: Date;
  lv: 'run' | 'ok' | 'warn' | 'err';
  m: string;
  id?: string;
}
export const SAMPLE_DOC_OPTIONS = [
  { id: 'doc_inv_sample', name: 'INV-2026-0417.pdf', kind: 'inv' as const },
  { id: 'doc_rcp_sample', name: 'receipt_0918.jpg', kind: 'rcp' as const },
];

interface RunData {
  runId: string | null;
  running: boolean;
  RS: Record<string, StepState>;
  OUT: Record<string, unknown>;
  logs: LogLine[];
  live: Set<string>;
  layers: Set<OverlayLayer>;
  cls: string | null;
  extractNode: string | null;
  result: RunResult | null;
  docId: string;
  open: boolean;
  tab: RunTab;
  review: boolean;
}
interface RunState extends RunData {
  reset: (keepPanel?: boolean) => void;
  setDoc: (id: string) => void;
  setOpen: (v: boolean) => void;
  setTab: (t: RunTab) => void;
  toggleTab: (t: RunTab) => void;
  setOutput: (nodeId: string, out: unknown) => void;
  start: (pipelineId: string, graph: Graph, opts?: { onFinished?: () => void; beforeStart?: () => Promise<void> }) => Promise<void>;
}

const blank = (): Pick<RunData, 'runId' | 'running' | 'RS' | 'OUT' | 'logs' | 'live' | 'layers' | 'cls' | 'extractNode' | 'result' | 'review'> => ({
  runId: null,
  running: false,
  RS: {},
  OUT: {},
  logs: [],
  live: new Set(),
  layers: new Set(),
  cls: null,
  extractNode: null,
  result: null,
  review: false,
});

let abort: AbortController | null = null;

export const useRun = create<RunState>((set, get) => ({
  ...blank(),
  docId: 'doc_inv_sample',
  open: false,
  tab: 'log',
  reset: (keepPanel = true) => {
    abort?.abort();
    set({ ...blank(), ...(keepPanel ? {} : { open: false, tab: 'log' as RunTab }) });
  },
  setDoc: (docId) => {
    abort?.abort();
    set({ ...blank(), docId });
  },
  setOpen: (open) => set({ open }),
  setTab: (tab) => set({ tab, open: true }),
  toggleTab: (t) => {
    const s = get();
    if (s.open && s.tab === t) set({ open: false });
    else set({ tab: t, open: true });
  },
  setOutput: (id, out) => set({ OUT: { ...get().OUT, [id]: out } }),
  start: async (pipelineId, graph, opts = {}) => {
    abort?.abort();
    const ac = new AbortController();
    abort = ac;
    const doc = SAMPLE_DOC_OPTIONS.find((d) => d.id === get().docId) ?? SAMPLE_DOC_OPTIONS[0]!;
    set({ ...blank(), running: true, open: true, tab: 'log' });
    const log = (lv: LogLine['lv'], m: string, id?: string) => set({ logs: [...get().logs, { t: new Date(), lv, m, id }] });
    const t0 = performance.now();
    try {
      await opts.beforeStart?.();
      const run = await api.createTestRun(pipelineId, { document_id: doc.id, version: 'draft', deliver: false });
      set({ runId: run.id });
      log('run', `Test run · ${doc.name}`);
      const name = (id?: string) => nodeById(graph, id)?.name ?? id ?? '';
      await streamRunEvents(
        run.id,
        (ev: RunEvent) => {
          const s = get();
          const n = nodeById(graph, ev.node_id);
          switch (ev.event) {
            case 'step.started': {
              if (!n) break;
              const RS = { ...s.RS, [n.id]: 'run' as StepState };
              subsOf(graph, n).forEach((x) => (RS[x.id] = 'run'));
              const live = new Set(s.live);
              graph.edges.filter((e) => e.slot && e.to === n.id).forEach((e) => live.add(e.id));
              set({ RS, live });
              log('run', `${n.name} started`, n.id);
              break;
            }
            case 'step.finished':
            case 'step.failed': {
              if (!n) break;
              const failed = ev.event === 'step.failed';
              const RS = { ...s.RS, [n.id]: (failed ? 'fail' : 'ok') as StepState };
              const OUT = { ...s.OUT, [n.id]: ev.step?.output ?? {} };
              subsOf(graph, n).forEach((x) => {
                RS[x.id] = 'ok';
                OUT[x.id] = { component: x.name, config: x.config };
              });
              const port = ev.step?.output_port;
              const live = new Set(s.live);
              if (!failed) graph.edges.filter((e) => !e.slot && e.from === n.id && (!port || e.from_port === port)).forEach((e) => live.add(e.id));
              const layers = new Set(ev.layers ?? [...s.layers]);
              const patch: Partial<RunData> = { RS, OUT, live, layers };
              if (n.type === 'ai.classify') patch.cls = String((ev.step?.output as { type?: string } | undefined)?.type ?? '');
              if (CATALOG[n.type]?.ai && (n.type === 'ai.extract' || n.type === 'ai.agent')) patch.extractNode = n.id;
              set(patch);
              const secs = ((ev.step?.duration_ms ?? 0) / 1000).toFixed(1);
              if (failed) log('err', `${n.name} failed · ${ev.step?.error?.title ?? 'error'}`, n.id);
              else log('ok', `${n.name} · ${secs} s${port ? ' → ' + port : ''}`, n.id);
              break;
            }
            case 'run.routed':
              set({ review: true });
              break;
            case 'run.finished': {
              const RS = { ...get().RS };
              graph.nodes.forEach((x) => {
                if (!RS[x.id]) RS[x.id] = 'skip';
              });
              const total = ((performance.now() - t0) / 1000).toFixed(1);
              const review = get().review || ev.status === 'review';
              set({ RS, running: false, review });
              log(review ? 'warn' : ev.status === 'failed' ? 'err' : 'ok', `Finished in ${total} s${review ? ' · routed to human review' : ''}`);
              toast(`Test run finished in ${total} s`);
              setTimeout(() => {
                if (get().runId === run.id) set({ live: new Set() });
              }, 2500);
              break;
            }
            default:
              void name;
          }
        },
        ac.signal,
      );
      if (ac.signal.aborted) return;
      try {
        const result = await api.getRunResult(run.id);
        if (get().runId === run.id) set({ result });
      } catch {
        /* result optional */
      }
      opts.onFinished?.();
    } catch (e) {
      if (ac.signal.aborted) return;
      set({ running: false });
      if (isApiError(e) && e.problem.issues?.length) {
        set({ tab: 'problems', open: true });
        toast(`Resolve ${e.problem.issues.length} issue${e.problem.issues.length > 1 ? 's' : ''} before testing`, true);
      } else {
        log('err', errorMessage(e));
        toast(errorMessage(e), true);
      }
    }
  },
}));
