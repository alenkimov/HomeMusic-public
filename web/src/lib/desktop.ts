import { useSyncExternalStore } from 'react';

/** A computer: a wide window with a mouse. Phones, tablets and the small windows of a browser are not. */
const QUERY = '(min-width: 1024px) and (hover: hover) and (pointer: fine)';

const mql = typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(QUERY) : null;

/** The desktop-only extras (the living «Моя волна» hero): a browser on a
 * computer. Read it again on every render — the window can be resized or moved to another screen. */
export const isDesktop = (): boolean => !!mql?.matches;

const subscribe = (cb: () => void) => {
  mql?.addEventListener('change', cb);
  return () => mql?.removeEventListener('change', cb);
};

/** `isDesktop()` that re-renders the component when it changes (no reload needed). */
export const useDesktop = (): boolean => useSyncExternalStore(subscribe, isDesktop, () => false);
