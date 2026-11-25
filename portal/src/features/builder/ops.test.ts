import { describe, expect, it } from 'vitest';
import type { Graph } from '../../api/types';
import { newNode, NH, NW } from './graph';
import { addConnected, canTarget, completeDrag, reason, targetAt, tipFor, type DragSpec } from './ops';

const a = newNode('intake.upload', 0, 0, {}, undefined, 'a');
const b = newNode('read.ocr', 400, 0, {}, undefined, 'b');
const x = newNode('ai.extract', 800, 0, {}, undefined, 'x');
const m = newNode('ai.model', 800, 300, {}, undefined, 'm');
const g: Graph = { nodes: [a, b, x, m], edges: [] };

describe('connection targeting (snapping)', () => {
  it('a wire from an output snaps to any step with an input within reach', () => {
    const d: DragSpec = { t: 'wire', n: a, port: 'out' };
    expect(targetAt(g, d, b.x - 20, b.y + 10)?.id).toBe('b'); // 20px left of the card still snaps
    expect(targetAt(g, d, b.x + NW / 2, b.y + NH + 200)).toBeUndefined();
    expect(canTarget(d, a)).toBe(false);
    expect(canTarget(d, m)).toBe(false);
    expect(reason(d, m)).toMatch(/AI component/);
    expect(tipFor(g, d, b)).toEqual([b.x - 8, b.y + NH / 2]);
  });
  it('dragging back from an input connects from the source’s free output', () => {
    const d: DragSpec = { t: 'rin', n: b };
    const r = completeDrag(g, d, a);
    expect(r.g.edges).toEqual([expect.objectContaining({ from: 'a', from_port: 'out', to: 'b' })]);
  });
  it('a component plug snaps to AI steps with the matching slot', () => {
    const d: DragSpec = { t: 'plug', n: m };
    expect(canTarget(d, x)).toBe(true);
    expect(canTarget(d, b)).toBe(false);
    expect(reason(d, b)).toMatch(/not an AI step/);
    const r = completeDrag(g, d, x);
    expect(r.g.edges).toEqual([expect.objectContaining({ from: 'm', to: 'x', slot: 'model', from_port: 'plug' })]);
  });
  it('dragging from a slot snaps to matching components', () => {
    const d: DragSpec = { t: 'slot', n: x, slot: 'model' };
    expect(canTarget(d, m)).toBe(true);
    expect(canTarget(d, b)).toBe(false);
    expect(reason(d, b)).toBe('Only Model components fit this slot.');
  });
  it('“connect a new step” refuses intake steps', () => {
    expect(addConnected(g, b, 'out', 'intake.email').error).toBe('Intake steps cannot receive a connection');
    const r = addConnected(g, b, 'out', 'read.layout', [700, 100]);
    expect(r.g.nodes.at(-1)).toMatchObject({ type: 'read.layout', x: 704, y: 104 });
  });
});
