/** Small shared helpers ported from the prototype. */
export const uid = (p: string): string => p + Math.random().toString(36).slice(2, 8);
export const clone = <T>(o: T): T => (o === undefined ? o : (JSON.parse(JSON.stringify(o)) as T));
export const clamp = (v: number, a: number, b: number): number => Math.max(a, Math.min(b, v));
export const list = (s: unknown): string[] =>
  String(s ?? '')
    .split(',')
    .map((x) => x.trim())
    .filter(Boolean);
export const fmt = (n: number): string => n.toLocaleString('en-US');
export const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

export function ago(t: number | string | null | undefined, now = Date.now()): string {
  if (t == null) return '—';
  const ms = typeof t === 'string' ? Date.parse(t) : t;
  if (Number.isNaN(ms)) return '—';
  const m = Math.round((now - ms) / 6e4);
  return m < 1 ? 'Just now' : m < 60 ? m + ' min ago' : m < 1440 ? Math.round(m / 60) + ' h ago' : Math.round(m / 1440) + ' d ago';
}

export const initials = (name: string): string =>
  name
    .split(' ')
    .map((x) => x[0] ?? '')
    .join('')
    .slice(0, 2)
    .toUpperCase();

export const snap8 = (v: number): number => Math.round(v / 8) * 8;

export function downloadText(filename: string, text: string, mime = 'text/csv'): void {
  try {
    const url = URL.createObjectURL(new Blob([text], { type: mime }));
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch {
    /* download blocked in sandboxed hosts */
  }
}
