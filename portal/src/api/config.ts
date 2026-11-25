/**
 * Runtime API configuration. Mode and base URL come from env (VITE_API_MODE / VITE_API_BASE_URL)
 * and can be overridden in Settings → API access. Credentials are kept in memory and, when allowed,
 * in sessionStorage (wrapped — sandboxed hosts may throw). Credentials are never logged.
 */
import { safeSession } from '../lib/storage';

export type ApiMode = 'mock' | 'live';
export type AuthScheme = 'bearer' | 'api_key';

export interface ApiConfig {
  mode: ApiMode;
  baseUrl: string;
  scheme: AuthScheme;
  /** credential; only ever held in memory/sessionStorage */
  credential: string;
}

const KEY = 'dis.api.config';
const envMode = (import.meta.env.VITE_API_MODE as string | undefined) === 'live' ? 'live' : 'mock';
const envBase = (import.meta.env.VITE_API_BASE_URL as string | undefined) || 'http://localhost:8080/v1';

function load(): ApiConfig {
  const base: ApiConfig = { mode: envMode, baseUrl: envBase, scheme: 'bearer', credential: '' };
  try {
    const raw = safeSession.get(KEY);
    if (raw) {
      const s = JSON.parse(raw) as Partial<ApiConfig>;
      return { ...base, ...s, mode: s.mode === 'live' ? 'live' : 'mock' };
    }
  } catch {
    /* ignore */
  }
  return base;
}

let current = load();
const listeners = new Set<(c: ApiConfig) => void>();

export const getApiConfig = (): ApiConfig => current;
export function setApiConfig(patch: Partial<ApiConfig>): void {
  current = { ...current, ...patch };
  safeSession.set(KEY, JSON.stringify(current));
  listeners.forEach((l) => l(current));
}
export function onApiConfig(l: (c: ApiConfig) => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}
export const MOCK_BASE = 'https://mock.docintel.local/v1';
export const effectiveBase = (c: ApiConfig = current): string => (c.mode === 'mock' ? MOCK_BASE : c.baseUrl.replace(/\/+$/, ''));

/** Auth headers for the current credential. */
export function authHeaders(c: ApiConfig = current): Record<string, string> {
  if (!c.credential) return {};
  return c.scheme === 'api_key' ? { 'X-API-Key': c.credential } : { Authorization: `Bearer ${c.credential}` };
}
