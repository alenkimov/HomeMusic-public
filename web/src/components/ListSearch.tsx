import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { TrackOut } from '../api/types';
import { useDesktop } from '../lib/desktop';
import { Icon } from './Icon';

/** A row the search of a list found: its place in the whole list and, when the words were found in its lyrics,
 * the line they are in. */
export interface Found {
  index: number;
  line?: string;
}

/** The search of a list, as its bar (`ListBar`) shows it. */
export interface ListSearch {
  /** the rows to show (null: no search — the whole list) */
  found: Found[] | null;
  open: boolean;
  /** a phone's search takes the whole top of the page: the head of the list steps aside for what is found */
  alone: boolean;
  show: () => void;
  close: () => void;
  /** the field itself, for the bar */
  field: ReactNode;
}

/** The field of a bar's search, and what is typed in it: the magnifier of the bar or Ctrl+F (instead of the browser's
 * own find, which sees only the rows on the page) opens it in the bar itself; Esc closes it and clears it. What is
 * searched, and how, is the page's: a list of tracks looks through its own rows (`useListSearch`), «История» asks the
 * Hub. `field(count)`: the field, with how many were found. */
export function useSearchField(placeholder: string): { q: string; open: boolean; alone: boolean; show: () => void; close: () => void; field: (count?: number | null) => ReactNode } {
  const desktop = useDesktop();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState('');
  const input = useRef<HTMLInputElement>(null);

  const show = () => {
    if (!desktop) window.scrollTo(0, 0); // what is found starts right under the field
    setOpen(true);
    window.setTimeout(() => {
      input.current?.focus();
      input.current?.select();
    }, 0);
  };
  const close = () => {
    setOpen(false);
    setQ('');
  };

  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && !e.shiftKey && !e.altKey && e.code === 'KeyF') {
        e.preventDefault();
        show();
      } else if (e.key === 'Escape' && open) close();
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, desktop]);

  const field = (count?: number | null) => (
    <div className="flex h-10 min-w-0 flex-1 items-center gap-2 rounded-xl bg-fg/10 pl-3 pr-1" role="search">
      <Icon name="search" size={18} className="shrink-0 text-muted" />
      <input
        ref={input}
        autoFocus
        type="search"
        enterKeyHint="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
        placeholder={placeholder}
        aria-label={placeholder}
        className="min-w-0 flex-1 bg-transparent text-[15px] outline-none placeholder:text-dim [&::-webkit-search-cancel-button]:hidden"
      />
      {q.trim() && count !== null && count !== undefined && <span className="shrink-0 text-xs tabular-nums text-muted">{count}</span>}
      <button
        type="button"
        aria-label={q ? 'Стереть' : 'Закрыть поиск'}
        title={q ? 'Стереть' : 'Закрыть поиск'}
        onClick={() => {
          if (!q) return close();
          setQ('');
          input.current?.focus();
        }}
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-muted hover:text-fg"
      >
        <Icon name="x" size={16} />
      </button>
    </div>
  );
  return { q, open, alone: open && !desktop, show, close, field };
}

/** The search inside a list of tracks (a playlist, «Любимое»): by title, artist and album, and inside the
 * lyrics the Hub keeps (`/api/lyrics/search`). */
export function useListSearch(tracks: readonly TrackOut[], placeholder = 'Название, исполнитель или строчка'): ListSearch {
  const f = useSearchField(placeholder);
  const [inLyrics, setInLyrics] = useState<Map<string, string>>(new Map());
  const needle = f.q.trim().toLocaleLowerCase('ru');

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

  return { found, open: f.open, alone: f.alone, show: f.show, close: f.close, field: f.field(found?.length) };
}
