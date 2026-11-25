/**
 * Custom edges. FlowEdge: 2px grey cubic using the prototype `handle()` routing, leaving/entering ports
 * horizontally, with a drawn 10px arrowhead path at the input port (not an SVG marker).
 * AiEdge: dashed teal vertical curve, no arrowhead. Hover/selected → cobalt with a midpoint × delete.
 */
import { memo } from 'react';
import { EdgeLabelRenderer, type Edge, type EdgeProps } from '@xyflow/react';
import type { GraphNode, SlotName } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { aiGeom, flowGeom } from './geometry';
import { NH, SW, portPos, slotX } from './graph';
import { useEditor } from './store';
import { useCanvasUi } from './canvasUi';

export interface FlowEdgeData extends Record<string, unknown> {
  a: GraphNode;
  b: GraphNode;
  port: string;
  slot?: SlotName;
  live: boolean;
}
export type FlowEdgeT = Edge<FlowEdgeData, 'flow' | 'ai'>;

/** Geometry for a graph edge given its endpoint nodes (exported for tests). */
export function edgeGeometry(a: GraphNode, b: GraphNode, port: string, slot?: SlotName) {
  if (slot) return aiGeom(a.x + SW / 2, a.y, b.x + slotX(b, slot), b.y + NH);
  const [x1, y1] = portPos(a, port);
  return flowGeom(x1, y1, b.x - 8, b.y + NH / 2);
}

let hideT: ReturnType<typeof setTimeout> | undefined;

function EdgeView({ id, data, selected }: EdgeProps<FlowEdgeT>) {
  const hovE = useCanvasUi((s) => s.hovE);
  const setHovE = useCanvasUi((s) => s.setHovE);
  const del = useEditor((s) => s.deleteEdge);
  if (!data) return null;
  const q = edgeGeometry(data.a, data.b, data.port, data.slot);
  const hov = hovE === id;
  const st = `${selected ? 'w-sel' : ''} ${hov ? 'w-hov' : ''} ${data.live ? 'w-live' : ''}`;
  const enter = () => {
    clearTimeout(hideT);
    setHovE(id);
  };
  const leave = () => {
    clearTimeout(hideT);
    hideT = setTimeout(() => {
      if (useCanvasUi.getState().hovE === id) setHovE(null);
    }, 500);
  };
  return (
    <>
      <path className={`w ${data.slot ? 'w-ai' : ''} ${st}`} d={q.d} />
      {q.a && <path className={`wa ${st}`} d={q.a} />}
      <path className="w-hit" d={q.d} onPointerEnter={enter} onPointerLeave={leave} data-e={id} />
      {(hov || selected) && (
        <EdgeLabelRenderer>
          <button
            className="edel nodrag nopan"
            style={{ transform: `translate(-50%,-50%) translate(${q.m[0]}px,${q.m[1]}px)`, left: 0, top: 0 }}
            title="Delete connection"
            aria-label="Delete connection"
            onPointerEnter={enter}
            onPointerLeave={leave}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              setHovE(null);
              del(id);
            }}
          >
            <Icon name="close" size={14} />
          </button>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const FlowEdge = memo(EdgeView);
export const AiEdge = memo(EdgeView);
