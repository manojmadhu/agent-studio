import { beforeAll, describe, expect, it } from 'vitest';
import { api } from '../client';
import { mockBackend } from './backend';
import { ApiError } from '../errors';
import { streamRunEvents, createSseParser } from '../sse';
import type { RunEvent } from '../types';

const SECRET = 'sk-ant-SUPERSECRET-value-1234WXYZ';
const expectProblem = async (p: Promise<unknown>, status: number, code?: string) => {
  try {
    await p;
  } catch (e) {
    expect(e).toBeInstanceOf(ApiError);
    expect((e as ApiError).status).toBe(status);
    if (code) expect((e as ApiError).code).toBe(code);
    return e as ApiError;
  }
  throw new Error(`expected HTTP ${status}`);
};

beforeAll(() => {
  const m = mockBackend();
  m.latency = 0;
  m.speed = 0.002;
  m.reset();
});

describe('mock backend · connections', () => {
  it('never returns secret material — only secret_hint', async () => {
    const c = await api.createConnection({ name: 'Test key', service: 'anthropic', settings: { account: 'x' }, auth: { type: 'api_key', key: SECRET } });
    expect(c.secret_hint).toBe('••••WXYZ');
    expect(c.status).toBe('unverified');
    const all = await api.listConnections();
    const got = await mockBackend().fetch(new Request(`https://mock.docintel.local/v1/connections/${c.id}`));
    const bodies = JSON.stringify(all) + JSON.stringify(c) + (await got.text());
    expect(bodies).not.toContain(SECRET);
    expect(bodies).not.toContain('"auth"');
    // the vault keeps it, the API does not expose it
    expect(mockBackend().vault.get(c.id)).toMatchObject({ key: SECRET });
  });
  it('rotation replaces the hint', async () => {
    const c = await api.createConnection({ name: 'Rot', service: 'http_api', settings: { base_url: 'https://x' }, auth: { type: 'bearer', token: 'tok-AAAA1111' } });
    const r = await api.updateConnection(c.id, { auth: { type: 'bearer', token: 'tok-BBBB2222' } });
    expect(r.secret_hint).toBe('••••2222');
    expect(JSON.stringify(r)).not.toContain('tok-');
  });
  it('validates the auth scheme against the service', async () => {
    await expectProblem(api.createConnection({ name: 'x', service: 'gmail', auth: { type: 'api_key', key: 'k' } }), 422, 'validation');
  });
  it('delete in use → 409 with usages; force deletes', async () => {
    const e = await expectProblem(api.deleteConnection('con_gmail'), 409, 'connection_in_use');
    expect(e.problem.usages?.length).toBeGreaterThan(0);
    expect(e.problem.usages?.[0]).toMatchObject({ resource_type: 'pipeline', resource_id: 'pl_ap' });
    await api.deleteConnection('con_gmail', true);
    const list = await api.listConnections();
    expect(list.data.some((c) => c.id === 'con_gmail')).toBe(false);
  });
  it('unused connections delete without force', async () => {
    const c = await api.createConnection({ name: 'Tmp', service: 'qdrant', settings: { url: 'http://q' }, auth: { type: 'none' } });
    await api.deleteConnection(c.id);
    expect((await api.listConnections()).data.some((x) => x.id === c.id)).toBe(false);
  });
});

describe('mock backend · drafts with ETag / If-Match', () => {
  it('saves with the current ETag and returns a new one', async () => {
    const d = await api.getDraft('pl_exp');
    expect(d.etag).toMatch(/^W\/"pl_exp-\d+"$/);
    const s = await api.saveDraft('pl_exp', d.etag, { graph: d.data.graph, change: 'Moved steps' });
    expect(s.etag).not.toBe(d.etag);
    expect(s.data.changes).toContain('Moved steps');
  });
  it('a stale ETag → 412 etag_mismatch', async () => {
    const d = await api.getDraft('pl_kyc');
    await api.saveDraft('pl_kyc', d.etag, { graph: d.data.graph, change: 'Someone else' });
    await expectProblem(api.saveDraft('pl_kyc', d.etag, { graph: d.data.graph, change: 'Me' }), 412, 'etag_mismatch');
  });
  it('publishing an invalid draft → 422 with issues; valid draft publishes vN+1', async () => {
    const d = await api.getDraft('pl_exp');
    const broken = { ...d.data.graph, edges: [] };
    const s = await api.saveDraft('pl_exp', d.etag, { graph: broken, change: 'Broke it' });
    const e = await expectProblem(api.publishVersion('pl_exp', 'x'), 422, 'pipeline_invalid');
    expect(e.problem.issues?.length).toBeGreaterThan(0);
    await api.saveDraft('pl_exp', s.etag, { graph: d.data.graph, change: 'Fixed it' });
    const v = await api.publishVersion('pl_exp', 'Fixed');
    expect(v.version).toBe(7);
    expect((await api.getPipeline('pl_exp')).live_version).toBe(7);
    expect((await api.getDraft('pl_exp')).data.changes).toEqual([]);
  });
  it('idempotency keys replay creates', async () => {
    const req = () =>
      new Request('https://mock.docintel.local/v1/pipelines', { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': 'abc' }, body: JSON.stringify({ name: 'Idem', intake_type: 'intake.upload' }) });
    const a = await (await mockBackend().fetch(req())).json();
    const r2 = await mockBackend().fetch(req());
    expect(r2.headers.get('Idempotent-Replayed')).toBe('true');
    expect((await r2.json()).id).toBe(a.id);
  });
});

describe('mock backend · runs over SSE', () => {
  beforeAll(() => mockBackend().reset());
  it('emits the RunEvent sequence and produces a result with extracted fields', async () => {
    const run = await api.createTestRun('pl_ap', { document_id: 'doc_inv_sample' });
    const evs: RunEvent[] = [];
    await streamRunEvents(run.id, (e) => evs.push(e));
    expect(evs[0]!.event).toBe('run.started');
    expect(evs.at(-1)).toMatchObject({ event: 'run.finished', status: 'completed' });
    const started = evs.filter((e) => e.event === 'step.started').map((e) => e.node_id);
    expect(started[0]).toBe('ap_n1');
    expect(evs.find((e) => e.node_id === 'ap_n6' && e.event === 'step.finished')?.step?.output_port).toBe('invoice');
    expect(evs.find((e) => e.node_id === 'ap_n11' && e.event === 'step.finished')?.step?.output_port).toBe('pass');
    expect(started).not.toContain('ap_n15'); // receipt branch skipped
    const res = await api.getRunResult(run.id);
    expect(res.fields).toHaveLength(10);
    expect(res.fields?.find((f) => f.name === 'iban')).toMatchObject({ pii: true, masked: true, value: null });
    expect(res.classification?.type).toBe('invoice');
  });
  it('blocks test runs on graphs with errors (422)', async () => {
    const p = await api.createPipeline({ name: 'Empty', intake_type: 'intake.storage' }); // storage connection is required and empty
    await expectProblem(api.createTestRun(p.id, { document_id: 'doc_inv_sample' }), 422, 'pipeline_invalid');
  });
  it('parses SSE frames across chunk boundaries', () => {
    const frames: string[] = [];
    const parse = createSseParser((f) => frames.push(`${f.id}|${f.event}|${f.data}`));
    parse('id: 1\nevent: a\nda');
    parse('ta: {"x":1}\n\n: comment\nid: 2\ndata: l1\ndata: l2\n\n');
    expect(frames).toEqual(['1|a|{"x":1}', '2|undefined|l1\nl2']);
  });
});

describe('mock backend · other resources', () => {
  it('workers: health, invoke through the adapter, delete in use → 409', async () => {
    const h = await api.checkWorkerHealth('wrk_excel');
    expect(h.status).toBe('healthy');
    const out = await api.invokeWorker('wrk_excel', 'doc_inv_sample');
    expect(out.pages).toHaveLength(2);
    await expectProblem(api.deleteWorker('wrk_paddle'), 409, 'worker_in_use');
  });
  it('MCP: discover, toggle a tool, call with schema validation', async () => {
    const m = await api.discoverMcpTools('mcp_vendors');
    expect(m.tools?.map((t) => t.name)).toContain('match_po');
    const u = await api.updateMcpServer('mcp_vendors', { tools: [{ name: 'get_vendor', enabled: false }] });
    expect(u.tools?.find((t) => t.name === 'get_vendor')?.enabled).toBe(false);
    await expectProblem(api.callMcpTool('mcp_vendors', 'match_po', {}), 422, 'invalid_arguments');
    const r = await api.callMcpTool('mcp_vendors', 'match_po', { po_number: 'PO-1' });
    expect(r.is_error).toBe(false);
  });
  it('API keys: secret shown once on create and rotate only', async () => {
    const k = await api.createApiKey({ name: 'CI', scopes: ['runs:read'] });
    expect(k.secret).toMatch(/^dik_live_/);
    const list = await api.listApiKeys();
    expect(JSON.stringify(list)).not.toContain(k.secret);
    const r = await api.rotateApiKey(k.id);
    expect(r.secret).not.toBe(k.secret);
  });
  it('providers: disabling one is reflected in validation context data', async () => {
    const p = await api.updateProvider('prv_gemini', { enabled: false });
    expect(p.enabled).toBe(false);
    const v = await api.validatePipeline('pl_exp');
    expect(v.valid).toBe(false);
    expect(v.issues.some((i) => i.code === 'provider_disabled')).toBe(true);
    await api.updateProvider('prv_gemini', { enabled: true });
  });
});
