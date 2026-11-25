/** MCP servers: tool servers for AI steps (MCP tools component) and Master data match. */
import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router';
import { api } from '../../api/client';
import { errorMessage, isApiError } from '../../api/errors';
import { qk, useApiMutation, useConnections, useMcpServers } from '../../api/hooks';
import type { McpServer, McpTransport, UsageRef } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { Dialog } from '../../ui/Dialog';
import { Badge, Empty, Loading, PageHead, Spinner, Switch } from '../../ui/bits';
import { toast } from '../../ui/toast';
import { ago } from '../../lib/util';
import { useCrumbs } from '../../app/crumbs';
import { ConnectionSelect } from '../connections/ConnectionSelect';
import { UsageList } from '../connections/ConnectionsPage';

const TRANSPORTS: [McpTransport, string][] = [
  ['streamable_http', 'Streamable HTTP'],
  ['sse', 'Server-sent events (legacy)'],
  ['stdio_gateway', 'stdio via gateway'],
];

export const exampleArgs = (schema: unknown): string => {
  const props = (schema as { properties?: Record<string, { type?: string }> } | undefined)?.properties ?? {};
  const o: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(props)) o[k] = v.type === 'number' || v.type === 'integer' ? 0 : v.type === 'object' ? {} : '';
  return JSON.stringify(o, null, 2);
};

const headersToText = (h?: Record<string, string>) =>
  Object.entries(h ?? {})
    .map(([k, v]) => `${k}: ${v}`)
    .join('\n');
const textToHeaders = (t: string): Record<string, string> =>
  Object.fromEntries(
    t
      .split('\n')
      .map((l) => l.split(':'))
      .filter((p) => p.length >= 2 && p[0]!.trim())
      .map((p) => [p[0]!.trim(), p.slice(1).join(':').trim()]),
  );

function McpDrawer({ m, onClose }: { m: McpServer; onClose: () => void }) {
  const [form, setForm] = useState({ name: m.name, url: m.url, connection_id: m.connection_id ?? null, headers: headersToText(m.headers) });
  const [tool, setTool] = useState(m.tools?.[0]?.name ?? '');
  const [args, setArgs] = useState(exampleArgs(m.tools?.[0]?.input_schema));
  const [res, setRes] = useState<string>('');
  const [resErr, setResErr] = useState('');
  const [usages, setUsages] = useState<UsageRef[] | null>(null);
  const save = useApiMutation(() => api.updateMcpServer(m.id, { name: form.name, url: form.url, connection_id: form.connection_id, headers: textToHeaders(form.headers) }), [qk.mcp], () => toast(`${form.name} saved`));
  const discover = useApiMutation(() => api.discoverMcpTools(m.id), [qk.mcp], (r) => toast(`${r.tools?.length ?? 0} tools discovered`));
  const toggleTool = useApiMutation((t: { name: string; enabled: boolean }) => api.updateMcpServer(m.id, { tools: [t] }), [qk.mcp]);
  const call = async () => {
    setRes('');
    setResErr('');
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(args || '{}') as Record<string, unknown>;
    } catch {
      setResErr('Arguments must be valid JSON');
      return;
    }
    try {
      const r = await api.callMcpTool(m.id, tool, parsed);
      setRes(`${r.is_error ? 'Error' : 'Result'} · ${r.latency_ms ?? '?'} ms\n\n` + (r.content ?? []).map((c) => String((c as { text?: string }).text ?? JSON.stringify(c))).join('\n'));
    } catch (e) {
      setResErr(errorMessage(e));
    }
  };
  const del = async () => {
    try {
      await api.deleteMcpServer(m.id);
      toast(`${m.name} removed`);
      onClose();
    } catch (e) {
      if (isApiError(e) && e.status === 409) setUsages(e.problem.usages ?? []);
      else toast(errorMessage(e), true);
    }
  };
  return (
    <div className="scrim light" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={m.name}>
        <div className="ihead">
          <span className="tile lg">
            <Icon name="plug" size={18} />
          </span>
          <div className="grow">
            <h2>{m.name}</h2>
            <small>
              {TRANSPORTS.find((t) => t[0] === m.transport)?.[1]} · discovered {ago(m.last_discovered_at)}
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
              <span className="lab">URL</span>
              <input className="input mono" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
            </label>
            <ConnectionSelect value={form.connection_id} onChange={(v) => setForm({ ...form, connection_id: v })} services={['mcp', 'http_api']} hint="Bearer, API key header or OAuth 2.0 client credentials." />
            <label className="field">
              <span className="lab">Extra headers</span>
              <textarea className="textarea mono" rows={2} placeholder="X-Tenant: example" value={form.headers} onChange={(e) => setForm({ ...form, headers: e.target.value })} />
              <span className="hint">Non-secret headers, one per line. Put secrets in the connection.</span>
            </label>
            <h3 className="section-h">
              Tools
              <span className="grow" />
              <button className="btn xs" onClick={() => discover.mutate(undefined)} disabled={discover.isPending}>
                {discover.isPending ? <Spinner size={13} /> : <Icon name="renew" size={13} />}
                Discover tools
              </button>
            </h3>
            {(m.tools ?? []).length ? (
              m.tools!.map((t) => (
                <div className="toolrow" key={t.name}>
                  <span className="grow">
                    <b className="mono" style={{ fontWeight: 500, fontSize: 13 }}>
                      {t.name}
                    </b>
                    <br />
                    <small className="muted">{t.description}</small>
                  </span>
                  <Switch checked={t.enabled !== false} label={`Enable ${t.name}`} onChange={(v) => toggleTool.mutate({ name: t.name, enabled: v })} />
                </div>
              ))
            ) : (
              <p className="muted">No tools yet. Discover them from the server.</p>
            )}
            {(m.tools ?? []).length > 0 && (
              <>
                <h3 className="section-h">Call a tool</h3>
                <label className="field">
                  <span className="lab">Tool</span>
                  <select
                    className="select"
                    value={tool}
                    onChange={(e) => {
                      setTool(e.target.value);
                      setArgs(exampleArgs(m.tools?.find((t) => t.name === e.target.value)?.input_schema));
                    }}
                  >
                    {m.tools!.map((t) => (
                      <option key={t.name}>{t.name}</option>
                    ))}
                  </select>
                </label>
                <label className="field">
                  <span className="lab">Arguments (JSON)</span>
                  <textarea className="textarea mono" rows={4} value={args} onChange={(e) => setArgs(e.target.value)} spellCheck={false} />
                </label>
                <button className="btn sm" onClick={() => void call()}>
                  <Icon name="play" size={14} />
                  Call tool
                </button>
                {resErr && (
                  <div className="alert e" style={{ marginTop: 12 }} role="alert">
                    <Icon name="warning" />
                    <span>{resErr}</span>
                  </div>
                )}
                {res && (
                  <pre className="codebox" style={{ marginTop: 12 }}>
                    {res}
                  </pre>
                )}
              </>
            )}
            {usages && (
              <>
                <div className="alert e" style={{ marginTop: 16 }}>
                  <Icon name="warning" />
                  <span>This server is used by pipeline steps.</span>
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
          <button className="btn primary" onClick={() => save.mutate(undefined)} disabled={save.isPending}>
            Save server
          </button>
        </div>
      </aside>
    </div>
  );
}

export function McpPage() {
  useCrumbs([['MCP servers', '/tools']]);
  const mcp = useMcpServers();
  const conns = useConnections().data ?? [];
  const [params, setParams] = useSearchParams();
  const [open, setOpen] = useState<string | null>(null);
  const [add, setAdd] = useState(false);
  const [form, setForm] = useState<{ name: string; transport: McpTransport; url: string; connection_id: string | null }>({ name: '', transport: 'streamable_http', url: '', connection_id: null });
  useEffect(() => {
    if (params.get('new')) {
      setAdd(true);
      setParams({}, { replace: true });
    }
  }, [params, setParams]);
  const toggle = useApiMutation((a: { m: McpServer; on: boolean }) => api.updateMcpServer(a.m.id, { enabled: a.on }), [qk.mcp], (r) => toast(`${r.name} ${r.enabled ? 'enabled' : 'disabled'}`));
  const create = useApiMutation(() => api.createMcpServer({ name: form.name.trim(), transport: form.transport, url: form.url.trim(), connection_id: form.connection_id ?? undefined }), [qk.mcp], (m) => {
    toast(`${m.name} added. Discover its tools next.`);
    setOpen(m.id);
  });
  const current = mcp.data?.find((m) => m.id === open);
  return (
    <div className="page">
      <PageHead
        title="MCP servers"
        desc="Tool servers available to AI steps (MCP tools component) and to Master data match."
        actions={
          <button className="btn primary" onClick={() => setAdd(true)}>
            <Icon name="add" />
            Add server
          </button>
        }
      />
      <div className="card">
        <div className="tablewrap">
          {mcp.isLoading ? (
            <Loading />
          ) : (
            <table className="table stack">
              <thead>
                <tr>
                  <th>Server</th>
                  <th>Status</th>
                  <th>Tools</th>
                  <th>Connection</th>
                  <th className="n">Used by</th>
                </tr>
              </thead>
              <tbody>
                {mcp.data?.length ? (
                  mcp.data.map((m) => (
                    <tr key={m.id} className="click" data-id={m.id} onClick={() => setOpen(m.id)}>
                      <td>
                        <div className="row" style={{ gap: 12 }}>
                          <span className="tile">
                            <Icon name="plug" />
                          </span>
                          <div className="cellt">
                            <b>{m.name}</b>
                            <small className="mono">{m.url}</small>
                          </div>
                        </div>
                      </td>
                      <td onClick={(e) => e.stopPropagation()}>
                        <span className="row" style={{ gap: 8 }}>
                          <Switch checked={m.enabled} label={`Enable ${m.name}`} onChange={(on) => toggle.mutate({ m, on })} />
                          {m.enabled ? 'Enabled' : 'Disabled'}
                        </span>
                      </td>
                      <td>
                        <div className="row" style={{ flexWrap: 'wrap', gap: 4 }}>
                          {(m.tools ?? []).map((t) => (
                            <span key={t.name} className="badge mono" style={{ fontSize: 11.5, opacity: t.enabled === false ? 0.5 : 1 }}>
                              {t.name}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td className="sub">{conns.find((c) => c.id === m.connection_id)?.name ?? '—'}</td>
                      <td className="n">{m.usage_count ?? 0}</td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td colSpan={5}>
                      <Empty icon="plug" title="No MCP servers" />
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {current && <McpDrawer key={current.id + (current.last_discovered_at ?? '') + (current.tools?.map((t) => t.enabled).join() ?? '')} m={current} onClose={() => setOpen(null)} />}
      {add && (
        <Dialog
          title="Add MCP server"
          desc="Register a Model Context Protocol server. Its tools become available to AI steps."
          okLabel="Add server"
          busy={create.isPending}
          onClose={() => setAdd(false)}
          onOk={async () => {
            if (!form.name.trim() || !form.url.trim()) {
              toast('Name and URL are required', true);
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
            <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <div className="grid2">
            <label className="field">
              <span className="lab">Transport</span>
              <select className="select" value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value as McpTransport })}>
                {TRANSPORTS.map(([k, l]) => (
                  <option key={k} value={k}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="lab">
                URL<span className="req">*</span>
              </span>
              <input className="input mono" placeholder="https://mcp.internal/…" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} />
            </label>
          </div>
          <ConnectionSelect value={form.connection_id} onChange={(v) => setForm({ ...form, connection_id: v })} services={['mcp', 'http_api']} />
        </Dialog>
      )}
      <p className="muted" style={{ fontSize: 12.5, marginTop: 12 }}>
        <Badge tone="ai">AI</Badge> Tools are only exposed to AI steps whose MCP tools component lists them (or all enabled tools when none are selected).
      </p>
    </div>
  );
}
