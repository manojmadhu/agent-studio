import { createContext, useContext } from 'react';
import type { SlotName } from '../../api/types';

/** Callbacks the canvas nodes/edges need (provided by BuilderPage). */
export interface BuilderActions {
  testNode: (id: string) => void;
  configure: (id: string) => void;
  slotMenu: (hostId: string, slot: SlotName, x: number, y: number, pos?: [number, number]) => void;
  portMenu: (fromId: string, port: string, x: number, y: number, pos?: [number, number]) => void;
}
export const BuilderActionsCtx = createContext<BuilderActions | null>(null);
export function useBuilderActions(): BuilderActions {
  const c = useContext(BuilderActionsCtx);
  if (!c) throw new Error('BuilderActionsCtx missing');
  return c;
}
