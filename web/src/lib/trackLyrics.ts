import { useEffect, useSyncExternalStore } from 'react';
import { api } from '../api/client';
import type { LyricsOut } from './lyrics';

/** The texts of the tracks that play, kept in memory: the text's own screen (`components/Lyrics.tsx`) and the line
 * shown while the track is sought through (`components/SeekLine.tsx`) read the same one, asked for once — when the
 * track starts, so that it is there by the time it is needed. */

const KEEP = 30;
const texts = new Map<string, LyricsOut>();
const asked = new Map<string, Promise<LyricsOut | null>>();
const subs = new Set<() => void>();
let version = 0;

const keyOf = (ref: string, songId?: string | null) => `${songId ?? ''}|${ref}`;
const subscribe = (fn: () => void) => {
  subs.add(fn);
  return () => void subs.delete(fn);
};

/** The text as it is now (found anew, another source's, shifted in time): everything that shows it follows. */
export function putLyrics(ref: string, songId: string | null | undefined, text: LyricsOut) {
  const key = keyOf(ref, songId);
  texts.delete(key);
  texts.set(key, text);
  for (const old of texts.keys()) {
    if (texts.size <= KEEP) break;
    texts.delete(old);
  }
  version++;
  subs.forEach((f) => f());
}

export const knownLyrics = (ref: string, songId?: string | null): LyricsOut | undefined => texts.get(keyOf(ref, songId));

/** The track's text: from memory, or asked of the Hub (once, however many ask). null: the Hub did not answer. */
export function loadLyrics(ref: string, songId?: string | null): Promise<LyricsOut | null> {
  const key = keyOf(ref, songId);
  const have = texts.get(key);
  if (have) return Promise.resolve(have);
  let p = asked.get(key);
  if (!p) {
    p = api
      .lyrics({ nd_song_id: songId ?? null, ref })
      .then(
        (r) => {
          putLyrics(ref, songId, r);
          return r as LyricsOut | null;
        },
        () => null,
      )
      .finally(() => asked.delete(key));
    asked.set(key, p);
  }
  return p;
}

/** The text of a track, once it is known (asked for when the track is first seen). */
export function useTrackLyrics(ref?: string | null, songId?: string | null): LyricsOut | undefined {
  useEffect(() => {
    if (ref) void loadLyrics(ref, songId);
  }, [ref, songId]);
  useSyncExternalStore(subscribe, () => version);
  return ref ? knownLyrics(ref, songId) : undefined;
}
