/**
 * Builder side panels (v1.1). Both are hidden by default:
 * - the step palette opens from the + buttons and closes after a step is added or on an outside click;
 * - the configure panel is visible while a step/connection is selected, or when pipeline settings are opened,
 *   and closes on an outside click.
 */
import { create } from 'zustand';
import { useEditor } from './store';

interface PanelState {
  palette: boolean;
  pipelineSettings: boolean;
  setPalette: (open: boolean) => void;
  togglePalette: () => void;
  openPipelineSettings: () => void;
  closeConfigure: () => void;
}

export const usePanels = create<PanelState>((set, get) => ({
  palette: false,
  pipelineSettings: false,
  setPalette: (palette) => set({ palette }),
  togglePalette: () => set({ palette: !get().palette }),
  openPipelineSettings: () => {
    const s = useEditor.getState();
    if (s.sel.node || s.sel.edge) s.select({});
    set({ pipelineSettings: true, palette: false });
  },
  closeConfigure: () => {
    const s = useEditor.getState();
    if (s.sel.node || s.sel.edge) s.select({});
    set({ pipelineSettings: false });
  },
}));

/** Whether the configure panel is showing. */
export function useConfigureOpen(): boolean {
  const sel = useEditor((s) => s.sel);
  const pipe = usePanels((s) => s.pipelineSettings);
  return !!(sel.node || sel.edge) || pipe;
}

/** Elements that count as "inside" for the outside-click rule (menus, dialogs and toasts float above the builder). */
const KEEP_OPEN = '.menu,.scrim,.dialog,.toasts,.toast,[role=dialog],[data-keep-panels]';

/**
 * Pointer-down handler for the whole document. Closes the palette and the configure panel when the user presses
 * outside them. Pressing a step or a connection on the canvas keeps (or switches) the configure panel.
 */
export function onOutsidePointer(e: PointerEvent): void {
  const t = e.target as Element | null;
  if (!t || !t.isConnected || t.closest(KEEP_OPEN)) return;
  const p = usePanels.getState();
  if (p.palette && !t.closest('#pal,[data-act=openpal]')) p.setPalette(false);
  const onCanvasItem = t.closest('.react-flow__node,.react-flow__edge,.edel,[data-e]');
  // the step list and its + buttons keep the selection, so a clicked step is added after the selected one
  if (!t.closest('#insp,#pal,[data-act=openpal],[data-act=pipeline-settings]') && !onCanvasItem) p.closeConfigure();
  else if (onCanvasItem && p.pipelineSettings) usePanels.setState({ pipelineSettings: false });
}
