/** Review workspace: editable fields with confidence on the left, document with field overlays on the right. */
import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { api } from '../../api/client';
import { qk, useApiMutation, usePipelines, useReviewItem } from '../../api/hooks';
import { Icon } from '../../ui/Icon';
import { Loading } from '../../ui/bits';
import { toast } from '../../ui/toast';
import { useCrumbs } from '../../app/crumbs';
import { FIELD_COLORS } from '../builder/catalog';
import { DocumentViewer } from '../document/DocumentViewer';

export function ReviewWorkspace() {
  const { id = '' } = useParams();
  const nav = useNavigate();
  const item = useReviewItem(id);
  const pipes = usePipelines().data ?? [];
  const r = item.data;
  useCrumbs([['Review queue', '/review'], [r?.document.filename ?? 'Document', `/review/${id}`]]);
  const [vals, setVals] = useState<Record<string, string>>({});
  useEffect(() => {
    if (r?.result?.fields) setVals(Object.fromEntries(r.result.fields.map((f) => [f.name, f.value == null ? '' : String(f.value)])));
  }, [r]);
  const decide = useApiMutation(
    (action: 'approve' | 'reject') => {
      const corrections: Record<string, unknown> = {};
      for (const f of r?.result?.fields ?? []) if ((f.value == null ? '' : String(f.value)) !== vals[f.name]) corrections[f.name] = vals[f.name];
      return api.decideReviewItem(id, { action, ...(Object.keys(corrections).length ? { corrections } : {}) });
    },
    [qk.review, qk.metrics, qk.runs],
    (res, action) => {
      toast(action === 'approve' ? `${res.document.filename} approved and sent on` : `${res.document.filename} rejected`);
      nav('/review');
    },
  );
  if (item.isLoading || !r) return <Loading label="Opening document" />;
  const fields = r.result?.fields ?? [];
  const rcp = r.document.filename.startsWith('receipt');
  const docNo = rcp ? undefined : r.document.filename.replace(/\.pdf$/, '');
  return (
    <div className="review">
      <section className="pane">
        <div className="ihead">
          <span className="tile lg">
            <Icon name="task--approved" size={18} />
          </span>
          <div className="grow">
            <h2 className="mono" style={{ fontSize: 14 }}>
              {r.document.filename}
            </h2>
            <small>{pipes.find((p) => p.id === r.pipeline_id)?.name ?? r.pipeline_id}</small>
          </div>
        </div>
        <div className="row" style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', background: 'var(--warning-soft)', color: 'var(--warning)', fontSize: 13 }}>
          <Icon name="warning--alt" />
          {r.reason}
        </div>
        <div className="pbody">
          {fields.map((f, i) => {
            const low = (f.confidence ?? 0) < 0.85;
            return (
              <div key={f.name} className={`rfield ${low ? 'low' : ''}`}>
                <div className="lab">
                  <span className="fdot" style={{ ['--fc' as string]: FIELD_COLORS[i % 10] }} />
                  <span className="mono">{f.name}</span>
                  {f.required && <span style={{ color: 'var(--danger)' }}>*</span>}
                  <span className={`badge ${low ? 'warning' : ''} num`}>{f.confidence != null ? f.confidence.toFixed(2) : '—'}</span>
                </div>
                <input className="input" value={vals[f.name] ?? ''} aria-label={f.name} onChange={(e) => setVals({ ...vals, [f.name]: e.target.value })} />
              </div>
            );
          })}
        </div>
        <div className="row" style={{ padding: '12px 16px', borderTop: '1px solid var(--border)', gap: 8 }}>
          <button className="btn sm" onClick={() => nav('/review')}>
            <Icon name="arrow--left" size={14} />
            Back
          </button>
          <div className="grow" />
          <button className="btn sm danger" disabled={decide.isPending} onClick={() => decide.mutate('reject')}>
            Reject
          </button>
          <button className="btn primary sm" disabled={decide.isPending} onClick={() => decide.mutate('approve')}>
            <Icon name="checkmark" size={14} />
            Approve and continue
          </button>
        </div>
      </section>
      <section className="canvas2" id="rv">
        <DocumentViewer kind={rcp ? 'rcp' : 'inv'} zoom={1} layers={{ fields: true }} fields={fields.map((f) => ({ name: f.name, confidence: f.confidence ?? 0 }))} threshold={0.85} docNo={docNo} />
      </section>
    </div>
  );
}
