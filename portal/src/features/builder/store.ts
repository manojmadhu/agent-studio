/**
 * Builder editor state (zustand) with undo/redo (zundo `temporal`).
 * Only the graph (nodes + edges) is tracked by history; selection and UI state are not.
 * Node drags record one history entry per gesture; consecutive keystrokes in one field coalesce.
 */
import { create } from 'zustand';
import { temporal } from 'zundo';
import type { Graph, GraphEdge, GraphNode, NodeSettings, SlotName } from '../../api/types';
import type { Config } from './catalog';
import * as ops from './ops';
import { toast } from '../../ui/toast';

export type InspectorTab = 'configure' | 'settings' | 'output';
export interface Selection {
  node?: string;
  edge?: string;
}

interface EditorData {
  pipelineId: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  sel: Selection;
  itab: InspectorTab;
  /** change messages not yet sent to the server */
  pending: string[];
  /** monotonically increasing graph revision (drives autosave) */
  rev: number;
  /** server-side draft change list */
  serverChanges: string[];
  etag: string;
  saving: boolean;
  savedAt: number | null;
  conflict: boolean;
}

export interface EditorState extends EditorData {
  load: (pipelineId: string, g: Graph, etag: string, changes: string[]) => void;
  graph: () => Graph;
  /** apply an op result: set graph, selection, change list and show toast/error */
  apply: (r: ops.OpResult) => boolean;
  select: (s: Selection, tab?: InspectorTab) => void;
  setTab: (t: InspectorTab) => void;
  addNode: (type: string, pos: [number, number] | null, fromSel: boolean, center?: [number, number]) => boolean;
  connect: (from: string, port: string, to: string) => void;
  attachTo: (subId: string, hostId: string, slot: SlotName) => void;
  addSub: (hostId: string, slot: SlotName, type: string, pos?: [number, number]) => void;
  addConnected: (fromId: string, port: string, type: string, pos?: [number, number]) => void;
  deleteNode: (id: string) => void;
  deleteEdge: (id: string) => void;
  deleteSelection: () => void;
  duplicate: (id: string) => void;
  unplug: (subId: string) => void;
  replaceIntake: (id: string, type: string) => void;
  tidy: () => void;
  dragMove: (pos: Record<string, { x: number; y: number }>, done: boolean) => void;
  updateConfig: (id: string, patch: Config, coalesceKey?: string) => void;
  updateSettings: (id: string, patch: Partial<NodeSettings>) => void;
  rename: (id: string, name: string, commit: boolean) => void;
  markChange: (msg: string) => void;
  undo: () => void;
  redo: () => void;
  /** autosave bookkeeping */
  takePending: () => string[];
  saved: (etag: string, changes: string[]) => void;
  setSaving: (v: boolean) => void;
  setConflict: (v: boolean) => void;
}

const initial: EditorData = { pipelineId: null, nodes: [], edges: [], sel: {}, itab: 'configure', pending: [], rev: 0, serverChanges: [], etag: '', saving: false, savedAt: null, conflict: false };

let dragging = false;
let lastCoalesce: { key: string; at: number } | null = null;

export const useEditor = create<EditorState>()(
  temporal(
    (set, get) => {
      const silently = (fn: () => void) => {
        const t = useEditor.temporal.getState();
        t.pause();
        try {
          fn();
        } finally {
          t.resume();
        }
      };
      const change = (msg: string) => {
        const s = get();
        set({ pending: s.pending.includes(msg) ? s.pending : [...s.pending, msg], rev: s.rev + 1 });
      };
      const nodeOf = (id?: string) => (id ? get().nodes.find((n) => n.id === id) : undefined);
      const self: EditorState = {
        ...initial,
        load: (pipelineId, g, etag, changes) => {
          silently(() => set({ ...initial, pipelineId, nodes: g.nodes, edges: g.edges, etag, serverChanges: changes, savedAt: Date.now() }));
          useEditor.temporal.getState().clear();
        },
        graph: () => ({ nodes: get().nodes, edges: get().edges }),
        apply: (r) => {
          if (r.error) {
            toast(r.error, true);
            return false;
          }
          if (r.change) {
            set({ nodes: r.g.nodes, edges: r.g.edges, ...(r.select ? { sel: r.select, itab: 'configure' as InspectorTab } : {}) });
            silently(() => change(r.change!));
          }
          if (r.toast) toast(r.toast);
          return !!r.change;
        },
        select: (sel, tab) => silently(() => set({ sel, ...(tab ? { itab: tab } : {}) })),
        setTab: (itab) => silently(() => set({ itab })),
        addNode: (type, pos, fromSel, center) => get().apply(ops.addNode(get().graph(), type, pos, fromSel ? nodeOf(get().sel.node) : undefined, center)),
        connect: (from, port, to) => void get().apply(ops.connect(get().graph(), from, port, to)),
        attachTo: (subId, hostId, slot) => {
          const sub = nodeOf(subId);
          const host = nodeOf(hostId);
          if (sub && host) get().apply(ops.attach(get().graph(), sub, host, slot));
        },
        addSub: (hostId, slot, type, pos) => {
          const host = nodeOf(hostId);
          if (host) get().apply(ops.addSub(get().graph(), host, slot, type, pos));
        },
        addConnected: (fromId, port, type, pos) => {
          const from = nodeOf(fromId);
          if (from) get().apply(ops.addConnected(get().graph(), from, port, type, pos));
        },
        deleteNode: (id) => {
          const r = ops.deleteNode(get().graph(), id);
          if (get().apply(r) && get().sel.node === id) silently(() => set({ sel: {} }));
        },
        deleteEdge: (id) => {
          if (get().apply(ops.deleteEdge(get().graph(), id)) && get().sel.edge === id) silently(() => set({ sel: {} }));
        },
        deleteSelection: () => {
          const { sel } = get();
          if (sel.node) get().deleteNode(sel.node);
          else if (sel.edge) get().deleteEdge(sel.edge);
        },
        duplicate: (id) => void get().apply(ops.duplicateNode(get().graph(), id)),
        unplug: (subId) => void get().apply(ops.unplug(get().graph(), subId)),
        replaceIntake: (id, type) => void get().apply(ops.replaceIntake(get().graph(), id, type)),
        tidy: () => void get().apply(ops.tidy(get().graph())),
        dragMove: (pos, done) => {
          const g = ops.moveNodes(get().graph(), pos);
          if (!dragging && !done) {
            dragging = true;
            set({ nodes: g.nodes }); // recorded: history keeps the pre-drag positions
          } else if (done && !dragging) {
            set({ nodes: g.nodes }); // discrete move (keyboard)
            silently(() => change('Moved steps'));
          } else {
            silently(() => set({ nodes: g.nodes }));
            if (done) {
              dragging = false;
              silently(() => change('Moved steps'));
            }
          }
        },
        updateConfig: (id, patch, coalesceKey) => {
          const n = nodeOf(id);
          if (!n) return;
          const g = ops.updateNode(get().graph(), id, { config: patch });
          const now = Date.now();
          const key = coalesceKey ? `${id}:${coalesceKey}` : '';
          if (key && lastCoalesce && lastCoalesce.key === key && now - lastCoalesce.at < 1500) silently(() => set({ nodes: g.nodes }));
          else set({ nodes: g.nodes });
          lastCoalesce = key ? { key, at: now } : null;
          silently(() => change(`Edited “${n.name}”`));
        },
        updateSettings: (id, patch) => {
          const n = nodeOf(id);
          if (!n) return;
          set({ nodes: ops.updateNode(get().graph(), id, { settings: patch }).nodes });
          silently(() => change(`Edited “${n.name}” settings`));
        },
        rename: (id, name, commit) => {
          const g = ops.updateNode(get().graph(), id, { name });
          const key = `${id}:__name`;
          const now = Date.now();
          if (lastCoalesce && lastCoalesce.key === key && now - lastCoalesce.at < 1500) silently(() => set({ nodes: g.nodes }));
          else set({ nodes: g.nodes });
          lastCoalesce = { key, at: now };
          if (commit) silently(() => change('Renamed a step'));
        },
        markChange: (msg) => silently(() => change(msg)),
        undo: () => {
          const t = useEditor.temporal.getState();
          if (!t.pastStates.length) {
            toast('Nothing to undo');
            return;
          }
          t.undo();
          silently(() => set({ sel: {} }));
          silently(() => change('Edited the canvas'));
        },
        redo: () => {
          const t = useEditor.temporal.getState();
          if (!t.futureStates.length) return;
          t.redo();
          silently(() => set({ sel: {} }));
          silently(() => change('Edited the canvas'));
        },
        takePending: () => {
          const p = get().pending;
          silently(() => set({ pending: [] }));
          return p;
        },
        saved: (etag, changes) => silently(() => set({ etag, serverChanges: changes, savedAt: Date.now() })),
        setSaving: (saving) => silently(() => set({ saving })),
        setConflict: (conflict) => silently(() => set({ conflict })),
      };
      return self;
    },
    {
      partialize: (s) => ({ nodes: s.nodes, edges: s.edges }),
      equality: (a, b) => a.nodes === b.nodes && a.edges === b.edges,
      limit: 100,
    },
  ),
);

/** Draft change list shown in the header: server list + unsaved local changes. */
export const draftChanges = (s: Pick<EditorState, 'serverChanges' | 'pending'>): string[] => [...s.serverChanges, ...s.pending.filter((p) => !s.serverChanges.includes(p))];
