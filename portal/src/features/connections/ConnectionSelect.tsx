/** Connection picker used by Models, Workers and MCP servers — every component authenticates through a Connection. */
import { Link } from 'react-router';
import { useConnections } from '../../api/hooks';
import type { ServiceId } from '../../api/types';
import { AUTH_LABELS } from './authFields';

export function ConnectionSelect({ value, onChange, services, label = 'Connection', allowNone = true, hint }: { value: string | null | undefined; onChange: (v: string | null) => void; services?: ServiceId[]; label?: string; allowNone?: boolean; hint?: string }) {
  const all = useConnections().data ?? [];
  const cs = services ? all.filter((c) => services.includes(c.service)) : all;
  return (
    <label className="field">
      <span className="lab">{label}</span>
      <select className="select" value={value ?? ''} onChange={(e) => onChange(e.target.value || null)} aria-label={label}>
        {allowNone && <option value="">No connection (unauthenticated)</option>}
        {cs.map((c) => (
          <option key={c.id} value={c.id}>
            {c.name} · {AUTH_LABELS[c.auth_type]}
            {c.secret_hint ? ` · ${c.secret_hint}` : ''}
          </option>
        ))}
      </select>
      <span className="hint">
        {hint ?? 'Credentials are stored in the vault.'} <Link to="/connections">Manage connections</Link>.
      </span>
    </label>
  );
}
