/** Server state (TanStack Query) — thin hooks over `api`. */
import { useMemo } from 'react';
import { QueryClient, useMutation, useQuery, useQueryClient, type QueryKey } from '@tanstack/react-query';
import { api } from './client';
import { errorMessage } from './errors';
import { toast } from '../ui/toast';
import type { SummaryCtx } from '../features/builder/catalog';
import type { ValidationCtx } from '../features/builder/validate';

export const qk = {
  me: ['me'],
  pipelines: ['pipelines'],
  pipeline: (id: string) => ['pipelines', id],
  draft: (id: string) => ['draft', id],
  versions: (id: string) => ['versions', id],
  templates: ['templates'],
  runs: ['runs'],
  review: ['review'],
  reviewItem: (id: string) => ['review', id],
  connections: ['connections'],
  usage: (id: string) => ['connections', id, 'usage'],
  services: ['services'],
  providers: ['providers'],
  workers: ['workers'],
  mcp: ['mcp'],
  skills: ['skills'],
  policies: ['policies'],
  collections: ['collections'],
  metrics: ['metrics'],
  apiKeys: ['apiKeys'],
  webhooks: ['webhooks'],
  settings: ['settings'],
  audit: ['audit'],
} as const;

export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: 15_000, retry: (n, e) => n < 1 && !(e && typeof e === 'object' && 'status' in e && (e as { status: number }).status < 500), refetchOnWindowFocus: false },
      mutations: { onError: (e) => toast(errorMessage(e), true) },
    },
  });
}

const data = <T,>(r: { data: T }) => r.data;

export const useMe = () => useQuery({ queryKey: qk.me, queryFn: api.me });
export const usePipelines = () => useQuery({ queryKey: qk.pipelines, queryFn: () => api.listPipelines({ limit: 200 }).then(data) });
export const usePipeline = (id: string) => useQuery({ queryKey: qk.pipeline(id), queryFn: () => api.getPipeline(id), enabled: !!id });
export const useVersions = (id: string, enabled = true) => useQuery({ queryKey: qk.versions(id), queryFn: () => api.listVersions(id).then(data), enabled });
export const useTemplates = (enabled = true) => useQuery({ queryKey: qk.templates, queryFn: () => api.listTemplates().then(data), enabled });
export const useRuns = () => useQuery({ queryKey: qk.runs, queryFn: () => api.listRuns({ limit: 200 }).then(data) });
export const useReviewItems = () => useQuery({ queryKey: qk.review, queryFn: () => api.listReviewItems().then(data) });
export const useReviewItem = (id: string) => useQuery({ queryKey: qk.reviewItem(id), queryFn: () => api.getReviewItem(id), enabled: !!id });
export const useConnections = () => useQuery({ queryKey: qk.connections, queryFn: () => api.listConnections().then(data) });
export const useConnectionUsage = (id: string | null) => useQuery({ queryKey: qk.usage(id ?? ''), queryFn: () => api.connectionUsage(id!).then(data), enabled: !!id });
export const useServices = () => useQuery({ queryKey: qk.services, queryFn: () => api.services().then(data), staleTime: Infinity });
export const useProviders = () => useQuery({ queryKey: qk.providers, queryFn: () => api.listProviders().then(data) });
export const useWorkers = () => useQuery({ queryKey: qk.workers, queryFn: () => api.listWorkers().then(data) });
export const useMcpServers = () => useQuery({ queryKey: qk.mcp, queryFn: () => api.listMcpServers().then(data) });
export const useSkills = () => useQuery({ queryKey: qk.skills, queryFn: () => api.listSkills().then(data) });
export const usePolicies = () => useQuery({ queryKey: qk.policies, queryFn: () => api.listPolicies().then(data) });
export const useCollections = () => useQuery({ queryKey: qk.collections, queryFn: () => api.listCollections().then(data) });
export const useMetrics = () => useQuery({ queryKey: qk.metrics, queryFn: api.metrics });
export const useApiKeys = () => useQuery({ queryKey: qk.apiKeys, queryFn: () => api.listApiKeys().then(data) });
export const useWebhooks = () => useQuery({ queryKey: qk.webhooks, queryFn: () => api.listWebhooks().then(data) });
export const useSettings = () => useQuery({ queryKey: qk.settings, queryFn: api.getSettings });
export const useAudit = () => useQuery({ queryKey: qk.audit, queryFn: () => api.listAuditEvents().then(data) });

/** Mutation that invalidates the given keys on success and toasts errors (problem+json). */
export function useApiMutation<TArgs, TRes>(fn: (a: TArgs) => Promise<TRes>, invalidate: QueryKey[] = [], onSuccess?: (r: TRes, a: TArgs) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: async (r, a) => {
      await Promise.all(invalidate.map((k) => qc.invalidateQueries({ queryKey: k })));
      onSuccess?.(r, a);
    },
  });
}

/** Resource lookups used by the builder (summaries, validation, field options). */
const E: never[] = [];
export function useWorkspaceRefs() {
  const c = useConnections();
  const p = useProviders();
  const connections = c.data ?? E;
  const providers = p.data ?? E;
  const workers = useWorkers().data ?? E;
  const mcp = useMcpServers().data ?? E;
  const skills = useSkills().data ?? E;
  const policies = usePolicies().data ?? E;
  const collections = useCollections().data ?? E;
  const ready = c.isSuccess && p.isSuccess;
  return useMemo(() => ({ connections, providers, workers, mcp, skills, policies, collections, ready }), [connections, providers, workers, mcp, skills, policies, collections, ready]);
}
export type WorkspaceRefs = ReturnType<typeof useWorkspaceRefs>;

export function summaryCtx(r: WorkspaceRefs): SummaryCtx {
  const name = (arr: { id: string; name: string }[], fallback: string) => (id: unknown) => arr.find((x) => x.id === id)?.name ?? (id ? String(id) : fallback);
  return {
    connName: name(r.connections, 'Not connected'),
    workerName: name(r.workers, 'No worker'),
    mcpName: name(r.mcp, 'No server'),
    skillName: name(r.skills, 'None'),
  };
}
export const validationCtx = (r: WorkspaceRefs): ValidationCtx => ({
  connections: r.connections,
  providers: r.providers.map((p) => ({ id: p.id, name: p.name, enabled: p.enabled })),
});
