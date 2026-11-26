import { useSyncExternalStore } from 'react';

/** The full player's light, chosen on each device («Меню» → «Фон плеера»): `blur` — three copies of the cover under
 * a 64 px blur, drifting (the look it had first; the blur is a GPU's work at every frame), `light` — three radial
 * gradients of the cover's colors (as soft, nearly nothing to draw). */
export type PlayerLight = 'blur' | 'light';

const KEY = 'hm-player-light';
const subs = new Set<() => void>();
let current: PlayerLight = (() => {
  try {
    return localStorage.getItem(KEY) === 'light' ? 'light' : 'blur';
  } catch {
    return 'blur';
  }
})();

export function setPlayerLight(v: PlayerLight) {
  current = v;
  try {
    localStorage.setItem(KEY, v);
  } catch {
    /* kept until the page is closed */
  }
  subs.forEach((f) => f());
}

export const usePlayerLight = () =>
  useSyncExternalStore((cb) => { subs.add(cb); return () => void subs.delete(cb); }, () => current, () => current);
