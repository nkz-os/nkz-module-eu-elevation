import { useSyncExternalStore } from 'react';

/**
 * CORINE Land Cover overlay state, shared by the layer-toggle and the
 * map-layer widgets. Module-level so it survives the toggle being unmounted
 * whenever the host closes its Layers panel.
 */
export interface ClcLayerState {
  enabled: boolean;
  opacity: number; // 0..1
}

let state: ClcLayerState = { enabled: false, opacity: 0.6 };
const listeners = new Set<() => void>();

export const clcLayerStore = {
  get: (): ClcLayerState => state,
  set: (patch: Partial<ClcLayerState>): void => {
    const next = { ...state, ...patch };
    if (next.enabled === state.enabled && next.opacity === state.opacity) return;
    state = next;
    listeners.forEach((l) => l());
  },
  subscribe: (listener: () => void): (() => void) => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
};

export function useClcLayer(): [ClcLayerState, (patch: Partial<ClcLayerState>) => void] {
  const value = useSyncExternalStore(clcLayerStore.subscribe, clcLayerStore.get);
  return [value, clcLayerStore.set];
}
