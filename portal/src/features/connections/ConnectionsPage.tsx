/**
 * Connections (credentials vault). Forms are data-driven: services and their allowed auth schemes come from
 * `/catalog/services`; each auth scheme renders the fields of its OpenAPI `AuthConfig` member.
 * Secrets are write-only — the API only ever returns `secret_hint`.
 */
import { useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router';
import { api } from '../../api/client';
import { errorMessage, isApiError } from '../../api/errors';
import { qk, useApiMutation, useConnections, useConnectionUsage, useServices } from '../../api/hooks';
import type { AuthType, Connection, FieldSpec, ServiceDefinition, ServiceId, TestResult, UsageRef } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { Dialog } from '../../ui/Dialog';
import { Badge, Empty, Loading, PageHead, Spinner } from '../../ui/bits';
import { openMenuFor } from '../../ui/menuStore';
import { toast } from '../../ui/toast';
import { ago } from '../../lib/util';
import { useCrumbs } from '../../app/crumbs';
import { AUTH_FIELDS, AUTH_LABELS, buildAuth, missingAuth } from './authFields';
import { useQueryClient } from '@tanstack/react-query';

export const SERVICE_ICON: Partial<Record<ServiceId, string>> = {
  gmail: 'email',
  microsoft_365_mail: 'email',
  whatsapp_business: 'chat',
  slack: 'chat',
  anthropic: 'ai-generate',
  openai: 'ai-generate',
  azure_openai: 'ai-generate',
  google_gemini: 'ai-generate',
  ollama: 'cloud-services',
  openai_compatible: 'cloud-services',
  postgresql: 'data--base',
  mssql: 'data--base',
  redis: 'data--base',
  amazon_s3: 'folder--move-to',
  azure_blob: 'folder--move-to',
  sftp: 'folder--move-to',
  qdrant: 'catalog',
  pgvector: 'catalog',
  azure_ai_search: 'catalog',
  http_api: 'earth',
  mcp: 'plug',
  document_worker: 'chip',
};

export function ConnStatus({ s }: { s: Connection['status'] }) {
  if (s === 'connected') return <Badge tone="success" dot>Connected</Badge>;
  if (s === 'failing') return <Badge tone="danger" dot>Failing</Badge>;
  if (s === 'pending_authorization') return <Badge tone="info" dot>Sign-in needed</Badge>;
  if (s === 'expired') return <Badge tone="danger" dot>Expired</Badge>;
  return <Badge tone="warning" dot>Not verified</Badge>;
}

export const connDetail = (c: Connection): string => {
  const s = c.settings ?? {};
  const v = s.account ?? s.endpoint ?? s.base_url ?? s.url ?? s.bucket ?? s.phone_number ?? (s.host ? `${String(s.host)}${s.database ? ' / ' + String(s.database) : ''}` : s.project);
  return v ? String(v) : '—';
};

/** Non-secret settings form generated from the service's `settings_fields`. */
function SettingsFields({ fields, values, onChange }: { fields: FieldSpec[]; values: Record<string, string>; onChange: (k: string, v: string) => void }) {
  return (
    <>
      {fields.map((f) => (
        <label className="field" key={f.key}>
          <span className="lab">
            {f.label}
            {f.required && <span className="req">*</span>}
          </span>
          {f.type === 'select' ? (
            <select className="select" value={values[f.key] ?? String(f.default ?? '')} onChange={(e) => onChange(f.key, e.target.value)}>
              {(f.options ?? []).map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          ) : (
            <input
              className={`input ${f.monospace ? 'mono' : ''}`}
              type={f.type === 'number' ? 'number' : 'text'}
              value={values[f.key] ?? ''}
              placeholder={f.default != null ? String(f.default) : undefined}
              onChange={(e) => onChange(f.key, e.target.value)}
              name={`s_${f.key}`}
            />
          )}
          {f.hint && <span className="hint">{f.hint}</span>}
        </label>
      ))}
    </>
  );
}

/** Credential fields for one auth scheme. Secret inputs are password fields and never pre-filled. */
export function AuthFields({ type, values, onChange }: { type: AuthType; values: Record<string, string>; onChange: (k: string, v: string) => void }) {
  const fs = AUTH_FIELDS[type];
  if (!fs.length) return <p className="muted" style={{ fontSize: 13 }}>No credentials are sent to this service.</p>;
  return (
    <>
      {fs.map((f) => (
        <label className="field" key={f.key}>
          <span className="lab">
            {f.label}
            {f.required && <span className="req">*</span>}
            {(f.kind === 'secret' || f.kind === 'secret-textarea') && (
              <span className="badge" style={{ marginLeft: 'auto', height: 18 }}>
                <Icon name="locked" size={12} />
                Write-only
              </span>
            )}
          </span>
          {f.kind === 'select' ? (
            <select className="select" value={values[f.key] ?? f.default ?? ''} onChange={(e) => onChange(f.key, e.target.value)}>
              {f.options!.map((o) => (
                <option key={o}>{o}</option>
              ))}
            </select>
          ) : f.kind === 'textarea' || f.kind === 'secret-textarea' ? (
            <textarea className="textarea mono" rows={4} value={values[f.key] ?? ''} placeholder={f.placeholder} onChange={(e) => onChange(f.key, e.target.value)} spellCheck={false} autoComplete="off" />
          ) : (
            <input
              className="input mono"
              type={f.kind === 'secret' ? 'password' : 'text'}
              autoComplete={f.kind === 'secret' ? 'new-password' : 'off'}
              value={values[f.key] ?? ''}
              placeholder={f.placeholder ?? f.default}
              name={`a_${f.key}`}
              onChange={(e) => onChange(f.key, e.target.value)}
            />
          )}
          {f.hint && <span className="hint">{f.hint}</span>}
        </label>
      ))}
    </>
  );
}

const CATEGORY_LABEL: Record<string, string> = { mail: 'Mail', messaging: 'Messaging', model_provider: 'Model providers', database: 'Databases', storage: 'Storage', vector_store: 'Vector stores', api: 'APIs', mcp: 'MCP', worker: 'Workers' };

const NO_SERVICES: ServiceDefinition[] = [];
export function NewConnectionDialog({ onClose, onCreated, preset }: { onClose: () => void; onCreated?: (c: Connection) => void; preset?: ServiceId }) {
  const services = useServices().data ?? NO_SERVICES;
  const [svcId, setSvcId] = useState<ServiceId>(preset ?? 'http_api');
  const svc = services.find((s) => s.id === svcId);
  const [authType, setAuthType] = useState<AuthType>('api_key');
  const [name, setName] = useState('');
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [auth, setAuth] = useState<Record<string, string>>({});
  const [err, setErr] = useState('');
  useEffect(() => {
    if (svc && !svc.auth_types.includes(authType)) setAuthType(svc.auth_types[0]!);
  }, [svc, authType]);
  const create = useApiMutation(
    () => {
      const st: Record<string, unknown> = {};
      for (const f of svc?.settings_fields ?? []) {
        const v = settings[f.key] ?? (f.default != null ? String(f.default) : '');
        if (v !== '') st[f.key] = f.type === 'number' ? Number(v) : v;
      }
      return api.createConnection({ name: name.trim(), service: svcId, settings: st, auth: buildAuth(authType, auth) as never });
    },
    [qk.connections],
  );
  const groups = useMemo(() => {
    const g: Record<string, ServiceDefinition[]> = {};
    services.forEach((s) => (g[s.category ?? 'api'] = [...(g[s.category ?? 'api'] ?? []), s]));
    return g;
  }, [services]);
  const save = async () => {
    if (!name.trim()) {
      setErr('Give the connection a name');
      return false;
    }
    const miss = [...(svc?.settings_fields ?? []).filter((f) => f.required && !(settings[f.key] ?? (f.default != null ? String(f.default) : '')).trim()).map((f) => f.label), ...missingAuth(authType, auth)];
    if (miss.length) {
      setErr(`Required: ${miss.join(', ')}`);
      return false;
    }
    try {
      const c = await create.mutateAsync();
      setAuth({}); // drop secret material from memory
      toast('Connection saved. Only a hint of the secret is kept.');
      onCreated?.(c);
      if (c.auth_type === 'oauth2_authorization_code') toast('Open the connection and choose “Sign in” to finish authorization.');
    } catch {
      return false;
    }
  };
  return (
    <Dialog title="New connection" desc="Secrets are sent to the vault and cannot be viewed again. Only the last four characters are shown." okLabel="Save connection" busy={create.isPending} onOk={save} onClose={onClose} wide>
      <div className="grid2">
        <label className="field">
          <span className="lab">Service</span>
          <select className="select" name="svc" value={svcId} onChange={(e) => setSvcId(e.target.value as ServiceId)}>
            {Object.entries(groups).map(([cat, ss]) => (
              <optgroup key={cat} label={CATEGORY_LABEL[cat] ?? cat}>
                {ss.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="lab">
            Name<span className="req">*</span>
          </span>
          <input className="input" name="n" placeholder="e.g. Claims mailbox" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
      </div>
      {svc && (
        <>
          {(svc.settings_fields ?? []).length > 0 && (
            <>
              <h3 className="section-h" style={{ marginTop: 0 }}>
                Settings
              </h3>
              <div className="grid2">
                <SettingsFields fields={svc.settings_fields ?? []} values={settings} onChange={(k, v) => setSettings({ ...settings, [k]: v })} />
              </div>
            </>
          )}
          <h3 className="section-h">
            <Icon name="locked" />
            Authentication
          </h3>
          <label className="field">
            <span className="lab">Method</span>
            <select className="select" name="auth_type" value={authType} onChange={(e) => setAuthType(e.target.value as AuthType)}>
              {svc.auth_types.map((a) => (
                <option key={a} value={a}>
                  {AUTH_LABELS[a]}
                </option>
              ))}
            </select>
          </label>
          <AuthFields type={authType} values={auth} onChange={(k, v) => setAuth({ ...auth, [k]: v })} />
        </>
      )}
      {err && (
        <div className="alert e" role="alert">
          <Icon name="warning" />
          <span>{err}</span>
        </div>
      )}
    </Dialog>
  );
}

function RotateDialog({ conn, services, onClose }: { conn: Connection; services: ServiceDefinition[]; onClose: () => void }) {
  const svc = services.find((s) => s.id === conn.service);
  const [type, setType] = useState<AuthType>(conn.auth_type);
  const [vals, setVals] = useState<Record<string, string>>({});
  const [err, setErr] = useState('');
  const m = useApiMutation(() => api.updateConnection(conn.id, { auth: buildAuth(type, vals) as never }), [qk.connections]);
  return (
    <Dialog
      title={`Rotate credentials · ${conn.name}`}
      desc="The new secret replaces the current one in the vault. Components using this connection pick it up on their next call."
      okLabel="Rotate secret"
      busy={m.isPending}
      onClose={onClose}
      onOk={async () => {
        const miss = missingAuth(type, vals);
        if (miss.length) {
          setErr(`Required: ${miss.join(', ')}`);
          return false;
        }
        try {
          const c = await m.mutateAsync();
          setVals({});
          toast(`Credentials rotated · ${c.secret_hint ?? 'no secret'}`);
        } catch {
          return false;
        }
      }}
    >
      <label className="field">
        <span className="lab">Method</span>
        <select className="select" value={type} onChange={(e) => setType(e.target.value as AuthType)}>
          {(svc?.auth_types ?? [conn.auth_type]).map((a) => (
            <option key={a} value={a}>
              {AUTH_LABELS[a]}
            </option>
          ))}
        </select>
      </label>
      <AuthFields type={type} values={vals} onChange={(k, v) => setVals({ ...vals, [k]: v })} />
      {err && (
        <div className="alert e" role="alert">
          <Icon name="warning" />
          <span>{err}</span>
        </div>
      )}
    </Dialog>
  );
}

export function UsageList({ usages }: { usages: UsageRef[] }) {
  if (!usages.length) return <p className="muted" style={{ fontSize: 13 }}>Not used by any pipeline or component.</p>;
  const icon: Record<string, string> = { pipeline: 'flow', provider: 'machine-learning-model', worker: 'chip', mcp_server: 'plug', knowledge_collection: 'catalog', webhook: 'webhook' };
  return (
    <div>
      {usages.map((u, i) => (
        <div className="toolrow" key={i}>
          <Icon name={icon[u.resource_type] ?? 'link'} />
          <span className="grow">{u.label}</span>
          <span className="muted mono" style={{ fontSize: 12 }}>
            {u.node_id ?? u.resource_id}
          </span>
        </div>
      ))}
    </div>
  );
}

function DeleteDialog({ conn, onClose }: { conn: Connection; onClose: () => void }) {
  const qc = useQueryClient();
  const [inUse, setInUse] = useState<UsageRef[] | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (force: boolean) => {
    setBusy(true);
    try {
      await api.deleteConnection(conn.id, force);
      await qc.invalidateQueries({ queryKey: qk.connections });
      toast(`Deleted “${conn.name}”`);
      onClose();
    } catch (e) {
      if (isApiError(e) && e.status === 409) setInUse(e.problem.usages ?? []);
      else toast(errorMessage(e), true);
    } finally {
      setBusy(false);
    }
  };
  if (inUse)
    return (
      <Dialog title={`“${conn.name}” is in use`} desc={`Deleting it breaks ${inUse.length} component${inUse.length > 1 ? 's' : ''}: they will show “Connection was removed” until another connection is chosen.`} okLabel="Delete anyway" danger busy={busy} onOk={async () => (await run(true), false)} onClose={onClose}>
        <UsageList usages={inUse} />
      </Dialog>
    );
  return <Dialog title={`Delete “${conn.name}”?`} desc="The credentials are removed from the vault." okLabel="Delete connection" danger busy={busy} onOk={async () => (await run(false), false)} onClose={onClose} />;
}

function ConnectionDrawer({ conn, services, onClose, onRotate, onDelete }: { conn: Connection; services: ServiceDefinition[]; onClose: () => void; onRotate: () => void; onDelete: () => void }) {
  const usage = useConnectionUsage(conn.id);
  const [test, setTest] = useState<TestResult | null>(null);
  const qc = useQueryClient();
  const svc = services.find((s) => s.id === conn.service);
  const t = useApiMutation(() => api.testConnection(conn.id), [qk.connections], (r) => {
    setTest(r);
    toast(r.ok ? `${conn.name}: ${r.message ?? 'connected'}` : `${conn.name}: ${r.message ?? 'test failed'}`, !r.ok);
  });
  const oauth = useApiMutation(() => api.startOAuth(conn.id), [], (r) => {
    try {
      if (window.location.protocol.startsWith('http') && !r.authorization_url.includes('accounts.example.com')) window.open(r.authorization_url, 'oauth', 'width=520,height=640');
    } catch {
      /* popup blocked */
    }
    toast('Complete sign-in in the provider window. Status updates when the callback arrives.');
    setTimeout(() => void qc.invalidateQueries({ queryKey: qk.connections }), 1600);
  });
  return (
    <div className="scrim light" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={conn.name}>
        <div className="ihead">
          <span className="tile lg">
            <Icon name={SERVICE_ICON[conn.service] ?? 'link'} size={18} />
          </span>
          <div className="grow" style={{ minWidth: 0 }}>
            <h2>{conn.name}</h2>
            <small>
              {svc?.name ?? conn.service} · {AUTH_LABELS[conn.auth_type]}
            </small>
          </div>
          <button className="btn ghost icon sm" aria-label="Close" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        <div className="pbody">
          <div className="ibody">
            <dl className="kv">
              <dt>Status</dt>
              <dd>
                <ConnStatus s={conn.status} />
              </dd>
              <dt>Secret</dt>
              <dd className="mono" data-testid="secret-hint">
                {conn.secret_hint ?? 'No secret'}
              </dd>
              {Object.entries(conn.settings ?? {}).map(([k, v]) => (
                <FragmentKV key={k} k={k} v={String(v)} />
              ))}
              <dt>Last tested</dt>
              <dd>{ago(conn.last_tested_at)}</dd>
              <dt>Last used</dt>
              <dd>{ago(conn.last_used_at)}</dd>
              <dt>ID</dt>
              <dd className="mono">{conn.id}</dd>
            </dl>
            {test && (
              <div className={`alert ${test.ok ? '' : 'e'}`} style={test.ok ? { background: 'var(--success-soft)', color: 'var(--success)' } : undefined} role="status">
                <Icon name={test.ok ? 'checkmark--filled' : 'warning'} />
                <span>
                  {test.message} {test.latency_ms != null && <span className="num">· {test.latency_ms} ms</span>}
                </span>
              </div>
            )}
            <div className="row" style={{ flexWrap: 'wrap', marginBottom: 8 }}>
              <button className="btn sm" onClick={() => t.mutate(undefined)} disabled={t.isPending}>
                {t.isPending ? <Spinner /> : <Icon name="connect" size={14} />}
                Test connection
              </button>
              {conn.auth_type === 'oauth2_authorization_code' && (
                <button className="btn sm" onClick={() => oauth.mutate(undefined)} disabled={oauth.isPending}>
                  <Icon name="launch" size={14} />
                  Sign in
                </button>
              )}
              <button className="btn sm" onClick={onRotate}>
                <Icon name="renew" size={14} />
                Rotate secret
              </button>
              <button className="btn sm danger" onClick={onDelete}>
                <Icon name="trash-can" size={14} />
                Delete
              </button>
            </div>
            <h3 className="section-h">Used by</h3>
            {usage.isLoading ? <Loading /> : <UsageList usages={usage.data ?? []} />}
          </div>
        </div>
      </aside>
    </div>
  );
}
function FragmentKV({ k, v }: { k: string; v: string }) {
  return (
    <>
      <dt>{k.replace(/_/g, ' ')}</dt>
      <dd className="mono">{v}</dd>
    </>
  );
}

export function ConnectionsPage() {
  useCrumbs([['Connections', '/connections']]);
  const conns = useConnections();
  const services = useServices().data ?? [];
  const [params, setParams] = useSearchParams();
  const [dlg, setDlg] = useState<{ t: 'new' } | { t: 'view' | 'rotate' | 'delete'; c: Connection } | null>(null);
  useEffect(() => {
    if (params.get('new')) {
      setDlg({ t: 'new' });
      setParams({}, { replace: true });
    }
  }, [params, setParams]);
  const test = useApiMutation((c: Connection) => api.testConnection(c.id), [qk.connections], (r, c) => toast(r.ok ? `${c.name}: ${r.message ?? 'connected'}` : `${c.name}: ${r.message ?? 'test failed'}`, !r.ok));
  const svcName = (id: ServiceId) => services.find((s) => s.id === id)?.name ?? id;
  const current = dlg && 'c' in dlg ? conns.data?.find((x) => x.id === dlg.c.id) ?? dlg.c : null;
  return (
    <div className="page">
      <PageHead
        title="Connections"
        desc="Credentials for mailboxes, messaging, models, databases, workers and APIs. Secrets are write-only and stored in the vault."
        actions={
          <button className="btn primary" onClick={() => setDlg({ t: 'new' })}>
            <Icon name="add" />
            New connection
          </button>
        }
      />
      <div className="card">
        <div className="tablewrap">
          {conns.isLoading ? (
            <Loading />
          ) : (
            <table className="table stack">
              <thead>
                <tr>
                  <th>Connection</th>
                  <th>Service</th>
                  <th>Authentication</th>
                  <th>Secret</th>
                  <th>Status</th>
                  <th className="n">Used by</th>
                  <th>Last used</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {conns.data?.length ? (
                  conns.data.map((c) => (
                    <tr key={c.id} className="click" data-id={c.id} onClick={() => setDlg({ t: 'view', c })}>
                      <td>
                        <div className="row" style={{ gap: 12 }}>
                          <span className="tile">
                            <Icon name={SERVICE_ICON[c.service] ?? 'link'} />
                          </span>
                          <div className="cellt">
                            <b>{c.name}</b>
                            <small className="mono" style={{ fontSize: 12 }}>
                              {connDetail(c)}
                            </small>
                          </div>
                        </div>
                      </td>
                      <td className="sub">{svcName(c.service)}</td>
                      <td className="sub" style={{ fontSize: 13 }}>
                        {AUTH_LABELS[c.auth_type]}
                      </td>
                      <td className="mono" style={{ fontSize: 12.5 }}>
                        {c.secret_hint ?? 'No secret'}
                      </td>
                      <td>
                        <ConnStatus s={c.status} />
                      </td>
                      <td className="n">{c.usage_count ?? 0}</td>
                      <td className="sub">{ago(c.last_used_at)}</td>
                      <td className="n">
                        <button
                          className="btn ghost icon sm"
                          aria-label={`Actions for ${c.name}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            openMenuFor(e.currentTarget, [
                              { l: 'Test connection', i: 'connect', fn: () => test.mutate(c) },
                              { l: 'Rotate secret', i: 'renew', fn: () => setDlg({ t: 'rotate', c }) },
                              { l: 'Details and usage', i: 'view', fn: () => setDlg({ t: 'view', c }) },
                              { l: 'Delete', i: 'trash-can', danger: true, fn: () => setDlg({ t: 'delete', c }) },
                            ]);
                          }}
                        >
                          <Icon name="overflow-menu--horizontal" size={18} />
                        </button>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={8}>
                      <Empty icon="password" title="No connections yet" />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {dlg?.t === 'new' && <NewConnectionDialog onClose={() => setDlg(null)} onCreated={(c) => setTimeout(() => setDlg({ t: 'view', c }), 0)} />}
      {dlg?.t === 'view' && current && <ConnectionDrawer conn={current} services={services} onClose={() => setDlg(null)} onRotate={() => setDlg({ t: 'rotate', c: current })} onDelete={() => setDlg({ t: 'delete', c: current })} />}
      {dlg?.t === 'rotate' && current && <RotateDialog conn={current} services={services} onClose={() => setDlg(null)} />}
      {dlg?.t === 'delete' && current && <DeleteDialog conn={current} onClose={() => setDlg(null)} />}
    </div>
  );
}
