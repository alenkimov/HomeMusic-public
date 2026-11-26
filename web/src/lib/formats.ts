/** What this browser's `<audio>` cannot play, of the formats the library has, and what else it can: the Hub serves a
 * file in a format it cannot play as its copy (`cant`, `can` of `/api/listen/stream`) — Opus put into MP4 as it is
 * where the browser plays that (`can=opus-mp4`: Safari of iOS 17 and later), else AAC. Chrome and Firefox play them
 * all.
 *
 * Safari is never trusted with Ogg, whatever it answers: it says it plays Ogg Opus and does not, or plays it with the
 * time wrong (WebKit bug 293310, Safari 18.5–26.1) — as Navidrome's player does not trust it either. */

const TESTS: Record<string, string[]> = {
  ogg: ['audio/ogg; codecs="opus"', 'audio/ogg; codecs="vorbis"'],
  webm: ['audio/webm; codecs="opus"'],
  flac: ['audio/flac'],
};

/** Every format the Hub can serve another way: for a browser that said it plays a file and then did not. */
export const CANT_ANY = Object.keys(TESTS).join(',');

export interface Formats {
  cant: string;
  can: string;
}

/** Safari's engine — every browser of an iPhone, Safari of a Mac — with its major version (0: unknown). */
function webkit(): { on: boolean; version: number } {
  const ua = navigator.userAgent;
  const on = /AppleWebKit/.test(ua) && !/Chrome\/|Chromium|Android|Edg\//.test(ua);
  const v = /Version\/(\d+)/.exec(ua) ?? /OS (\d+)_\d/.exec(ua);
  return { on, version: v ? Number(v[1]) : 0 };
}

let found: Formats | null = null;

export function formats(): Formats {
  if (found) return found;
  const a = typeof document !== 'undefined' ? document.createElement('audio') : null;
  if (!a) return (found = { cant: '', can: '' });
  const plays = (t: string) => !!a.canPlayType(t).replace(/no/, '');
  const safari = webkit();
  const cant = Object.entries(TESTS)
    .filter(([f, types]) => (f === 'ogg' && safari.on) || types.some((t) => !plays(t)))
    .map(([f]) => f);
  // Opus in MP4: what the browser says, or — as Jellyfin tells it — Safari 17 and later that plays Opus in CAF
  const opusMp4 = plays('audio/mp4; codecs="opus"') || (safari.on && safari.version >= 17 && plays('audio/x-caf; codecs="opus"'));
  return (found = { cant: cant.join(','), can: opusMp4 ? 'opus-mp4' : '' });
}

/** A file in any format the Hub can serve another way, and in a format every browser plays (AAC). */
export const ANY_FORMAT: Formats = { cant: CANT_ANY, can: '' };
export const AS_IT_IS: Formats = { cant: '', can: '' };

/** A copy kept on the device (its `Content-Type`) is in a format this browser does not play: one fetched before the
 * browser was told so — an Ogg Opus file kept on an iPhone before Safari was given Opus in MP4 or AAC. */
export function cantPlayType(contentType: string | null | undefined): boolean {
  const m = /^audio\/(ogg|webm)\b/i.exec((contentType ?? '').trim());
  return !!m && formats().cant.split(',').includes(m[1].toLowerCase());
}
