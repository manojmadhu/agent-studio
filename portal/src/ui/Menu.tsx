import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Icon } from './Icon';
import { useMenu, type MenuItem } from './menuStore';
import { clamp } from '../lib/util';

/** Floating menu used for context menus, row menus and "connect a new step" (prototype `.menu`). */
export function Menu() {
  const spec = useMenu((s) => s.menu);
  const close = useMenu((s) => s.close);
  if (!spec) return null;
  return <MenuBody key={`${spec.x},${spec.y},${spec.title}`} spec={spec} close={close} />;
}

function MenuBody({ spec, close }: { spec: NonNullable<ReturnType<typeof useMenu.getState>['menu']>; close: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const [q, setQ] = useState('');
  const [pos, setPos] = useState({ left: spec.x, top: spec.y, ready: false });
  const [act, setAct] = useState(0);

  useLayoutEffect(() => {
    const m = ref.current;
    if (!m) return;
    const w = m.offsetWidth;
    const h = m.offsetHeight;
    setPos({ left: clamp(spec.x, 8, window.innerWidth - w - 8), top: clamp(spec.y, 8, window.innerHeight - h - 8), ready: true });
  }, [spec.x, spec.y]);

  useEffect(() => {
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) close();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
    };
    document.addEventListener('pointerdown', onDown, true);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown, true);
      window.removeEventListener('keydown', onKey);
    };
  }, [close]);

  useEffect(() => {
    if (!spec.search) ref.current?.querySelector<HTMLButtonElement>('.mi')?.focus();
  }, [spec.search]);

  const visible: { item: MenuItem; heading?: string }[] = [];
  let grp: string | null = null;
  for (const it of spec.items) {
    if (it.h) {
      grp = it.h;
      continue;
    }
    if (!((it.l ?? '') + ' ' + (it.d ?? '')).toLowerCase().includes(q.toLowerCase())) continue;
    visible.push({ item: it, heading: grp ?? undefined });
    grp = null;
  }
  const run = (it: MenuItem) => {
    close();
    it.fn?.();
  };
  const onKeyNav = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setAct((a) => Math.min(a + 1, visible.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setAct((a) => Math.max(a - 1, 0));
    } else if (e.key === 'Enter' && spec.search && visible[act]) {
      e.preventDefault();
      run(visible[act].item);
    }
  };

  return (
    <div className="menu" role="menu" ref={ref} style={{ left: pos.left, top: pos.top, visibility: pos.ready ? 'visible' : 'hidden' }} onKeyDown={onKeyNav}>
      {spec.search ? (
        <div className="mh">
          <input
            className="input sm"
            autoFocus
            placeholder={spec.title || 'Search'}
            aria-label={spec.title || 'Search'}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setAct(0);
            }}
          />
        </div>
      ) : spec.title ? (
        <h5 style={{ margin: '10px 14px 0' }}>{spec.title}</h5>
      ) : null}
      <div className="ml">
        {visible.length ? (
          visible.map(({ item, heading }, i) => (
            <div key={i}>
              {heading && <h5>{heading}</h5>}
              <button className={`mi ${item.danger ? 'danger' : ''} ${spec.search && i === act ? 'act' : ''}`} role="menuitem" onClick={() => run(item)}>
                {item.i && (
                  <span className="tile" style={{ ['--c' as string]: item.c || 'var(--text-2)', width: 28, height: 28 }}>
                    <Icon name={item.i} size={15} />
                  </span>
                )}
                <span>
                  <b className={item.mono ? 'mono' : ''}>{item.l}</b>
                  {item.d && <small>{item.d}</small>}
                </span>
                {item.k && <span className="kbd">{item.k}</span>}
              </button>
            </div>
          ))
        ) : (
          <p className="muted" style={{ padding: 12 }}>
            No matches
          </p>
        )}
      </div>
    </div>
  );
}
