/** Transient canvas UI state (hover, active connection drag). Not part of undo history. */
import { create } from 'zustand';
import type { DragSpec } from './ops';

interface CanvasUi {
  hovE: string | null;
  drag: DragSpec | null;
  hoverTarget: string | null;
  setHovE: (id: string | null) => void;
  setDrag: (d: DragSpec | null) => void;
  setHoverTarget: (id: string | null) => void;
}
export const useCanvasUi = create<CanvasUi>((set) => ({
  hovE: null,
  drag: null,
  hoverTarget: null,
  setHovE: (hovE) => set({ hovE }),
  setDrag: (drag) => set({ drag, hoverTarget: null }),
  setHoverTarget: (hoverTarget) => set({ hoverTarget }),
}));
