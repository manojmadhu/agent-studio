/**
 * In-app routing for the sandboxed preview host: react-router runs on a memory history and the
 * location is mirrored to a *plain* hash (`#pipelines`, `#builder-pl_ap`, `#review-rv1`) — never `#/path`.
 */
export function pathToHash(path: string): string {
  const segs = path.split('/').filter(Boolean);
  return segs.length ? segs.map(encodeURIComponent).join('-') : 'pipelines';
}

export function hashToPath(hash: string): string {
  const h = decodeURIComponent(hash.replace(/^#/, '')).replace(/^\/+/, '');
  if (!h) return '/pipelines';
  const i = h.indexOf('-');
  return i < 0 ? `/${h}` : `/${h.slice(0, i)}/${h.slice(i + 1)}`;
}

export function readHash(): string {
  try {
    return window.location.hash;
  } catch {
    return '';
  }
}

export function writeHash(path: string): void {
  try {
    const h = '#' + pathToHash(path);
    if (window.location.hash !== h) window.history.pushState(null, '', h);
  } catch {
    /* hash writes can be blocked in sandboxed frames */
  }
}
