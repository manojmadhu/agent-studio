/** Custom React Flow nodes: StepNode (248×68 step card) and AiComponentNode (208×48 dashed teal pill). */
import { memo, useEffect } from 'react';
import { Handle, Position, useUpdateNodeInternals, type Node, type NodeProps } from '@xyflow/react';
import type { GraphNode, SlotName } from '../../api/types';
import { Icon } from '../../ui/Icon';
import { SLOTS, STAGES, def } from './catalog';
import { NH, insOf, isSub, needOf, outsOf, slotX, slotsOf } from './graph';
import { useEditor } from './store';
import type { StepState } from './runStore';
import { useBuilderActions } from './actions';

export interface StepNodeData extends Record<string, unknown> {
  gn: GraphNode;
  summary: string;
  status?: StepState;
  bad: boolean;
  /** slot → has a component */
  full: Partial<Record<SlotName, boolean>>;
  /** connection-drag highlighting */
  tgt?: boolean;
  dim?: boolean;
  hovt?: boolean;
  selected: boolean;
}
export type StepFlowNode = Node<StepNodeData, 'step' | 'component'>;

function Toolbar({ id }: { id: string }) {
  const a = useBuilderActions();
  const duplicate = useEditor((s) => s.duplicate);
  const del = useEditor((s) => s.deleteNode);
  return (
    <div className="ntb nodrag nopan" onPointerDown={(e) => e.stopPropagation()}>
      <button title="Test step" aria-label="Test step" onClick={() => a.testNode(id)}>
        <Icon name="play" size={15} />
      </button>
      <button title="Duplicate (Ctrl+D)" aria-label="Duplicate" onClick={() => duplicate(id)}>
        <Icon name="copy" size={15} />
      </button>
      <button title="Configure" aria-label="Configure" onClick={() => a.configure(id)}>
        <Icon name="settings--adjust" size={15} />
      </button>
      <button className="del" title="Delete (Del)" aria-label="Delete step" onClick={() => del(id)}>
        <Icon name="trash-can" size={15} />
      </button>
    </div>
  );
}

function statusIcon(status: StepState | undefined, bad: boolean) {
  if (status === 'run') return <Icon name="in-progress" size={16} className="spin" />;
  if (status === 'ok') return <Icon name="checkmark--filled" size={16} />;
  if (status === 'fail' || bad) return <Icon name="warning" size={16} />;
  return null;
}

function cls(d: StepNodeData, extra: string) {
  const ai = def(d.gn.type).ai;
  return [
    'nd',
    extra,
    ai ? 'ai' : '',
    d.selected ? 'sel' : '',
    d.status && d.status !== 'fail' ? d.status : '',
    (d.bad && !d.status) || d.status === 'fail' ? 'bad' : '',
    d.tgt ? 'tgt' : '',
    d.dim ? 'dim' : '',
    d.hovt ? 'hovt' : '',
  ]
    .filter(Boolean)
    .join(' ');
}

export const StepNode = memo(function StepNode({ id, data }: NodeProps<StepFlowNode>) {
  const a = useBuilderActions();
  const n = data.gn;
  const d = def(n.type);
  const outs = outsOf(n);
  const slots = slotsOf(n);
  const need = needOf(n);
  const update = useUpdateNodeInternals();
  const sig = outs.join('|');
  useEffect(() => {
    update(id);
  }, [id, sig, update]);
  return (
    <div className={cls(data, '')} style={{ ['--sc' as string]: `var(${STAGES[d.stage].color})` }} aria-label={n.name} data-id={n.id}>
      <Toolbar id={n.id} />
      {d.ai && <span className="aitag">AI</span>}
      <span className="nic">
        <Icon name={d.icon} size={20} />
      </span>
      <span className="ntx">
        <b>{n.name}</b>
        <small>{data.summary}</small>
      </span>
      <span className="nst">{statusIcon(data.status, data.bad)}</span>
      {insOf(n) ? <Handle type="target" position={Position.Left} id="in" className="port in" aria-label={`${n.name} input`} /> : null}
      {outs.map((o, i) => (
        <Handle
          key={o}
          type="source"
          position={Position.Right}
          id={o}
          className={`port out ${o === 'error' ? 'err' : ''}`}
          style={{ top: NH / 2 + (i - (outs.length - 1) / 2) * 24 }}
          aria-label={`${n.name} output ${o}`}
          onClick={(e) => a.portMenu(n.id, o, e.clientX, e.clientY)}
        >
          {outs.length > 1 && <em>{o}</em>}
        </Handle>
      ))}
      {slots.map((s) => {
        const x = slotX(n, s);
        const has = !!data.full[s];
        const req = need.includes(s);
        return (
          <span key={s}>
            <Handle type="target" position={Position.Bottom} id={s} className={`slot ${has ? 'full' : ''} ${req && !has ? 'need' : ''}`} style={{ left: x }} aria-label={`${SLOTS[s].label} slot`} />
            <span className="sl" style={{ left: x }}>
              <span>
                {SLOTS[s].label}
                {req && <b className="req">*</b>}
              </span>
              <button
                className="sadd nodrag nopan"
                aria-label={`Add ${SLOTS[s].label}`}
                title={`Add ${SLOTS[s].label}`}
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => a.slotMenu(n.id, s, e.clientX + 8, e.clientY + 8)}
              >
                <Icon name="add" size={13} />
              </button>
            </span>
          </span>
        );
      })}
    </div>
  );
});

export const AiComponentNode = memo(function AiComponentNode({ data }: NodeProps<StepFlowNode>) {
  const n = data.gn;
  const d = def(n.type);
  return (
    <div className={cls(data, 'sub')} style={{ ['--sc' as string]: 'var(--st-ai)' }} aria-label={n.name} data-id={n.id}>
      <Toolbar id={n.id} />
      <span className="nic">
        <Icon name={d.icon} size={16} />
      </span>
      <span className="ntx">
        <b>{n.name}</b>
        <small>{data.summary}</small>
      </span>
      <span className="nst">{statusIcon(data.status, data.bad)}</span>
      <Handle type="source" position={Position.Top} id="plug" className="port plug" title="Drag onto an AI step slot" aria-label={`${n.name} plug`} />
    </div>
  );
});

export const isComponentNode = (n: GraphNode) => isSub(n);
