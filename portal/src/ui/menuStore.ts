import { create } from 'zustand';

export interface MenuItem {
  /** group heading */
  h?: string;
  l?: string;
  d?: string;
  i?: string;
  /** tile colour (CSS value) */
  c?: string;
  k?: string;
  danger?: boolean;
  mono?: boolean;
  fn?: () => void;
}
export interface MenuSpec {
  x: number;
  y: number;
  items: MenuItem[];
  search?: boolean;
  title?: string;
}
interface MenuState {
  menu: MenuSpec | null;
  open: (m: MenuSpec) => void;
  close: () => void;
}
export const useMenu = create<MenuState>((set) => ({
  menu: null,
  open: (menu) => set({ menu }),
  close: () => set({ menu: null }),
}));
export const openMenuAt = (x: number, y: number, items: MenuItem[], o: { search?: boolean; title?: string } = {}): void =>
  useMenu.getState().open({ x, y, items, search: o.search ?? true, title: o.title ?? '' });
export const openMenuFor = (el: Element, items: MenuItem[], o: { search?: boolean; title?: string } = {}): void => {
  const r = el.getBoundingClientRect();
  openMenuAt(r.left, r.bottom + 6, items, { search: false, ...o });
};
export const closeMenu = (): void => useMenu.getState().close();
