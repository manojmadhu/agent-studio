/**
 * Server-sent events for `/runs/{id}/events`, read with fetch streaming so the same auth headers
 * (Authorization / X-API-Key) can be sent as for every other call. Works against the live backend and
 * against the mock backend (which returns a `text/event-stream` ReadableStream). Supports Last-Event-ID resume.
 */
import { authHeaders, effectiveBase, getApiConfig } from './config';
import { ApiError, toProblem } from './errors';
import { mockBackend } from './mock/backend';
import type { RunEvent } from './types';

export interface SseFrame {
  id?: string;
  event?: string;
  data: string;
}

/** Incremental SSE parser (exported for tests). */
export function createSseParser(onFrame: (f: SseFrame) => void) {
  let buf = '';
  let cur: Partial<SseFrame> & { data?: string } = {};
  return (chunk: string) => {
    buf += chunk.replace(/\r\n?/g, '\n');
    let i: number;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i);
      buf = buf.slice(i + 1);
      if (line === '') {
        if (cur.data != null) onFrame({ id: cur.id, event: cur.event, data: cur.data });
        cur = {};
        continue;
      }
      if (line.startsWith(':')) continue;
      const c = line.indexOf(':');
      const field = c < 0 ? line : line.slice(0, c);
      const value = c < 0 ? '' : line.slice(c + 1).replace(/^ /, '');
      if (field === 'data') cur.data = cur.data == null ? value : cur.data + '\n' + value;
      else if (field === 'event') cur.event = value;
      else if (field === 'id') cur.id = value;
    }
  };
}

export async function streamRunEvents(runId: string, onEvent: (e: RunEvent) => void, signal?: AbortSignal, lastEventId?: string): Promise<void> {
  const cfg = getApiConfig();
  const headers: Record<string, string> = { Accept: 'text/event-stream', ...authHeaders(cfg) };
  if (lastEventId) headers['Last-Event-ID'] = lastEventId;
  const req = new Request(`${effectiveBase(cfg)}/runs/${encodeURIComponent(runId)}/events`, { headers, signal });
  const res = cfg.mode === 'mock' ? await mockBackend().fetch(req) : await fetch(req);
  if (!res.ok || !res.body) throw new ApiError(await toProblem(res));
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let finished = false;
  const parse = createSseParser((f) => {
    try {
      const ev = JSON.parse(f.data) as RunEvent;
      onEvent(ev);
      if (ev.event === 'run.finished') finished = true;
    } catch {
      /* ignore malformed frame */
    }
  });
  try {
    while (!finished) {
      const { done, value } = await reader.read();
      if (done) break;
      parse(dec.decode(value, { stream: true }));
    }
  } finally {
    try {
      await reader.cancel();
    } catch {
      /* ignore */
    }
  }
}
