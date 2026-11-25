/**
 * Typed API client (openapi-fetch over the generated `paths`). One client for both modes:
 * in mock mode the transport is the in-app MockBackend's fetch; in live mode it is window.fetch.
 */
import createClient, { type Middleware } from 'openapi-fetch';
import type { paths } from './schema';
import { authHeaders, effectiveBase, getApiConfig, onApiConfig } from './config';
import { ApiError, toProblem } from './errors';
import { mockBackend } from './mock/backend';
import type * as T from './types';

const transport = (req: Request): Promise<Response> => (getApiConfig().mode === 'mock' ? mockBackend().fetch(req) : globalThis.fetch(req));

const newKey = (): string => {
  try {
    return crypto.randomUUID();
  } catch {
    return 'idem_' + Date.now().toString(36) + Math.random().toString(36).slice(2);
  }
};

const middleware: Middleware = {
  onRequest({ request }) {
    for (const [k, v] of Object.entries(authHeaders())) request.headers.set(k, v);
    if (request.method === 'POST' && !request.headers.has('Idempotency-Key')) request.headers.set('Idempotency-Key', newKey());
    return request;
  },
};

function make() {
  const c = createClient<paths>({ baseUrl: effectiveBase(), fetch: transport });
  c.use(middleware);
  return c;
}
let client = make();
onApiConfig(() => {
  client = make();
});
export const getClient = () => client;

interface Res<D> {
  data?: D;
  error?: unknown;
  response: Response;
}
async function call<D>(p: Promise<Res<D>>): Promise<D> {
  const { data, error, response } = await p;
  if (!response.ok) throw new ApiError(await toProblem(response, error));
  return data as D;
}
async function callWithEtag<D>(p: Promise<Res<D>>): Promise<{ data: D; etag: string }> {
  const { data, error, response } = await p;
  if (!response.ok) throw new ApiError(await toProblem(response, error));
  return { data: data as D, etag: response.headers.get('ETag') ?? (data as { etag?: string })?.etag ?? '' };
}
const id = (v: string) => ({ params: { path: { id: v } } });

/** All backend operations used by the portal. */
export const api = {
  me: () => call(client.GET('/me')),
  // API keys
  listApiKeys: () => call(client.GET('/api-keys')),
  createApiKey: (body: T.ApiKeyCreate) => call(client.POST('/api-keys', { body })),
  rotateApiKey: (key: string, grace = 24) => call(client.POST('/api-keys/{id}/rotate', { ...id(key), body: { grace_period_hours: grace } })),
  revokeApiKey: (key: string) => call(client.DELETE('/api-keys/{id}', id(key))),
  // catalog
  stepTypes: () => call(client.GET('/catalog/step-types')),
  services: () => call(client.GET('/catalog/services')),
  // pipelines
  listPipelines: (query: { status?: T.PipelineStatus; q?: string; limit?: number } = {}) => call(client.GET('/pipelines', { params: { query } })),
  getPipeline: (p: string) => call(client.GET('/pipelines/{id}', id(p))),
  createPipeline: (body: T.PipelineCreate) => call(client.POST('/pipelines', { body, params: { header: { 'Idempotency-Key': newKey() } } })),
  updatePipeline: (p: string, body: T.PipelineUpdate) => call(client.PATCH('/pipelines/{id}', { ...id(p), body })),
  deletePipeline: (p: string) => call(client.DELETE('/pipelines/{id}', id(p))),
  duplicatePipeline: (p: string, name?: string) => call(client.POST('/pipelines/{id}/duplicate', { ...id(p), body: name ? { name } : {} })),
  activatePipeline: (p: string) => call(client.POST('/pipelines/{id}/activate', id(p))),
  pausePipeline: (p: string) => call(client.POST('/pipelines/{id}/pause', id(p))),
  getDraft: (p: string) => callWithEtag(client.GET('/pipelines/{id}/draft', id(p))),
  saveDraft: (p: string, etag: string, body: T.DraftSave) =>
    callWithEtag(client.PUT('/pipelines/{id}/draft', { params: { path: { id: p }, header: { 'If-Match': etag } }, body })),
  validatePipeline: (p: string, graph?: T.Graph) => call(client.POST('/pipelines/{id}/validate', { ...id(p), ...(graph ? { body: graph } : {}) })),
  listVersions: (p: string) => call(client.GET('/pipelines/{id}/versions', id(p))),
  publishVersion: (p: string, release_note: string, draft_etag?: string) =>
    call(client.POST('/pipelines/{id}/versions', { ...id(p), body: { release_note, ...(draft_etag ? { draft_etag } : {}) } })),
  getVersion: (p: string, version: number) => call(client.GET('/pipelines/{id}/versions/{version}', { params: { path: { id: p, version } } })),
  restoreVersion: (p: string, version: number) =>
    callWithEtag(client.POST('/pipelines/{id}/versions/{version}/restore', { params: { path: { id: p, version } } })),
  listTemplates: () => call(client.GET('/templates')),
  instantiateTemplate: (t: string, name?: string) => call(client.POST('/templates/{id}/instantiate', { ...id(t), body: name ? { name } : {} })),
  // runs
  createTestRun: (p: string, body: { document_id: string; version?: number | 'draft'; deliver?: boolean }) =>
    call(client.POST('/pipelines/{id}/test-runs', { ...id(p), body })),
  testStep: (p: string, node: string, document_id: string) =>
    call(client.POST('/pipelines/{id}/steps/{node_id}/test', { params: { path: { id: p, node_id: node } }, body: { document_id } })),
  listRuns: (query: { pipeline_id?: string; status?: T.RunStatus; test?: boolean; limit?: number } = {}) => call(client.GET('/runs', { params: { query } })),
  getRun: (r: string) => call(client.GET('/runs/{id}', id(r))),
  getRunResult: (r: string) => call(client.GET('/runs/{id}/result', id(r))),
  cancelRun: (r: string) => call(client.POST('/runs/{id}/cancel', id(r))),
  // review
  listReviewItems: () => call(client.GET('/review-items')),
  getReviewItem: (r: string) => call(client.GET('/review-items/{id}', id(r))),
  claimReviewItem: (r: string) => call(client.POST('/review-items/{id}/claim', id(r))),
  decideReviewItem: (r: string, body: T.ReviewDecision) => call(client.POST('/review-items/{id}/decision', { ...id(r), body })),
  // connections
  listConnections: () => call(client.GET('/connections', { params: { query: { limit: 200 } } })),
  createConnection: (body: T.ConnectionCreate) => call(client.POST('/connections', { body })),
  updateConnection: (c: string, body: T.ConnectionUpdate) => call(client.PATCH('/connections/{id}', { ...id(c), body })),
  deleteConnection: (c: string, force = false) => call(client.DELETE('/connections/{id}', { params: { path: { id: c }, query: { force } } })),
  testConnection: (c: string) => call(client.POST('/connections/{id}/test', id(c))),
  connectionUsage: (c: string) => call(client.GET('/connections/{id}/usage', id(c))),
  startOAuth: (c: string) => call(client.POST('/connections/{id}/oauth/authorize', { ...id(c), body: {} })),
  // providers
  listProviders: () => call(client.GET('/providers')),
  createProvider: (body: T.ProviderCreate) => call(client.POST('/providers', { body })),
  updateProvider: (p: string, body: T.ProviderUpdate) => call(client.PATCH('/providers/{id}', { ...id(p), body })),
  deleteProvider: (p: string) => call(client.DELETE('/providers/{id}', id(p))),
  testProvider: (p: string, model?: string) => call(client.POST('/providers/{id}/test', { ...id(p), body: model ? { model } : {} })),
  syncProviderModels: (p: string) => call(client.POST('/providers/{id}/models/sync', id(p))),
  // workers
  listWorkers: () => call(client.GET('/workers')),
  registerWorker: (body: T.WorkerCreate) => call(client.POST('/workers', { body })),
  updateWorker: (w: string, body: T.WorkerUpdate) => call(client.PATCH('/workers/{id}', { ...id(w), body })),
  deleteWorker: (w: string) => call(client.DELETE('/workers/{id}', id(w))),
  checkWorkerHealth: (w: string) => call(client.POST('/workers/{id}/health', id(w))),
  invokeWorker: (w: string, document_id: string) => call(client.POST('/workers/{id}/invoke', { ...id(w), body: { document_id } })),
  // MCP
  listMcpServers: () => call(client.GET('/mcp-servers')),
  createMcpServer: (body: T.McpServerCreate) => call(client.POST('/mcp-servers', { body })),
  updateMcpServer: (m: string, body: T.McpServerUpdate) => call(client.PATCH('/mcp-servers/{id}', { ...id(m), body })),
  deleteMcpServer: (m: string) => call(client.DELETE('/mcp-servers/{id}', id(m))),
  discoverMcpTools: (m: string) => call(client.POST('/mcp-servers/{id}/discover', id(m))),
  callMcpTool: (m: string, tool: string, args: Record<string, unknown>) =>
    call(client.POST('/mcp-servers/{id}/tools/{tool}/call', { params: { path: { id: m, tool } }, body: { arguments: args } })),
  // knowledge, skills, policies
  listCollections: () => call(client.GET('/knowledge-collections')),
  listSkills: () => call(client.GET('/skills')),
  createSkill: (body: T.SkillCreate) => call(client.POST('/skills', { body })),
  deleteSkill: (s: string) => call(client.DELETE('/skills/{id}', id(s))),
  publishSkillVersion: (s: string, instructions: string, release_note: string) =>
    call(client.POST('/skills/{id}/versions', { ...id(s), body: { instructions, release_note } })),
  listPolicies: () => call(client.GET('/policies')),
  updatePolicy: (p: string, body: T.PolicyUpdate) => call(client.PATCH('/policies/{id}', { ...id(p), body })),
  // metrics, audit, webhooks, settings
  metrics: () => call(client.GET('/metrics/overview', { params: { query: { window: '24h' } } })),
  listAuditEvents: () => call(client.GET('/audit-events', { params: { query: { limit: 20 } } })),
  listWebhooks: () => call(client.GET('/webhooks')),
  createWebhook: (body: { url: string; events: string[] }) => call(client.POST('/webhooks', { body })),
  deleteWebhook: (w: string) => call(client.DELETE('/webhooks/{id}', id(w))),
  getSettings: () => call(client.GET('/settings')),
  updateSettings: (body: T.WorkspaceSettings) => call(client.PATCH('/settings', { body })),
};
export type Api = typeof api;
