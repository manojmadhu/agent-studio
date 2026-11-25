/** Pipeline builder: header · palette | canvas + run panel | configure. */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router';
import { ReactFlowProvider } from '@xyflow/react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { errorMessage, isApiError } from '../../api/errors';
import { qk, summaryCtx, useApiMutation, usePipeline, useVersions, useWorkspaceRefs, validationCtx } from '../../api/hooks';
import type { Pipeline, SlotName } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { Dialog } from '../../ui/Dialog';
import { Loading, Spinner } from '../../ui/bits';
import { openMenuAt } from '../../ui/menuStore';
import { toast } from '../../ui/toast';
import { ago } from '../../lib/util';
import { useCrumbs } from '../../app/crumbs';
import { SLOTS } from './catalog';
import { deserializeGraph, isSub } from './graph';
import { issues as computeIssues, errorCount } from './validate';
import { draftChanges, useEditor } from './store';
import { useRun } from './runStore';
import { saveNow, useAutosave } from './autosave';
import { Canvas, canvasApi, slotMenuItems, stepMenuItems } from './Canvas';
import { Palette } from './Palette';
import { Inspector } from './Inspector';
import { RunPanel } from './RunPanel';
import { BuilderActionsCtx, type BuilderActions } from './actions';
import { onOutsidePointer, usePanels } from './panels';


export function BuilderPage() {
  const { id = '' } = useParams();
  const pipeline = usePipeline(id);
  const draft = useQuery({ queryKey: qk.draft(id), queryFn: () => api.getDraft(id), staleTime: Infinity, gcTime: 0, enabled: !!id, retry: false });
  const loadedFor = useEditor((s) => s.pipelineId);
  const nav = useNavigate();
  useCrumbs([['Pipelines', '/pipelines'], [pipeline.data?.name ?? 'Pipeline', `/builder/${id}`]]);

  useEffect(() => {
    if (draft.data && useEditor.getState().pipelineId !== id) {
      useEditor.getState().load(id, deserializeGraph(draft.data.data.graph), draft.data.etag, draft.data.data.changes ?? []);
      useRun.getState().reset(false);
    }
  }, [draft.data, id]);

  if (pipeline.error || draft.error) {
    return (
      <div className="page">
        <div className="empty">
          <Icon name="warning" size={24} />
          <h3>Pipeline could not be opened</h3>
          <p>{errorMessage(pipeline.error ?? draft.error)}</p>
          <button className="btn" onClick={() => nav('/pipelines')}>
            Back to pipelines
          </button>
        </div>
      </div>
    );
  }
  if (!pipeline.data || !draft.data || loadedFor !== id) return <Loading label="Opening pipeline" />;
  return (
    <ReactFlowProvider>
      <Builder pipeline={pipeline.data} />
    </ReactFlowProvider>
  );
}

function Builder({ pipeline }: { pipeline: Pipeline }) {
  const qc = useQueryClient();
  const refs = useWorkspaceRefs();
  const nodes = useEditor((s) => s.nodes);
  const edges = useEditor((s) => s.edges);
  const pending = useEditor((s) => s.pending);
  const serverChanges = useEditor((s) => s.serverChanges);
  const saving = useEditor((s) => s.saving);
  const conflict = useEditor((s) => s.conflict);
  const running = useRun((s) => s.running);
  const pipeSettings = usePanels((s) => s.pipelineSettings);

  // side panels: hidden by default, close on an outside press or Escape
  useEffect(() => {
    usePanels.setState({ palette: false, pipelineSettings: false });
    const onEsc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || document.querySelector('.scrim,.menu')) return;
      const p = usePanels.getState();
      if (p.palette) p.setPalette(false);
      else p.closeConfigure();
    };
    document.addEventListener('pointerdown', onOutsidePointer, true);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('pointerdown', onOutsidePointer, true);
      document.removeEventListener('keydown', onEsc);
      usePanels.setState({ palette: false, pipelineSettings: false });
    };
  }, []);
  const flush = useAutosave();
  const [dlg, setDlg] = useState<'versions' | 'publish' | null>(null);
  const g = useMemo(() => ({ nodes, edges }), [nodes, edges]);
  const vctx = useMemo(() => validationCtx(refs), [refs]);
  const sumCtx = useMemo(() => summaryCtx(refs), [refs]);
  const refsReady = refs.ready;
  const iss = useMemo(() => (refsReady ? computeIssues(g, vctx) : []), [g, vctx, refsReady]);
  const errs = errorCount(iss);
  const bad = useMemo(() => new Set(iss.filter((i) => i.level === 'error' && i.node_id).map((i) => i.node_id!)), [iss]);
  const changes = draftChanges({ serverChanges, pending });
  const live = pipeline.live_version ?? 0;

  const invalidatePipeline = useCallback(() => {
    void qc.invalidateQueries({ queryKey: qk.pipeline(pipeline.id) });
    void qc.invalidateQueries({ queryKey: qk.pipelines });
  }, [qc, pipeline.id]);

  const validate = useCallback(() => {
    useRun.getState().setTab('problems');
    toast(iss.length ? `${iss.length} problem${iss.length > 1 ? 's' : ''} found` : 'No problems found', iss.some((i) => i.level === 'error'));
  }, [iss]);

  const runTest = useCallback(() => {
    if (errs) {
      useRun.getState().setTab('problems');
      toast(`Resolve ${errs} issue${errs > 1 ? 's' : ''} before testing`, true);
      return;
    }
    void useRun.getState().start(pipeline.id, useEditor.getState().graph(), {
      beforeStart: async () => {
        await flush();
      },
      onFinished: () => void qc.invalidateQueries({ queryKey: qk.runs }),
    });
  }, [errs, flush, pipeline.id, qc]);

  const testNode = useCallback(
    async (nodeId: string) => {
      const n = useEditor.getState().nodes.find((x) => x.id === nodeId);
      if (!n) return;
      try {
        await flush();
        const docId = useRun.getState().docId;
        const r = await api.testStep(pipeline.id, nodeId, docId);
        useRun.getState().setOutput(nodeId, isSub(n) ? { component: n.name, config: n.config } : r.output ?? {});
        useEditor.getState().select({ node: nodeId }, 'output');
        toast(`Tested “${n.name}” on ${docId === 'doc_rcp_sample' ? 'receipt_0918.jpg' : 'INV-2026-0417.pdf'}`);
      } catch (e) {
        toast(errorMessage(e), true);
      }
    },
    [flush, pipeline.id],
  );

  const slotMenu = useCallback((hostId: string, slot: SlotName, x: number, y: number, pos?: [number, number]) => {
    openMenuAt(x, y, slotMenuItems(slot, (t) => useEditor.getState().addSub(hostId, slot, t, pos)), { search: false, title: 'Add ' + SLOTS[slot].label.toLowerCase() });
  }, []);
  const portMenu = useCallback((fromId: string, port: string, x: number, y: number, pos?: [number, number]) => {
    openMenuAt(x, y, stepMenuItems((t) => useEditor.getState().addConnected(fromId, port, t, pos), true), { title: 'Connect a new step' });
  }, []);
  const configure = useCallback((nodeId: string) => {
    useEditor.getState().select({ node: nodeId }, 'configure');
    requestAnimationFrame(() => document.querySelector<HTMLElement>('#insp [data-k]')?.focus());
  }, []);
  const goto = useCallback((nodeId: string) => {
    useEditor.getState().select({ node: nodeId }, 'configure');
    canvasApi.current?.centerOn(nodeId);
  }, []);
  const actions: BuilderActions = useMemo(() => ({ testNode: (i) => void testNode(i), configure, slotMenu, portMenu }), [testNode, configure, slotMenu, portMenu]);

  // keyboard (prototype bindings)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.matches('input,textarea,select,[contenteditable]') || document.querySelector('.scrim')) return;
      const s = useEditor.getState();
      const mod = e.ctrlKey || e.metaKey;
      if ((e.key === 'Delete' || e.key === 'Backspace') && (s.sel.node || s.sel.edge)) {
        e.preventDefault();
        s.deleteSelection();
      } else if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) s.redo();
        else s.undo();
      } else if (mod && e.key.toLowerCase() === 'y') {
        e.preventDefault();
        s.redo();
      } else if (mod && e.key.toLowerCase() === 'd' && s.sel.node) {
        e.preventDefault();
        s.duplicate(s.sel.node);
      } else if (e.key === 'Enter') {
        const el = t.closest<HTMLElement>('.react-flow__node');
        if (el?.dataset.id) {
          s.select({ node: el.dataset.id }, 'configure');
          }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const patchPipeline = useApiMutation((b: { name?: string; description?: string; owner_team?: string }) => api.updatePipeline(pipeline.id, b), [qk.pipelines, qk.pipeline(pipeline.id)], () =>
    useEditor.getState().markChange('Edited pipeline details'),
  );

  const toggleActive = async (on: boolean) => {
    if (on && errs) {
      toast(`Resolve ${errs} issue${errs > 1 ? 's' : ''} in “${pipeline.name}” before activating`, true);
      return;
    }
    try {
      await (on ? api.activatePipeline(pipeline.id) : api.pausePipeline(pipeline.id));
      toast(`${pipeline.name} ${on ? 'activated' : 'paused'}`);
      invalidatePipeline();
    } catch (e) {
      toast(errorMessage(e), true);
    }
  };

  return (
    <BuilderActionsCtx.Provider value={actions}>
      <div className="builder">
        <div className="chead" id="chead">
          <span className="tile lg ai">
            <Icon name="flow" size={18} />
          </span>
          <div style={{ minWidth: 0 }}>
            <h1>{pipeline.name}</h1>
            <div className="meta">
              <span className="mono">{live ? `v${live}` : 'Unpublished'}</span>·<span>{pipeline.owner_team}</span>·
              {changes.length ? (
                <span className="badge info">
                  {changes.length} draft change{changes.length > 1 ? 's' : ''}
                </span>
              ) : (
                <span>Published</span>
              )}
              {errs > 0 && (
                <button className="badge danger" onClick={validate}>
                  <Icon name="warning" size={12} />
                  {errs} issue{errs > 1 ? 's' : ''}
                </button>
              )}
              <span id="saved" aria-live="polite">
                {saving || pending.length ? 'Saving…' : 'Saved'}
              </span>
            </div>
          </div>
          <div className="grow" />
          <label className="row sub hide-s" style={{ fontSize: 13.5, fontWeight: 500 }}>
            <span className="switch">
              <input type="checkbox" aria-label="Pipeline active" checked={pipeline.status === 'active'} onChange={(e) => void toggleActive(e.target.checked)} />
              <span />
            </span>
            {pipeline.status === 'active' ? 'Active' : pipeline.status === 'paused' ? 'Paused' : 'Draft'}
          </label>
          <button className="btn sm hide-s" data-act="pipeline-settings" aria-pressed={pipeSettings} onClick={() => (pipeSettings ? usePanels.getState().closeConfigure() : usePanels.getState().openPipelineSettings())}>
            <Icon name="settings--adjust" size={15} />
            Settings
          </button>
          <button className="btn sm hide-s" onClick={() => setDlg('versions')}>
            <Icon name="recently-viewed" size={15} />
            Versions
          </button>
          <button className="btn sm" onClick={runTest} disabled={running}>
            {running ? <Spinner size={15} /> : <Icon name="play" size={15} />}
            Test run
          </button>
          <button
            className="btn primary sm"
            disabled={!changes.length}
            onClick={() => {
              if (errs) {
                toast(`Resolve ${errs} issue${errs > 1 ? 's' : ''} before publishing`, true);
                useRun.getState().setTab('problems');
                return;
              }
              setDlg('publish');
            }}
          >
            Publish v{live + 1}
          </button>
        </div>
        <div className="bpanes">
          <Palette />
          <section className="stagewrap">
            <Canvas sumCtx={sumCtx} bad={bad} onValidate={validate} onTestNode={(i) => void testNode(i)} onConfigure={configure} slotMenu={slotMenu} portMenu={portMenu} />
            <RunPanel graph={g} issues={iss} onRun={runTest} onGoto={goto} onShowOutput={(i) => {
              useEditor.getState().select({ node: i }, 'output');
                  }} />
          </section>
          <Inspector pipeline={pipeline} issues={iss} refs={refs} vctx={vctx} sumCtx={sumCtx} onPipelineField={(p) => patchPipeline.mutate(p)} onGoto={goto} onTestNode={(i) => void testNode(i)} slotMenu={slotMenu} />
        </div>
      </div>
      {dlg === 'versions' && <VersionsDialog pipeline={pipeline} changes={changes} onClose={() => setDlg(null)} />}
      {dlg === 'publish' && <PublishDialog pipeline={pipeline} changes={changes} flush={flush} onClose={() => setDlg(null)} onDone={invalidatePipeline} />}
      {conflict && <ConflictDialog pipelineId={pipeline.id} />}
    </BuilderActionsCtx.Provider>
  );
}

function VersionsDialog({ pipeline, changes, onClose }: { pipeline: Pipeline; changes: string[]; onClose: () => void }) {
  const vs = useVersions(pipeline.id);
  const qc = useQueryClient();
  const restore = async (v: number) => {
    try {
      const r = await api.restoreVersion(pipeline.id, v);
      useEditor.getState().load(pipeline.id, deserializeGraph(r.data.graph), r.etag || r.data.etag, r.data.changes ?? []);
      void qc.invalidateQueries({ queryKey: qk.pipelines });
      toast(`Version ${v} restored into the draft`);
      onClose();
      requestAnimationFrame(() => canvasApi.current?.fit());
    } catch (e) {
      toast(errorMessage(e), true);
    }
  };
  return (
    <Dialog title="Versions" desc={pipeline.name} onClose={onClose}>
      {changes.length > 0 && (
        <div className="row" style={{ padding: '10px 0', borderBottom: '1px solid var(--border)' }}>
          <span className="badge info">Draft</span>
          <span className="grow">
            {changes.length} change{changes.length > 1 ? 's' : ''} since {pipeline.live_version ? `v${pipeline.live_version}` : 'creation'}
          </span>
        </div>
      )}
      {vs.isLoading ? (
        <Loading />
      ) : vs.data?.length ? (
        vs.data.map((h) => (
          <div key={h.version} className="row" style={{ padding: '10px 0', borderBottom: '1px solid var(--border)', alignItems: 'flex-start' }}>
            <span className="mono" style={{ width: 36 }}>
              v{h.version}
            </span>
            <span className="grow">
              <b style={{ fontWeight: 500 }}>{h.release_note}</b>
              <br />
              <small className="muted">
                {h.published_by} · {ago(h.published_at)}
              </small>
            </span>
            {h.live || h.version === pipeline.live_version ? (
              <span className="badge success">Live</span>
            ) : (
              <button className="btn ghost xs" onClick={() => void restore(h.version)}>
                Restore
              </button>
            )}
          </div>
        ))
      ) : (
        <p className="muted">No published versions yet.</p>
      )}
    </Dialog>
  );
}

function PublishDialog({ pipeline, changes, flush, onClose, onDone }: { pipeline: Pipeline; changes: string[]; flush: () => Promise<boolean>; onClose: () => void; onDone: () => void }) {
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const qc = useQueryClient();
  const next = (pipeline.live_version ?? 0) + 1;
  const publish = async () => {
    setBusy(true);
    try {
      if (!(await flush())) return false;
      const etag = useEditor.getState().etag;
      const v = await api.publishVersion(pipeline.id, note.trim() || changes[0] || 'Published', etag);
      const d = await api.getDraft(pipeline.id);
      useEditor.getState().saved(d.etag, d.data.changes ?? []);
      void qc.invalidateQueries({ queryKey: qk.versions(pipeline.id) });
      onDone();
      toast(`Version ${v.version} published`);
      return true;
    } catch (e) {
      if (isApiError(e) && e.problem.issues?.length) useRun.getState().setTab('problems');
      toast(errorMessage(e), true);
      return false;
    } finally {
      setBusy(false);
    }
  };
  return (
    <Dialog
      title={`Publish version ${next}`}
      desc={pipeline.status === 'active' ? 'New documents use this version immediately. Runs in progress finish on the current version.' : 'This version is used when the pipeline is activated.'}
      okLabel="Publish"
      busy={busy}
      onOk={publish}
      onClose={onClose}
    >
      <div style={{ marginBottom: 14 }}>
        {changes.map((d) => (
          <div key={d} className="row sub" style={{ padding: '4px 0', fontSize: 13.5 }}>
            <Icon name="checkmark" size={14} />
            {d}
          </div>
        ))}
      </div>
      <label className="field" style={{ margin: 0 }}>
        <span className="lab">Release note</span>
        <input className="input" name="note" placeholder="What changed and why" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
      </label>
    </Dialog>
  );
}

function ConflictDialog({ pipelineId }: { pipelineId: string }) {
  const qc = useQueryClient();
  const reload = async () => {
    const d = await api.getDraft(pipelineId);
    useEditor.getState().load(pipelineId, deserializeGraph(d.data.graph), d.etag || d.data.etag, d.data.changes ?? []);
    void qc.invalidateQueries({ queryKey: qk.pipelines });
    toast('Loaded the latest saved draft');
  };
  const overwrite = async () => {
    const d = await api.getDraft(pipelineId);
    const s = useEditor.getState();
    s.saved(d.etag || d.data.etag, d.data.changes ?? []);
    s.setConflict(false);
    if (!s.pending.length) s.markChange('Edited the canvas');
    if (await saveNow()) toast('Your version of the draft was saved');
  };
  return (
    <Dialog
      title="This draft was changed by someone else"
      desc="Another editor saved this pipeline after you opened it. Reload their draft, or keep your changes and overwrite it."
      okLabel="Keep mine and overwrite"
      onOk={async () => {
        await overwrite();
      }}
      cancelLabel="Reload their draft"
      onClose={() => {
        if (useEditor.getState().conflict) void reload();
      }}
    />
  );
}
