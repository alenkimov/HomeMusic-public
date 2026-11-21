import { useEffect, useRef, useState, type RefObject } from 'react';
import { api } from '../api/client';
import type { TrackAudience, TrackAudienceRequest, TrackOut } from '../api/types';

export function audienceKey(track: TrackOut): string {
  return JSON.stringify([track.ref, track.nd_song_id, track.library_track_id, track.spotify_id, track.artist, track.title]);
}

/** Fetch missing counts for visible rows in bounded batches, outside the list request. */
export function useTrackAudience(root: RefObject<HTMLDivElement | null>, tracks: readonly TrackOut[], spotify: boolean, lastfm: boolean, twoLists: boolean) {
  const cache = useRef(new Map<string, TrackAudience>());
  const [audience, setAudience] = useState<ReadonlyMap<string, TrackAudience>>(() => new Map());

  useEffect(() => {
    const table = root.current;
    if ((!spotify && !lastfm) || !table || typeof IntersectionObserver === 'undefined') return;
    const index = new Map(tracks.map((track) => [audienceKey(track), track]));
    const pending = new Map<string, TrackAudienceRequest>();
    const queued = new Set<string>();
    const controller = new AbortController();
    let timer: number | undefined;
    let loading = false;

    async function flush() {
      if (loading || !pending.size || controller.signal.aborted) return;
      loading = true;
      const batch = [...pending].slice(0, 16);
      for (const [key] of batch) pending.delete(key);
      try {
        const result = await api.trackAudience(batch.map(([, input]) => input), controller.signal);
        if (controller.signal.aborted) return;
        batch.forEach(([key, input], i) => {
          const value = result[i];
          if (!value) return;
          const known = cache.current.get(key) ?? {};
          cache.current.set(key, {
            ...known,
            ...(input.spotify ? { spotify_plays: value.spotify_plays ?? null } : {}),
            ...(input.lastfm ? { lastfm_listeners: value.lastfm_listeners ?? null } : {}),
          });
        });
        setAudience(new Map(cache.current));
      } catch {
        // Another visit or a change in visible columns can retry the failed batch.
      } finally {
        loading = false;
        void flush();
      }
    }

    const observer = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        const key = (entry.target as HTMLElement).dataset.trackAudience ?? '';
        const track = index.get(key);
        if (!track || queued.has(key)) continue;
        const known = cache.current.get(key);
        const sp = spotify && track.spotify_plays == null && known?.spotify_plays === undefined;
        const lf = lastfm && track.lastfm_listeners == null && known?.lastfm_listeners === undefined;
        if (!sp && !lf) continue;
        queued.add(key);
        pending.set(key, {
          ref: track.ref, nd_song_id: track.nd_song_id, library_track_id: track.library_track_id,
          spotify_id: track.spotify_id, artist: (track.artists?.[0] || track.artist).slice(0, 512),
          title: track.title.slice(0, 512), spotify: sp, lastfm: lf,
        });
      }
      if (pending.size && timer === undefined) {
        timer = window.setTimeout(() => {
          timer = undefined;
          void flush();
        }, 50);
      }
    });
    table.querySelectorAll('[data-track-audience]').forEach((row) => observer.observe(row));
    return () => {
      observer.disconnect();
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [root, tracks, spotify, lastfm, twoLists]);

  return audience;
}
