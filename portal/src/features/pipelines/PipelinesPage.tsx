/** Pipelines list: KPIs, status filter, search, table with row menu, templates, new pipeline. */
import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router';
import { api } from '../../api/client';
import { errorMessage } from '../../api/errors';
import { qk, useApiMutation, useMetrics, usePipelines, useTemplates } from '../../api/hooks';
import type { Pipeline } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { Dialog } from '../../ui/Dialog';
import { Empty, Loading, PageHead, PipelineStatusBadge } from '../../ui/bits';
import { openMenuFor } from '../../ui/menuStore';
import { toast } from '../../ui/toast';
import { ago, fmt } from '../../lib/util';
import { useCrumbs } from '../../app/crumbs';
import { CATALOG } from '../builder/catalog';

type Filter = 'all' | 'active' | 'paused' | 'draft';

export function PipelinesPage() {
  useCrumbs([['Pipelines', '/pipelines']]);
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const pipes = usePipelines();
  const m = useMetrics().data;
  const [pf, setPf] = useState<Filter>('all');
  const [pq, setPq] = useState('');
  const [dlg, setDlg] = useState<'new' | 'templates' | { del: Pipeline } | null>(null);
  useEffect(() => {
    if (params.get('new')) {
      setDlg('new');
      setParams({}, { replace: true });
    }
  }, [params, setParams]);

  const all = pipes.data ?? [];
  const rows = all.filter((p) => (pf === 'all' || p.status === pf) && (p.name + p.owner_team).toLowerCase().includes(pq.toLowerCase()));
  const invalidate = [qk.pipelines, qk.metrics];
  const activate = useApiMutation((p: Pipeline) => (p.status === 'active' ? api.pausePipeline(p.id) : api.activatePipeline(p.id)), invalidate, (r) => toast(`${r.name} ${r.status === 'active' ? 'activated' : 'paused'}`));
  const duplicate = useApiMutation((p: Pipeline) => api.duplicatePipeline(p.id), invalidate, () => toast('Duplicated as a draft'));
  const remove = useApiMutation((p: Pipeline) => api.deletePipeline(p.id), invalidate, () => toast('Pipeline deleted'));

  const rowMenu = (el: HTMLElement, p: Pipeline) =>
    openMenuFor(el, [
      { l: 'Open in builder', i: 'flow', fn: () => nav(`/builder/${p.id}`) },
      { l: p.status === 'active' ? 'Pause pipeline' : 'Activate pipeline', i: p.status === 'active' ? 'pause' : 'play', fn: () => activate.mutate(p) },
      { l: 'Duplicate', i: 'copy', fn: () => duplicate.mutate(p) },
      { l: 'Delete', i: 'trash-can', danger: true, fn: () => setDlg({ del: p }) },
    ]);

  const pct = (v?: number) => (v == null ? '—' : (v * 100).toFixed(1) + '%');
  const kpiDelta = (v: number | undefined, unit: string, invert = false) => {
    if (v == null) return null;
    const up = v >= 0;
    return (
      <small className={invert ? (up ? 'neg' : '') : up ? '' : 'neg'}>
        <Icon name={up ? 'arrow--up' : 'arrow--down'} size={12} />
        {unit}
      </small>
    );
  };

  return (
    <div className="page">
      <PageHead
        title="Pipelines"
        desc="Document pipelines in this workspace. Each one turns incoming documents into validated, structured data."
        actions={
          <>
            <button className="btn" onClick={() => setDlg('templates')}>
              <Icon name="catalog" />
              Templates
            </button>
            <button className="btn primary" onClick={() => setDlg('new')}>
              <Icon name="add" />
              New pipeline
            </button>
          </>
        }
      />
      <div className="kpis">
        <div className="card kpi">
          <span>Documents processed · 24 h</span>
          <b className="num">{m ? fmt(m.documents ?? 0) : '—'}</b>
          {kpiDelta(m?.documents_change, `${((m?.documents_change ?? 0) * 100).toFixed(1)}% vs previous day`)}
        </div>
        <div className="card kpi">
          <span>Straight-through rate</span>
          <b className="num">{pct(m?.straight_through_rate)}</b>
          {kpiDelta(m?.straight_through_change, `${((m?.straight_through_change ?? 0) * 100).toFixed(1)} pts`)}
        </div>
        <div className="card kpi">
          <span>Waiting for review</span>
          <b className="num">{m?.review_waiting ?? '—'}</b>
          {m && (
            <small className="neg">
              <Icon name="arrow--up" size={12} />
              {m.review_near_sla ?? 0} near SLA
            </small>
          )}
        </div>
        <div className="card kpi">
          <span>Median processing time</span>
          <b className="num">{m?.median_ms != null ? (m.median_ms / 1000).toFixed(1) + ' s' : '—'}</b>
          {m?.median_change_ms != null && (
            <small className={m.median_change_ms > 0 ? 'neg' : ''}>
              <Icon name={m.median_change_ms > 0 ? 'arrow--up' : 'arrow--down'} size={12} />
              {Math.abs(m.median_change_ms / 1000).toFixed(1)} s {m.median_change_ms > 0 ? 'slower' : 'faster'}
            </small>
          )}
        </div>
      </div>
      <div className="card">
        <div className="toolbar">
          <div className="seg" role="tablist">
            {(
              [
                ['all', 'All'],
                ['active', 'Active'],
                ['paused', 'Paused'],
                ['draft', 'Draft'],
              ] as [Filter, string][]
            ).map(([k, l]) => (
              <button key={k} role="tab" aria-selected={pf === k} className={pf === k ? 'on' : ''} onClick={() => setPf(k)}>
                {l} <span className="muted num">{k === 'all' ? all.length : all.filter((p) => p.status === k).length}</span>
              </button>
            ))}
          </div>
          <div className="grow" />
          <div style={{ position: 'relative', width: 'min(280px,100%)' }}>
            <span style={{ position: 'absolute', left: 10, top: 10, color: 'var(--text-3)' }}>
              <Icon name="search" />
            </span>
            <input className="input" placeholder="Search pipelines" value={pq} onChange={(e) => setPq(e.target.value)} style={{ paddingLeft: 34 }} aria-label="Search pipelines" />
          </div>
        </div>
        <div className="tablewrap">
          {pipes.isLoading ? (
            <Loading />
          ) : (
            <table className="table stack">
              <thead>
                <tr>
                  <th>Pipeline</th>
                  <th>Status</th>
                  <th>Intake</th>
                  <th className="n">Documents · 24 h</th>
                  <th className="n">Straight-through</th>
                  <th className="n">Median time</th>
                  <th>Version</th>
                  <th>Updated</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.length ? (
                  rows.map((p) => {
                    const d = p.intake_type ? CATALOG[p.intake_type] : undefined;
                    const ai = p.ai_component_count ?? 0;
                    const k = p.kpi ?? {};
                    return (
                      <tr key={p.id} className="click" data-id={p.id} onClick={() => nav(`/builder/${p.id}`)}>
                        <td>
                          <div className="row" style={{ gap: 12 }}>
                            <span className={`tile ${ai ? 'ai' : ''}`}>
                              <Icon name="flow" />
                            </span>
                            <div className="cellt">
                              <b>{p.name}</b>
                              <small>
                                {p.owner_team} · {p.step_count ?? 0} steps{ai ? ` · ${ai} AI` : ''}
                              </small>
                            </div>
                          </div>
                        </td>
                        <td>
                          <PipelineStatusBadge status={p.status} />
                        </td>
                        <td>
                          <span className="row sub" style={{ gap: 6 }}>
                            {d && <Icon name={d.icon} size={15} />}
                            {d ? d.name : '—'}
                          </span>
                        </td>
                        <td className="n">{k.documents_24h ? fmt(k.documents_24h) : '—'}</td>
                        <td className="n">{k.straight_through_rate ? (k.straight_through_rate * 100).toFixed(1) + '%' : '—'}</td>
                        <td className="n">{k.median_ms ? (k.median_ms / 1000).toFixed(1) + ' s' : '—'}</td>
                        <td>
                          <span className="mono">{p.live_version ? `v${p.live_version}` : '—'}</span>
                          {p.draft_changes?.length ? (
                            <>
                              {' '}
                              <span className="badge info">Draft changes</span>
                            </>
                          ) : null}
                        </td>
                        <td className="sub">{ago(p.updated_at)}</td>
                        <td className="n">
                          <button
                            className="btn ghost icon sm"
                            aria-label={`Actions for ${p.name}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              rowMenu(e.currentTarget, p);
                            }}
                          >
                            <Icon name="overflow-menu--horizontal" size={18} />
                          </button>
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={9}>
                      <Empty title="No pipelines match">
                        <p>Try another filter or search term.</p>
                      </Empty>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {dlg === 'new' && <NewPipelineDialog onClose={() => setDlg(null)} />}
      {dlg === 'templates' && <TemplatesDialog onClose={() => setDlg(null)} />}
      {dlg && typeof dlg === 'object' && (
        <Dialog
          title={`Delete “${dlg.del.name}”?`}
          desc="The pipeline and its versions are deleted. Run history is kept for audit."
          okLabel="Delete pipeline"
          danger
          onOk={async () => {
            try {
              await remove.mutateAsync(dlg.del);
            } catch {
              return false;
            }
          }}
          onClose={() => setDlg(null)}
        />
      )}
    </div>
  );
}

function NewPipelineDialog({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [name, setName] = useState('Untitled pipeline');
  const [t, setT] = useState('intake.email');
  const create = useApiMutation(() => api.createPipeline({ name: name.trim() || 'Untitled pipeline', intake_type: t }), [qk.pipelines]);
  return (
    <Dialog
      title="New pipeline"
      desc="Start with an intake step. Build the rest on the canvas."
      okLabel="Create pipeline"
      busy={create.isPending}
      onOk={async () => {
        try {
          const p = await create.mutateAsync();
          nav(`/builder/${p.id}`);
        } catch {
          return false;
        }
      }}
      onClose={onClose}
    >
      <label className="field">
        <span className="lab">Name</span>
        <input className="input" name="n" value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field" style={{ margin: 0 }}>
        <span className="lab">Intake</span>
        <select className="select" name="t" value={t} onChange={(e) => setT(e.target.value)}>
          {Object.values(CATALOG)
            .filter((d) => d.stage === 'intake')
            .map((d) => (
              <option key={d.type} value={d.type}>
                {d.name}
              </option>
            ))}
        </select>
      </label>
    </Dialog>
  );
}

function TemplatesDialog({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const tpl = useTemplates();
  const inst = useApiMutation((id: string) => api.instantiateTemplate(id), [qk.pipelines]);
  return (
    <Dialog title="Templates" desc="Start from a proven pipeline. Templates are added as drafts." onClose={onClose}>
      {tpl.isLoading ? (
        <Loading />
      ) : (
        tpl.data?.map((t) => (
          <button
            key={t.id}
            className="mi"
            style={{ border: '1px solid var(--border)', marginBottom: 8 }}
            disabled={inst.isPending}
            onClick={async () => {
              try {
                const p = await inst.mutateAsync(t.id);
                onClose();
                toast('Template added as a draft');
                nav(`/builder/${p.id}`);
              } catch (e) {
                toast(errorMessage(e), true);
              }
            }}
          >
            <span className="tile ai">
              <Icon name="flow" />
            </span>
            <span>
              <b>{t.name}</b>
              <small>
                {t.step_count} steps · {t.ai_component_count ?? 0} AI components
              </small>
            </span>
          </button>
        ))
      )}
    </Dialog>
  );
}
