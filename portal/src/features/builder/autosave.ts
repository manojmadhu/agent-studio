/**
 * Draft autosave: debounced `PUT /pipelines/{id}/draft` with `If-Match: <etag>`.
 * A 412 means someone else saved first → the conflict dialog lets the user reload or overwrite.
 */
import { useCallback, useEffect, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '../../api/client';
import { isApiError, errorMessage } from '../../api/errors';
import { qk } from '../../api/hooks';
import { toast } from '../../ui/toast';
import { serializeGraph } from './graph';
import { useEditor } from './store';

let chain: Promise<void> = Promise.resolve();

export async function saveNow(): Promise<boolean> {
  const run = async (): Promise<boolean> => {
    const s = useEditor.getState();
    if (!s.pipelineId || s.conflict) return !s.conflict;
    const pending = s.takePending();
    if (!pending.length) return true;
    s.setSaving(true);
    try {
      let etag = s.etag;
      let changes = s.serverChanges;
      for (const change of pending) {
        const r = await api.saveDraft(s.pipelineId, etag, { graph: serializeGraph(useEditor.getState().graph()), change });
        etag = r.etag || r.data.etag;
        changes = r.data.changes ?? changes;
      }
      useEditor.getState().saved(etag, changes);
      return true;
    } catch (e) {
      // put the messages back so nothing is lost
      const cur = useEditor.getState();
      useEditor.setState({ pending: [...pending, ...cur.pending.filter((p) => !pending.includes(p))] });
      if (isApiError(e) && e.status === 412) cur.setConflict(true);
      else toast(errorMessage(e), true);
      return false;
    } finally {
      useEditor.getState().setSaving(false);
    }
  };
  const p = chain.then(run);
  chain = p.then(
    () => undefined,
    () => undefined,
  );
  return p;
}

export function useAutosave(delay = 700) {
  const qc = useQueryClient();
  const t = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => {
    const unsub = useEditor.subscribe((s, prev) => {
      if (s.rev !== prev.rev && s.pending.length) {
        clearTimeout(t.current);
        t.current = setTimeout(() => {
          void saveNow().then((ok) => {
            if (ok) void qc.invalidateQueries({ queryKey: qk.pipelines });
          });
        }, delay);
      }
    });
    return () => {
      unsub();
      clearTimeout(t.current);
      void saveNow();
    };
  }, [qc, delay]);
  return useCallback(async () => {
    clearTimeout(t.current);
    return saveNow();
  }, []);
}
