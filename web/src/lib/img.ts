/** The image CDNs of the catalogs and the services whose pictures the Hub gets and keeps (`hub/app/covers.py`,
 * `ALLOWED` — the same list). */
const PROXIED = ['scdn.co', 'spotifycdn.com', 'dzcdn.net', 'avatars.yandex.net', 'avatars.mds.yandex.net', 'userapi.com',
  'ytimg.com', 'sndcdn.com', 'lastfm.freetls.fastly.net', 'mzstatic.com'];

function proxied(src: string): boolean {
  if (!src.startsWith('https://')) return false;
  try {
    const host = new URL(src).hostname.toLowerCase();
    return PROXIED.some((d) => host === d || host.endsWith(`.${d}`));
  } catch {
    return false;
  }
}

/** The address a picture is asked at: a catalog's picture through the Hub (`/api/covers`), which asks its CDN once
 * and keeps it — a device needs no way to the CDN of its own (some networks don't reach Spotify's), and the service
 * worker keeps it for when there is no network. The Hub's own pictures and the rest as they are. */
export function imageUrl<T extends string | null | undefined>(src: T): T | string {
  return src && proxied(src) ? `/api/covers?url=${encodeURIComponent(src)}` : src;
}
