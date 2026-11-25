/** Minimap (prototype renderMini): nodes + current viewport rectangle; click to move there. */
import { useViewport } from '@xyflow/react';
import { CATALOG } from './catalog';
import { isSub, nH, nW } from './graph';
import { bounds } from './layout';
import { useEditor } from './store';

export function MiniMap({ width, height, onMoveTo }: { width: number; height: number; onMoveTo: (x: number, y: number) => void }) {
  const nodes = useEditor((s) => s.nodes);
  const sel = useEditor((s) => s.sel.node);
  const v = useViewport();
  const b = bounds({ nodes, edges: [] });
  if (!b || !width) return <div className="mini" aria-hidden="true" />;
  const vb = [-v.x / v.zoom, -v.y / v.zoom, width / v.zoom, height / v.zoom] as const;
  const x0 = Math.min(b[0], vb[0]);
  const y0 = Math.min(b[1], vb[1]);
  const x1 = Math.max(b[2], vb[0] + vb[2]);
  const y1 = Math.max(b[3], vb[1] + vb[3]);
  const W = x1 - x0;
  const H = y1 - y0;
  return (
    <div
      className="mini"
      title="Minimap — click to move"
      role="button"
      aria-label="Minimap — click to move"
      onClick={(e) => {
        const r = e.currentTarget.getBoundingClientRect();
        const s = Math.min(r.width / W, r.height / H);
        const ox = (r.width - W * s) / 2;
        const oy = (r.height - H * s) / 2;
        onMoveTo(x0 + (e.clientX - r.left - ox) / s, y0 + (e.clientY - r.top - oy) / s);
      }}
    >
      <svg viewBox={`${x0} ${y0} ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
        {nodes.map((n) => (
          <rect key={n.id} x={n.x} y={n.y} width={nW(n)} height={nH(n)} rx={10} fill={CATALOG[n.type]?.ai ? '#0e7490' : isSub(n) ? '#8cc9d4' : '#9aa5b5'} opacity={sel === n.id ? 1 : 0.7} />
        ))}
        <rect x={vb[0]} y={vb[1]} width={vb[2]} height={vb[3]} fill="rgba(22,82,200,.08)" stroke="#1652c8" strokeWidth={W / 120} />
      </svg>
    </div>
  );
}
