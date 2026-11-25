import { describe, expect, it } from 'vitest';
import type { Graph } from '../../api/types';
import { deserializeGraph, edgeOK, insOf, newNode, outsOf, serializeGraph, slotsOf } from './graph';
import { graphAP } from '../../api/mock/seed';

describe('graph serialisation to the API Graph shape', () => {
  it('normalises positions, settings and edges', () => {
    const a = newNode('intake.upload', 10.4, 20.6, {}, undefined, 'a');
    const b = newNode('read.ocr', 300, 20, {}, undefined, 'b');
    const m = newNode('ai.model', 0, 200, {}, undefined, 'm');
    const x = newNode('ai.extract', 600, 20, {}, undefined, 'x');
    delete (b as { settings?: unknown }).settings;
    const g: Graph = {
      nodes: [a, b, m, x],
      edges: [
        { id: 'e1', from: 'a', from_port: 'out', to: 'b' },
        { id: 'e2', from: 'm', from_port: 'whatever', to: 'x', slot: 'model' },
        { id: 'e3', from: 'b', from_port: 'nope', to: 'x' }, // invalid port
        { id: 'e4', from: 'a', from_port: 'out', to: 'gone' }, // dangling
        { id: 'e5', from: 'b', from_port: 'out', to: 'a' }, // intake has no input
      ],
    };
    const s = serializeGraph(g);
    expect(s.nodes[0]).toMatchObject({ id: 'a', x: 10, y: 21, settings: { retries: 2, timeout_s: 60, on_error: 'stop' } });
    expect(s.nodes[1]!.settings).toEqual({ retries: 2, timeout_s: 60, on_error: 'stop', notes: '' });
    expect(s.edges).toEqual([
      { id: 'e1', from: 'a', from_port: 'out', to: 'b' },
      { id: 'e2', from: 'm', from_port: 'plug', to: 'x', slot: 'model' },
    ]);
    expect(Object.keys(s)).toEqual(['nodes', 'edges']);
    expect(Object.keys(s.nodes[0]!).sort()).toEqual(['config', 'id', 'name', 'settings', 'type', 'x', 'y']);
  });
  it('round-trips the AP template unchanged', () => {
    const g = graphAP();
    const once = serializeGraph(g);
    expect(serializeGraph(deserializeGraph(JSON.parse(JSON.stringify(once)) as Graph))).toEqual(once);
    expect(once.edges.length).toBe(g.edges.length);
  });
  it('deserialise fills config defaults and drops unknown step types', () => {
    const g = deserializeGraph({ nodes: [{ id: 'a', type: 'read.ocr', name: 'OCR', x: 0, y: 0, config: {} }, { id: 'z', type: 'unknown.x', name: 'Z', x: 0, y: 0, config: {} }], edges: [] });
    expect(g.nodes).toHaveLength(1);
    expect(g.nodes[0]!.config).toMatchObject({ worker: 'wrk_paddle', lang: 'English + Arabic' });
  });
  it('ports: dynamic outputs and error output', () => {
    const r = newNode('route.type', 0, 0, { paths: 'invoice, receipt' });
    expect(outsOf(r)).toEqual(['invoice', 'receipt', 'other']);
    r.settings = { retries: 2, timeout_s: 60, on_error: 'error_output' };
    expect(outsOf(r)).toEqual(['invoice', 'receipt', 'other', 'error']);
    expect(insOf(newNode('intake.email'))).toBe(0);
    expect(outsOf(newNode('ai.model'))).toEqual([]);
    expect(slotsOf(newNode('ai.agent'))).toEqual(['model', 'memory', 'tools', 'knowledge', 'parser']);
  });
  it('edgeOK checks slot compatibility', () => {
    const m = newNode('ai.memory', 0, 0, {}, undefined, 'm');
    const x = newNode('ai.extract', 0, 0, {}, undefined, 'x');
    const ag = newNode('ai.agent', 0, 0, {}, undefined, 'g');
    const g: Graph = { nodes: [m, x, ag], edges: [] };
    expect(edgeOK({ id: '1', from: 'm', from_port: 'plug', to: 'x', slot: 'memory' }, g)).toBe(false);
    expect(edgeOK({ id: '1', from: 'm', from_port: 'plug', to: 'g', slot: 'memory' }, g)).toBe(true);
    expect(edgeOK({ id: '1', from: 'm', from_port: 'plug', to: 'g', slot: 'model' }, g)).toBe(false);
  });
});
