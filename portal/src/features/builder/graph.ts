/** Graph helpers (pure). The graph is stored in the OpenAPI `Graph` shape. */
import type { Graph, GraphEdge, GraphNode, NodeSettings, SlotName } from '../../api/types';
import { CATALOG, SLOTS, def, defaults, hasDef, type Config } from './catalog';
import { uid } from '../../lib/util';

export const NW = 248;
export const NH = 68;
export const SW = 208;
export const SH = 48;
export const GAP = 300;

export const SETTINGS_DEFAULT: NodeSettings = { retries: 2, timeout_s: 60, on_error: 'stop', notes: '' };

export const isSub = (n: Pick<GraphNode, 'type'>): boolean => !!CATALOG[n.type]?.sub;
/** 1 when the node has an input port (prototype `insOf`). */
export const insOf = (n: Pick<GraphNode, 'type'>): 0 | 1 => (isSub(n) || !CATALOG[n.type]?.hasInput ? 0 : 1);
export const slotsOf = (n: Pick<GraphNode, 'type'>): SlotName[] => CATALOG[n.type]?.slots ?? [];
export const needOf = (n: Pick<GraphNode, 'type'>): SlotName[] => CATALOG[n.type]?.need ?? ['model'];
export function outsOf(n: Pick<GraphNode, 'type' | 'config' | 'settings'>): string[] {
  if (isSub(n)) return [];
  const o = CATALOG[n.type]?.outs;
  let r = o ? o(n.config as Config) : ['out'];
  if (n.settings?.on_error === 'error_output' && r.length) r = [...r, 'error'];
  return r;
}
export const nW = (n: Pick<GraphNode, 'type'>): number => (isSub(n) ? SW : NW);
export const nH = (n: Pick<GraphNode, 'type'>): number => (isSub(n) ? SH : NH);
export const mainNodes = (g: Graph): GraphNode[] => g.nodes.filter((n) => !isSub(n));
export const nodeById = (g: Graph, id: string | undefined): GraphNode | undefined => (id ? g.nodes.find((n) => n.id === id) : undefined);
export const subsOf = (g: Graph, n: GraphNode, slot?: SlotName): GraphNode[] =>
  g.edges
    .filter((e) => e.slot && e.to === n.id && (!slot || e.slot === slot))
    .map((e) => nodeById(g, e.from))
    .filter((x): x is GraphNode => !!x);
export const hostOf = (g: Graph, sub: GraphNode): GraphNode | undefined => {
  const e = g.edges.find((x) => x.slot && x.from === sub.id);
  return e && nodeById(g, e.to);
};
export const modelOf = (g: Graph, n: GraphNode): GraphNode | undefined => subsOf(g, n, 'model')[0];
/** x offset of a slot diamond along the bottom of an AI step. */
export const slotX = (n: Pick<GraphNode, 'type'>, s: SlotName): number => {
  const sl = slotsOf(n);
  const i = sl.indexOf(s);
  return (NW * (i + 0.5)) / sl.length;
};
/** Output port centre (prototype `portPos`). */
export const portPos = (n: GraphNode, port: string): [number, number] => {
  const o = outsOf(n);
  const i = Math.max(0, o.indexOf(port));
  return [n.x + NW, n.y + NH / 2 + (i - (o.length - 1) / 2) * 24];
};
export const portOffsetY = (count: number, i: number): number => NH / 2 + (i - (count - 1) / 2) * 24;

export function edgeOK(e: GraphEdge, g: Graph): boolean {
  const a = nodeById(g, e.from);
  const b = nodeById(g, e.to);
  if (!a || !b || !hasDef(a.type) || !hasDef(b.type)) return false;
  if (e.slot) return isSub(a) && def(a.type).sub === e.slot && slotsOf(b).includes(e.slot);
  return outsOf(a).includes(e.from_port) && !!insOf(b);
}

export function newNode(type: string, x = 0, y = 0, config: Config = {}, name?: string, id = uid('n_')): GraphNode {
  return { id, type, name: name ?? def(type).name, x, y, config: { ...defaults(type), ...config }, settings: { ...SETTINGS_DEFAULT } };
}

/**
 * Normalise a graph to the API `Graph` shape: integer positions, complete settings,
 * no dangling/invalid edges, no unknown keys.
 */
export function serializeGraph(g: Graph): Graph {
  const nodes: GraphNode[] = g.nodes.map((n) => ({
    id: n.id,
    type: n.type,
    name: n.name,
    x: Math.round(n.x),
    y: Math.round(n.y),
    config: JSON.parse(JSON.stringify(n.config ?? {})) as Config,
    settings: { ...SETTINGS_DEFAULT, ...(n.settings ?? {}) },
  }));
  const ng: Graph = { nodes, edges: [] };
  ng.edges = g.edges
    .filter((e) => edgeOK(e, { nodes, edges: [] }))
    .map((e) => {
      const out: GraphEdge = { id: e.id, from: e.from, from_port: e.slot ? 'plug' : e.from_port, to: e.to };
      if (e.slot) out.slot = e.slot;
      return out;
    });
  return ng;
}

/** Accept a graph from the API, filling defaults for anything the server left out. */
export function deserializeGraph(g: Graph): Graph {
  const nodes = g.nodes
    .filter((n) => hasDef(n.type))
    .map((n) => ({ ...n, config: { ...defaults(n.type), ...(n.config ?? {}) }, settings: { ...SETTINGS_DEFAULT, ...(n.settings ?? {}) } }));
  const ng: Graph = { nodes, edges: [] };
  ng.edges = g.edges.filter((e) => edgeOK(e, ng));
  return ng;
}

export const slotLabel = (s: SlotName): string => SLOTS[s].label;
