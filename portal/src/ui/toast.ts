import { create } from 'zustand';

export interface ToastItem {
  id: number;
  msg: string;
  bad: boolean;
}
interface ToastState {
  items: ToastItem[];
  push: (msg: string, bad?: boolean) => void;
  dismiss: (id: number) => void;
}
let seq = 0;
export const useToasts = create<ToastState>((set, get) => ({
  items: [],
  push: (msg, bad = false) => {
    const id = ++seq;
    set({ items: [...get().items.slice(-2), { id, msg, bad }] });
    setTimeout(() => get().dismiss(id), 3800);
  },
  dismiss: (id) => set({ items: get().items.filter((t) => t.id !== id) }),
}));
/** Show a toast (max 3 visible, 3.8 s), like the prototype. */
export const toast = (msg: string, bad = false): void => useToasts.getState().push(msg, bad);
