/** Small presentational pieces shared by screens. */
import type { ReactNode } from 'react';
import { Icon } from './Icon';

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <span className="switch">
      <input type="checkbox" checked={checked} disabled={disabled} aria-label={label} onChange={(e) => onChange(e.target.checked)} />
      <span />
    </span>
  );
}

export type Tone = 'success' | 'warning' | 'danger' | 'info' | 'brand' | 'ai' | '';
export function Badge({ tone = '', dot, children, className = '', title }: { tone?: Tone; dot?: boolean; children: ReactNode; className?: string; title?: string }) {
  return (
    <span className={`badge ${tone} ${className}`.trim()} title={title}>
      {dot && <span className="d" />}
      {children}
    </span>
  );
}

export function PipelineStatusBadge({ status }: { status: string }) {
  if (status === 'active') return <Badge tone="success" dot>Active</Badge>;
  if (status === 'paused') return <Badge tone="warning" dot>Paused</Badge>;
  return <Badge dot>Draft</Badge>;
}

export function RunStatusBadge({ status }: { status: string }) {
  switch (status) {
    case 'completed':
      return <Badge tone="success" dot>Completed</Badge>;
    case 'review':
      return <Badge tone="warning" dot>In review</Badge>;
    case 'running':
    case 'queued':
      return <Badge tone="info" dot>{status === 'running' ? 'Running' : 'Queued'}</Badge>;
    case 'cancelled':
      return <Badge dot>Cancelled</Badge>;
    default:
      return <Badge tone="danger" dot>Failed</Badge>;
  }
}

export function PageHead({ title, desc, actions }: { title: string; desc?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="ph">
      <div>
        <h1>{title}</h1>
        {desc && <p>{desc}</p>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}

export function Empty({ icon = 'search', title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <Icon name={icon} size={24} />
      <h3>{title}</h3>
      {children}
    </div>
  );
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="empty" role="status">
      <Icon name="in-progress" size={22} className="spin" />
      <p>{label}…</p>
    </div>
  );
}

export function Field({ label, req, hint, err, children, className = '' }: { label: string; req?: boolean; hint?: ReactNode; err?: string; children: ReactNode; className?: string }) {
  return (
    <label className={`field ${className}`.trim()}>
      <span className="lab">
        {label}
        {req && <span className="req">*</span>}
      </span>
      {children}
      {err && <span className="err">{err}</span>}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );
}

export function Tile({ icon, ai, size = 16, color, lg, className = '' }: { icon: string; ai?: boolean; size?: number; color?: string; lg?: boolean; className?: string }) {
  return (
    <span className={`tile ${lg ? 'lg' : ''} ${ai ? 'ai' : ''} ${className}`.trim()} style={color ? { ['--c' as string]: color } : undefined}>
      <Icon name={icon} size={size} />
    </span>
  );
}

export function Spinner({ size = 14 }: { size?: number }) {
  return <Icon name="in-progress" size={size} className="spin" />;
}
