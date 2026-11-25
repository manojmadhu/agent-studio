/** Step palette (left pane): search, grouped by stage; drag onto the canvas or click to add. */
import { useState } from 'react';
import { Icon } from '../../ui/Icon';
import { CATALOG, STAGES, STAGE_ORDER } from './catalog';
import { useEditor } from './store';
import { canvasApi } from './Canvas';
import { usePanels } from './panels';

export function Palette() {
  const [q, setQ] = useState('');
  const open = usePanels((s) => s.palette);
  const lq = q.toLowerCase();
  const groups = STAGE_ORDER.map((k) => ({ k, items: Object.values(CATALOG).filter((d) => d.stage === k && (d.name + ' ' + d.description).toLowerCase().includes(lq)) })).filter((g) => g.items.length);
  const add = (t: string) => {
    useEditor.getState().addNode(t, null, true, canvasApi.current?.center());
    usePanels.getState().setPalette(false);
  };
  return (
    <aside className={`palette ${open ? 'open' : ''}`} id="pal" aria-label="Steps" aria-hidden={!open} inert={!open}>
      <div className="phd">
        <div style={{ position: 'relative', width: '100%' }}>
          <span style={{ position: 'absolute', left: 10, top: 8, color: 'var(--text-3)' }}>
            <Icon name="search" />
          </span>
          <input className="input sm" placeholder="Search steps" value={q} onChange={(e) => setQ(e.target.value)} style={{ paddingLeft: 34 }} aria-label="Search steps" />
        </div>
        <button className="btn ghost icon sm" aria-label="Close steps" onClick={() => usePanels.getState().setPalette(false)}>
          <Icon name="close" />
        </button>
      </div>
      <div className="pbody">
        {groups.length ? (
          groups.map(({ k, items }) => (
            <div className="pgrp" key={k}>
              <h5 style={{ ['--c' as string]: `var(${STAGES[k].color})` }}>
                <i />
                {STAGES[k].label}
              </h5>
              {items.map((d) => (
                <button
                  key={d.type}
                  className="pit"
                  draggable
                  data-add={d.type}
                  title={d.description}
                  onDragStart={(e) => {
                    e.dataTransfer.setData('text/plain', d.type);
                    e.dataTransfer.effectAllowed = 'copy';
                  }}
                  onClick={() => add(d.type)}
                >
                  <span className={`tile ${d.ai ? 'ai' : ''}`} style={{ ['--c' as string]: `var(${STAGES[k].color})` }}>
                    <Icon name={d.icon} size={15} />
                  </span>
                  <span>
                    <b>{d.name}</b>
                    <small>{d.description}</small>
                  </span>
                </button>
              ))}
            </div>
          ))
        ) : (
          <p className="muted" style={{ padding: 16 }}>
            No steps match.
          </p>
        )}
      </div>
    </aside>
  );
}
