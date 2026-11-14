import type { SyntheticEvent } from 'react';

/** The catalog's pictures live on two hosts with the same paths. The one its answers name (`cdn-images`) is a cache
 * that refuses some networks whatever it has not kept yet — an artist's photo or a cover that never shows while its
 * neighbours do; the other (`e-cdns-images`) is where the pictures come from and answers. So a picture is asked of
 * the second, and of the first if that one fails. */
const HOSTS = ['e-cdns-images.dzcdn.net', 'cdn-images.dzcdn.net'];
const at = (h: string) => `https://${h}/`;

/** The address a picture is asked at. */
export function imageUrl<T extends string | null | undefined>(src: T): T | string {
  return src && src.startsWith(at(HOSTS[1])) ? at(HOSTS[0]) + src.slice(at(HOSTS[1]).length) : src;
}

function otherHost(src: string): string | null {
  for (const [i, h] of HOSTS.entries()) if (src.startsWith(at(h))) return at(HOSTS[1 - i]) + src.slice(at(h).length);
  return null;
}

/** `onError` of an `<img>`: ask the other host for it. False: there is none, or it was asked already. */
export function retryImage(e: SyntheticEvent<HTMLImageElement>): boolean {
  const img = e.currentTarget;
  const alt = otherHost(img.src);
  if (!alt || img.dataset.retried) return false;
  img.dataset.retried = '1';
  img.src = alt;
  return true;
}
