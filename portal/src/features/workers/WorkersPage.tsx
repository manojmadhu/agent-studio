/** Workers: document and image processing services (OCR, layout, spreadsheet …) the pipelines call. */
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import { api } from '../../api/client';
import { errorMessage, isApiError } from '../../api/errors';
import { qk, useApiMutation, useConnections, useWorkers } from '../../api/hooks';
import type { PagesResult, UsageRef, Worker, WorkerAdapter, WorkerKind } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { Dialog } from '../../ui/Dialog';
import { Badge, Empty, Loading, PageHead, Spinner } from '../../ui/bits';
import { toast } from '../../ui/toast';
import { useCrumbs } from '../../app/crumbs';
import { ConnectionSelect } from '../connections/ConnectionSelect';
import { UsageList } from '../connections/ConnectionsPage';
import { SAMPLE_DOC_OPTIONS } from '../builder/runStore';

export const ADAPTERS: Record<WorkerAdapter, { label: string; invoke: string; health: string; desc: string }> = {
  generic_pages_v1: { label: 'Generic pages (docintel.pages.v1)', invoke: '/invoke', health: '/health', desc: 'POST multipart file; the worker returns PagesResult directly.' },
  excel_parser_v1: { label: 'Excel Parser Service', invoke: '/to-text', health: '/health', desc: 'POST multipart file to /to-text; {text_repr, sheet_names, total_cells, char_count} → one text block per sheet.' },
  paddle_docparse_v1: { label: 'Paddle DocParse', invoke: '/parse', health: '/health', desc: 'Paddle DocParse response mapped to blocks, tables and bounding boxes (routes to confirm).' },
};
const KINDS: WorkerKind[] = ['ocr', 'layout', 'image', 'spreadsheet', 'classifier', 'custom'];
const KIND_LABEL: Record<WorkerKind, string> = { ocr: 'OCR and layout', layout: 'Layout', image: 'Image processing', spreadsheet: 'Spreadsheet reader', classifier: 'Classifier', custom: 'Custom' };

export function HealthBadge({ h }: { h: Worker['health'] }) {
  if (h?.status === 'healthy') return <Badge tone="success" dot>Healthy · {h.latency_ms} ms</Badge>;
  if (h?.status === 'degraded') return <Badge tone="warning" dot>Degraded</Badge>;
  if (h?.status === 'unreachable') return <Badge tone="danger" dot>Unreachable</Badge>;
  return <Badge dot>Not checked</Badge>;
}

function WorkerForm({ value, onChange }: { value: Partial<Worker>; onChange: (w: Partial<Worker>) => void }) {
  const ad = value.adapter ? ADAPTERS[value.adapter] : undefined;
  return (
    <>
      <div className="grid2">
        <label className="field">
          <span className="lab">
            Name<span className="req">*</span>
          </span>
          <input className="input" value={value.name ?? ''} onChange={(e) => onChange({ ...value, name: e.target.value })} />
        </label>
        <label className="field">
          <span className="lab">Kind</span>
          <select className="select" value={value.kind ?? 'ocr'} onChange={(e) => onChange({ ...value, kind: e.target.value as WorkerKind })}>
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="field">
        <span className="lab">
          Base URL<span className="req">*</span>
        </span>
        <input className="input mono" placeholder="http://localhost:8000" value={value.base_url ?? ''} onChange={(e) => onChange({ ...value, base_url: e.target.value })} />
      </label>
      <label className="field">
        <span className="lab">Adapter</span>
        <select
          className="select"
          value={value.adapter ?? 'generic_pages_v1'}
          onChange={(e) => {
            const a = e.target.value as WorkerAdapter;
            onChange({ ...value, adapter: a, invoke_path: ADAPTERS[a].invoke, health_path: ADAPTERS[a].health });
          }}
        >
          {Object.entries(ADAPTERS).map(([k, v]) => (
            <option key={k} value={k}>
              {v.label} · {k}
            </option>
          ))}
        </select>
        {ad && <span className="hint">{ad.desc}</span>}
      </label>
      <div className="grid2">
        <label className="field">
          <span className="lab">Invoke path</span>
          <input className="input mono" value={value.invoke_path ?? ''} placeholder={ad?.invoke} onChange={(e) => onChange({ ...value, invoke_path: e.target.value })} />
        </label>
        <label className="field">
          <span className="lab">Health path</span>
          <input className="input mono" value={value.health_path ?? ''} placeholder={ad?.health} onChange={(e) => onChange({ ...value, health_path: e.target.value })} />
        </label>
        <label className="field">
          <span className="lab">Timeout (seconds)</span>
          <input className="input" type="number" min={1} value={value.timeout_s ?? 60} onChange={(e) => onChange({ ...value, timeout_s: +e.target.value })} />
        </label>
        <label className="field">
          <span className="lab">Max concurrency</span>
          <input className="input" type="number" min={1} value={value.max_concurrency ?? 4} onChange={(e) => onChange({ ...value, max_concurrency: +e.target.value })} />
        </label>
      </div>
      <ConnectionSelect value={value.connection_id} onChange={(v) => onChange({ ...value, connection_id: v })} services={['document_worker', 'http_api']} hint="Used for every call to the worker (API key, bearer, mTLS …)." />
    </>
  );
}

function PagesSummary({ r }: { r: PagesResult }) {
  const blocks = r.pages.reduce((a, p) => a + (p.blocks?.length ?? 0), 0);
  const tables = r.pages.reduce((a, p) => a + (p.tables?.length ?? 0), 0);
  return (
    <>
      <p className="row" style={{ gap: 6, flexWrap: 'wrap', margin: '12px 0 8px' }}>
        <Badge tone="success">{r.pages.length} pages</Badge>
        <Badge>{blocks} text blocks</Badge>
        <Badge>{tables} tables</Badge>
        {r.worker_latency_ms != null && <Badge>{r.worker_latency_ms} ms</Badge>}
      </p>
      <pre className="codebox">{JSON.stringify(r, null, 2)}</pre>
    </>
  );
}

function WorkerDrawer({ w, onClose }: { w: Worker; onClose: () => void }) {
  const [form, setForm] = useState<Partial<Worker>>(w);
  const [doc, setDoc] = useState(SAMPLE_DOC_OPTIONS[0]!.id);
  const [out, setOut] = useState<PagesResult | null>(null);
  const [invErr, setInvErr] = useState('');
  const [usages, setUsages] = useState<UsageRef[] | null>(null);
  const save = useApiMutation(
    () => api.updateWorker(w.id, { name: form.name, base_url: form.base_url, adapter: form.adapter, invoke_path: form.invoke_path, health_path: form.health_path, connection_id: form.connection_id ?? null, timeout_s: form.timeout_s, max_concurrency: form.max_concurrency }),
    [qk.workers],
    () => toast(`${form.name} saved`),
  );
  const health = useApiMutation(() => api.checkWorkerHealth(w.id), [qk.workers], (h) => toast(h.status === 'healthy' ? `${w.name} is healthy` : `${w.name}: ${h.message ?? h.status}`, h.status !== 'healthy'));
  const invoke = async () => {
    setInvErr('');
    setOut(null);
    try {
      setOut(await api.invokeWorker(w.id, doc));
    } catch (e) {
      setInvErr(errorMessage(e));
    }
  };
  const del = async () => {
    try {
      await api.deleteWorker(w.id);
      toast(`${w.name} removed`);
      onClose();
    } catch (e) {
      if (isApiError(e) && e.status === 409) setUsages(e.problem.usages ?? []);
      else toast(errorMessage(e), true);
    }
  };
  return (
    <div className="scrim light" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={w.name}>
        <div className="ihead">
          <span className="tile lg">
            <Icon name="chip" size={18} />
          </span>
          <div className="grow">
            <h2>{w.name}</h2>
            <small>
              {KIND_LABEL[w.kind]} · <span className="mono">{w.adapter}</span>
            </small>
          </div>
          <button className="btn ghost icon sm" aria-label="Close" onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        <div className="pbody">
          <div className="ibody">
            <WorkerForm value={form} onChange={setForm} />
            <h3 className="section-h">Health</h3>
            <div className="row">
              <HealthBadge h={w.health} />
              <span className="muted" style={{ fontSize: 12.5 }}>
                {w.health?.message}
              </span>
              <div className="grow" />
              <button className="btn sm" onClick={() => health.mutate(undefined)} disabled={health.isPending}>
                {health.isPending ? <Spinner /> : <Icon name="activity" size={14} />}
                Check health
              </button>
            </div>
            <h3 className="section-h">Test invoke</h3>
            <div className="row">
              <select className="select sm" value={doc} onChange={(e) => setDoc(e.target.value)} aria-label="Sample document">
                {SAMPLE_DOC_OPTIONS.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </select>
              <button className="btn sm" onClick={() => void invoke()}>
                <Icon name="play" size={14} />
                Invoke
              </button>
            </div>
            <span className="hint">
              The backend sends the document to <span className="mono">{(form.base_url ?? '') + (form.invoke_path ?? '')}</span> and normalises the response through the adapter into PagesResult.
            </span>
            {invErr && (
              <div className="alert e" style={{ marginTop: 12 }} role="alert">
                <Icon name="warning" />
                <span>{invErr}</span>
              </div>
            )}
            {out && <PagesSummary r={out} />}
            {usages && (
              <>
                <div className="alert e" style={{ marginTop: 16 }}>
                  <Icon name="warning" />
                  <span>This worker is used by pipeline steps. Change them before removing it.</span>
                </div>
                <UsageList usages={usages} />
              </>
            )}
          </div>
        </div>
        <div className="df">
          <button className="btn danger" onClick={() => void del()}>
            Remove
          </button>
          <div className="grow" />
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
          <button className="btn primary" disabled={save.isPending} onClick={() => save.mutate(undefined)}>
            Save worker
          </button>
        </div>
      </aside>
    </div>
  );
}

export function WorkersPage() {
  useCrumbs([['Workers', '/workers']]);
  const workers = useWorkers();
  const conns = useConnections().data ?? [];
  const [params, setParams] = useSearchParams();
  const [open, setOpen] = useState<string | null>(null);
  const [add, setAdd] = useState(false);
  const [form, setForm] = useState<Partial<Worker>>({ kind: 'ocr', adapter: 'generic_pages_v1', invoke_path: '/invoke', health_path: '/health', timeout_s: 60, max_concurrency: 4 });
  useEffect(() => {
    if (params.get('new')) {
      setAdd(true);
      setParams({}, { replace: true });
    }
  }, [params, setParams]);
  const health = useApiMutation((w: Worker) => api.checkWorkerHealth(w.id), [qk.workers], (h, w) => toast(h.status === 'healthy' ? `${w.name} is healthy (${h.latency_ms} ms)` : `${w.name} did not respond`, h.status !== 'healthy'));
  const create = useApiMutation(
    () =>
      api.registerWorker({
        name: form.name ?? '',
        kind: form.kind ?? 'ocr',
        base_url: form.base_url ?? '',
        adapter: form.adapter ?? 'generic_pages_v1',
        invoke_path: form.invoke_path,
        health_path: form.health_path,
        connection_id: form.connection_id ?? undefined,
        timeout_s: form.timeout_s,
        max_concurrency: form.max_concurrency,
      }),
    [qk.workers],
    (w) => toast(`${w.name} registered`),
  );
  const current = workers.data?.find((w) => w.id === open);
  return (
    <div className="page">
      <PageHead
        title="Workers"
        desc="Document and image processing services the pipelines call. Each worker has an adapter that normalises its output to PagesResult."
        actions={
          <button className="btn primary" onClick={() => setAdd(true)}>
            <Icon name="add" />
            Register worker
          </button>
        }
      />
      <div className="card">
        <div className="tablewrap">
          {workers.isLoading ? (
            <Loading />
          ) : (
            <table className="table stack">
              <thead>
                <tr>
                  <th>Worker</th>
                  <th>Endpoint</th>
                  <th>Adapter</th>
                  <th>Routes</th>
                  <th>Connection</th>
                  <th>Health</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {workers.data?.length ? (
                  workers.data.map((w) => (
                    <tr key={w.id} className="click" data-id={w.id} onClick={() => setOpen(w.id)}>
                      <td>
                        <div className="row" style={{ gap: 12 }}>
                          <span className="tile">
                            <Icon name="chip" />
                          </span>
                          <div className="cellt">
                            <b>{w.name}</b>
                            <small>{KIND_LABEL[w.kind]}</small>
                          </div>
                        </div>
                      </td>
                      <td className="mono" style={{ fontSize: 12.5 }}>
                        {w.base_url}
                      </td>
                      <td className="mono" style={{ fontSize: 12.5 }}>
                        {w.adapter}
                      </td>
                      <td className="sub mono" style={{ fontSize: 12.5 }}>
                        POST {w.invoke_path} · GET {w.health_path}
                      </td>
                      <td className="sub">{conns.find((c) => c.id === w.connection_id)?.name ?? '—'}</td>
                      <td>
                        <HealthBadge h={w.health} />
                      </td>
                      <td className="n">
                        <button
                          className="btn sm"
                          disabled={health.isPending && health.variables?.id === w.id}
                          onClick={(e) => {
                            e.stopPropagation();
                            health.mutate(w);
                          }}
                        >
                          {health.isPending && health.variables?.id === w.id ? (
                            <>
                              <Spinner />
                              Checking
                            </>
                          ) : (
                            'Check health'
                          )}
                        </button>
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={7}>
                      <Empty icon="chip" title="No workers registered" />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {current && <WorkerDrawer key={current.id + (current.health?.checked_at ?? '')} w={current} onClose={() => setOpen(null)} />}
      {add && (
        <Dialog
          title="Register worker"
          desc="Connect a document or image processing service. The backend calls it with the selected connection’s credentials."
          okLabel="Register worker"
          wide
          busy={create.isPending}
          onClose={() => setAdd(false)}
          onOk={async () => {
            if (!form.name?.trim() || !form.base_url?.trim()) {
              toast('Name and base URL are required', true);
              return false;
            }
            return create.mutateAsync(undefined).then(
              () => undefined,
              () => false,
            );
          }}
        >
          <WorkerForm value={form} onChange={setForm} />
        </Dialog>
      )}
    </div>
  );
}
