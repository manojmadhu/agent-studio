/** Configure panel (right pane, 384px): pipeline settings, connection, or step Configure / Settings / Output. */
import { useState } from 'react';
import type { GraphNode, Pipeline, SlotName, ValidationIssue } from '../../api/types';
import type { WorkspaceRefs } from '../../api/hooks';
import { Icon } from '../../ui/Icon';
import { openMenuFor } from '../../ui/menuStore';
import { SLOTS, STAGES, def, type SummaryCtx } from './catalog';
import { hostOf, isSub, mainNodes, needOf, nodeById, outsOf, slotsOf, subsOf } from './graph';
import { FieldInput } from './FieldInput';
import { useEditor, type InspectorTab } from './store';
import { useRun } from './runStore';
import type { ValidationCtx } from './validate';
import { usePanels, useConfigureOpen } from './panels';

export interface InspectorProps {
  pipeline: Pipeline;
  issues: ValidationIssue[];
  refs: WorkspaceRefs;
  vctx: ValidationCtx;
  sumCtx: SummaryCtx;
  onPipelineField: (patch: { name?: string; description?: string; owner_team?: string }) => void;
  onGoto: (id: string) => void;
  onTestNode: (id: string) => void;
  slotMenu: (hostId: string, slot: SlotName, x: number, y: number) => void;
}

const TABS: [InspectorTab, string][] = [
  ['configure', 'Configure'],
  ['settings', 'Settings'],
  ['output', 'Output'],
];

function closePanel() {
  usePanels.getState().closeConfigure();
}

function PipelineSettings({ pipeline, issues, refs, onPipelineField, onGoto }: InspectorProps) {
  const nodes = useEditor((s) => s.nodes);
  const [name, setName] = useState(pipeline.name);
  const [desc, setDesc] = useState(pipeline.description ?? '');
  const main = mainNodes({ nodes, edges: [] }).length;
  return (
    <>
      <div className="ihead">
        <span className="tile lg ai">
          <Icon name="settings--adjust" size={18} />
        </span>
        <div className="grow">
          <h2>Pipeline settings</h2>
          <small>
            {main} steps · {nodes.length - main} AI components · v{pipeline.live_version ?? 0}
          </small>
        </div>
        <button className="btn ghost icon sm" aria-label="Close panel" onClick={closePanel}>
          <Icon name="close" />
        </button>
      </div>
      <div className="pbody">
        <div className="ibody">
          <label className="field">
            <span className="lab">Name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} onBlur={() => name.trim() && name !== pipeline.name && onPipelineField({ name: name.trim() })} />
          </label>
          <label className="field">
            <span className="lab">Description</span>
            <textarea className="textarea" rows={3} value={desc} onChange={(e) => setDesc(e.target.value)} onBlur={() => desc !== (pipeline.description ?? '') && onPipelineField({ description: desc })} />
          </label>
          <label className="field">
            <span className="lab">Owner team</span>
            <select className="select" value={pipeline.owner_team} onChange={(e) => onPipelineField({ owner_team: e.target.value })}>
              {[...new Set(['Finance Operations', 'People Operations', 'Compliance', pipeline.owner_team])].map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
          <h3 style={{ fontSize: 14, margin: '20px 0 8px' }}>Checks</h3>
          {issues.length ? (
            issues.map((i, k) => (
              <div key={k} className="row" style={{ alignItems: 'flex-start', padding: '8px 0', borderBottom: '1px solid var(--border)', fontSize: 13.5 }}>
                <span style={{ color: `var(--${i.level === 'error' ? 'danger' : 'warning'})` }}>
                  <Icon name={i.level === 'error' ? 'warning' : 'warning--alt'} />
                </span>
                <span className="grow">{i.message}</span>
                {i.node_id && (
                  <button className="btn ghost xs" onClick={() => onGoto(i.node_id!)}>
                    Show
                  </button>
                )}
              </div>
            ))
          ) : (
            <p className="row" style={{ color: 'var(--success)' }}>
              <Icon name="checkmark--filled" />
              No issues. Ready to publish.
            </p>
          )}
          <h3 style={{ fontSize: 14, margin: '20px 0 8px' }}>Workspace policies applied</h3>
          {refs.policies
            .filter((x) => x.enabled)
            .map((x) => (
              <p key={x.id} className="row sub" style={{ margin: '0 0 8px', fontSize: 13.5, alignItems: 'flex-start' }}>
                <Icon name="security" />
                {x.name}
              </p>
            ))}
        </div>
      </div>
    </>
  );
}

function SlotsSection({ n, sumCtx, slotMenu }: { n: GraphNode; sumCtx: SummaryCtx; slotMenu: InspectorProps['slotMenu'] }) {
  const nodes = useEditor((s) => s.nodes);
  const edges = useEditor((s) => s.edges);
  const select = useEditor((s) => s.select);
  const unplug = useEditor((s) => s.unplug);
  const g = { nodes, edges };
  const need = needOf(n);
  return (
    <>
      <div style={{ height: 6 }} />
      <h3 style={{ fontSize: 14, margin: '6px 0 4px' }} className="row">
        <Icon name="ai-generate" />
        <span className="ai-text">AI components</span>
      </h3>
      <p className="muted" style={{ margin: '0 0 12px', fontSize: 12.5 }}>
        Plug components into this step. You can also drag them onto its slots on the canvas.
      </p>
      {slotsOf(n).map((s) => {
        const subs = subsOf(g, n, s);
        return (
          <div className="slotbox" key={s}>
            <div className="sh">
              <div className="grow">
                <b>
                  {SLOTS[s].label}
                  {need.includes(s) && <span style={{ color: 'var(--danger)' }}> *</span>}
                </b>
                <small>{SLOTS[s].description}</small>
              </div>
              <button
                className="btn xs"
                data-slotadd={s}
                onClick={(e) => {
                  const r = e.currentTarget.getBoundingClientRect();
                  slotMenu(n.id, s, r.left - 200, r.bottom + 6);
                }}
              >
                <Icon name="add" size={13} />
                {subs.length && SLOTS[s].max === 1 ? 'Replace' : 'Add'}
              </button>
            </div>
            {subs.map((x) => {
              let sum = '';
              try {
                sum = def(x.type).sum(x.config, sumCtx);
              } catch {
                sum = '';
              }
              return (
                <div className="att" key={x.id}>
                  <span className="tile" style={{ width: 28, height: 28, ['--c' as string]: 'var(--st-ai)' }}>
                    <Icon name={def(x.type).icon} size={14} />
                  </span>
                  <span className="tx">
                    <b>{x.name}</b>
                    <small>{sum}</small>
                  </span>
                  <button className="btn ghost xs" onClick={() => select({ node: x.id }, 'configure')}>
                    Edit
                  </button>
                  <button className="btn ghost icon xs" aria-label={`Unplug ${x.name}`} title="Unplug" onClick={() => unplug(x.id)}>
                    <Icon name="unlink" size={14} />
                  </button>
                </div>
              );
            })}
          </div>
        );
      })}
    </>
  );
}

function NodeView(props: InspectorProps & { n: GraphNode }) {
  const { n, issues, refs, vctx, sumCtx, onTestNode, slotMenu } = props;
  const itab = useEditor((s) => s.itab);
  const setTab = useEditor((s) => s.setTab);
  const nodes = useEditor((s) => s.nodes);
  const edges = useEditor((s) => s.edges);
  const rename = useEditor((s) => s.rename);
  const updateSettings = useEditor((s) => s.updateSettings);
  const select = useEditor((s) => s.select);
  const out = useRun((s) => s.OUT[n.id]);
  const d = def(n.type);
  const st = STAGES[d.stage];
  const g = { nodes, edges };
  const host = isSub(n) ? hostOf(g, n) : undefined;
  const errs = issues.filter((x) => x.node_id === n.id);
  const set = n.settings ?? { retries: 2, timeout_s: 60, on_error: 'stop' as const, notes: '' };
  const stepMenu = (el: HTMLElement) =>
    openMenuFor(el, [
      { l: 'Test step', i: 'play', fn: () => onTestNode(n.id) },
      { l: 'Duplicate', i: 'copy', k: 'Ctrl D', fn: () => useEditor.getState().duplicate(n.id) },
      ...(host ? [{ l: 'Unplug from AI step', i: 'unlink', fn: () => useEditor.getState().unplug(n.id) }] : []),
      { l: 'Delete step', i: 'trash-can', danger: true, k: 'Del', fn: () => useEditor.getState().deleteNode(n.id) },
    ]);
  let body;
  if (itab === 'configure')
    body = (
      <>
        <label className="field">
          <span className="lab">Step name</span>
          <input className="input" data-k="__name" value={n.name} onChange={(e) => rename(n.id, e.target.value, false)} onBlur={(e) => rename(n.id, e.target.value, true)} />
        </label>
        {d.fields.length ? d.fields.map((f) => <FieldInput key={f.key} f={f} node={n} refs={refs} vctx={vctx} />) : <p className="muted">No settings needed.</p>}
        {slotsOf(n).length > 0 && <SlotsSection n={n} sumCtx={sumCtx} slotMenu={slotMenu} />}
        {isSub(n) && (
          <div className={`alert ${host ? '' : 'w'}`} style={host ? { background: 'var(--brand-soft)', color: 'var(--brand-ink)' } : undefined}>
            <Icon name={host ? 'link' : 'unlink'} />
            <span>
              {host ? (
                <>
                  Plugged into{' '}
                  <button className="lnk" onClick={() => select({ node: host.id }, 'configure')}>
                    {host.name}
                  </button>{' '}
                  · {SLOTS[d.sub!].label}
                </>
              ) : (
                `Not plugged in. Drag the diamond on top of this component onto an AI step’s ${SLOTS[d.sub!].label} slot.`
              )}
            </span>
          </div>
        )}
      </>
    );
  else if (itab === 'settings')
    body = (
      <>
        <label className="field">
          <span className="lab">Retries on failure</span>
          <select className="select" value={set.retries ?? 2} onChange={(e) => updateSettings(n.id, { retries: +e.target.value })}>
            {[0, 1, 2, 3, 5].map((k) => (
              <option key={k} value={k}>
                {k === 0 ? 'No retries' : `${k} with exponential backoff`}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="lab">Timeout (seconds)</span>
          <input className="input" type="number" min={1} value={set.timeout_s ?? 60} onChange={(e) => updateSettings(n.id, { timeout_s: +e.target.value })} />
        </label>
        {!isSub(n) && outsOf({ ...n, settings: undefined }).length > 0 && (
          <label className="field">
            <span className="lab">When this step fails</span>
            <select className="select" value={set.on_error ?? 'stop'} onChange={(e) => updateSettings(n.id, { on_error: e.target.value as 'stop' | 'continue' | 'error_output' })}>
              <option value="stop">Stop the run</option>
              <option value="continue">Continue</option>
              <option value="error_output">Error output</option>
            </select>
            <span className="hint">“Error output” adds a red output you can connect to a fallback.</span>
          </label>
        )}
        <label className="field">
          <span className="lab">Notes</span>
          <textarea className="textarea" rows={3} placeholder="Why this step exists, owner, links" value={set.notes ?? ''} onChange={(e) => updateSettings(n.id, { notes: e.target.value })} />
        </label>
        <p className="muted mono" style={{ fontSize: 12 }}>
          Step ID {n.id}
        </p>
      </>
    );
  else
    body = out ? (
      <pre className="json" style={{ padding: 0 }}>
        {JSON.stringify(out, null, 2)}
      </pre>
    ) : (
      <div className="empty" style={{ padding: '24px 8px' }}>
        <Icon name="play" size={20} />
        <h3>No output yet</h3>
        <p>Test this step or run the pipeline.</p>
        <button className="btn sm" onClick={() => onTestNode(n.id)}>
          <Icon name="play" size={14} />
          Test step
        </button>
      </div>
    );
  return (
    <>
      <div className="ihead">
        <span className={`tile lg ${d.ai ? 'ai' : ''}`} style={{ ['--c' as string]: `var(${st.color})` }}>
          <Icon name={d.icon} size={18} />
        </span>
        <div className="grow" style={{ minWidth: 0 }}>
          <h2>{n.name}</h2>
          <small>
            {st.label} · {d.name}
          </small>
        </div>
        <button className="btn ghost icon sm" aria-label="Step actions" onClick={(e) => stepMenu(e.currentTarget)}>
          <Icon name="overflow-menu--horizontal" size={18} />
        </button>
        <button className="btn ghost icon sm" aria-label="Close panel" onClick={closePanel}>
          <Icon name="close" />
        </button>
      </div>
      <div className="tabs itabs" role="tablist">
        {TABS.map(([k, lb]) => (
          <button key={k} role="tab" aria-selected={itab === k} className={`tab ${itab === k ? 'on' : ''}`} onClick={() => setTab(k)}>
            {lb}
          </button>
        ))}
      </div>
      <div className="pbody">
        <div className="ibody">
          {errs.map((x, i) => (
            <div key={i} className={`alert ${x.level === 'error' ? 'e' : 'w'}`}>
              <Icon name="warning" />
              <span>{x.message.replace(n.name + ': ', '')}</span>
            </div>
          ))}
          {body}
        </div>
      </div>
    </>
  );
}

export function Inspector(props: InspectorProps) {
  const sel = useEditor((s) => s.sel);
  const open = useConfigureOpen();
  const nodes = useEditor((s) => s.nodes);
  const edges = useEditor((s) => s.edges);
  const del = useEditor((s) => s.deleteEdge);
  const g = { nodes, edges };
  let content;
  const e = sel.edge ? edges.find((x) => x.id === sel.edge) : undefined;
  const n = sel.node ? nodeById(g, sel.node) : undefined;
  if (e) {
    const a = nodeById(g, e.from);
    const b = nodeById(g, e.to);
    content = (
      <>
        <div className="ihead">
          <span className="tile lg">
            <Icon name="connect" size={18} />
          </span>
          <div className="grow" style={{ minWidth: 0 }}>
            <h2>Connection</h2>
            <small>
              {a?.name} → {b?.name}
            </small>
          </div>
          <button className="btn ghost icon sm" aria-label="Close panel" onClick={closePanel}>
            <Icon name="close" />
          </button>
        </div>
        <div className="pbody">
          <div className="ibody">
            <dl className="row" style={{ justifyContent: 'space-between', margin: '0 0 16px' }}>
              <dt className="muted">{e.slot ? 'Slot' : 'Output'}</dt>
              <dd style={{ margin: 0 }} className="mono">
                {e.slot ? SLOTS[e.slot].label : e.from_port}
              </dd>
            </dl>
            <button className="btn sm danger" onClick={() => del(e.id)}>
              <Icon name="trash-can" size={14} />
              Delete connection
            </button>
            <p className="hint">Or hover the wire and press × at its middle, or select it and press Delete.</p>
          </div>
        </div>
      </>
    );
  } else if (n) content = <NodeView key={n.id} {...props} n={n} />;
  else content = <PipelineSettings key={props.pipeline.id + props.pipeline.name} {...props} />;
  return (
    <aside className={`insp ${open ? 'open' : ''}`} id="insp" aria-label="Configuration" aria-hidden={!open} inert={!open}>
      {content}
    </aside>
  );
}
