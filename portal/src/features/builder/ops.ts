/**
 * Graph editing operations (pure). Each returns a new Graph plus feedback for the UI.
 * Behaviour mirrors the prototype's addNode / attach / addSub / delNode / delEdge / dupNode / tidy.
 */
import type { Graph, GraphEdge, GraphNode, NodeSettings, SlotName } from '../../api/types';
import { CATALOG, SLOTS, def, defaults, type Config } from './catalog';
import { GAP, NH, NW, SH, SW, hostOf, insOf, isSub, mainNodes, nH, nW, newNode, nodeById, outsOf, slotsOf, subsOf } from './graph';
import { freeSpot, pickHost, subSpot, tidyPositions } from './layout';
import { clone, snap8, uid } from '../../lib/util';

export interface OpResult {
  g: Graph;
  /** node to select afterwards */
  select?: { node?: string; edge?: string };
  /** change description for the draft change list (none → no change made) */
  change?: string;
  toast?: string;
  error?: string;
}

const eid = () => uid('e_');
const withNodes = (g: Graph, nodes: GraphNode[]): Graph => ({ nodes, edges: g.edges });

/** Add a step or component. `pos` null → automatic placement (next to `from` or at `center`). */
export function addNode(g: Graph, type: string, pos: [number, number] | null, from?: GraphNode, center: [number, number] = [400, 200]): OpResult {
  const d = def(type);
  if (d.stage === 'intake' && mainNodes(g).some((m) => !insOf(m)))
    return { g, error: 'A pipeline has one intake step. Delete the current intake first, or use “Replace intake” from its menu.' };
  const n = newNode(type);
  if (d.sub && pos == null) {
    const host = pickHost(g, d.sub, from);
    if (host) {
      const r = attach({ nodes: [...g.nodes, n], edges: g.edges }, n, host, d.sub);
      if (r.error) return { ...r, g };
      return { ...r, toast: host !== from ? `Plugged into “${host.name}” · ${SLOTS[d.sub].label}` : r.toast };
    }
  }
  let x: number;
  let y: number;
  if (pos == null) {
    if (from && outsOf(from).length && insOf(n)) [x, y] = freeSpot(from.x + GAP, from.y, g);
    else [x, y] = freeSpot(center[0] - nW(n) / 2, center[1] - nH(n) / 2, g, nH(n), nW(n));
  } else [x, y] = pos;
  n.x = snap8(x);
  n.y = snap8(y);
  const edges = [...g.edges];
  if (from && outsOf(from).length && insOf(n)) {
    const port = outsOf(from).find((o) => !edges.some((e) => e.from === from.id && e.from_port === o)) ?? outsOf(from)[0]!;
    edges.push({ id: eid(), from: from.id, from_port: port, to: n.id });
  }
  return { g: { nodes: [...g.nodes, n], edges }, select: { node: n.id }, change: `Added “${n.name}”` };
}

/** Plug component `sub` (already in g) into `host`'s `slot`. */
export function attach(g: Graph, sub: GraphNode, host: GraphNode, slot: SlotName): OpResult {
  if (!slotsOf(host).includes(slot)) return { g, error: `${host.name} has no ${SLOTS[slot].label} slot.` };
  if (def(sub.type).sub !== slot) return { g, error: `Only ${SLOTS[slot].label} components fit this slot.` };
  let edges = [...g.edges];
  let toast: string | undefined;
  if (SLOTS[slot].max === 1) {
    const old = subsOf(g, host, slot).filter((s) => s.id !== sub.id);
    old.forEach((o) => (edges = edges.filter((e) => !(e.slot && e.from === o.id))));
    if (old.length) toast = `Replaced the ${SLOTS[slot].label.toLowerCase()} on “${host.name}”`;
  } else if (subsOf(g, host, slot).filter((s) => s.id !== sub.id).length >= SLOTS[slot].max) return { g, error: `${SLOTS[slot].label} is full` };
  edges = edges.filter((e) => !(e.slot && e.from === sub.id));
  let nodes = g.nodes;
  if (!sub.x && !sub.y) {
    const [x, y] = subSpot({ nodes: g.nodes, edges }, host, slot, sub);
    nodes = g.nodes.map((m) => (m.id === sub.id ? { ...m, x, y } : m));
  }
  edges.push({ id: eid(), from: sub.id, from_port: 'plug', to: host.id, slot });
  return { g: { nodes, edges }, select: { node: sub.id }, change: `Connected ${def(sub.type).name} to “${host.name}”`, toast };
}

/** Create a component of `type` and plug it into host/slot (slot "+" menus, drop onto an AI step). */
export function addSub(g: Graph, host: GraphNode, slot: SlotName, type: string, pos?: [number, number]): OpResult {
  const n = newNode(type);
  if (pos) {
    n.x = snap8(pos[0]) || 8;
    n.y = snap8(pos[1]) || 8;
  }
  const r = attach({ nodes: [...g.nodes, n], edges: g.edges }, n, host, slot);
  return r.error ? { ...r, g } : r;
}

/** Connect an output port to a step input (no duplicates). */
export function connect(g: Graph, fromId: string, port: string, toId: string): OpResult {
  const a = nodeById(g, fromId);
  const b = nodeById(g, toId);
  if (!a || !b || a.id === b.id) return { g };
  if (!outsOf(a).includes(port) || !insOf(b)) return { g };
  if (g.edges.some((x) => !x.slot && x.from === fromId && x.from_port === port && x.to === toId)) return { g };
  return { g: { nodes: g.nodes, edges: [...g.edges, { id: eid(), from: fromId, from_port: port, to: toId }] }, change: 'Connected steps' };
}

/** First free output port of a node (used when dragging backwards from an input). */
export const freePort = (g: Graph, b: GraphNode): string => {
  const o = outsOf(b);
  return o.find((x) => !g.edges.some((e) => e.from === b.id && e.from_port === x)) ?? o[0]!;
};

/** Add a new step connected from `from`/`port` ("connect a new step" menu). */
export function addConnected(g: Graph, from: GraphNode, port: string, type: string, pos?: [number, number]): OpResult {
  const d = def(type);
  if (d.stage === 'intake') return { g, error: 'Intake steps cannot receive a connection' };
  const n = newNode(type);
  const [nx, ny] = pos ?? freeSpot(from.x + GAP, from.y, g);
  n.x = snap8(nx);
  n.y = snap8(ny);
  return {
    g: { nodes: [...g.nodes, n], edges: [...g.edges, { id: eid(), from: from.id, from_port: port, to: n.id }] },
    select: { node: n.id },
    change: `Added “${n.name}”`,
  };
}

export function deleteNode(g: Graph, id: string): OpResult & { unplugged?: number } {
  const n = nodeById(g, id);
  if (!n) return { g };
  const subs = subsOf(g, n).length;
  return {
    g: { nodes: g.nodes.filter((x) => x.id !== id), edges: g.edges.filter((e) => e.from !== id && e.to !== id) },
    change: `Deleted “${n.name}”`,
    toast: `Deleted “${n.name}”${subs ? ` · ${subs} component${subs > 1 ? 's' : ''} unplugged` : ''} — Ctrl+Z to undo`,
    unplugged: subs,
  };
}

export function deleteEdge(g: Graph, id: string): OpResult {
  if (!g.edges.some((e) => e.id === id)) return { g };
  return { g: { nodes: g.nodes, edges: g.edges.filter((e) => e.id !== id) }, change: 'Removed a connection' };
}

export function unplug(g: Graph, subId: string): OpResult {
  if (!g.edges.some((x) => x.slot && x.from === subId)) return { g };
  return { g: { nodes: g.nodes, edges: g.edges.filter((x) => !(x.slot && x.from === subId)) }, change: 'Unplugged a component' };
}

export function duplicateNode(g: Graph, id: string): OpResult {
  const n = nodeById(g, id);
  if (!n) return { g };
  if (!insOf(n) && !isSub(n)) return { g, error: 'A pipeline has one intake step' };
  const c = clone(n);
  c.id = uid('n_');
  [c.x, c.y] = freeSpot(n.x + 32, n.y + nH(n) + 40, g, nH(n), nW(n));
  c.name += ' (copy)';
  return { g: withNodes(g, [...g.nodes, c]), select: { node: c.id }, change: 'Duplicated a step' };
}

export function replaceIntake(g: Graph, id: string, type: string): OpResult {
  const d = def(type);
  return { g: withNodes(g, g.nodes.map((n) => (n.id === id ? { ...n, type, name: d.name, config: defaults(type) } : n))), change: 'Replaced intake' };
}

export function moveNodes(g: Graph, pos: Record<string, { x: number; y: number }>): Graph {
  return withNodes(g, g.nodes.map((n) => (pos[n.id] ? { ...n, x: pos[n.id]!.x, y: pos[n.id]!.y } : n)));
}

export function tidy(g: Graph): OpResult {
  return { g: moveNodes(g, tidyPositions(g)), change: 'Tidied the layout' };
}

export function updateNode(g: Graph, id: string, patch: { name?: string; config?: Config; settings?: Partial<NodeSettings> }): Graph {
  return withNodes(
    g,
    g.nodes.map((n) => {
      if (n.id !== id) return n;
      const out: GraphNode = { ...n };
      if (patch.name != null) out.name = patch.name;
      if (patch.config) out.config = { ...n.config, ...patch.config };
      if (patch.settings) out.settings = { ...(n.settings ?? { retries: 2, timeout_s: 60, on_error: 'stop' }), ...patch.settings } as NodeSettings;
      return out;
    }),
  );
}

/* ── Connection targeting (prototype canTarget / inReach / targetAt / reason) ── */
export type DragKind = 'wire' | 'rin' | 'plug' | 'slot';
export interface DragSpec {
  t: DragKind;
  n: GraphNode;
  port?: string;
  slot?: SlotName;
}

export function canTarget(d: DragSpec, b: GraphNode | undefined): boolean {
  if (!b || b.id === d.n.id) return false;
  if (d.t === 'wire') return !!insOf(b);
  if (d.t === 'rin') return outsOf(b).length > 0;
  if (d.t === 'plug') return slotsOf(b).includes(CATALOG[d.n.type]!.sub!);
  if (d.t === 'slot') return isSub(b) && CATALOG[b.type]!.sub === d.slot;
  return false;
}
const inReach = (b: GraphNode, sx: number, sy: number) => {
  const pad = 30;
  const bot = slotsOf(b).length ? 64 : pad;
  return sx >= b.x - pad && sx <= b.x + nW(b) + pad && sy >= b.y - pad && sy <= b.y + nH(b) + bot;
};
export function targetAt(g: Graph, d: DragSpec, sx: number, sy: number): GraphNode | undefined {
  let best: GraphNode | undefined;
  let bd = 1e9;
  for (const b of g.nodes) {
    if (!canTarget(d, b) || !inReach(b, sx, sy)) continue;
    const dd = Math.hypot(sx - (b.x + nW(b) / 2), sy - (b.y + nH(b) / 2));
    if (dd < bd) {
      bd = dd;
      best = b;
    }
  }
  return best;
}
export const nodeAt = (g: Graph, sx: number, sy: number): GraphNode | undefined => g.nodes.find((b) => sx >= b.x && sx <= b.x + nW(b) && sy >= b.y && sy <= b.y + nH(b));

export function reason(d: DragSpec, b: GraphNode): string {
  if (d.t === 'wire') return isSub(b) ? `${b.name} is an AI component. Drag its top diamond onto an AI step.` : 'Intake steps start the pipeline and cannot receive a connection.';
  if (d.t === 'rin') return isSub(b) ? 'AI components plug into AI step slots.' : `${b.name} has no output.`;
  if (d.t === 'plug') {
    const sl = SLOTS[CATALOG[d.n.type]!.sub!].label;
    return slotsOf(b).length ? `${b.name} has no ${sl} slot.` : `${b.name} is not an AI step. Drop on Extract fields, Classify document or AI agent.`;
  }
  return isSub(b) ? `${b.name} is not a ${SLOTS[d.slot!].label} component.` : `Only ${SLOTS[d.slot!].label} components fit this slot.`;
}

/** Where a temporary wire should snap to when hovering a target node (prototype tipFor). */
export function tipFor(g: Graph, d: DragSpec, b: GraphNode): [number, number] {
  if (d.t === 'wire') return [b.x - 8, b.y + NH / 2];
  if (d.t === 'rin') {
    const o = outsOf(b);
    const port = freePort(g, b);
    const i = Math.max(0, o.indexOf(port));
    return [b.x + NW, b.y + NH / 2 + (i - (o.length - 1) / 2) * 24];
  }
  if (d.t === 'plug') {
    const sl = slotsOf(b);
    return [b.x + (NW * (sl.indexOf(CATALOG[d.n.type]!.sub!) + 0.5)) / sl.length, b.y + NH];
  }
  return [b.x + SW / 2, b.y];
}

/** Apply a completed drag onto target b. */
export function completeDrag(g: Graph, d: DragSpec, b: GraphNode): OpResult {
  if (d.t === 'wire') return connect(g, d.n.id, d.port!, b.id);
  if (d.t === 'rin') return connect(g, b.id, freePort(g, b), d.n.id);
  if (d.t === 'plug') return attach(g, d.n, b, CATALOG[d.n.type]!.sub!);
  return attach(g, b, d.n, d.slot!);
}

export { SH, SW, hostOf };
export type { GraphEdge };
