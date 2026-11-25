import { describe, expect, it } from 'vitest';
import type { Graph } from '../../api/types';
import { newNode } from './graph';
import { fieldErr, issues, type ValidationCtx } from './validate';
import { CATALOG } from './catalog';
import { graphAP, graphExp, graphKyc, graphXls } from '../../api/mock/seed';

const ctx: ValidationCtx = {
  connections: ['con_gmail', 'con_wa', 'con_pg', 'con_erp', 'con_qdrant', 'con_s3'].map((id) => ({ id })),
  providers: [
    { id: 'prv_anthropic', name: 'Anthropic', enabled: true },
    { id: 'prv_openai', name: 'OpenAI', enabled: true },
    { id: 'prv_azure', name: 'Azure OpenAI', enabled: true },
    { id: 'prv_gemini', name: 'Google Gemini', enabled: true },
    { id: 'prv_ollama', name: 'Ollama (self-hosted)', enabled: false },
  ],
};
const msgs = (g: Graph, c = ctx) => issues(g, c).map((i) => `${i.level}:${i.message}`);
let k = 0;
const N = (type: string, cfg = {}, name?: string) => newNode(type, 0, 0, cfg, name, `n${++k}`);
const E = (from: string, to: string, port = 'out') => ({ id: `e${++k}`, from, from_port: port, to });
const S = (from: string, to: string, slot: 'model' | 'parser' | 'tools' | 'memory' | 'knowledge') => ({ id: `e${++k}`, from, from_port: 'plug', to, slot });

function minimal(): Graph {
  const a = N('intake.upload');
  const b = N('out.db');
  return { nodes: [a, b], edges: [E(a.id, b.id)] };
}

describe('validate.ts — port of issues()', () => {
  it('seeded templates have no blocking errors', () => {
    for (const g of [graphAP(), graphExp(), graphXls()]) expect(issues(g, ctx).filter((i) => i.level === 'error')).toEqual([]);
  });
  it('the KYC template reports nothing blocking with its connections present', () => {
    expect(issues(graphKyc(), ctx).filter((i) => i.level === 'error')).toEqual([]);
  });
  it('a minimal valid pipeline has no issues', () => {
    expect(issues(minimal(), ctx)).toEqual([]);
  });
  it('missing intake', () => {
    const b = N('out.db');
    expect(msgs({ nodes: [b], edges: [] })).toContain('error:Add an intake step to start the pipeline');
  });
  it('no deliver step is a warning', () => {
    const a = N('intake.upload');
    expect(msgs({ nodes: [a], edges: [] })).toContain('warning:No deliver step: results are not stored anywhere');
  });
  it('required field', () => {
    const g = minimal();
    g.nodes[0]!.config.types = '';
    expect(msgs(g)).toContain('error:Upload portal: Accepted file types — required');
  });
  it('connection was removed', () => {
    const g = minimal();
    g.nodes[1]!.config.conn = 'con_gone';
    expect(msgs(g)).toContain('error:Save to database: Database — connection was removed');
  });
  it('must be a number', () => {
    const f = CATALOG['intake.upload']!.fields.find((x) => x.key === 'maxMb')!;
    expect(fieldErr(f, { maxMb: 'abc' }, ctx)).toBe('Must be a number');
    expect(fieldErr(f, { maxMb: 12 }, ctx)).toBe('');
  });
  it('component not plugged in (warning)', () => {
    const g = minimal();
    g.nodes.push(N('tool.calc', {}, 'Calculator'));
    expect(msgs(g)).toContain('warning:Calculator is not plugged into an AI step');
  });
  it('model provider missing or disabled', () => {
    const g = minimal();
    const a = g.nodes[0]!;
    const x = N('ai.classify');
    const m = N('ai.model', { provider: 'prv_nope' });
    g.nodes.push(x, m);
    g.edges = [E(a.id, x.id), E(x.id, g.nodes[1]!.id), S(m.id, x.id, 'model')];
    expect(msgs(g)).toContain('error:Language model: choose a provider');
    m.config.provider = 'prv_ollama';
    expect(msgs(g)).toContain('error:Language model: Ollama (self-hosted) is disabled in Models');
  });
  it('output schema needs fields with snake_case names', () => {
    const g = minimal();
    const p = N('ai.parser', { schema: [] });
    g.nodes.push(p);
    expect(msgs(g)).toContain('error:Output schema: add at least one field');
    p.config.schema = [{ name: 'Bad Name', type: 'string' }];
    expect(msgs(g)).toContain('error:Output schema: field names must be snake_case');
  });
  it('no incoming connection', () => {
    const g = minimal();
    g.nodes.push(N('read.ocr', {}, 'OCR'));
    expect(msgs(g)).toContain('error:OCR has no incoming connection');
  });
  it('outputs leading nowhere (named for multi-output steps)', () => {
    const g = minimal();
    const r = N('route.confidence', {}, 'Gate');
    g.edges.push(E(g.nodes[0]!.id, r.id));
    g.nodes.push(r);
    const m = msgs(g);
    expect(m).toContain('warning:Gate → “pass” leads nowhere');
    expect(m).toContain('warning:Gate → “review” leads nowhere');
    const o = N('read.ocr', {}, 'OCR');
    g.nodes.push(o);
    g.edges.push(E(g.nodes[0]!.id, o.id));
    expect(msgs(g)).toContain('warning:OCR leads nowhere');
  });
  it('AI steps need a model; Extract fields also needs an output schema', () => {
    const g = minimal();
    const x = N('ai.extract', {}, 'Extract');
    g.nodes.push(x);
    g.edges.push(E(g.nodes[0]!.id, x.id), E(x.id, g.nodes[1]!.id));
    const m = msgs(g);
    expect(m).toContain('error:Extract needs a language model');
    expect(m).toContain('error:Extract needs an output schema');
    const c = N('ai.classify', {}, 'Classify');
    g.nodes.push(c);
    g.edges.push(E(g.nodes[0]!.id, c.id), E(c.id, g.nodes[1]!.id));
    expect(msgs(g)).toContain('error:Classify needs a language model');
    expect(msgs(g).filter((x) => x.startsWith('error:Classify needs an output'))).toEqual([]);
  });
  it('confidence threshold range', () => {
    const g = minimal();
    const r = N('route.confidence', { threshold: 1.5 }, 'Gate');
    g.nodes.push(r);
    expect(msgs(g)).toContain('error:Gate: threshold must be between 0 and 1');
    r.config.threshold = 0;
    expect(msgs(g)).toContain('error:Gate: threshold must be between 0 and 1');
    r.config.threshold = 0.85;
    expect(msgs(g)).not.toContain('error:Gate: threshold must be between 0 and 1');
  });
  it('loops are rejected', () => {
    const g = minimal();
    const a = N('read.ocr');
    const b = N('read.layout');
    g.nodes.push(a, b);
    g.edges.push(E(g.nodes[0]!.id, a.id), E(a.id, b.id), E(b.id, a.id));
    expect(msgs(g)).toContain('error:The pipeline contains a loop. Use retries instead of wiring back.');
  });
  it('issues carry node ids and machine codes', () => {
    const g = minimal();
    g.nodes.push(N('read.ocr', {}, 'OCR'));
    const i = issues(g, ctx).find((x) => x.code === 'no_incoming_connection');
    expect(i?.node_id).toBe(g.nodes[2]!.id);
  });
});
