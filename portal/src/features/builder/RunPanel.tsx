/** Run panel under the canvas: Run log · Extracted data · Document (overlays) · Problems. */
import { useEffect, useRef } from 'react';
import type { Graph, ValidationIssue } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { FIELD_COLORS, INVOICE_SCHEMA, RECEIPT_SCHEMA, type SchemaField } from './catalog';
import { mainNodes, nodeById, subsOf } from './graph';
import { SAMPLE_DOC_OPTIONS, useRun, type RunTab } from './runStore';
import { DocumentViewer } from '../document/DocumentViewer';
import { VALUES } from '../../api/mock/sampleData';

export interface RunPanelProps {
  graph: Graph;
  issues: ValidationIssue[];
  onRun: () => void;
  onGoto: (id: string) => void;
  onShowOutput: (id: string) => void;
}

const lvColor = { ok: 'success', run: 'cyan', warn: 'warning', err: 'danger' } as const;

export function thresholdOf(g: Graph): number {
  const n = mainNodes(g).find((x) => x.type === 'route.confidence');
  const t = n ? Number(n.config.threshold) : 0.85;
  return Number.isFinite(t) ? t : 0.85;
}

export function RunPanel({ graph, issues, onRun, onGoto, onShowOutput }: RunPanelProps) {
  const r = useRun();
  const bodyRef = useRef<HTMLDivElement>(null);
  const doc = SAMPLE_DOC_OPTIONS.find((d) => d.id === r.docId) ?? SAMPLE_DOC_OPTIONS[0]!;
  const ex = r.extractNode ? nodeById(graph, r.extractNode) : undefined;
  const parser = ex && subsOf(graph, ex, 'parser')[0];
  const schema = ((parser?.config.schema as SchemaField[] | undefined) ?? (doc.kind === 'rcp' ? RECEIPT_SCHEMA : INVOICE_SCHEMA)) as SchemaField[];
  const has = r.layers.has('fields');
  const threshold = thresholdOf(graph);
  const resultFields = r.result?.fields;
  const valueOf = (name: string): [unknown, number] | undefined => {
    const f = resultFields?.find((x) => x.name === name);
    if (f) return [f.value, f.confidence ?? 0];
    const v = VALUES[doc.kind][name];
    return v ? [v[0], v[1]] : undefined;
  };
  const tabs: [RunTab, string, number][] = [
    ['log', 'Run log', r.logs.length],
    ['data', 'Extracted data', has ? schema.length : 0],
    ['doc', 'Document', 0],
    ['problems', 'Problems', issues.length],
  ];
  useEffect(() => {
    if (r.open && r.tab === 'log' && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [r.logs.length, r.open, r.tab]);

  let body = null;
  if (r.tab === 'log')
    body = r.logs.length ? (
      r.logs.map((l, i) => (
        <div className="logrow" key={i}>
          <time>{l.t.toTimeString().slice(0, 8)}</time>
          <span style={{ color: `var(--${lvColor[l.lv]})` }}>
            <Icon name={l.lv === 'ok' ? 'checkmark--filled' : l.lv === 'run' ? 'in-progress' : 'warning--alt'} size={15} className={l.lv === 'run' && r.running && i === r.logs.length - 1 ? 'spin' : ''} />
          </span>
          <span>{l.m}</span>
          {l.id && r.OUT[l.id] ? (
            <button className="lnk" onClick={() => onShowOutput(l.id!)}>
              Output
            </button>
          ) : (
            <span />
          )}
        </div>
      ))
    ) : (
      <div className="empty">
        <Icon name="play" size={22} />
        <h3>Test the pipeline</h3>
        <p>Runs a sample document through every step. Steps light up on the canvas as they execute.</p>
        <button className="btn primary sm" onClick={onRun}>
          <Icon name="play" size={14} />
          Test run
        </button>
      </div>
    );
  else if (r.tab === 'data')
    body = has ? (
      <table className="table ftab">
        <thead>
          <tr>
            <th>Field</th>
            <th>Value</th>
            <th>Confidence</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {schema.map((f, i) => {
            const v = valueOf(f.name);
            const dot = <span className="fdot" style={{ ['--fc' as string]: FIELD_COLORS[i % 10] }} />;
            if (!v)
              return (
                <tr key={f.name}>
                  <td>
                    {dot}
                    <span className="mono">{f.name}</span>
                  </td>
                  <td className="muted">Not found</td>
                  <td>—</td>
                  <td>{f.req && <span className="badge danger">Required</span>}</td>
                </tr>
              );
            const low = v[1] < threshold;
            const masked = f.pii && r.layers.has('redactions');
            return (
              <tr key={f.name}>
                <td>
                  {dot}
                  <span className="mono">{f.name}</span>
                  {f.pii && <> <span className="badge">PII</span></>}
                </td>
                <td>{masked ? <span className="mono muted">•••• masked</span> : typeof v[0] === 'number' ? v[0].toLocaleString('en-US', { minimumFractionDigits: 2 }) : String(v[0] ?? '')}</td>
                <td>
                  <span className="row" style={{ gap: 8 }}>
                    <span className="meter" style={{ ['--c' as string]: low ? 'var(--warning)' : 'var(--success)' }}>
                      <i style={{ width: `${v[1] * 100}%` }} />
                    </span>
                    <span className="num" style={low ? { color: 'var(--warning)', fontWeight: 600 } : { color: 'var(--text-3)' }}>
                      {v[1].toFixed(2)}
                    </span>
                  </span>
                </td>
                <td>{low && <span className="badge warning">Below threshold</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    ) : (
      <div className="empty">
        <p>Extracted fields appear when a test run reaches an extraction step.</p>
      </div>
    );
  else if (r.tab === 'doc')
    body = (
      <div className="docview">
        <div>
          <DocumentViewer
            kind={doc.kind}
            zoom={0.62}
            layers={{ blocks: r.layers.has('blocks'), tables: r.layers.has('tables'), fields: has, redactions: r.layers.has('redactions') }}
            fields={schema.map((f) => ({ name: f.name, confidence: valueOf(f.name)?.[1] ?? 0 })).filter((f) => f.confidence > 0)}
            threshold={threshold}
          />
        </div>
        <div style={{ flex: 1, minWidth: 200 }}>
          <label className="field">
            <span className="lab">Test document</span>
            <select className="select sm" value={r.docId} onChange={(e) => r.setDoc(e.target.value)} aria-label="Test document">
              {SAMPLE_DOC_OPTIONS.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </label>
          {r.cls && (
            <p className="row" style={{ margin: '0 0 10px' }}>
              <span className="badge ai">
                <Icon name="tag--group" size={12} />
                Classified as {r.cls} · 0.96
              </span>
            </p>
          )}
          <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>
            {r.runId ? 'Overlays show what each completed step found: text blocks, tables, extracted fields and redactions.' : 'Run a test to see text blocks, tables, fields and redactions on the page.'}
          </p>
        </div>
      </div>
    );
  else
    body = issues.length ? (
      issues.map((i, k) => (
        <div className="logrow" key={k} style={{ gridTemplateColumns: '20px minmax(0,1fr) auto' }}>
          <span style={{ color: `var(--${i.level === 'error' ? 'danger' : 'warning'})` }}>
            <Icon name={i.level === 'error' ? 'warning' : 'warning--alt'} size={15} />
          </span>
          <span>{i.message}</span>
          {i.node_id ? (
            <button className="lnk" onClick={() => onGoto(i.node_id!)}>
              Show
            </button>
          ) : (
            <span />
          )}
        </div>
      ))
    ) : (
      <div className="empty">
        <Icon name="checkmark--filled" size={22} />
        <h3>No problems</h3>
        <p>The pipeline is ready to test and publish.</p>
      </div>
    );

  return (
    <div className={`runp ${r.open ? 'open' : ''} ${r.open && r.tab === 'doc' ? 'tall' : ''}`} id="runp">
      <div className="tabs" role="tablist">
        {tabs.map(([k, l, n]) => (
          <button key={k} role="tab" aria-selected={r.open && r.tab === k} className={`tab ${r.open && r.tab === k ? 'on' : ''}`} onClick={() => r.toggleTab(k)}>
            {l}
            {n ? <span className={`badge num ${k === 'problems' && issues.some((i) => i.level === 'error') ? 'danger' : ''}`}>{n}</span> : null}
          </button>
        ))}
        <div className="grow" />
        <button className="btn ghost icon xs" aria-label={`${r.open ? 'Collapse' : 'Expand'} run panel`} onClick={() => r.setOpen(!r.open)}>
          <Icon name={r.open ? 'chevron--down' : 'chevron--up'} />
        </button>
      </div>
      {r.open && (
        <div className="rbody" ref={bodyRef}>
          {body}
        </div>
      )}
    </div>
  );
}
