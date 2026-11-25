/** Runs: every document processed, with the pipeline version that handled it. */
import { useState } from 'react';
import { usePipelines, useRuns } from '../../api/hooks';
import type { Run } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { Badge, Empty, Loading, PageHead, RunStatusBadge } from '../../ui/bits';
import { ago, downloadText } from '../../lib/util';
import { useCrumbs } from '../../app/crumbs';

export function toCsv(rows: Run[], pname: (id: string) => string): string {
  const q = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`;
  const head = ['run_id', 'document', 'pipeline', 'status', 'fields', 'duration_ms', 'version', 'started_at', 'test'];
  return [head.join(','), ...rows.map((r) => [r.id, r.document?.filename, pname(r.pipeline_id), r.status, r.field_count, r.duration_ms, r.pipeline_version, r.started_at, r.test].map(q).join(','))].join('\n');
}

export function RunsPage() {
  useCrumbs([['Runs', '/runs']]);
  const runs = useRuns();
  const pipes = usePipelines().data ?? [];
  const [pf, setPf] = useState('');
  const [sf, setSf] = useState('');
  const pname = (id: string) => pipes.find((p) => p.id === id)?.name ?? id;
  const rows = (runs.data ?? []).filter((r) => (!pf || r.pipeline_id === pf) && (!sf || r.status === sf));
  return (
    <div className="page">
      <PageHead
        title="Runs"
        desc="Every document processed, with the pipeline version that handled it."
        actions={
          <button className="btn" onClick={() => downloadText('runs.csv', toCsv(rows, pname))}>
            <Icon name="download" />
            Export CSV
          </button>
        }
      />
      <div className="card">
        <div className="toolbar">
          <select className="select sm" style={{ width: 'auto', minWidth: 220 }} value={pf} onChange={(e) => setPf(e.target.value)} aria-label="Filter by pipeline">
            <option value="">All pipelines</option>
            {pipes.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          <select className="select sm" style={{ width: 'auto', minWidth: 160 }} value={sf} onChange={(e) => setSf(e.target.value)} aria-label="Filter by status">
            <option value="">Any status</option>
            <option value="completed">Completed</option>
            <option value="review">In review</option>
            <option value="failed">Failed</option>
            <option value="running">Running</option>
          </select>
          <div className="grow" />
          <span className="muted num" style={{ fontSize: 13 }}>
            {rows.length} runs
          </span>
        </div>
        <div className="tablewrap">
          {runs.isLoading ? (
            <Loading />
          ) : (
            <table className="table stack">
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Document</th>
                  <th>Pipeline</th>
                  <th>Status</th>
                  <th className="n">Fields</th>
                  <th className="n">Duration</th>
                  <th>Version</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {rows.length ? (
                  rows.map((r) => (
                    <tr key={r.id} title={r.error?.detail}>
                      <td className="mono" style={{ fontSize: 13 }}>
                        {r.id}
                        {r.test && (
                          <>
                            {' '}
                            <Badge>Test</Badge>
                          </>
                        )}
                      </td>
                      <td className="mono" style={{ fontSize: 13 }}>
                        {r.document?.filename}
                      </td>
                      <td className="sub">{pname(r.pipeline_id)}</td>
                      <td>
                        <RunStatusBadge status={r.status} />
                      </td>
                      <td className="n">{r.field_count ?? 0}</td>
                      <td className="n">{r.duration_ms != null ? (r.duration_ms / 1000).toFixed(1) + ' s' : '—'}</td>
                      <td className="mono">{r.pipeline_version === 'draft' ? 'draft' : `v${r.pipeline_version ?? '—'}`}</td>
                      <td className="sub">{ago(r.started_at)}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={8}>
                      <Empty icon="recently-viewed" title="No runs">
                        <p>Runs appear here when documents are processed.</p>
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
