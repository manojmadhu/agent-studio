import { useEffect } from 'react';
import { create } from 'zustand';

export type Crumb = [label: string, to: string];
interface CrumbState {
  crumbs: Crumb[];
  set: (c: Crumb[]) => void;
}
export const useCrumbStore = create<CrumbState>((set) => ({ crumbs: [], set: (crumbs) => set({ crumbs }) }));

/** Set the top-bar breadcrumbs for the current screen. */
export function useCrumbs(c: Crumb[]): void {
  const key = JSON.stringify(c);
  useEffect(() => {
    useCrumbStore.getState().set(JSON.parse(key) as Crumb[]);
  }, [key]);
}
