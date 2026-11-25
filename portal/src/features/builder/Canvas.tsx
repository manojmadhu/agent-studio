/**
 * Builder canvas on @xyflow/react, styled and behaving like the prototype canvas:
 * ports + snapping, drag-to-empty → "Connect a new step", AI slot plugging (both directions),
 * context menus, palette drag-drop, 8px snapping, custom wheel (pan vs zoom), minimap, fit/tidy.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ReactFlow,
  useReactFlow,
  type Connection,
  type ConnectionLineComponentProps,
  type EdgeTypes,
  type FinalConnectionState,
  type IsValidConnection,
  type NodeChange,
  type NodeTypes,
  type Viewport,
} from "@xyflow/react";
import type { Graph, SlotName } from "../../api/types";
import { Icon } from "../../ui/Icon";
import { openMenuAt, closeMenu, type MenuItem } from "../../ui/menuStore";
import { toast } from "../../ui/toast";
import {
  CATALOG,
  SLOTS,
  STAGES,
  STAGE_ORDER,
  def,
  type SummaryCtx,
} from "./catalog";
import {
  NH,
  NW,
  SH,
  SW,
  hostOf,
  insOf,
  isSub,
  mainNodes,
  nH,
  nW,
  nodeById,
  outsOf,
  portPos,
  slotX,
  slotsOf,
  subsOf,
} from "./graph";
import { curve, vcurve } from "./geometry";
import { bounds } from "./layout";
import {
  canTarget,
  completeDrag,
  nodeAt,
  reason,
  targetAt,
  tipFor,
  type DragSpec,
} from "./ops";
import { AiComponentNode, StepNode, type StepFlowNode } from "./StepNode";
import { AiEdge, FlowEdge, type FlowEdgeT } from "./FlowEdge";
import { MiniMap } from "./MiniMap";
import { useEditor } from "./store";
import { useRun } from "./runStore";
import { usePanels } from "./panels";
import { useCanvasUi } from "./canvasUi";
import { clamp } from "../../lib/util";

const nodeTypes: NodeTypes = { step: StepNode, component: AiComponentNode };
const edgeTypes: EdgeTypes = { flow: FlowEdge, ai: AiEdge };

/** Imperative canvas API used by toolbar buttons, Problems "Show", palette click-add. */
export interface CanvasApi {
  fit: (all?: boolean) => void;
  zoomBy: (f: number) => void;
  centerOn: (id: string) => void;
  /** viewport centre in flow coordinates */
  center: () => [number, number];
}
export const canvasApi: { current: CanvasApi | null } = { current: null };

export function stepMenuItems(
  fn: (type: string) => void,
  noIntake = false,
): MenuItem[] {
  const it: MenuItem[] = [];
  for (const k of STAGE_ORDER) {
    if (k === "ai" || (noIntake && k === "intake")) continue;
    it.push({ h: STAGES[k].label });
    Object.values(CATALOG)
      .filter((d) => d.stage === k)
      .forEach((d) =>
        it.push({
          l: d.name,
          d: d.description,
          i: d.icon,
          c: `var(${STAGES[k].color})`,
          fn: () => fn(d.type),
        }),
      );
  }
  return it;
}
export function slotMenuItems(
  slot: SlotName,
  fn: (type: string) => void,
): MenuItem[] {
  return Object.values(CATALOG)
    .filter((d) => d.sub === slot)
    .map((d) => ({
      l: d.name,
      d: d.description,
      i: d.icon,
      c: "var(--st-ai)",
      fn: () => fn(d.type),
    }));
}

function ConnectionLine({ toX, toY }: ConnectionLineComponentProps) {
  const drag = useCanvasUi((s) => s.drag);
  const tgt = useCanvasUi((s) => s.hoverTarget);
  const nodes = useEditor((s) => s.nodes);
  const edges = useEditor((s) => s.edges);
  if (!drag) return null;
  const g: Graph = { nodes, edges };
  const n = nodeById(g, drag.n.id) ?? drag.n;
  const b = tgt ? nodeById(g, tgt) : undefined;
  const [tx, ty] = b ? tipFor(g, drag, b) : [toX, toY];
  let d: string;
  if (drag.t === "wire") {
    const [x1, y1] = portPos(n, drag.port!);
    d = curve(x1, y1, tx, ty);
  } else if (drag.t === "rin") d = curve(tx, ty, n.x, n.y + NH / 2);
  else if (drag.t === "plug") d = vcurve(n.x + SW / 2, n.y, tx, ty);
  else d = vcurve(tx, ty, n.x + slotX(n, drag.slot!), n.y + NH);
  return <path className="w w-tmp" d={d} />;
}

const pointOf = (e: MouseEvent | TouchEvent): [number, number] => {
  if ("changedTouches" in e && e.changedTouches.length)
    return [e.changedTouches[0]!.clientX, e.changedTouches[0]!.clientY];
  const m = e as MouseEvent;
  return [m.clientX, m.clientY];
};

export interface CanvasProps {
  sumCtx: SummaryCtx;
  bad: Set<string>;
  onValidate: () => void;
  onTestNode: (id: string) => void;
  onConfigure: (id: string) => void;
  slotMenu: (
    hostId: string,
    slot: SlotName,
    x: number,
    y: number,
    pos?: [number, number],
  ) => void;
  portMenu: (
    fromId: string,
    port: string,
    x: number,
    y: number,
    pos?: [number, number],
  ) => void;
}

export function Canvas({
  sumCtx,
  bad,
  onValidate,
  onTestNode,
  onConfigure,
  slotMenu,
  portMenu,
}: CanvasProps) {
  const nodes = useEditor((s) => s.nodes);
  const edges = useEditor((s) => s.edges);
  const sel = useEditor((s) => s.sel);
  const RS = useRun((s) => s.RS);
  const live = useRun((s) => s.live);
  const drag = useCanvasUi((s) => s.drag);
  const hoverTarget = useCanvasUi((s) => s.hoverTarget);
  const rf = useReactFlow<StepFlowNode, FlowEdgeT>();
  const cvRef = useRef<HTMLDivElement>(null);
  const zlRef = useRef<HTMLSpanElement>(null);
  const g: Graph = useMemo(() => ({ nodes, edges }), [nodes, edges]);
  const [size, setSize] = useState({ w: 0, h: 0 });
  useEffect(() => {
    const cv = cvRef.current;
    if (!cv) return;
    const ro = new ResizeObserver(() =>
      setSize({ w: cv.clientWidth, h: cv.clientHeight }),
    );
    ro.observe(cv);
    return () => ro.disconnect();
  }, []);

  const rfNodes: StepFlowNode[] = useMemo(
    () =>
      nodes.map((n) => {
        const sub = isSub(n);
        let summary = "";
        try {
          summary = def(n.type).sum(n.config, sumCtx);
        } catch {
          summary = "";
        }
        const full: Partial<Record<SlotName, boolean>> = {};
        slotsOf(n).forEach((s) => (full[s] = subsOf(g, n, s).length > 0));
        const ok = drag ? canTarget(drag, n) : false;
        return {
          id: n.id,
          type: sub ? "component" : "step",
          position: { x: n.x, y: n.y },
          width: nW(n),
          height: nH(n),
          measured: { width: nW(n), height: nH(n) },
          selected: sel.node === n.id,
          data: {
            gn: n,
            summary,
            status: RS[n.id],
            bad: bad.has(n.id),
            full,
            selected: sel.node === n.id,
            tgt: !!drag && ok,
            dim: !!drag && !ok && n.id !== drag.n.id,
            hovt: hoverTarget === n.id,
          },
        };
      }),
    [nodes, g, sel.node, RS, bad, drag, hoverTarget, sumCtx],
  );

  const rfEdges: FlowEdgeT[] = useMemo(
    () =>
      edges
        .map((e): FlowEdgeT | null => {
          const a = nodeById(g, e.from);
          const b = nodeById(g, e.to);
          if (!a || !b) return null;
          return {
            id: e.id,
            source: e.from,
            target: e.to,
            sourceHandle: e.slot ? "plug" : e.from_port,
            targetHandle: e.slot ?? "in",
            type: e.slot ? "ai" : "flow",
            selected: sel.edge === e.id,
            data: {
              a,
              b,
              port: e.from_port,
              slot: e.slot,
              live: live.has(e.id),
            },
          };
        })
        .filter((x): x is FlowEdgeT => !!x),
    [edges, g, sel.edge, live],
  );

  // ── view helpers (prototype fitView / zoomAt)
  const applyBg = useCallback((v: Viewport) => {
    const cv = cvRef.current;
    if (!cv) return;
    cv.style.backgroundSize = `${20 * v.zoom}px ${20 * v.zoom}px`;
    cv.style.backgroundPosition = `${v.x}px ${v.y}px`;
    cv.classList.toggle("lowzoom", v.zoom < 0.35);
    if (zlRef.current)
      zlRef.current.textContent = Math.round(v.zoom * 100) + "%";
  }, []);
  const setView = useCallback(
    (v: Viewport) => {
      void rf.setViewport(v);
      applyBg(v);
    },
    [rf, applyBg],
  );
  const fit = useCallback(
    (all?: boolean) => {
      const cv = cvRef.current;
      if (!cv) return;
      const gg = useEditor.getState().graph();
      const b = bounds(gg);
      if (!b) return setView({ x: 60, y: 60, zoom: 1 });
      const W = cv.clientWidth;
      const H = cv.clientHeight - 50;
      const kf = Math.min(W / (b[2] - b[0]), H / (b[3] - b[1]));
      if (all !== true && kf < 0.5) {
        const k = clamp(H / (b[3] - b[1]), 0.5, 0.8);
        const start = mainNodes(gg).find((n) => !insOf(n)) ?? gg.nodes[0]!;
        return setView({
          zoom: k,
          x: 40 - (start.x - 20) * k,
          y: 50 + (H - (b[3] - b[1]) * k) / 2 - b[1] * k,
        });
      }
      const k = clamp(kf, 0.12, 1.1);
      setView({
        zoom: k,
        x: (W - (b[2] - b[0]) * k) / 2 - b[0] * k,
        y: 50 + (H - (b[3] - b[1]) * k) / 2 - b[1] * k,
      });
    },
    [setView],
  );
  const zoomAt = useCallback(
    (f: number, cx: number, cy: number) => {
      const v = rf.getViewport();
      const k = clamp(v.zoom * f, 0.2, 2);
      setView({
        x: cx - (cx - v.x) * (k / v.zoom),
        y: cy - (cy - v.y) * (k / v.zoom),
        zoom: k,
      });
    },
    [rf, setView],
  );

  useEffect(() => {
    canvasApi.current = {
      fit,
      zoomBy: (f) => {
        const cv = cvRef.current;
        if (cv) zoomAt(f, cv.clientWidth / 2, cv.clientHeight / 2);
      },
      centerOn: (id) => {
        const n = useEditor.getState().nodes.find((x) => x.id === id);
        const cv = cvRef.current;
        if (!n || !cv) return;
        const v = rf.getViewport();
        setView({
          zoom: v.zoom,
          x: cv.clientWidth / 2 - (n.x + nW(n) / 2) * v.zoom,
          y: cv.clientHeight / 2 - (n.y + nH(n) / 2) * v.zoom,
        });
      },
      center: () => {
        const cv = cvRef.current;
        const v = rf.getViewport();
        if (!cv) return [400, 200];
        return [
          (cv.clientWidth / 2 - v.x) / v.zoom,
          (cv.clientHeight / 2 - v.y) / v.zoom,
        ];
      },
    };
    return () => {
      canvasApi.current = null;
    };
  }, [fit, zoomAt, rf, setView]);

  // wheel: ctrl/⌘ or a mouse wheel zooms; trackpad scroll pans (prototype behaviour)
  useEffect(() => {
    const cv = cvRef.current;
    if (!cv) return;
    const onWheel = (e: WheelEvent) => {
      if ((e.target as Element).closest(".ctl,.react-flow__minimap")) return;
      e.preventDefault();
      const r = cv.getBoundingClientRect();
      if (
        e.ctrlKey ||
        e.metaKey ||
        (e.deltaX === 0 && Math.abs(e.deltaY) >= 40)
      )
        zoomAt(
          Math.exp(-e.deltaY * 0.0015),
          e.clientX - r.left,
          e.clientY - r.top,
        );
      else {
        const v = rf.getViewport();
        setView({ ...v, x: v.x - e.deltaX, y: v.y - e.deltaY });
      }
    };
    cv.addEventListener("wheel", onWheel, { passive: false });
    return () => cv.removeEventListener("wheel", onWheel);
  }, [rf, zoomAt, setView]);

  // ── node changes: positions only (selection is owned by the editor store)
  const onNodesChange = useCallback((changes: NodeChange<StepFlowNode>[]) => {
    const pos: Record<string, { x: number; y: number }> = {};
    let done = false;
    let any = false;
    for (const c of changes) {
      if (c.type === "position" && c.position) {
        pos[c.id] = {
          x: Math.round(c.position.x / 8) * 8,
          y: Math.round(c.position.y / 8) * 8,
        };
        any = true;
        if (!c.dragging) done = true;
      } else if (c.type === "position" && c.dragging === false) done = true;
    }
    if (any || done) useEditor.getState().dragMove(pos, done);
  }, []);

  // ── connections
  const isValidConnection: IsValidConnection = useCallback((c) => {
    const s = useEditor.getState();
    const a = s.nodes.find((n) => n.id === c.source);
    const b = s.nodes.find((n) => n.id === c.target);
    if (!a || !b || a.id === b.id) return false;
    if (c.sourceHandle === "plug")
      return (
        isSub(a) &&
        !!c.targetHandle &&
        def(a.type).sub === c.targetHandle &&
        slotsOf(b).includes(c.targetHandle as SlotName)
      );
    return (
      c.targetHandle === "in" &&
      outsOf(a).includes(c.sourceHandle ?? "") &&
      !!insOf(b)
    );
  }, []);
  const onConnect = useCallback((c: Connection) => {
    const s = useEditor.getState();
    if (c.sourceHandle === "plug" && c.targetHandle)
      s.attachTo(c.source, c.target, c.targetHandle as SlotName);
    else if (c.sourceHandle) s.connect(c.source, c.sourceHandle, c.target);
  }, []);
  const onConnectStart = useCallback(
    (
      _: unknown,
      p: {
        nodeId: string | null;
        handleId: string | null;
        handleType: "source" | "target" | null;
      },
    ) => {
      closeMenu();
      const n = useEditor.getState().nodes.find((x) => x.id === p.nodeId);
      if (!n || !p.handleId) return;
      let d: DragSpec;
      if (p.handleType === "source")
        d =
          p.handleId === "plug"
            ? { t: "plug", n }
            : { t: "wire", n, port: p.handleId };
      else
        d =
          p.handleId === "in"
            ? { t: "rin", n }
            : { t: "slot", n, slot: p.handleId as SlotName };
      useCanvasUi.getState().setDrag(d);
      cvRef.current?.classList.add("connecting");
    },
    [],
  );
  const startPt = useRef<[number, number] | null>(null);
  const onConnectEnd = useCallback(
    (e: MouseEvent | TouchEvent, st: FinalConnectionState) => {
      const ui = useCanvasUi.getState();
      const d = ui.drag;
      ui.setDrag(null);
      cvRef.current?.classList.remove("connecting");
      if (!d || st.isValid) return;
      const [cx, cy] = pointOf(e);
      const p = rf.screenToFlowPosition({ x: cx, y: cy });
      const s = useEditor.getState();
      const gg = s.graph();
      const b = targetAt(gg, d, p.x, p.y);
      if (b) {
        s.apply(completeDrag(gg, d, b));
        return;
      }
      const under = nodeAt(gg, p.x, p.y);
      const why = under && under.id !== d.n.id ? reason(d, under) : "";
      const moved = startPt.current
        ? Math.hypot(cx - startPt.current[0], cy - startPt.current[1]) > 6
        : true;
      if (why) toast(why, true);
      else if (!under) {
        if (d.t === "wire")
          portMenu(
            d.n.id,
            d.port!,
            cx,
            cy,
            moved ? [p.x, p.y - NH / 2] : undefined,
          );
        else if (d.t === "slot")
          slotMenu(
            d.n.id,
            d.slot!,
            cx + 8,
            cy + 8,
            moved ? [p.x - SW / 2, p.y] : undefined,
          );
      }
    },
    [rf, portMenu, slotMenu],
  );
  const onPointerMove = useCallback(
    (e: React.PointerEvent) => {
      const d = useCanvasUi.getState().drag;
      if (!d) return;
      const p = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const b = targetAt(useEditor.getState().graph(), d, p.x, p.y);
      const id = b?.id ?? null;
      if (useCanvasUi.getState().hoverTarget !== id)
        useCanvasUi.getState().setHoverTarget(id);
    },
    [rf],
  );

  // ── context menus
  const nodeMenu = useCallback(
    (e: React.MouseEvent, id: string) => {
      e.preventDefault();
      const s = useEditor.getState();
      const n = s.nodes.find((x) => x.id === id);
      if (!n) return;
      s.select({ node: n.id });
      const it: MenuItem[] = [
        { l: "Configure", i: "settings--adjust", fn: () => onConfigure(n.id) },
        { l: "Test step", i: "play", fn: () => onTestNode(n.id) },
        { l: "Duplicate", i: "copy", k: "Ctrl D", fn: () => s.duplicate(n.id) },
      ];
      if (!insOf(n) && !isSub(n))
        it.push({
          l: "Replace intake",
          i: "email--new",
          fn: () =>
            openMenuAt(
              e.clientX,
              e.clientY,
              Object.values(CATALOG)
                .filter((d) => d.stage === "intake")
                .map((d) => ({
                  l: d.name,
                  d: d.description,
                  i: d.icon,
                  fn: () => useEditor.getState().replaceIntake(n.id, d.type),
                })),
              { search: false },
            ),
        });
      if (isSub(n) && hostOf(s.graph(), n))
        it.push({
          l: "Unplug from AI step",
          i: "unlink",
          fn: () => useEditor.getState().unplug(n.id),
        });
      slotsOf(n).forEach((sl) =>
        it.push({
          l: "Add " + SLOTS[sl].label.toLowerCase(),
          i: "add",
          c: "var(--st-ai)",
          fn: () => slotMenu(n.id, sl, e.clientX + 8, e.clientY + 8),
        }),
      );
      it.push({
        l: "Delete step",
        i: "trash-can",
        danger: true,
        k: "Del",
        fn: () => useEditor.getState().deleteNode(n.id),
      });
      openMenuAt(e.clientX, e.clientY, it, { search: false });
    },
    [onConfigure, onTestNode, slotMenu],
  );
  const paneMenu = useCallback(
    (e: React.MouseEvent | MouseEvent) => {
      e.preventDefault();
      const p = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const add = (t: string) =>
        useEditor
          .getState()
          .addNode(
            t,
            [p.x - nW({ type: t }) / 2, p.y - nH({ type: t }) / 2],
            false,
          );
      openMenuAt(
        e.clientX,
        e.clientY,
        [
          { h: "Canvas" },
          {
            l: "Add step here…",
            i: "add",
            fn: () =>
              openMenuAt(
                e.clientX,
                e.clientY,
                [
                  ...stepMenuItems(add),
                  { h: "AI components" },
                  ...Object.values(CATALOG)
                    .filter((d) => d.sub)
                    .map((d) => ({
                      l: d.name,
                      d: d.description,
                      i: d.icon,
                      c: "var(--st-ai)",
                      fn: () => add(d.type),
                    })),
                ],
                {
                  title: "Search steps",
                },
              ),
          },
          { l: "Fit to screen", i: "fit-to-screen", fn: () => fit(true) },
          {
            l: "Tidy layout",
            i: "chart--network",
            fn: () => {
              useEditor.getState().tidy();
              requestAnimationFrame(() => fit(true));
            },
          },
          { l: "Validate", i: "security", fn: onValidate },
        ],
        { search: false },
      );
    },
    [rf, fit, onValidate],
  );

  // ── palette drag-and-drop
  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const t = e.dataTransfer.getData("text/plain");
      if (!CATALOG[t]) return;
      const p = rf.screenToFlowPosition({ x: e.clientX, y: e.clientY });
      const s = useEditor.getState();
      const host = nodeAt(s.graph(), p.x, p.y);
      const sub = CATALOG[t]!.sub;
      if (sub && host && slotsOf(host).includes(sub)) {
        s.addSub(host.id, sub, t);
        usePanels.getState().setPalette(false);
        return;
      }
      s.addNode(
        t,
        [p.x - nW({ type: t }) / 2, p.y - nH({ type: t }) / 2],
        false,
      );
      usePanels.getState().setPalette(false);
    },
    [rf],
  );

  const empty = nodes.length === 0;
  return (
    <div
      className="cv"
      id="cv"
      ref={cvRef}
      aria-label="Pipeline canvas"
      onDragOver={(e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "copy";
      }}
      onDrop={onDrop}
      onPointerMove={onPointerMove}
      onPointerDownCapture={(e) => {
        startPt.current = [e.clientX, e.clientY];
      }}
    >
      <ReactFlow<StepFlowNode, FlowEdgeT>
        nodes={rfNodes}
        edges={rfEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={(_, n) => {
          const s = useEditor.getState();
          if (s.sel.node !== n.id)
            s.select(
              { node: n.id },
              s.itab === "output" && !useRun.getState().OUT[n.id]
                ? "configure"
                : undefined,
            );
        }}
        onNodeDragStart={(_, n) => {
          closeMenu();
          const s = useEditor.getState();
          if (s.sel.node !== n.id) s.select({ node: n.id });
        }}
        onNodeDoubleClick={(_, n) => onConfigure(n.id)}
        onEdgeClick={(_, ed) => useEditor.getState().select({ edge: ed.id })}
        onPaneClick={() => {
          closeMenu();
          const s = useEditor.getState();
          if (s.sel.node || s.sel.edge) s.select({});
        }}
        onNodeContextMenu={(e, n) => nodeMenu(e, n.id)}
        onEdgeContextMenu={(e, ed) => {
          e.preventDefault();
          useEditor.getState().select({ edge: ed.id });
          openMenuAt(
            e.clientX,
            e.clientY,
            [
              {
                l: "Delete connection",
                i: "trash-can",
                danger: true,
                k: "Del",
                fn: () => useEditor.getState().deleteEdge(ed.id),
              },
            ],
            { search: false },
          );
        }}
        onPaneContextMenu={paneMenu}
        onConnectStart={onConnectStart}
        onConnect={onConnect}
        onConnectEnd={onConnectEnd}
        isValidConnection={isValidConnection}
        connectionLineComponent={ConnectionLine}
        connectionRadius={28}
        connectOnClick={false}
        onMove={(_, v) => applyBg(v)}
        onInit={() => requestAnimationFrame(() => fit())}
        snapToGrid
        snapGrid={[8, 8]}
        minZoom={0.12}
        maxZoom={2}
        zoomOnScroll={false}
        zoomOnPinch={false}
        panOnScroll={false}
        zoomOnDoubleClick={false}
        preventScrolling
        deleteKeyCode={null}
        selectionKeyCode={null}
        multiSelectionKeyCode={null}
        elementsSelectable
        nodesFocusable
        edgesFocusable={false}
        elevateNodesOnSelect={false}
        proOptions={{ hideAttribution: true }}
        style={{ width: "100%", height: "100%" }}
      ></ReactFlow>
      {!empty && (
        <MiniMap
          width={size.w}
          height={size.h}
          onMoveTo={(x, y) => {
            const v = rf.getViewport();
            setView({
              zoom: v.zoom,
              x: size.w / 2 - x * v.zoom,
              y: size.h / 2 - y * v.zoom,
            });
          }}
        />
      )}
      <div className="ctl tl">
        <AddStepsButton />
        <span
          style={{ width: 1, background: "var(--border)", margin: "4px 2px" }}
        />
        <button
          className="btn ghost icon sm"
          title="Undo (Ctrl+Z)"
          aria-label="Undo"
          onClick={() => useEditor.getState().undo()}
        >
          <Icon name="undo" />
        </button>
        <button
          className="btn ghost icon sm"
          title="Redo (Ctrl+Shift+Z)"
          aria-label="Redo"
          onClick={() => useEditor.getState().redo()}
        >
          <Icon name="redo" />
        </button>
        <span
          style={{ width: 1, background: "var(--border)", margin: "4px 2px" }}
        />
        <button
          className="btn ghost icon sm"
          aria-label="Zoom out"
          onClick={() => canvasApi.current?.zoomBy(1 / 1.2)}
        >
          <Icon name="zoom--out" />
        </button>
        <span className="zl num" ref={zlRef}>
          100%
        </span>
        <button
          className="btn ghost icon sm"
          aria-label="Zoom in"
          onClick={() => canvasApi.current?.zoomBy(1.2)}
        >
          <Icon name="zoom--in" />
        </button>
        <button
          className="btn ghost icon sm"
          title="Fit to screen"
          aria-label="Fit to screen"
          onClick={() => fit(true)}
        >
          <Icon name="fit-to-screen" />
        </button>
        <button
          className="btn ghost icon sm"
          title="Tidy layout"
          aria-label="Tidy layout"
          onClick={() => {
            useEditor.getState().tidy();
            requestAnimationFrame(() => fit(true));
          }}
        >
          <Icon name="chart--network" />
        </button>
      </div>
      <div className="ctl tr">
        <button className="btn ghost sm" onClick={onValidate}>
          <Icon name="security" size={15} />
          Validate
        </button>
      </div>
      <div className="hintbar">
        Press + to add steps · drag from a port to connect · click a step to
        configure it
      </div>
      {empty && (
        <div className="cempty">
          <div>
            <button
              className="addfirst"
              data-act="openpal"
              aria-label="Add a step"
              title="Add a step"
              onClick={() => usePanels.getState().setPalette(true)}
            >
              <Icon name="add" size={28} />
            </button>
            <h3>Add your first step</h3>
            <p>Start with an intake such as Email inbox or Upload portal.</p>
          </div>
        </div>
      )}
    </div>
  );
}

function AddStepsButton() {
  const open = usePanels((s) => s.palette);
  return (
    <button
      className="btn ghost icon sm addbtn"
      data-act="openpal"
      title="Add steps"
      aria-label="Add steps"
      aria-expanded={open}
      aria-controls="pal"
      onClick={() => usePanels.getState().togglePalette()}
    >
      <Icon name="add" />
    </button>
  );
}

export { NW, SH };
