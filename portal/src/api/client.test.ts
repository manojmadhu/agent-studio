import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from './client';
import { getApiConfig, setApiConfig } from './config';
import { ApiError, errorMessage } from './errors';

afterEach(() => {
  setApiConfig({ mode: 'mock', credential: '' });
  vi.restoreAllMocks();
});

describe('live API client', () => {
  it('sends auth + Idempotency-Key to the configured base URL', async () => {
    const seen: Request[] = [];
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      seen.push(input as Request);
      return new Response(JSON.stringify({ id: 'pl_1', name: 'X', status: 'draft', live_version: null, owner_team: 'Ops' }), { status: 201, headers: { 'Content-Type': 'application/json' } });
    });
    setApiConfig({ mode: 'live', baseUrl: 'https://api.example.test/v1', scheme: 'api_key', credential: 'dik_test_abc' });
    const p = await api.createPipeline({ name: 'X', intake_type: 'intake.upload' });
    expect(p.id).toBe('pl_1');
    const r = seen[0]!;
    expect(r.url).toBe('https://api.example.test/v1/pipelines');
    expect(r.headers.get('X-API-Key')).toBe('dik_test_abc');
    expect(r.headers.get('Authorization')).toBeNull();
    expect(r.headers.get('Idempotency-Key')).toBeTruthy();
  });
  it('uses Bearer tokens and If-Match on draft saves; maps problem+json to ApiError', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const req = input as Request;
      expect(req.headers.get('Authorization')).toBe('Bearer jwt-123');
      expect(req.headers.get('If-Match')).toBe('W/"pl_1-3"');
      return new Response(JSON.stringify({ type: 'https://x/errors/etag', title: 'The draft was changed by someone else', status: 412, code: 'etag_mismatch' }), {
        status: 412,
        headers: { 'Content-Type': 'application/problem+json' },
      });
    });
    setApiConfig({ mode: 'live', baseUrl: 'https://api.example.test/v1', scheme: 'bearer', credential: 'jwt-123' });
    const e = await api.saveDraft('pl_1', 'W/"pl_1-3"', { graph: { nodes: [], edges: [] } }).catch((x: unknown) => x);
    expect(e).toBeInstanceOf(ApiError);
    expect((e as ApiError).status).toBe(412);
    expect((e as ApiError).code).toBe('etag_mismatch');
    expect(errorMessage(e)).toBe('The draft was changed by someone else');
  });
  it('mode switch back to mock never calls the network', async () => {
    const f = vi.spyOn(globalThis, 'fetch');
    setApiConfig({ mode: 'mock' });
    expect(getApiConfig().mode).toBe('mock');
    const list = await api.listPipelines();
    expect(list.data.length).toBeGreaterThan(0);
    expect(f).not.toHaveBeenCalled();
  });
});
