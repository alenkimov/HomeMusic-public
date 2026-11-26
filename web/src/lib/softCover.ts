import { useEffect, useState } from 'react';

/** A cover as a soft light: the light behind the head of an album, a playlist, an artist without a wide picture.
 *
 * It was the cover itself under `filter: blur(64px)` across the whole width, under a gradient mask: Safari of an iPhone
 * works that filter out on the main thread over millions of pixels, and an album or a playlist opened held the whole
 * app for a second or two. Now the blur is made once, of the cover drawn 24 pixels wide (a box blur in JS: a fraction
 * of a millisecond), and the tiny picture is stretched over the place — stretched, it is as soft as the filter made
 * it — with no filter at all. A cover that cannot be read (another site's, without CORS) gives no light. */

const SIZE = 24;
const cache = new Map<string, string | null>();
const waiting = new Map<string, Promise<string | null>>();

function blur(px: Uint8ClampedArray, radius: number) {
  const tmp = new Float32Array(px.length);
  const pass = (from: ArrayLike<number>, to: Float32Array | Uint8ClampedArray, horizontal: boolean) => {
    for (let y = 0; y < SIZE; y++) {
      for (let x = 0; x < SIZE; x++) {
        let r = 0, g = 0, b = 0, n = 0;
        for (let d = -radius; d <= radius; d++) {
          const xx = horizontal ? Math.min(SIZE - 1, Math.max(0, x + d)) : x;
          const yy = horizontal ? y : Math.min(SIZE - 1, Math.max(0, y + d));
          const i = (yy * SIZE + xx) * 4;
          r += from[i]; g += from[i + 1]; b += from[i + 2]; n++;
        }
        const o = (y * SIZE + x) * 4;
        to[o] = r / n; to[o + 1] = g / n; to[o + 2] = b / n; to[o + 3] = 255;
      }
    }
  };
  for (let k = 0; k < 2; k++) { // twice: near a gaussian
    pass(px, tmp, true);
    pass(tmp, px, false);
  }
}

function saturate(px: Uint8ClampedArray, amount: number) {
  for (let i = 0; i < px.length; i += 4) {
    const grey = 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2];
    px[i] = grey + (px[i] - grey) * amount;
    px[i + 1] = grey + (px[i + 1] - grey) * amount;
    px[i + 2] = grey + (px[i + 2] - grey) * amount;
  }
}

export interface SoftLook {
  /** the colors a half stronger */
  vivid?: boolean;
}

const keyOf = (url: string, look: SoftLook) => `${look.vivid ? 'v' : ''}:${url}`;

/** The soft picture of `url` as a data address (null: not readable). */
export function softCover(url: string, look: SoftLook = {}): Promise<string | null> {
  const key = keyOf(url, look);
  if (cache.has(key)) return Promise.resolve(cache.get(key) ?? null);
  const pending = waiting.get(key);
  if (pending) return pending;
  const work = new Promise<string | null>((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.decoding = 'async';
    const done = (r: string | null) => {
      if (cache.size > 100) cache.clear();
      cache.set(key, r);
      waiting.delete(key);
      resolve(r);
    };
    img.onload = () => {
      try {
        const c = document.createElement('canvas');
        c.width = c.height = SIZE;
        const g = c.getContext('2d', { willReadFrequently: true });
        if (!g) return done(null);
        g.imageSmoothingQuality = 'high';
        g.drawImage(img, 0, 0, SIZE, SIZE);
        const data = g.getImageData(0, 0, SIZE, SIZE);
        blur(data.data, 2);
        if (look.vivid) saturate(data.data, 1.5);
        g.putImageData(data, 0, 0);
        done(c.toDataURL('image/png'));
      } catch {
        done(null); // tainted: another site's cover without CORS
      }
    };
    img.onerror = () => done(null);
    img.src = url;
  });
  waiting.set(key, work);
  return work;
}

/** The soft picture of a cover, once it is made (at once when it was made before). */
export function useSoftCover(url: string | null | undefined, look: SoftLook = {}): string | null {
  const { vivid = false } = look;
  const key = url ? keyOf(url, { vivid }) : '';
  const [made, setMade] = useState<{ key: string; src: string | null }>(() => ({ key, src: url ? cache.get(key) ?? null : null }));
  useEffect(() => {
    if (!url) return;
    let on = true;
    void softCover(url, { vivid }).then((src) => on && setMade({ key, src }));
    return () => { on = false; };
  }, [url, vivid, key]);
  return made.key === key ? made.src : (url ? cache.get(key) ?? null : null);
}


// --- colors of a cover: the full player's drifting light -------------------------------------------------------

export type Rgb = [number, number, number];
const colorCache = new Map<string, Rgb[] | null>();

/** Three colors of a cover — its upper left, its middle, its lower right, blurred and a half stronger — for the
 * full player's light: radial gradients that drift. A gradient has a soft edge by itself and costs nothing to draw;
 * a picture with a soft edge of its own lost the edge in a browser (its square corners came out dark). */
export function coverColors(url: string): Promise<Rgb[] | null> {
  if (colorCache.has(url)) return Promise.resolve(colorCache.get(url) ?? null);
  return new Promise((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.decoding = 'async';
    const done = (r: Rgb[] | null) => {
      if (colorCache.size > 100) colorCache.clear();
      colorCache.set(url, r);
      resolve(r);
    };
    img.onload = () => {
      try {
        const c = document.createElement('canvas');
        c.width = c.height = SIZE;
        const g = c.getContext('2d', { willReadFrequently: true });
        if (!g) return done(null);
        g.drawImage(img, 0, 0, SIZE, SIZE);
        const px = g.getImageData(0, 0, SIZE, SIZE).data;
        saturate(px, 1.5);
        const mean = (x0: number, y0: number, w: number): Rgb => {
          let r = 0, gg = 0, b = 0, n = 0;
          for (let y = y0; y < y0 + w; y++) for (let x = x0; x < x0 + w; x++) {
            const i = (y * SIZE + x) * 4;
            r += px[i]; gg += px[i + 1]; b += px[i + 2]; n++;
          }
          return [Math.round(r / n), Math.round(gg / n), Math.round(b / n)];
        };
        const q = SIZE / 2;
        done([mean(0, 0, q), mean(q / 2, q / 2, q), mean(q, q, q)]);
      } catch {
        done(null);
      }
    };
    img.onerror = () => done(null);
    img.src = url;
  });
}

export function useCoverColors(url: string | null | undefined): Rgb[] | null {
  const [made, setMade] = useState<{ url: string; colors: Rgb[] | null } | null>(null);
  useEffect(() => {
    if (!url) return;
    let on = true;
    void coverColors(url).then((colors) => on && setMade({ url, colors }));
    return () => { on = false; };
  }, [url]);
  if (!url) return null;
  return made?.url === url ? made.colors : colorCache.get(url) ?? null;
}

