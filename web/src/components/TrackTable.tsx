import { createContext, useContext, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import type { TrackAudience, TrackOut } from '../api/types';
import { useDesktop } from '../lib/desktop';
import { audienceKey, useTrackAudience } from '../lib/trackAudience';
import { fitTrackColumns, trackGridMinWidth, useTrackColumns, type TrackColumns, type TrackTableKind } from '../lib/trackColumns';

interface Table {
  visible: TrackColumns;
  audience: ReadonlyMap<string, TrackAudience>;
  compact: boolean;
  twoLists: boolean;
}

const TrackTableContext = createContext<Table | null>(null);
export const useTrackTable = () => useContext(TrackTableContext);

export function TrackTable({ tracks, numbered = false, album = false, added = false, tags = false, plays = false,
  periodPlays = false, kind = 'library', children }: {
  tracks: readonly TrackOut[];
  numbered?: boolean;
  album?: boolean;
  added?: boolean;
  tags?: boolean;
  plays?: boolean;
  periodPlays?: boolean;
  kind?: TrackTableKind;
  children: ReactNode;
}) {
  const root = useRef<HTMLDivElement>(null);
  const desktop = useDesktop();
  const settings = useTrackColumns();
  const [width, setWidth] = useState(Infinity);
  useLayoutEffect(() => {
    if (!desktop || !root.current) return;
    const el = root.current;
    const measure = () => {
      const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      setWidth(el.getBoundingClientRect().width / rem);
    };
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [desktop]);

  const allowed = {
    ...settings, album: album && settings.album, added: added && settings.added,
    tagLang: tags && settings.tagLang, tagGenre: tags && settings.tagGenre, tagOwn: tags && settings.tagOwn,
    plays: periodPlays || (plays && settings.plays),
  };
  const twoLists = kind === 'popular' && tracks.length > 5 && desktop && width !== Infinity
    && width >= 2 * trackGridMinWidth(numbered, allowed) + 1;
  const listWidth = twoLists ? (width - 1) / 2 : width;
  const potential = fitTrackColumns(listWidth, numbered, allowed, kind);
  const compact = width < trackGridMinWidth(numbered);
  const audience = useTrackAudience(root, tracks, desktop && !compact && potential.spotify,
    desktop && !compact && (potential.lastfm || fitTrackColumns(listWidth, numbered, { ...allowed, spotify: false }, kind).lastfm),
    twoLists);
  const visible = fitTrackColumns(listWidth, numbered, {
    ...allowed,
    spotify: allowed.spotify && tracks.some((track) => (track.spotify_plays ?? audience.get(audienceKey(track))?.spotify_plays) != null),
    lastfm: allowed.lastfm && tracks.some((track) => (track.lastfm_listeners ?? audience.get(audienceKey(track))?.lastfm_listeners) != null),
  }, kind);

  return (
    <TrackTableContext.Provider value={{ visible, audience, compact, twoLists }}>
      <div ref={root} className="min-w-0">{children}</div>
    </TrackTableContext.Provider>
  );
}
