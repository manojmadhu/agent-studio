/** ⌘K command palette: pages, pipelines and actions. */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router';
import { create } from 'zustand';
import { Icon } from '../ui/Icon';
import { usePipelines } from '../api/hooks';
import { NAV } from './nav';

interface CmdkState {
  open: boolean;
  set: (v: boolean) => void;
}
export const useCmdk = create<CmdkState>((set) => ({ open: false, set: (open) => set({ open }) }));

interface Item {
  l: string;
  d?: string;
  i: string;
  fn: () => void;
}

export function CommandPalette() {
  const open = useCmdk((s) => s.open);
  if (!open) return null;
  return <Palette />;
}

function Palette() {
  const close = () => useCmdk.getState().set(false);
  const nav = useNavigate();
  const pipesQ = usePipelines().data;
  const [q, setQ] = useState('');
  const [act, setAct] = useState(0);
  const ref = useRef<HTMLInputElement>(null);
  const items: Item[] = useMemo(
    () => [
      ...NAV.flatMap((g) => g.items.map((it) => ({ l: 'Go to ' + it.label, i: it.icon, fn: () => nav(it.to) }))),
      ...(pipesQ ?? []).map((p) => ({ l: p.name, d: 'Open in builder', i: 'flow', fn: () => nav(`/builder/${p.id}`) })),
      { l: 'New pipeline', i: 'add', fn: () => nav('/pipelines?new=1') },
      { l: 'New connection', i: 'password', fn: () => nav('/connections?new=1') },
      { l: 'Register worker', i: 'chip', fn: () => nav('/workers?new=1') },
      { l: 'Add MCP server', i: 'plug', fn: () => nav('/tools?new=1') },
      { l: 'API access settings', i: 'api--key', fn: () => nav('/settings') },
    ],
    [pipesQ, nav],
  );
  const cur = items.filter((x) => (x.l + (x.d ?? '')).toLowerCase().includes(q.toLowerCase()));
  const a = Math.min(act, Math.max(0, cur.length - 1));
  useEffect(() => ref.current?.focus(), []);
  const run = (it: Item | undefined) => {
    if (!it) return;
    close();
    it.fn();
  };
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && close()}>
      <div className="dialog cmdk" role="dialog" aria-modal="true" aria-label="Command palette">
        <input
          ref={ref}
          className="input"
          placeholder="Search pipelines, pages and actions"
          aria-label="Search"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setAct(0);
          }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              setAct(Math.min(a + 1, cur.length - 1));
              e.preventDefault();
            }
            if (e.key === 'ArrowUp') {
              setAct(Math.max(a - 1, 0));
              e.preventDefault();
            }
            if (e.key === 'Enter') run(cur[a]);
            if (e.key === 'Escape') close();
          }}
        />
        <div className="ml" role="listbox" style={{ maxHeight: '50vh', overflow: 'auto', padding: 6 }}>
          {cur.length ? (
            cur.map((x, i) => (
              <button key={x.l + i} className={`mi ${i === a ? 'act' : ''}`} role="option" aria-selected={i === a} onClick={() => run(x)}>
                <span className="tile" style={{ width: 28, height: 28 }}>
                  <Icon name={x.i} size={15} />
                </span>
                <span>
                  <b>{x.l}</b>
                  {x.d && <small>{x.d}</small>}
                </span>
              </button>
            ))
          ) : (
            <p className="muted" style={{ padding: 12 }}>
              No results
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
