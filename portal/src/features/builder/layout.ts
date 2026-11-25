/** Placement and auto-layout (prototype `freeSpot`, `tidy`). Pure functions over a Graph. */
import type { Graph, GraphNode, SlotName } from '../../api/types';
import { GAP, NH, NW, SH, SW, hostOf, isSub, mainNodes, nH, nW, slotsOf, subsOf } from './graph';
import { SLOTS } from './catalog';
import { snap8 } from '../../lib/util';

const boxOf = (m: GraphNode): [number, number, number, number] => [m.x - 16, m.y - 12, nW(m) + 32, nH(m) + (slotsOf(m).length ? 84 : 24)];

export function isFree(x: number, y: number, g: Graph, h = NH, w = NW, skip?: GraphNode): boolean {
  return !g.nodes.some((m) => {
    if (m === skip || (skip && m.id === skip.id)) return false;
    const [mx, my, mw, mh] = boxOf(m);
    return x < mx + mw && x + w > mx && y < my + mh && y + h > my;
  });
}

export function freeSpot(x: number, y: number, g: Graph, h = NH, w = NW, skip?: GraphNode): [number, number] {
  for (let r = 0; r < 10; r++)
    for (const dx of [0, 1, -1, 2, -2]) {
      const cx = x + dx * (w + 28);
      const cy = y + r * (h + 40);
      if (isFree(cx, cy, g, h, w, skip)) return [cx, cy];
    }
  return [x, y];
}

/** Position for a component newly attached under `host`'s slot. */
export function subSpot(g: Graph, host: GraphNode, slot: SlotName, sub: GraphNode): [number, number] {
  const sl = slotsOf(host);
  const sx = (NW * (sl.indexOf(slot) + 0.5)) / sl.length;
  const [x, y] = freeSpot(host.x + sx - SW / 2, host.y + NH + 112, g, SH, SW, sub);
  return [snap8(x), snap8(y)];
}

/** Choose which AI step a component should plug into when added without a target. */
export function pickHost(g: Graph, slot: SlotName, from?: GraphNode): GraphNode | undefined {
  if (from && slotsOf(from).includes(slot)) return from;
  if (from && isSub(from)) {
    const h = hostOf(g, from);
    if (h && slotsOf(h).includes(slot)) return h;
  }
  const ag = mainNodes(g).filter((n) => slotsOf(n).includes(slot));
  return ag.find((n) => !subsOf(g, n, slot).length) ?? (SLOTS[slot].max > 1 ? ag[ag.length - 1] : undefined);
}

/** Layered left-to-right layout; components sit under their host. Returns new node positions. */
export function tidyPositions(g: Graph): Record<string, { x: number; y: number }> {
  const main = mainNodes(g);
  const me = g.edges.filter((e) => !e.slot);
  const depth: Record<string, number> = {};
  const inc: Record<string, number> = {};
  main.forEach((n) => (inc[n.id] = 0));
  me.forEach((e) => (inc[e.to] = (inc[e.to] ?? 0) + 1));
  const q = main.filter((n) => !inc[n.id]).map((n) => n.id);
  q.forEach((id) => (depth[id] = 0));
  let guard = 0;
  while (q.length && guard++ < 3000) {
    const id = q.shift()!;
    me.filter((e) => e.from === id).forEach((e) => {
      if ((depth[e.to] ?? -1) < depth[id]! + 1 && depth[id]! < 60) {
        depth[e.to] = depth[id]! + 1;
        q.push(e.to);
      }
    });
  }
  const pos: Record<string, { x: number; y: number }> = {};
  const cols: Record<number, GraphNode[]> = {};
  main.forEach((n) => {
    const d = depth[n.id] ?? 0;
    (cols[d] = cols[d] ?? []).push(n);
  });
  Object.entries(cols).forEach(([d, ns]) => {
    const hs = ns.map((n) => (slotsOf(n).length && subsOf(g, n).length ? NH + 190 : NH + 60));
    const tot = hs.reduce((a, b) => a + b, 0);
    let y = 200 - tot / 2;
    ns.forEach((n, i) => {
      pos[n.id] = { x: 40 + Number(d) * GAP, y: snap8(y) };
      y += hs[i]!;
    });
  });
  main.forEach((h) => {
    const subs = subsOf(g, h);
    const hp = pos[h.id]!;
    subs.forEach((s, i) => {
      pos[s.id] = { x: hp.x + 20 + (i - (subs.length - 1) / 2) * 224, y: hp.y + NH + 112 };
    });
  });
  g.nodes
    .filter((n) => isSub(n) && !hostOf(g, n))
    .forEach((n, i) => {
      pos[n.id] = { x: 40 + i * 230, y: -60 };
    });
  return pos;
}

export function bounds(g: Graph): [number, number, number, number] | null {
  const ns = g.nodes;
  if (!ns.length) return null;
  return [
    Math.min(...ns.map((n) => n.x)) - 40,
    Math.min(...ns.map((n) => n.y)) - 60,
    Math.max(...ns.map((n) => n.x + nW(n))) + 80,
    Math.max(...ns.map((n) => n.y + nH(n))) + 70,
  ];
}
