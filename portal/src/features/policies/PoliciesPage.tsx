/** Policies: workspace guardrails applied to every run, on top of validate steps in each pipeline. */
import { api } from '../../api/client';
import { qk, useApiMutation, usePipelines, usePolicies } from '../../api/hooks';
import type { Policy } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { Badge, Empty, Loading, PageHead, Switch } from '../../ui/bits';
import { toast } from '../../ui/toast';
import { useCrumbs } from '../../app/crumbs';

const KIND_LABEL: Record<Policy['kind'], string> = {
  pii_redaction: 'PII redaction',
  prompt_injection: 'Prompt injection',
  schema_enforcement: 'Schema enforcement',
  data_residency: 'Data residency',
  retention: 'Retention',
  model_allowlist: 'Model allow-list',
  custom: 'Custom',
};

export function PoliciesPage() {
  useCrumbs([['Policies', '/policies']]);
  const pol = usePolicies();
  const pipes = usePipelines().data ?? [];
  const toggle = useApiMutation((a: { p: Policy; on: boolean }) => api.updatePolicy(a.p.id, { enabled: a.on }), [qk.policies], (r) => toast(`Policy ${r.enabled ? 'enforced' : 'paused'}: ${r.name}`));
  const scope = (p: Policy) => {
    const s = p.scope ?? {};
    if (s.all_pipelines) return 'All pipelines';
    if (s.owner_teams?.length) return s.owner_teams.join(', ') + ' pipelines';
    if (s.pipeline_ids?.length) return s.pipeline_ids.map((id) => pipes.find((x) => x.id === id)?.name ?? id).join(', ');
    return '—';
  };
  return (
    <div className="page">
      <PageHead title="Policies" desc="Workspace rules applied to every run, on top of validate steps in each pipeline." />
      <div className="card">
        <div className="tablewrap">
          {pol.isLoading ? (
            <Loading />
          ) : (
            <table className="table stack">
              <thead>
                <tr>
                  <th>Policy</th>
                  <th>Kind</th>
                  <th>Scope</th>
                  <th>Enforced</th>
                </tr>
              </thead>
              <tbody>
                {pol.data?.length ? (
                  pol.data.map((g) => (
                    <tr key={g.id}>
                      <td>
                        <div className="row" style={{ gap: 12 }}>
                          <span className="tile">
                            <Icon name="security" />
                          </span>
                          <div className="cellt">
                            <b>{g.name}</b>
                            <small>{g.description}</small>
                          </div>
                        </div>
                      </td>
                      <td>
                        <Badge>{KIND_LABEL[g.kind]}</Badge>
                      </td>
                      <td className="sub">{scope(g)}</td>
                      <td>
                        <Switch checked={g.enabled} label={`Enforce ${g.name}`} onChange={(on) => toggle.mutate({ p: g, on })} />
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={4}>
                      <Empty icon="security" title="No policies" />
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
