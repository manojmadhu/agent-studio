/** Form controls generated from catalogue FieldDefs (prototype `fieldHTML`). */
import { Link } from 'react-router';
import type { GraphNode } from '../../api/types';
import type { WorkspaceRefs } from '../../api/hooks';
import { Icon } from '../../ui/Icon';
import type { Config, FieldDef, SchemaField } from './catalog';
import { fieldErr, SNAKE, type ValidationCtx } from './validate';
import { useEditor } from './store';

const SERVICE_NAMES: Record<string, string> = {
  gmail: 'Gmail',
  whatsapp_business: 'WhatsApp Business',
  amazon_s3: 'Amazon S3',
  postgresql: 'PostgreSQL',
  qdrant: 'Qdrant',
  http_api: 'HTTP API',
};

type Opt = [value: string, label: string, disabled?: boolean];
function Select({ k, value, opts, onChange, id }: { k: string; value: unknown; opts: (string | Opt)[]; onChange: (v: string) => void; id: string }) {
  return (
    <select className="select" data-k={k} id={id} value={value == null ? '' : String(value)} onChange={(e) => onChange(e.target.value)}>
      {opts.map((o) =>
        Array.isArray(o) ? (
          <option key={o[0]} value={o[0]} disabled={o[2]}>
            {o[1]}
          </option>
        ) : (
          <option key={o} value={o}>
            {o}
          </option>
        ),
      )}
    </select>
  );
}

function Chips({ k, label, options, value, onChange, mono }: { k: string; label: string; options: string[]; value: string[]; onChange: (v: string[]) => void; mono?: boolean }) {
  return (
    <div className="row" style={{ flexWrap: 'wrap', gap: 6 }} data-k={k} role="group" aria-label={label}>
      {options.map((o) => {
        const on = value.includes(o);
        return (
          <label key={o} className={`badge ${mono ? 'mono' : ''}`} style={{ height: 30, padding: '0 10px', cursor: 'pointer', ...(on ? { background: 'var(--brand-soft)', color: 'var(--brand-ink)', borderColor: 'transparent' } : {}) }}>
            <input type="checkbox" className="sr" value={o} checked={on} onChange={(e) => onChange(e.target.checked ? [...value, o] : value.filter((x) => x !== o))} />
            {on && !mono && <Icon name="checkmark" size={12} />}
            {o}
          </label>
        );
      })}
    </div>
  );
}

function SchemaEditor({ node, value }: { node: GraphNode; value: SchemaField[] }) {
  const update = useEditor((s) => s.updateConfig);
  const set = (rows: SchemaField[], coalesce?: string) => update(node.id, { schema: rows }, coalesce);
  const patch = (i: number, p: Partial<SchemaField>, coalesce?: string) => set(value.map((r, j) => (j === i ? { ...r, ...p } : r)), coalesce);
  return (
    <>
      <table className="schema">
        <thead>
          <tr>
            <th>Field</th>
            <th style={{ width: 92 }}>Type</th>
            <th style={{ width: 40 }} title="Required">
              Req.
            </th>
            <th style={{ width: 36 }}>PII</th>
            <th style={{ width: 28 }} />
          </tr>
        </thead>
        <tbody>
          {value.map((x, i) => (
            <tr key={i}>
              <td>
                <input className="input mono" data-sk="name" value={x.name} aria-label="Field name" style={SNAKE.test(x.name) ? undefined : { borderColor: 'var(--danger)' }} onChange={(e) => patch(i, { name: e.target.value }, `schema.${i}`)} />
              </td>
              <td>
                <select className="select" aria-label="Type" value={x.type} onChange={(e) => patch(i, { type: e.target.value as SchemaField['type'] })}>
                  {['string', 'number', 'date', 'boolean', 'array'].map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </td>
              <td style={{ textAlign: 'center' }}>
                <input type="checkbox" className="check" checked={!!x.req} aria-label="Required" onChange={(e) => patch(i, { req: e.target.checked })} />
              </td>
              <td style={{ textAlign: 'center' }}>
                <input type="checkbox" className="check" checked={!!x.pii} aria-label="Personal data" onChange={(e) => patch(i, { pii: e.target.checked })} />
              </td>
              <td>
                <button className="btn ghost icon xs" aria-label="Remove field" onClick={() => set(value.filter((_, j) => j !== i))}>
                  <Icon name="close" size={14} />
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="btn sm" style={{ marginTop: 10 }} onClick={() => set([...value, { name: 'new_field', type: 'string' }])}>
        <Icon name="add" size={14} />
        Add field
      </button>
      <span className="hint">Fields marked PII are masked by redaction steps and in exports. Names must be snake_case.</span>
    </>
  );
}

export function FieldInput({ f, node, refs, vctx }: { f: FieldDef; node: GraphNode; refs: WorkspaceRefs; vctx: ValidationCtx }) {
  const update = useEditor((s) => s.updateConfig);
  const c = node.config as Config;
  const v = c[f.key];
  const er = fieldErr(f, c, vctx);
  const id = `f_${node.id}_${f.key}`;
  const set = (val: unknown, coalesce = false, extra: Config = {}) => update(node.id, { [f.key]: val, ...extra }, coalesce ? f.key : undefined);
  const lab = (
    <span className="lab">
      {f.label}
      {f.required && <span className="req">*</span>}
    </span>
  );
  const hint = (
    <>
      {er && <span className="err">{er}</span>}
      {f.hint && <span className="hint">{f.hint}</span>}
    </>
  );
  switch (f.type) {
    case 'switch':
      return (
        <div className="field row" style={{ justifyContent: 'space-between' }}>
          <span className="sub" style={{ fontWeight: 500, fontSize: 13.5 }}>
            {f.label}
          </span>
          <span className="switch">
            <input type="checkbox" data-k={f.key} checked={!!v} aria-label={f.label} onChange={(e) => set(e.target.checked)} />
            <span />
          </span>
        </div>
      );
    case 'chips':
      return (
        <div className="field">
          {lab}
          <Chips k={f.key} label={f.label} options={f.options ?? []} value={(v as string[]) ?? []} onChange={(x) => set(x)} />
          {hint}
        </div>
      );
    case 'select': {
      let opts: string[] = f.options ?? [];
      if (node.type === 'ai.knowledge' && f.key === 'collection' && refs.collections.length) opts = [...new Set([...refs.collections.map((k) => k.name), ...opts])];
      return (
        <label className="field" htmlFor={id}>
          {lab}
          <Select id={id} k={f.key} value={v} opts={opts} onChange={(x) => set(x)} />
          {hint}
        </label>
      );
    }
    case 'connection': {
      const cs = refs.connections.filter((x) => x.service === f.service);
      return (
        <label className="field" htmlFor={id}>
          {lab}
          <Select id={id} k={f.key} value={v} opts={[['', 'Select a connection'], ...cs.map((x): Opt => [x.id, x.name])]} onChange={(x) => set(x)} />
          {er || f.hint ? (
            hint
          ) : (
            <span className="hint">
              {cs.length ? (
                <>
                  Manage in <Link to="/connections">Connections</Link>.
                </>
              ) : (
                <>
                  No {SERVICE_NAMES[f.service ?? ''] ?? f.service} connection yet. <Link to="/connections">Add one</Link>.
                </>
              )}
            </span>
          )}
        </label>
      );
    }
    case 'worker':
      return (
        <label className="field" htmlFor={id}>
          {lab}
          <Select id={id} k={f.key} value={v} opts={refs.workers.map((w): Opt => [w.id, w.name])} onChange={(x) => set(x)} />
          {hint}
        </label>
      );
    case 'mcp_server':
      return (
        <label className="field" htmlFor={id}>
          {lab}
          <Select id={id} k={f.key} value={v} opts={refs.mcp.map((m): Opt => [m.id, m.name + (m.enabled ? '' : ' (disabled)')])} onChange={(x) => set(x, false, node.type === 'tool.mcp' ? { tools: [] } : {})} />
          {hint}
        </label>
      );
    case 'mcp_tools': {
      const m = refs.mcp.find((x) => x.id === c.server);
      return (
        <div className="field">
          {lab}
          <Chips k={f.key} label={f.label} mono options={(m?.tools ?? []).filter((t) => t.enabled !== false).map((t) => t.name)} value={(v as string[]) ?? []} onChange={(x) => set(x)} />
          <span className="hint">Leave all unselected to expose every tool.</span>
        </div>
      );
    }
    case 'skill':
      return (
        <label className="field" htmlFor={id}>
          {lab}
          <Select id={id} k={f.key} value={v} opts={[['none', 'None'], ...refs.skills.map((k): Opt => [k.id, k.name])]} onChange={(x) => set(x)} />
          <span className="hint">
            Versioned instruction packs. <Link to="/skills">View skills</Link>.
          </span>
        </label>
      );
    case 'provider':
      return (
        <label className="field" htmlFor={id}>
          {lab}
          <Select
            id={id}
            k={f.key}
            value={v}
            opts={refs.providers.map((p): Opt => [p.id, p.name + (p.enabled ? '' : ' (disabled)'), !p.enabled])}
            onChange={(x) => {
              const pv = refs.providers.find((p) => p.id === x);
              set(x, false, { model: pv?.default_model ?? pv?.models[0]?.id ?? '', conn: '' });
            }}
          />
          {hint}
        </label>
      );
    case 'model': {
      const pv = refs.providers.find((p) => p.id === c.provider) ?? refs.providers[0];
      return (
        <label className="field" htmlFor={id}>
          {lab}
          <Select id={id} k={f.key} value={v} opts={(pv?.models ?? []).filter((m) => m.enabled !== false && !m.embedding).map((m): Opt => [m.id, m.display_name ?? m.id])} onChange={(x) => set(x)} />
          {hint}
        </label>
      );
    }
    case 'provider_connection': {
      const pv = refs.providers.find((p) => p.id === c.provider) ?? refs.providers[0];
      const def = refs.connections.find((x) => x.id === pv?.connection_id)?.name ?? 'Not connected';
      return (
        <label className="field" htmlFor={id}>
          {lab}
          <Select id={id} k={f.key} value={v} opts={[['', 'Provider default · ' + def], ...refs.connections.filter((x) => x.service === pv?.kind).map((x): Opt => [x.id, x.name])]} onChange={(x) => set(x)} />
          {hint}
        </label>
      );
    }
    case 'range':
      return (
        <label className="field" htmlFor={id}>
          <span className="lab">
            {f.label}
            <span className="muted num" style={{ marginLeft: 'auto' }}>
              {String(v)}
            </span>
          </span>
          <input id={id} type="range" className="range" min={f.min ?? 0} max={f.max ?? 1} step={0.05} data-k={f.key} value={Number(v) || 0} aria-label={f.label} onChange={(e) => set(+e.target.value, true)} />
          <span className="hint">Lower values give more consistent extraction.</span>
        </label>
      );
    case 'textarea':
      return (
        <label className="field" htmlFor={id}>
          {lab}
          <textarea id={id} className="textarea" data-k={f.key} rows={4} value={v == null ? '' : String(v)} onChange={(e) => set(e.target.value, true)} />
          {hint}
        </label>
      );
    case 'schema':
      return (
        <div className="field">
          {lab}
          <SchemaEditor node={node} value={(v as SchemaField[]) ?? []} />
        </div>
      );
    case 'number':
      return (
        <label className="field" htmlFor={id}>
          {lab}
          <input id={id} className="input" data-k={f.key} type="number" step="any" value={v == null ? '' : String(v)} onChange={(e) => set(e.target.value === '' ? '' : +e.target.value, true)} />
          {hint}
        </label>
      );
    default:
      return (
        <label className="field" htmlFor={id}>
          {lab}
          <input id={id} className={`input ${f.monospace ? 'mono' : ''}`} data-k={f.key} type="text" value={v == null ? '' : String(v)} onChange={(e) => set(e.target.value, true)} />
          {hint}
        </label>
      );
  }
}
