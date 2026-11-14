import type { SyntheticEvent } from 'react';

/** The catalog's pictures live on two hosts with the same paths, and a network may be refused by one of them
 * (an artist's photo that never shows while everything else does). A picture that did not load is asked of the
 * other host, once. */
const HOSTS = ['cdn-images.dzcdn.net', 'e-cdns-images.dzcdn.net'];

export function otherHost(src: string): string | null {
  for (const [i, h] of HOSTS.entries()) if (src.startsWith(`https://${h}/`)) return `https://${HOSTS[1 - i]}/${src.slice(`https://${h}/`.length)}`;
  return null;
}

/** `onError` of an `<img>`: ask the other host for it. False: there is none, or it was asked already. */
export function retryImage(e: SyntheticEvent<HTMLImageElement>): boolean {
  const img = e.currentTarget;
  const alt = otherHost(img.src);
  if (!alt || img.dataset.retried === alt || img.dataset.retried === img.src) return false;
  img.dataset.retried = alt;
  img.src = alt;
  return true;
}
