/** Settings: API access (portal → backend), workspace settings, API keys, webhooks, audit trail. */
import { useState, useSyncExternalStore } from 'react';
import { api } from '../../api/client';
import { errorMessage } from '../../api/errors';
import { getApiConfig, onApiConfig, setApiConfig, type ApiMode, type AuthScheme } from '../../api/config';
import { qk, useApiKeys, useApiMutation, useAudit, useSettings, useWebhooks } from '../../api/hooks';
import type { ApiKey, Principal, WorkspaceSettings } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { Dialog } from '../../ui/Dialog';
import { Badge, Loading, PageHead, Spinner } from '../../ui/bits';
import { toast } from '../../ui/toast';
import { ago } from '../../lib/util';
import { useCrumbs } from '../../app/crumbs';

const SCOPES = ['pipelines:read', 'pipelines:write', 'runs:read', 'runs:write', 'ingest:write', 'review:write', 'connections:write', 'admin'];
const EVENTS = ['run.completed', 'run.failed', 'review.created', 'review.decided', 'pipeline.published', 'connection.failing'];

const useCfg = () => useSyncExternalStore(onApiConfig, getApiConfig);

function SecretOnce({ secret, what }: { secret: string; what: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <>
      <div className="secret-once" role="alert">
        <Icon name="warning--alt" />
        <span>Copy the {what} now. It is shown once and cannot be retrieved again.</span>
      </div>
      <div className="row">
        <code className="codebox grow" data-testid="secret-once">
          {secret}
        </code>
        <button
          className="btn sm"
          onClick={() => {
            try {
              void navigator.clipboard.writeText(secret);
              setCopied(true);
            } catch {
              /* clipboard blocked */
            }
          }}
        >
          <Icon name="copy" size={14} />
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>
    </>
  );
}

function ApiAccess() {
  const cfg = useCfg();
  const [mode, setMode] = useState<ApiMode>(cfg.mode);
  const [base, setBase] = useState(cfg.baseUrl);
  const [scheme, setScheme] = useState<AuthScheme>(cfg.scheme);
  const [cred, setCred] = useState('');
  const [me, setMe] = useState<Principal | null>(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  const apply = () => {
    setApiConfig({ mode, baseUrl: base.trim(), scheme, ...(cred ? { credential: cred } : {}) });
    setCred('');
    toast(mode === 'mock' ? 'Using the in-app mock backend' : `Using the live API at ${base.trim()}`);
  };
  const test = async () => {
    apply();
    setBusy(true);
    setErr('');
    setMe(null);
    try {
      setMe(await api.me());
    } catch (e) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="card pad" style={{ marginBottom: 16 }}>
      <h2 style={{ fontSize: 16, marginBottom: 4 }}>API access</h2>
      <p className="muted" style={{ margin: '0 0 16px', fontSize: 13 }}>
        How this portal talks to the Doc Intelligence backend. The credential stays in this browser tab (memory and session storage) and is never logged.
      </p>
      <div className="field">
        <span className="lab">Backend</span>
        <div className="seg" role="radiogroup" aria-label="Backend mode">
          <button role="radio" aria-checked={mode === 'mock'} className={mode === 'mock' ? 'on' : ''} onClick={() => setMode('mock')}>
            In-app mock (sandbox)
          </button>
          <button role="radio" aria-checked={mode === 'live'} className={mode === 'live' ? 'on' : ''} onClick={() => setMode('live')}>
            Live API
          </button>
        </div>
      </div>
      <label className="field">
        <span className="lab">Orchestration API base URL</span>
        <input className="input mono" value={base} disabled={mode === 'mock'} onChange={(e) => setBase(e.target.value)} placeholder="https://api.docintel.example.com/v1" />
        <span className="hint">Default from VITE_API_BASE_URL. Must include /v1.</span>
      </label>
      <div className="grid2">
        <label className="field">
          <span className="lab">Authentication</span>
          <select className="select" value={scheme} onChange={(e) => setScheme(e.target.value as AuthScheme)}>
            <option value="bearer">Authorization: Bearer (OIDC access token)</option>
            <option value="api_key">X-API-Key (workspace API key)</option>
          </select>
        </label>
        <label className="field">
          <span className="lab">{scheme === 'api_key' ? 'API key' : 'Access token'}</span>
          <input className="input mono" type="password" autoComplete="off" value={cred} placeholder={cfg.credential ? '•••• set for this session' : 'Not set'} onChange={(e) => setCred(e.target.value)} />
        </label>
      </div>
      <div className="row">
        <button className="btn primary sm" onClick={apply}>
          Apply
        </button>
        <button className="btn sm" onClick={() => void test()} disabled={busy}>
          {busy ? <Spinner /> : <Icon name="connect" size={14} />}
          Test with GET /me
        </button>
        {cfg.credential && (
          <button
            className="btn ghost sm"
            onClick={() => {
              setApiConfig({ credential: '' });
              toast('Credential cleared');
            }}
          >
            Clear credential
          </button>
        )}
        <div className="grow" />
        <Badge tone={cfg.mode === 'live' ? 'success' : 'warning'} dot>
          {cfg.mode === 'live' ? 'Live' : 'Sandbox'}
        </Badge>
      </div>
      {me && (
        <div className="alert" style={{ marginTop: 14, background: 'var(--success-soft)', color: 'var(--success)' }} role="status">
          <Icon name="checkmark--filled" />
          <span>
            Signed in as {me.name ?? me.id} ({me.kind}, {me.role}) · workspace {me.workspace.name} · {me.workspace.environment}
          </span>
        </div>
      )}
      {err && (
        <div className="alert e" style={{ marginTop: 14 }} role="alert">
          <Icon name="warning" />
          <span>{err}</span>
        </div>
      )}
    </div>
  );
}

function Workspace() {
  const s = useSettings();
  const [form, setForm] = useState<WorkspaceSettings | null>(null);
  const v = form ?? s.data ?? {};
  const save = useApiMutation((b: WorkspaceSettings) => api.updateSettings(b), [qk.settings, qk.me], () => {
    setForm(null);
    toast('Workspace settings saved');
  });
  if (s.isLoading) return <Loading />;
  return (
    <div className="card pad" style={{ marginBottom: 16 }}>
      <h2 style={{ fontSize: 16, marginBottom: 14 }}>Workspace</h2>
      <div className="grid2">
        <label className="field">
          <span className="lab">Name</span>
          <input className="input" value={v.name ?? ''} onChange={(e) => setForm({ ...v, name: e.target.value })} />
        </label>
        <label className="field">
          <span className="lab">Environment</span>
          <select className="select" value={v.environment ?? 'sandbox'} onChange={(e) => setForm({ ...v, environment: e.target.value as 'sandbox' | 'production' })}>
            <option value="sandbox">Sandbox — sample documents, simulated workers</option>
            <option value="production">Production</option>
          </select>
        </label>
        <label className="field">
          <span className="lab">Default data region</span>
          <select className="select" value={v.default_region ?? 'UAE North'} onChange={(e) => setForm({ ...v, default_region: e.target.value })}>
            {['UAE North', 'EU West', 'US East'].map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="lab">Retention (days)</span>
          <input className="input" type="number" value={v.retention_days ?? 0} onChange={(e) => setForm({ ...v, retention_days: +e.target.value })} />
        </label>
      </div>
      <button className="btn primary sm" disabled={!form || save.isPending} onClick={() => form && save.mutate(form)}>
        Save workspace
      </button>
    </div>
  );
}

function ApiKeys() {
  const keys = useApiKeys();
  const [dlg, setDlg] = useState<'new' | { revoke: ApiKey } | null>(null);
  const [secret, setSecret] = useState<{ name: string; secret: string } | null>(null);
  const [name, setName] = useState('');
  const [scopes, setScopes] = useState<string[]>(['runs:read']);
  const [days, setDays] = useState('');
  const create = useApiMutation(() => api.createApiKey({ name: name.trim(), scopes, ...(days ? { expires_in_days: +days } : {}) }), [qk.apiKeys], (k) => {
    setSecret({ name: k.name, secret: k.secret });
    setName('');
  });
  const rotate = useApiMutation((k: ApiKey) => api.rotateApiKey(k.id), [qk.apiKeys], (k) => setSecret({ name: k.name, secret: k.secret }));
  const revoke = useApiMutation((k: ApiKey) => api.revokeApiKey(k.id), [qk.apiKeys], () => toast('API key revoked'));
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="toolbar">
        <h2 style={{ fontSize: 16 }}>API keys</h2>
        <div className="grow" />
        <button className="btn sm primary" onClick={() => setDlg('new')}>
          <Icon name="add" size={14} />
          Create API key
        </button>
      </div>
      <div className="tablewrap">
        <table className="table stack">
          <thead>
            <tr>
              <th>Key</th>
              <th>Scopes</th>
              <th>Last used</th>
              <th>Expires</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(keys.data ?? []).map((k) => (
              <tr key={k.id}>
                <td>
                  <div className="cellt">
                    <b>{k.name}</b>
                    <small className="mono">{k.prefix}…</small>
                  </div>
                </td>
                <td>
                  <div className="row" style={{ flexWrap: 'wrap', gap: 4 }}>
                    {k.scopes.map((s) => (
                      <span key={s} className="badge mono" style={{ fontSize: 11.5 }}>
                        {s}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="sub">{ago(k.last_used_at)}</td>
                <td className="sub">{k.expires_at ? new Date(k.expires_at).toLocaleDateString('en-GB') : 'Never'}</td>
                <td className="n">
                  <div className="row" style={{ justifyContent: 'flex-end' }}>
                    <button className="btn xs" onClick={() => rotate.mutate(k)}>
                      <Icon name="renew" size={13} />
                      Rotate
                    </button>
                    <button className="btn xs danger" onClick={() => setDlg({ revoke: k })}>
                      Revoke
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {dlg === 'new' && (
        <Dialog
          title="Create API key"
          desc="Workspace API keys are sent as X-API-Key by machines (ingest, reporting)."
          okLabel="Create key"
          busy={create.isPending}
          onClose={() => setDlg(null)}
          onOk={async () => {
            if (!name.trim() || !scopes.length) {
              toast('A name and at least one scope are required', true);
              return false;
            }
            return create.mutateAsync(undefined).then(
              () => undefined,
              () => false,
            );
          }}
        >
          <label className="field">
            <span className="lab">
              Name<span className="req">*</span>
            </span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. ERP ingest" />
          </label>
          <div className="field">
            <span className="lab">Scopes</span>
            <div className="row" style={{ flexWrap: 'wrap', gap: 6 }} role="group" aria-label="Scopes">
              {SCOPES.map((s) => {
                const on = scopes.includes(s);
                return (
                  <label key={s} className="badge mono" style={{ height: 30, padding: '0 10px', cursor: 'pointer', ...(on ? { background: 'var(--brand-soft)', color: 'var(--brand-ink)', borderColor: 'transparent' } : {}) }}>
                    <input type="checkbox" className="sr" checked={on} onChange={(e) => setScopes(e.target.checked ? [...scopes, s] : scopes.filter((x) => x !== s))} />
                    {s}
                  </label>
                );
              })}
            </div>
          </div>
          <label className="field" style={{ margin: 0 }}>
            <span className="lab">Expires after (days)</span>
            <input className="input" type="number" min={1} max={730} value={days} onChange={(e) => setDays(e.target.value)} placeholder="Never" />
          </label>
        </Dialog>
      )}
      {dlg && typeof dlg === 'object' && (
        <Dialog title={`Revoke “${dlg.revoke.name}”?`} desc="Requests with this key fail immediately." okLabel="Revoke key" danger onClose={() => setDlg(null)} onOk={() => revoke.mutateAsync(dlg.revoke).then(() => undefined, () => false)} />
      )}
      {secret && (
        <Dialog title={`API key · ${secret.name}`} onClose={() => setSecret(null)}>
          <SecretOnce secret={secret.secret} what="API key" />
        </Dialog>
      )}
    </div>
  );
}

function Webhooks() {
  const hooks = useWebhooks();
  const [add, setAdd] = useState(false);
  const [url, setUrl] = useState('');
  const [events, setEvents] = useState<string[]>(['run.failed']);
  const [secret, setSecret] = useState<string | null>(null);
  const create = useApiMutation(() => api.createWebhook({ url: url.trim(), events }), [qk.webhooks], (w) => {
    setSecret((w as { signing_secret?: string }).signing_secret ?? null);
    setUrl('');
  });
  const del = useApiMutation((id: string) => api.deleteWebhook(id), [qk.webhooks], () => toast('Webhook deleted'));
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="toolbar">
        <h2 style={{ fontSize: 16 }}>Webhooks</h2>
        <div className="grow" />
        <button className="btn sm" onClick={() => setAdd(true)}>
          <Icon name="add" size={14} />
          Add webhook
        </button>
      </div>
      {(hooks.data ?? []).map((w) => (
        <div className="toolrow" key={w.id} style={{ padding: '10px 16px' }}>
          <Icon name="webhook" />
          <span className="grow mono" style={{ fontSize: 13 }}>
            {w.url}
          </span>
          <span className="row" style={{ gap: 4 }}>
            {w.events.map((e) => (
              <span key={e} className="badge mono" style={{ fontSize: 11.5 }}>
                {e}
              </span>
            ))}
          </span>
          <button className="btn ghost icon xs" aria-label={`Delete webhook ${w.url}`} onClick={() => del.mutate(w.id)}>
            <Icon name="trash-can" size={14} />
          </button>
        </div>
      ))}
      {add && (
        <Dialog
          title="Add webhook"
          desc="Events are delivered signed with X-DI-Signature (HMAC-SHA256)."
          okLabel="Add webhook"
          onClose={() => setAdd(false)}
          onOk={() => create.mutateAsync(undefined).then(() => undefined, () => false)}
        >
          <label className="field">
            <span className="lab">URL</span>
            <input className="input mono" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.example.com/docintel" />
          </label>
          <div className="field" style={{ margin: 0 }}>
            <span className="lab">Events</span>
            <div className="row" style={{ flexWrap: 'wrap', gap: 6 }}>
              {EVENTS.map((s) => {
                const on = events.includes(s);
                return (
                  <label key={s} className="badge mono" style={{ height: 30, padding: '0 10px', cursor: 'pointer', ...(on ? { background: 'var(--brand-soft)', color: 'var(--brand-ink)', borderColor: 'transparent' } : {}) }}>
                    <input type="checkbox" className="sr" checked={on} onChange={(e) => setEvents(e.target.checked ? [...events, s] : events.filter((x) => x !== s))} />
                    {s}
                  </label>
                );
              })}
            </div>
          </div>
        </Dialog>
      )}
      {secret && (
        <Dialog title="Webhook signing secret" onClose={() => setSecret(null)}>
          <SecretOnce secret={secret} what="signing secret" />
        </Dialog>
      )}
    </div>
  );
}

function Audit() {
  const a = useAudit();
  const items = a.data ?? [];
  return (
    <div className="card pad">
      <h2 style={{ fontSize: 16, marginBottom: 8 }}>Recent audit events</h2>
      {items.length ? (
        items.map((e) => (
          <div className="toolrow" key={e.id}>
            <span className="mono" style={{ fontSize: 12.5 }}>
              {e.action}
            </span>
            <span className="grow muted mono" style={{ fontSize: 12 }}>
              {e.resource_id}
            </span>
            <span className="muted" style={{ fontSize: 12.5 }}>
              {e.actor.name} · {ago(e.at)}
            </span>
          </div>
        ))
      ) : (
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>
          Changes made in this session appear here.
        </p>
      )}
    </div>
  );
}

export function SettingsPage() {
  useCrumbs([['Settings', '/settings']]);
  return (
    <div className="page" style={{ maxWidth: 880 }}>
      <PageHead title="Settings" desc="Workspace and integration settings." />
      <ApiAccess />
      <Workspace />
      <ApiKeys />
      <Webhooks />
      <Audit />
    </div>
  );
}
