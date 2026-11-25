/** What this browser's `<audio>` cannot play, of the formats the library has: the Hub serves a file in one of them as
 * its AAC copy (`cant` of `/api/listen/stream`). Safari of an iPhone plays no Ogg — every Opus file of the library is
 * Ogg Opus; Chrome and Firefox play them all. */

const TESTS: Record<string, string[]> = {
  ogg: ['audio/ogg; codecs="opus"', 'audio/ogg; codecs="vorbis"'],
  webm: ['audio/webm; codecs="opus"'],
  flac: ['audio/flac'],
};

/** Every format the Hub can serve another way: for a browser that said it plays a file and then did not. */
export const CANT_ANY = Object.keys(TESTS).join(',');

let found: string | null = null;

/** «ogg,webm»: what does not play here; '' — everything does. */
export function cantPlay(): string {
  if (found === null) {
    const a = typeof document !== 'undefined' ? document.createElement('audio') : null;
    found = a ? Object.entries(TESTS).filter(([, types]) => types.some((t) => !a.canPlayType(t))).map(([f]) => f).join(',') : '';
  }
  return found;
}
