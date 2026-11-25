import { beforeEach, describe, expect, it } from 'vitest';
import { useEditor } from './store';
import { newNode } from './graph';
import { useToasts } from '../../ui/toast';

const S = () => useEditor.getState();
const T = () => useEditor.temporal.getState();

beforeEach(() => {
  const a = newNode('intake.upload', 40, 200, {}, undefined, 'a');
  const b = newNode('read.ocr', 340, 200, {}, undefined, 'b');
  S().load('pl_t', { nodes: [a, b], edges: [] }, 'W/"1"', []);
  useToasts.setState({ items: [] });
});

describe('editor store', () => {
  it('adds a step next to the selected one and connects it', () => {
    S().select({ node: 'b' });
    expect(S().addNode('read.layout', null, true)).toBe(true);
    const n = S().nodes.at(-1)!;
    expect(n.type).toBe('read.layout');
    expect(n.x).toBe(340 + 300);
    expect(S().edges).toEqual([expect.objectContaining({ from: 'b', from_port: 'out', to: n.id })]);
    expect(S().sel).toEqual({ node: n.id });
    expect(S().pending).toContain('Added “Layout and tables”');
  });
  it('rejects a second intake', () => {
    expect(S().addNode('intake.email', [0, 0], false)).toBe(false);
    expect(S().nodes).toHaveLength(2);
    expect(useToasts.getState().items[0]?.bad).toBe(true);
  });
  it('connects ports without duplicates', () => {
    S().connect('a', 'out', 'b');
    S().connect('a', 'out', 'b');
    expect(S().edges).toHaveLength(1);
    S().connect('b', 'out', 'a'); // intake has no input
    expect(S().edges).toHaveLength(1);
  });
  it('deletes a node with its edges, and undo brings both back', () => {
    S().connect('a', 'out', 'b');
    S().deleteNode('b');
    expect(S().nodes.map((n) => n.id)).toEqual(['a']);
    expect(S().edges).toHaveLength(0);
    S().undo();
    expect(S().nodes.map((n) => n.id)).toEqual(['a', 'b']);
    expect(S().edges).toHaveLength(1);
    S().redo();
    expect(S().nodes.map((n) => n.id)).toEqual(['a']);
  });
  it('deletes an edge; undo/redo', () => {
    S().connect('a', 'out', 'b');
    const id = S().edges[0]!.id;
    S().deleteEdge(id);
    expect(S().edges).toHaveLength(0);
    S().undo();
    expect(S().edges.map((e) => e.id)).toEqual([id]);
    S().redo();
    expect(S().edges).toHaveLength(0);
  });
  it('plugs components into slots; single slots are replaced, multi slots accumulate', () => {
    S().addNode('ai.extract', [700, 200], false);
    const x = S().nodes.at(-1)!;
    S().addSub(x.id, 'model', 'ai.model');
    const m1 = S().nodes.at(-1)!;
    expect(S().edges.filter((e) => e.slot === 'model' && e.to === x.id).map((e) => e.from)).toEqual([m1.id]);
    expect(m1.y).toBeGreaterThan(x.y);
    S().addSub(x.id, 'model', 'ai.model');
    const m2 = S().nodes.at(-1)!;
    expect(S().edges.filter((e) => e.slot === 'model' && e.to === x.id).map((e) => e.from)).toEqual([m2.id]);
    S().addSub(x.id, 'tools', 'tool.calc');
    S().addSub(x.id, 'tools', 'tool.search');
    expect(S().edges.filter((e) => e.slot === 'tools')).toHaveLength(2);
    // unplug + undo
    S().unplug(m2.id);
    expect(S().edges.some((e) => e.slot === 'model')).toBe(false);
    S().undo();
    expect(S().edges.some((e) => e.slot === 'model')).toBe(true);
  });
  it('attachTo re-plugs an existing component (drag from the plug diamond)', () => {
    S().addNode('ai.extract', [700, 200], false);
    const x = S().nodes.at(-1)!;
    S().addNode('ai.memory', [100, 500], false); // free-floating component
    const mem = S().nodes.at(-1)!;
    S().attachTo(mem.id, x.id, 'memory'); // Extract fields has no Memory slot → rejected with a toast
    expect(S().edges.some((e) => e.from === mem.id)).toBe(false);
    expect(useToasts.getState().items.at(-1)?.msg).toBe('Extract fields has no Memory slot.');
    S().addNode('ai.agent', [700, 500], false);
    const ag = S().nodes.at(-1)!;
    S().attachTo(mem.id, ag.id, 'memory');
    expect(S().edges.filter((e) => e.from === mem.id)).toEqual([expect.objectContaining({ to: ag.id, slot: 'memory', from_port: 'plug' })]);
  });
  it('a node drag is one undo step', () => {
    T().clear();
    S().dragMove({ a: { x: 48, y: 200 } }, false);
    S().dragMove({ a: { x: 96, y: 208 } }, false);
    S().dragMove({ a: { x: 128, y: 216 } }, true);
    expect(S().nodes[0]).toMatchObject({ x: 128, y: 216 });
    expect(T().pastStates).toHaveLength(1);
    S().undo();
    expect(S().nodes[0]).toMatchObject({ x: 40, y: 200 });
    expect(S().pending).toContain('Moved steps');
  });
  it('keystrokes in one field coalesce into one undo step', () => {
    T().clear();
    S().updateConfig('a', { types: 'p' }, 'types');
    S().updateConfig('a', { types: 'pd' }, 'types');
    S().updateConfig('a', { types: 'pdf' }, 'types');
    expect(T().pastStates).toHaveLength(1);
    S().undo();
    expect(S().nodes[0]!.config.types).toBe('pdf, png, jpg, tiff');
  });
  it('duplicate and tidy', () => {
    S().duplicate('b');
    expect(S().nodes.at(-1)!.name).toBe('Text recognition (copy)');
    S().duplicate('a');
    expect(S().nodes).toHaveLength(3); // intake cannot be duplicated
    S().connect('a', 'out', 'b');
    S().tidy();
    expect(S().nodes.find((n) => n.id === 'a')!.x).toBe(40);
    expect(S().nodes.find((n) => n.id === 'b')!.x).toBe(340);
  });
  it('selection changes are not recorded in history', () => {
    T().clear();
    S().select({ node: 'a' });
    S().select({});
    expect(T().pastStates).toHaveLength(0);
  });
});
