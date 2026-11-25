/** Models: language model providers, their connection, endpoint, region and model list. */
import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { qk, useApiMutation, useConnections, useProviders } from '../../api/hooks';
import type { ModelInfo, Provider, ProviderKind, TestResult, UsageRef } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { Dialog } from '../../ui/Dialog';
import { Badge, Empty, Loading, PageHead, Spinner, Switch } from '../../ui/bits';
import { toast } from '../../ui/toast';
import { useCrumbs } from '../../app/crumbs';
import { ConnectionSelect } from '../connections/ConnectionSelect';
import { isApiError } from '../../api/errors';
import { UsageList } from '../connections/ConnectionsPage';

const KINDS: [ProviderKind, string, string][] = [
  ['anthropic', 'Anthropic', 'https://api.anthropic.com'],
  ['openai', 'OpenAI', 'https://api.openai.com/v1'],
  ['azure_openai', 'Azure OpenAI', 'https://<resource>.openai.azure.com'],
  ['google_gemini', 'Google Gemini', 'https://generativelanguage.googleapis.com'],
  ['ollama', 'Ollama (self-hosted)', 'http://localhost:11434'],
  ['openai_compatible', 'OpenAI-compatible', 'https://llm.internal/v1'],
];

function ProviderDrawer({ p, onClose }: { p: Provider; onClose: () => void }) {
  const [form, setForm] = useState({ name: p.name, base_url: p.base_url ?? '', data_region: p.data_region ?? '', connection_id: p.connection_id ?? null, default_model: p.default_model ?? '' });
  const [models, setModels] = useState<ModelInfo[]>(p.models);
  const [testModel, setTestModel] = useState(p.default_model ?? p.models[0]?.id ?? '');
  const [res, setRes] = useState<TestResult | null>(null);
  const [usages, setUsages] = useState<UsageRef[] | null>(null);
  useEffect(() => setModels(p.models), [p.models]);
  const save = useApiMutation(() => api.updateProvider(p.id, { ...form, base_url: form.base_url || undefined, models }), [qk.providers], () => toast(`${form.name} saved`));
  const sync = useApiMutation(() => api.syncProviderModels(p.id), [qk.providers], (r) => toast(`${r.models.length} models available`));
  const test = useApiMutation(() => api.testProvider(p.id, testModel), [], (r) => setRes(r));
  const del = useApiMutation(() => api.deleteProvider(p.id), [qk.providers], () => {
    toast(`${p.name} removed`);
    onClose();
  });
  return (
    <div className="scrim light" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={p.name}>
        <div className="ihead">
          <span className={`tile lg ${p.enabled ? 'ai' : ''}`}>
            <Icon name={p.kind === 'ollama' ? 'cloud-services' : 'ai-generate'} size={18} />
          </span>
          <div className="grow">
            <h2>{p.name}</h2>
            <small>
              {KINDS.find((k) => k[0] === p.kind)?.[1]} · {p.usage_count ?? 0} components
            </small>
          </div>
          <button className="btn ghost icon sm" aria-label="Close" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        <div className="pbody">
          <div className="ibody">
            <label className="field">
              <span className="lab">Name</span>
              <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </label>
            <label className="field">
              <span className="lab">Base URL</span>
              <input className="input mono" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
            </label>
            <div className="grid2">
              <label className="field">
                <span className="lab">Data region</span>
                <input className="input" value={form.data_region} onChange={(e) => setForm({ ...form, data_region: e.target.value })} />
              </label>
              <label className="field">
                <span className="lab">Default model</span>
                <select className="select" value={form.default_model} onChange={(e) => setForm({ ...form, default_model: e.target.value })}>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.display_name ?? m.id}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <ConnectionSelect value={form.connection_id} onChange={(v) => setForm({ ...form, connection_id: v })} services={[p.kind]} hint={`Only ${KINDS.find((k) => k[0] === p.kind)?.[1]} connections are listed.`} />
            <h3 className="section-h">
              Models
              <span className="grow" />
              <button className="btn xs" onClick={() => sync.mutate(undefined)} disabled={sync.isPending}>
                {sync.isPending ? <Spinner size={13} /> : <Icon name="renew" size={13} />}
                Sync from provider
              </button>
            </h3>
            {models.map((m, i) => (
              <div className="toolrow" key={m.id}>
                <span className="grow">
                  <b className="mono" style={{ fontWeight: 500, fontSize: 13 }}>
                    {m.display_name ?? m.id}
                  </b>
                  <br />
                  <small className="muted">
                    {m.context_window ? `${Math.round(m.context_window / 1000)}k context` : 'context unknown'}
                    {m.supports_tools ? ' · tools' : ''}
                    {m.supports_vision ? ' · vision' : ''}
                    {m.embedding ? ' · embedding' : ''}
                  </small>
                </span>
                <Switch checked={m.enabled !== false} label={`Enable ${m.id}`} onChange={(v) => setModels(models.map((x, j) => (j === i ? { ...x, enabled: v } : x)))} />
              </div>
            ))}
            <h3 className="section-h">Test</h3>
            <div className="row">
              <select className="select sm" value={testModel} onChange={(e) => setTestModel(e.target.value)} aria-label="Model to test">
                {models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.display_name ?? m.id}
                  </option>
                ))}
              </select>
              <button className="btn sm" onClick={() => test.mutate(undefined)} disabled={test.isPending}>
                {test.isPending ? <Spinner /> : <Icon name="play" size={14} />}
                Send test prompt
              </button>
            </div>
            {res && (
              <div className={`alert ${res.ok ? '' : 'e'}`} style={{ marginTop: 12, ...(res.ok ? { background: 'var(--success-soft)', color: 'var(--success)' } : {}) }} role="status">
                <Icon name={res.ok ? 'checkmark--filled' : 'warning'} />
                <span>
                  {res.message} · {res.latency_ms} ms
                </span>
              </div>
            )}
            {usages && (
              <>
                <div className="alert e" style={{ marginTop: 16 }}>
                  <Icon name="warning" />
                  <span>This provider is used by language model components. Change them before deleting it.</span>
                </div>
                <UsageList usages={usages} />
              </>
            )}
          </div>
        </div>
        <div className="df">
          <button
            className="btn danger"
            onClick={() =>
              del.mutateAsync(undefined).catch((e: unknown) => {
                if (isApiError(e) && e.status === 409) setUsages(e.problem.usages ?? []);
              })
            }
          >
            Delete
          </button>
          <div className="grow" />
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate(undefined)}>
            Save provider
          </button>
        </div>
      </aside>
    </div>
  );
}

function AddProviderDialog({ onClose }: { onClose: () => void }) {
  const [kind, setKind] = useState<ProviderKind>('openai_compatible');
  const [name, setName] = useState('');
  const [base, setBase] = useState('');
  const [region, setRegion] = useState('Global');
  const [conn, setConn] = useState<string | null>(null);
  const [models, setModels] = useState('');
  const create = useApiMutation(
    () =>
      api.createProvider({
        kind,
        name: name.trim() || KINDS.find((k) => k[0] === kind)![1],
        base_url: base || undefined,
        data_region: region,
        connection_id: conn ?? undefined,
        models: models
          .split(',')
          .map((s) => s.trim())
          .filter(Boolean)
          .map((id) => ({ id, enabled: true })),
      }),
    [qk.providers],
    (p) => toast(`${p.name} added`),
  );
  return (
    <Dialog title="Add provider" desc="Language model providers are available to AI steps through the Language model component." okLabel="Add provider" busy={create.isPending} onOk={() => create.mutateAsync(undefined).then(() => undefined, () => false)} onClose={onClose}>
      <div className="grid2">
        <label className="field">
          <span className="lab">Kind</span>
          <select className="select" value={kind} onChange={(e) => setKind(e.target.value as ProviderKind)}>
            {KINDS.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span className="lab">Name</span>
          <input className="input" value={name} placeholder={KINDS.find((k) => k[0] === kind)?.[1]} onChange={(e) => setName(e.target.value)} />
        </label>
      </div>
      <label className="field">
        <span className="lab">Base URL</span>
        <input className="input mono" value={base} placeholder={KINDS.find((k) => k[0] === kind)?.[2]} onChange={(e) => setBase(e.target.value)} />
      </label>
      <div className="grid2">
        <label className="field">
          <span className="lab">Data region</span>
          <input className="input" value={region} onChange={(e) => setRegion(e.target.value)} />
        </label>
        <label className="field">
          <span className="lab">Models</span>
          <input className="input mono" value={models} placeholder="model-a, model-b" onChange={(e) => setModels(e.target.value)} />
          <span className="hint">Or sync them after adding.</span>
        </label>
      </div>
      <ConnectionSelect value={conn} onChange={setConn} services={[kind]} />
    </Dialog>
  );
}

export function ModelsPage() {
  useCrumbs([['Models', '/models']]);
  const prov = useProviders();
  const conns = useConnections().data ?? [];
  const [open, setOpen] = useState<string | null>(null);
  const [add, setAdd] = useState(false);
  const toggle = useApiMutation((a: { p: Provider; on: boolean }) => api.updateProvider(a.p.id, { enabled: a.on }), [qk.providers], (r) => toast(`${r.name} ${r.enabled ? 'enabled' : 'disabled'}`));
  const current = prov.data?.find((p) => p.id === open);
  return (
    <div className="page">
      <PageHead
        title="Models"
        desc="Language model providers available to AI steps through the Language model component. Each uses a connection from the vault."
        actions={
          <button className="btn primary" onClick={() => setAdd(true)}>
            <Icon name="add" />
            Add provider
          </button>
        }
      />
      <div className="card">
        <div className="tablewrap">
          {prov.isLoading ? (
            <Loading />
          ) : (
            <table className="table stack">
              <thead>
                <tr>
                  <th>Provider</th>
                  <th>Status</th>
                  <th>Models</th>
                  <th>Connection</th>
                  <th>Data region</th>
                  <th className="n">Used by</th>
                </tr>
              </thead>
              <tbody>
                {prov.data?.length ? (
                  prov.data.map((p) => (
                    <tr key={p.id} className="click" data-id={p.id} onClick={() => setOpen(p.id)}>
                      <td>
                        <div className="row" style={{ gap: 12 }}>
                          <span className={`tile ${p.enabled ? 'ai' : ''}`}>
                            <Icon name={p.kind === 'ollama' ? 'cloud-services' : 'ai-generate'} />
                          </span>
                          <div className="cellt">
                            <b>{p.name}</b>
                            <small className="mono" style={{ fontSize: 12 }}>
                              {p.base_url}
                            </small>
                          </div>
                        </div>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <span className="row" style={{ gap: 8 }}>
                          <Switch checked={p.enabled} label={`Enable ${p.name}`} onChange={(on) => toggle.mutate({ p, on })} />
                          {p.enabled ? 'Enabled' : 'Disabled'}
                        </span>
                      </td>
                      <td>
                        <div className="row" style={{ flexWrap: 'wrap', gap: 4 }}>
                          {p.models.map((m) => (
                            <span key={m.id} className="badge mono" style={{ fontSize: 11.5 }}>
                              {m.display_name ?? m.id}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="sub">{conns.find((c) => c.id === p.connection_id)?.name ?? 'Not connected'}</td>
                      <td>{p.data_region === 'Global' ? <Badge>{p.data_region}</Badge> : <Badge tone="success">{p.data_region}</Badge>}</td>
                      <td className="n">{p.usage_count ?? 0} components</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={6}>
                      <Empty icon="machine-learning-model" title="No providers" />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {current && <ProviderDrawer key={current.id} p={current} onClose={() => setOpen(null)} />}
      {add && <AddProviderDialog onClose={() => setAdd(false)} />}
    </div>
  );
}
