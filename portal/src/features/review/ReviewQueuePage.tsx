/** Review queue: documents routed to a person (low confidence, failed rules, no master-data match). */
import { useNavigate } from 'react-router';
import { usePipelines, useReviewItems } from '../../api/hooks';
import { Icon } from '../../ui/Icon';
import { Empty, Loading, PageHead } from '../../ui/bits';
import { ago, initials } from '../../lib/util';
import { useCrumbs } from '../../app/crumbs';

export function ReviewQueuePage() {
  useCrumbs([['Review queue', '/review']]);
  const nav = useNavigate();
  const items = useReviewItems();
  const pipes = usePipelines().data ?? [];
  const pname = (id: string) => pipes.find((p) => p.id === id)?.name ?? id;
  return (
    <div className="page">
      <PageHead title="Review queue" desc="Documents a pipeline routed to a person: low confidence, failed rules or no master-data match." />
      <div className="card">
        <div className="tablewrap">
          {items.isLoading ? (
            <Loading />
          ) : (
            <table className="table stack">
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Pipeline</th>
                  <th>Reason</th>
                  <th>Waiting</th>
                  <th>SLA</th>
                  <th>Assignee</th>
                </tr>
              </thead>
              <tbody>
                {items.data?.length ? (
                  items.data.map((r) => {
                    const left = r.sla_due_at ? Math.round((Date.parse(r.sla_due_at) - Date.now()) / 6e4) : 999;
                    const rcp = r.document.filename.startsWith('receipt');
                    return (
                      <tr key={r.id} className="click" data-id={r.id} onClick={() => nav(`/review/${r.id}`)}>
                        <td>
                          <div className="row" style={{ gap: 12 }}>
                            <span className="tile">
                              <Icon name="document" />
                            </span>
                            <div className="cellt">
                              <b className="mono" style={{ fontSize: 13 }}>
                                {r.document.filename}
                              </b>
                              <small>{rcp ? 'Receipt' : 'Invoice'}</small>
                            </div>
                          </div>
                        </td>
                        <td className="sub">{pname(r.pipeline_id)}</td>
                        <td>
                          <span className={`badge ${r.reason_code === 'rule_failed' ? 'danger' : 'warning'}`}>{r.reason}</span>
                        </td>
                        <td className="sub">{ago(r.created_at).replace(' ago', '')}</td>
                        <td>{left < 60 ? <span className="badge danger">{Math.max(0, left)} min left</span> : <span className="badge">{Math.round(left / 60)} h left</span>}</td>
                        <td>
                          {r.assignee ? (
                            <span className="row" style={{ gap: 8 }}>
                              <span className="avatar">{initials(r.assignee)}</span>
                              {r.assignee}
                            </span>
                          ) : (
                            <span className="muted">Unassigned</span>
                          )}
                        </td>
                      </tr>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={6}>
                      <Empty icon="checkmark--filled" title="Queue is clear">
                        <p>Nothing needs review right now.</p>
                      </Empty>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
