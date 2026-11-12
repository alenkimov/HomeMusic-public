import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { NdSongOut } from '../api/types';
import { Icon } from './Icon';
import { IconButton } from './ui';

/** A row the search of a list found: its place in the whole list and, when the words were found in its lyrics,
 * the line they are in. */
export interface Found {
  index: number;
  line?: string;
}

/** The search inside a list of tracks (a playlist, «Любимое»): by title, artist and album, and inside the
 * lyrics the Hub keeps (`/api/lyrics/search`). A round button floats at the bottom right of the page; a click on it
 * or Ctrl+F (instead of the browser's own find, which sees only the rows on the page, not their texts) opens the
 * field. Esc closes it and brings the whole list back.
 * Returns the rows to show (null: no search — the whole list) and the floating control to render. */
export function useListSearch(tracks: readonly NdSongOut[], placeholder = 'Название, исполнитель или строчка из текста'): { found: Found[] | null; control: ReactNode; q: string } {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const [inLyrics, setInLyrics] = useState<Map<string, string>>(new Map());
  const input = useRef<HTMLInputElement>(null);
  const needle = q.trim().toLocaleLowerCase('ru');

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.code === 'KeyF') {
        e.preventDefault();
        setOpen(true);
        window.setTimeout(() => {
          input.current?.focus();
          input.current?.select();
        }, 0);
      } else if (e.key === 'Escape' && open) {
        setOpen(false);
        setQ('');
      }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [open]);

  // inside the lyrics: asked of the Hub, a moment after the typing stops
  useEffect(() => {
    // one letter finds everything; one hieroglyph is a word
    if (needle.length < 2 && ![...needle].some((ch) => ch.charCodeAt(0) >= 0x2e80)) {
      setInLyrics(new Map());
      return;
    }
    let alive = true;
    const timer = window.setTimeout(() => {
      api.lyricsSearch(needle)
        .then((hits) => {
          if (!alive) return;
          const m = new Map<string, string>();
          for (const h of hits) {
            if (h.nd_song_id) m.set(h.nd_song_id, h.line);
            if (h.ref) m.set(h.ref, h.line);
          }
          setInLyrics(m);
        })
        .catch(() => alive && setInLyrics(new Map()));
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [needle]);

  const found = useMemo<Found[] | null>(() => {
    if (!needle) return null;
    const out: Found[] = [];
    tracks.forEach((t, index) => {
      if (`${t.title} ${t.artists?.join(' ') ?? ''} ${t.artist} ${t.album_title ?? ''}`.toLocaleLowerCase('ru').includes(needle)) out.push({ index });
      else {
        const line = (t.nd_song_id && inLyrics.get(t.nd_song_id)) ?? inLyrics.get(t.ref);
        if (line !== undefined && line !== null && line !== '') out.push({ index, line });
        else if (line === '') out.push({ index, line: '' });
      }
    });
    return out;
  }, [tracks, needle, inLyrics]);

  const close = () => {
    setOpen(false);
    setQ('');
  };
  const control = (
    <div className="pointer-events-none fixed bottom-[calc(var(--tabbar-h)+var(--player-h,0px)+16px+env(safe-area-inset-bottom))] right-4 z-40 flex justify-end [body[data-cols-menu]_&]:hidden">
      {open ? (
        <div className="anim-up pointer-events-auto flex h-14 w-[26rem] max-w-[calc(100vw-2rem)] items-center gap-1 rounded-full border border-line bg-raised pl-4 pr-1 shadow-2xl" role="search">
          <Icon name="search" size={20} className="shrink-0 text-muted" />
          <input
            ref={input}
            autoFocus
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={placeholder}
            aria-label="Поиск по списку"
            className="min-w-0 flex-1 bg-transparent px-2 text-[15px] outline-none placeholder:text-dim"
          />
          {found && <span className="shrink-0 px-1 text-xs tabular-nums text-muted">{found.length}</span>}
          <IconButton icon="x" size={18} label="Закрыть поиск" onClick={close} className="text-muted hover:text-fg" />
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Поиск по списку (Ctrl+F)"
          title="Поиск по списку (Ctrl+F)"
          className="pointer-events-auto flex h-14 w-14 items-center justify-center rounded-full bg-accent text-on-accent shadow-2xl transition hover:scale-105 active:scale-95"
        >
          <Icon name="search" size={24} stroke={2.4} />
        </button>
      )}
    </div>
  );
  return { found, control, q: needle };
}
