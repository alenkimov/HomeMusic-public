import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, errorMessage, isAbort, peekCache, withQuery } from '../api/client';
import type { Candidate, LibTrackOut, ProviderSearchOut, SearchOut, SearchRecent, SearchTracksOut, TrackOut } from '../api/types';
import { ArtistBubble, ReleaseCard, TrackRow, TrackTableHeader, listenTrack } from '../components/catalog';
import { TrackMenuButton } from '../components/TrackMenu';
import { CandidateRow, fileRef } from '../components/candidate';
import { Icon } from '../components/Icon';
import { SaveButton } from '../components/PlaylistsButton';
import { Button, Cover, Empty, ErrorState, IconButton, Notice, SectionTitle, Segmented, SkeletonRows, Spinner, cx } from '../components/ui';
import { startDownload } from '../lib/actions';
import { STANDALONE } from '../lib/embedded';
import { useHubEvent } from '../lib/events';
import { fmtDuration } from '../lib/format';
import { useAsync, useDebounced, useRememberScrollX, type AsyncState } from '../lib/hooks';
import { extendQueue, markQueued, useListen } from '../lib/listen';
import { canPlay, canPlayAll, playList } from '../lib/player';
import { Link, enc, navigate } from '../lib/router';
import { FOCUS_SEARCH } from '../components/TabBar';
import { startChatWith } from './AssistantChat';

export type SearchTab = 'catalog' | 'files' | 'reuploads';

export const searchTab = (v: string | null): SearchTab => (v === 'files' || v === 'reuploads' ? v : 'catalog');

export function SearchPage({ initialQuery, initialTab }: { initialQuery: string; initialTab: SearchTab }) {
  const [q, setQ] = useState(initialQuery);
  const [tab, setTab] = useState<SearchTab>(initialTab);
  const dq = useDebounced(q.trim(), 400);
  const inputRef = useRef<HTMLInputElement>(null);
  const shots = useRef<HTMLInputElement>(null);
  const [nothing, setNothing] = useState(false); // the search that is on the screen found nothing
  // the assistant is the same search, asked in one's own words (or with a screenshot): one button away, never guessed
  const ask = (images: File[] = []) => startChatWith(q.trim() || (images.length ? 'Найди и скачай треки с этих скриншотов' : ''), images, false);

  // Keep the query in the URL so "back" from an artist/album restores the results.
  useEffect(() => {
    const target = withQuery('/search', { q: dq, tab: tab === 'catalog' ? undefined : tab });
    if (window.location.hash !== '#' + target) navigate(target, { replace: true });
  }, [dq, tab]);

  // External navigation to /search?q=… (links, tab bar) replaces the query.
  useEffect(() => {
    setQ((cur) => (cur.trim() === initialQuery ? cur : initialQuery));
  }, [initialQuery]);
  useEffect(() => setTab(initialTab), [initialTab]);

  useEffect(() => {
    const focus = () => {
      inputRef.current?.focus();
      inputRef.current?.select();
    };
    window.addEventListener(FOCUS_SEARCH, focus);
    return () => window.removeEventListener(FOCUS_SEARCH, focus);
  }, []);

  useEffect(() => {
    if (!initialQuery) inputRef.current?.focus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div>
      <header className="pt-safe sticky top-0 z-20 bg-bg/95 px-4 pb-3 backdrop-blur">
        <h1 className="sr-only">Поиск</h1>
        <div className="relative pt-3">
          <Icon name="search" size={20} className="pointer-events-none absolute left-3.5 top-[calc(50%+6px)] -translate-y-1/2 text-dim" />
          <input
            ref={inputRef}
            type="search"
            maxLength={512}
            enterKeyHint="search"
            className={cx('input h-12 rounded-2xl border-transparent bg-surface pl-11', STANDALONE ? 'pr-20' : 'pr-11')}
            placeholder={STANDALONE ? 'Трек, исполнитель или просьба' : 'Исполнитель, альбом или трек'}
            aria-label="Поиск"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== 'Enter') return;
              (e.target as HTMLInputElement).blur();
              // Enter is asking for it: the query is kept. What is merely typed is not («still lovi» on the way to a song)
              if (q.trim().length >= 2) remember({ kind: 'query', title: q.trim() });
            }}
          />
          <div className="absolute right-0.5 top-[calc(50%+6px)] flex -translate-y-1/2 items-center">
            {q && (
              <IconButton
                icon="x"
                label="Очистить"
                className="!w-9 text-muted"
                onClick={() => {
                  setQ('');
                  inputRef.current?.focus();
                }}
              />
            )}
            {STANDALONE && (
              <>
                <IconButton icon="image" label="Скриншот или список — ассистенту" className="!w-10 text-muted" onClick={() => shots.current?.click()} />
                <input ref={shots} type="file" accept="image/*" multiple className="hidden" onChange={(e) => { const files = Array.from(e.target.files ?? []); e.target.value = ''; if (files.length) ask(files); }} />
              </>
            )}
          </div>
        </div>
        {/* where to search is asked only once there is something to search for */}
        {q.trim() && (
          <div className="mt-3">
            <Segmented<SearchTab>
              label="Где искать"
              value={tab}
              onChange={setTab}
              options={[
                { value: 'catalog', label: 'Каталог' },
                { value: 'files', label: 'Файлы' },
                { value: 'reuploads', label: 'Без цензуры' },
              ]}
              // nothing was found: one more place to ask, the fourth in the row — the assistant
              after={STANDALONE && nothing && (
                <button type="button" onClick={() => ask()} className="flex min-h-9 shrink-0 items-center gap-1.5 rounded-full bg-raised px-4 text-sm hover:bg-hover">
                  <Icon name="sparkle" size={16} className="text-accent" />
                  Спросить ассистента
                </button>
              )}
            />
          </div>
        )}
      </header>

      {!dq ? (
        <RecentSearches onPick={setQ} />
      ) : tab === 'catalog' ? (
        <CatalogResults q={dq} onNothing={setNothing} />
      ) : tab === 'files' ? (
        <ProviderResults q={dq} onNothing={setNothing} />
      ) : (
        <ReuploadResults q={dq} onNothing={setNothing} />
      )}
    </div>
  );
}

/** «Недавнее» gets a line: a result was opened, or a query asked for. It is a convenience — a line that could not
 * be written is not worth a word to the listener. */
function remember(item: Omit<SearchRecent, 'key'>) {
  void api.rememberSearch(item).catch(() => undefined);
}

const RECENT_KIND: Record<SearchRecent['kind'], string> = { query: 'Запрос', track: 'Трек', album: 'Альбом', artist: 'Исполнитель' };

/** «Недавнее»: what was opened from the results (with its picture; a tap opens it again) and the queries asked for
 * (a tap searches again). × takes a line out. */
function RecentSearches({ onPick }: { onPick: (q: string) => void }) {
  const res = useAsync((s) => api.searchHistory(s), []);
  const [expanded, setExpanded] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState<string>();
  useHubEvent('search_history', () => res.reload(true));
  useHubEvent('reconnect', () => res.reload(true));
  useEffect(() => {
    const wake = () => { if (document.visibilityState === 'visible') res.reload(true); };
    document.addEventListener('visibilitychange', wake);
    window.addEventListener('hm:resume', wake);
    window.addEventListener('focus', wake);
    window.addEventListener('online', wake);
    return () => {
      document.removeEventListener('visibilitychange', wake);
      window.removeEventListener('hm:resume', wake);
      window.removeEventListener('focus', wake);
      window.removeEventListener('online', wake);
    };
  }, [res.reload]);

  const clear = async () => {
    setClearing(true);
    setClearError(undefined);
    try {
      await api.clearSearchHistory();
      res.reload(true);
    } catch (e) {
      setClearError(`Не удалось очистить историю. ${errorMessage(e)}`);
    } finally {
      setClearing(false);
    }
  };

  const forget = (key: string) => {
    res.setData((prev) => (prev ? { items: prev.items.filter((x) => x.key !== key), frequent: prev.frequent.filter((x) => x.key !== key) } : prev));
    api.forgetSearch(key).catch((e) => {
      setClearError(`Не удалось убрать из недавнего. ${errorMessage(e)}`);
      res.reload(true);
    });
  };

  const recent = res.data?.items ?? [];
  if (!res.data && res.loading) return <SkeletonRows n={3} />;
  if (res.error && !recent.length) return <ErrorState text={res.error} onRetry={() => res.reload()} />;
  if (!recent.length) {
    return <Empty icon="search" title="Что будем слушать?" text="Ищите по каталогу Deezer и hitmoz, по своей библиотеке или по файлам в источниках." />;
  }
  return (
    <div>
      <SectionTitle
        right={
          <Button
            small
            variant="ghost"
            disabled={clearing}
            onClick={() => void clear()}
          >
            Очистить
          </Button>
        }
      >
        Недавнее
      </SectionTitle>
      {(clearError || res.error) && <div className="px-4 pb-3"><Notice>{clearError || res.error}</Notice></div>}
      <ul id="recent-searches-list">
        {(expanded ? recent : recent.slice(0, 8)).map((r) => (
          <li key={r.key} className="flex items-center pr-1 hover:bg-fg/5">
            <button type="button" className="flex min-h-14 min-w-0 flex-1 items-center gap-3 py-1.5 pl-4 text-left" onClick={() => (r.link ? navigate(r.link) : onPick(r.title))}>
              {r.kind === 'query' ? (
                <span className="flex h-10 w-10 shrink-0 items-center justify-center text-dim">
                  <Icon name="search" size={18} />
                </span>
              ) : (
                <Cover src={r.cover_url} size={40} round={r.kind === 'artist'} icon={r.kind === 'artist' ? 'user' : r.kind === 'album' ? 'disc' : 'note'} alt="" />
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[15px]">{r.title}</span>
                <span className="block truncate text-[13px] text-muted">{[RECENT_KIND[r.kind], r.subtitle].filter(Boolean).join(' · ')}</span>
              </span>
            </button>
            <IconButton icon="x" size={16} label={`Убрать из недавнего: ${r.title}`} onClick={() => forget(r.key)} className="w-10 text-dim hover:text-fg" />
          </li>
        ))}
      </ul>
      {recent.length > 8 && (
        <div className="px-4 py-2">
          <Button variant="ghost" aria-expanded={expanded} aria-controls="recent-searches-list" onClick={() => setExpanded((v) => !v)}>
            {expanded ? 'Свернуть' : 'Показать ещё'}
          </Button>
        </div>
      )}
      {!!res.data?.frequent.length && (
        <section aria-label="Вы часто ищете">
          <SectionTitle>Вы часто ищете</SectionTitle>
          <div className="flex flex-wrap gap-2 px-4">
            {res.data.frequent.map((r) => (
              <button key={r.key} type="button" onClick={() => (r.link ? navigate(r.link) : onPick(r.title))} className="flex min-h-10 max-w-full items-center gap-2 rounded-full bg-surface px-4 text-[15px] hover:bg-raised">
                {r.kind === 'query' && <Icon name="search" size={15} className="shrink-0 text-dim" />}
                <span className="truncate">{r.title}</span>
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

/** Tracks of the list from its end at which the next page starts loading. */
const MORE_AT = 10;

interface MoreTracks {
  q: string;
  first: SearchOut | undefined;
  pages: TrackOut[][];
  next: number | null;
  loading: boolean;
  failed: boolean;
}

/** Pages loaded earlier for this search are still in the request cache: "back" to the results gets them (and the
 * scroll position) again at once. */
function restoreMore(q: string, first: SearchOut | undefined): MoreTracks {
  const pages: TrackOut[][] = [];
  let next = first?.tracks_next ?? null;
  for (let p: SearchTracksOut | undefined; next !== null && (p = peekCache(withQuery('/api/search/tracks', { q, offset: next }))); ) {
    pages.push(p.tracks);
    next = p.next_offset ?? null;
  }
  return { q, first, pages, next, loading: false, failed: false };
}

/** The search's tracks, more of them as the list is scrolled (GET /api/search/tracks): the next page is asked for
 * when the row `MORE_AT` from the end comes into view, until the catalog has no more. */
function useMoreTracks(q: string, first: SearchOut | undefined) {
  const [st, setSt] = useState(() => restoreMore(q, first));
  const ctl = useRef<AbortController | null>(null);
  let cur = st;
  if (st.q !== q || st.first !== first) {
    cur = restoreMore(q, first);
    setSt(cur);
  }
  // a new search: the page being loaded for the old one is not needed
  useEffect(() => () => ctl.current?.abort(), [q, first]);

  const tracks = useMemo(() => {
    const seen = new Set<string>();
    return [...(cur.first?.tracks ?? []), ...cur.pages.flat()].filter((t) => !seen.has(t.ref) && !!seen.add(t.ref));
  }, [cur.first, cur.pages]);

  const loadMore = () => {
    if (cur.loading || cur.next === null) return;
    const { q: forQ, first: forFirst, next } = cur;
    const c = new AbortController();
    ctl.current?.abort();
    ctl.current = c;
    const same = (s: MoreTracks) => s.q === forQ && s.first === forFirst;
    setSt((s) => (same(s) ? { ...s, loading: true, failed: false } : s));
    api.searchTracks(forQ, next, c.signal).then(
      (p) => setSt((s) => (same(s) && !c.signal.aborted ? { ...s, pages: [...s.pages, p.tracks], next: p.next_offset ?? null, loading: false } : s)),
      (e) => {
        if (c.signal.aborted || isAbort(e)) return;
        setSt((s) => (same(s) ? { ...s, loading: false, failed: true } : s));
      },
    );
  };

  // the row MORE_AT from the end (the last one in a short list) is watched; when the list grows, the next one is
  const trigger = cur.next !== null && !cur.loading && !cur.failed ? Math.max(0, tracks.length - MORE_AT) : -1;
  const sentinel = useRef<HTMLDivElement>(null);
  const loadRef = useRef(loadMore);
  loadRef.current = loadMore;
  useEffect(() => {
    const el = sentinel.current;
    if (trigger < 0 || !el || typeof IntersectionObserver === 'undefined') return;
    // a row above the screen counts too: a fast fling may pass it between two frames
    const o = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting || e.boundingClientRect.bottom < 0)) loadRef.current();
    });
    o.observe(el);
    return () => o.disconnect();
  }, [trigger, tracks]);

  // the page's own player plays on through the tracks loaded after it started (the app's player keeps its queue)
  useEffect(() => {
    if (!canPlayAll()) extendQueue(tracks.map(listenTrack));
  }, [tracks]);

  return { tracks, trigger, sentinel, loading: cur.loading, failed: cur.failed, loadMore };
}

/** Says to the page whether the search on the screen found nothing (and takes it back when it leaves the screen). */
function useNothing(nothing: boolean, tell: (nothing: boolean) => void) {
  useEffect(() => {
    tell(nothing);
    return () => tell(false);
  }, [nothing, tell]);
}

function CatalogResults({ q, onNothing }: { q: string; onNothing: (nothing: boolean) => void }) {
  const res = useAsync((s) => api.search(q, s).then((out) => ({ q, out })), [q]);
  const fresh = res.data?.q === q ? res.data.out : peekCache<SearchOut>(withQuery('/api/search', { q }));
  // while a new search loads, the results of the previous one stay on screen
  const [dataQ, data] = fresh ? [q, fresh] : res.data ? [res.data.q, res.data.out] : [q, undefined];
  const more = useMoreTracks(dataQ, data);
  const artistsRow = useRememberScrollX(`search:${q}:artists`);
  const albumsRow = useRememberScrollX(`search:${q}:albums`);
  useNothing(!!data && dataQ === q && !res.error && !data.library.length && !data.artists.length && !data.albums.length && !data.tracks.length, onNothing);
  if (res.loading && !data) return <SkeletonRows n={8} />;
  if (res.error) return <ErrorState text={res.error} onRetry={() => res.reload()} />;
  if (!data) return null;
  const empty = !data.library.length && !data.artists.length && !data.albums.length && !data.tracks.length;
  return (
    <div className="pb-4">
      {data.errors.length > 0 && (
        <div className="px-4 pt-2">
          <Notice>{data.errors.join('. ')}</Notice>
        </div>
      )}
      {empty && <Empty icon="search" title="Ничего не найдено" text="Попробуйте другое написание или поищите среди файлов в источниках." />}

      {data.library.length > 0 && (
        <section aria-label="В библиотеке">
          <SectionTitle>В библиотеке</SectionTitle>
          {data.library.slice(0, 5).map((t, _, shown) => (
            <Opened key={t.id} item={{ kind: 'track', ref: `lib:${t.id}`, title: t.title, subtitle: t.artist, link: t.album_id ? `/library/album/${t.album_id}` : null }}>
              <LibraryTrackRow t={t} onPlay={canPlay() && t.nd_song_id ? () => playList(shown, (x) => x.nd_song_id, t) : undefined} />
            </Opened>
          ))}
        </section>
      )}

      {data.artists.length > 0 && (
        <section aria-label="Исполнители">
          <SectionTitle>Исполнители</SectionTitle>
          <div ref={artistsRow} className="no-scrollbar flex gap-3 overflow-x-auto px-4 pb-1">
            {data.artists.map((a) => (
              <Opened key={a.ref} item={{ kind: 'artist', ref: a.ref, title: a.name, cover_url: a.picture_small_url ?? a.picture_url, link: `/artist/${enc(a.ref)}` }}>
                <ArtistBubble artist={a} />
              </Opened>
            ))}
          </div>
        </section>
      )}

      {data.albums.length > 0 && (
        <section aria-label="Альбомы">
          <SectionTitle>Альбомы</SectionTitle>
          <div ref={albumsRow} className="no-scrollbar flex gap-4 overflow-x-auto px-4 pb-1">
            {data.albums.map((a) => (
              <Opened key={a.ref} className="w-36 shrink-0" item={{ kind: 'album', ref: a.ref, title: a.title, subtitle: a.artist, cover_url: a.cover_small_url ?? a.cover_url, link: `/album/${enc(a.ref)}` }}>
                <ReleaseCard album={a} showArtist />
              </Opened>
            ))}
          </div>
        </section>
      )}

      {more.tracks.length > 0 && (
        <section aria-label="Треки">
          <SectionTitle>Треки</SectionTitle>
          <TrackTableHeader numbered album />
          {more.tracks.map((t, i) => (
            <Opened key={t.ref} item={{ kind: 'track', ref: t.ref, title: t.title, subtitle: t.artists?.length ? t.artists.join(', ') : t.artist, cover_url: t.cover_small_url ?? t.cover_url, link: t.album_ref ? `/album/${enc(t.album_ref)}` : null }}>
              {i === more.trigger && <div ref={more.sentinel} aria-hidden />}
              <TrackRow track={t} showAlbum queue={more.tracks} index={i + 1} />
            </Opened>
          ))}
          {more.loading && (
            <div className="flex justify-center py-4 text-muted" role="status" aria-label="Загружаю ещё">
              <Spinner size={20} />
            </div>
          )}
          {more.failed && (
            <div className="flex items-center justify-center gap-3 px-4 py-3 text-sm text-muted">
              Не удалось загрузить ещё
              <Button small variant="ghost" onClick={more.loadMore}>
                Повторить
              </Button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

/** A result of the search: whatever is done with it — opened, played, ♥ — it was found, and goes to «Недавнее». */
function Opened({ item, className, children }: { item: Omit<SearchRecent, 'key'>; className?: string; children: ReactNode }) {
  return (
    <div className={className} onClickCapture={() => remember(item)}>
      {children}
    </div>
  );
}

function LibraryTrackRow({ t, onPlay }: { t: LibTrackOut; onPlay?: () => void }) {
  const inner = (
    <>
      <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-md bg-accent/10 text-accent">
        <Icon name="check" size={20} stroke={2.6} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[15px]">{t.title}</div>
        <div className="truncate text-[13px] text-muted">{[t.artist, t.album].filter(Boolean).join(' · ')}</div>
      </div>
      <span className="shrink-0 text-xs tabular-nums text-dim">{fmtDuration(t.duration_s)}</span>
    </>
  );
  const row = t.album_id ? (
    <Link to={`/library/album/${t.album_id}`} className="flex min-h-16 min-w-0 flex-1 items-center gap-3 py-2 pl-4 hover:bg-fg/5">
      {inner}
    </Link>
  ) : (
    <div className="flex min-h-16 min-w-0 flex-1 items-center gap-3 py-2 pl-4">{inner}</div>
  );
  return (
    <div className="flex items-center pr-1">
      {row}
      {t.nd_song_id && <TrackMenuButton track={{ title: t.title, artist: t.artist, nd_song_id: t.nd_song_id, library_track_id: t.id }} />}
      <SaveButton songId={t.nd_song_id} listKey={String(t.id)} title={t.title} className={cx('!w-10', !onPlay && 'mr-1')} />
      {onPlay && <IconButton icon="play" label={`Слушать «${t.title}»`} onClick={onPlay} className="!text-accent" />}
    </div>
  );
}

/** Raw files from the regular sources. */
function ProviderResults({ q, onNothing }: { q: string; onNothing: (nothing: boolean) => void }) {
  const res = useAsync<ProviderSearchOut>((s) => api.searchProviders(q, s, 'sources'), [q]);
  useNothing(!res.loading && !!res.data && !res.data.candidates.length, onNothing);
  return (
    <div className="pb-4">
      <p className="px-4 pt-1 text-xs text-muted">Сырые файлы из hitmoz, hitmos.fm и YouTube. Выберите конкретный файл, если автоматический выбор не подходит; ▷ — послушать его до скачивания.</p>
      <FileResults res={res} skeleton={6} empty={<Empty icon="search" title="Файлы не найдены" />} />
    </div>
  );
}

/** Re-uploads by small channels (slower: SoundCloud answers in ~7 s). */
function ReuploadResults({ q, onNothing }: { q: string; onNothing: (nothing: boolean) => void }) {
  const res = useAsync<ProviderSearchOut>((s) => api.searchProviders(q, s, 'reuploads'), [q]);
  useNothing(!res.loading && !!res.data && !res.data.candidates.length, onNothing);
  return (
    <div className="pb-4">
      <p className="px-4 pt-1 text-xs text-muted">
        Копии с маленьких каналов YouTube и SoundCloud: там выкладывают треки, которые после закона 2026 года отредактировали в официальных версиях. Перед скачиванием их можно послушать.
      </p>
      <FileResults res={res} skeleton={4} empty={<Empty icon="search" title="Перезаливов не нашлось" />} />
    </div>
  );
}

function FileResults({ res, skeleton, empty }: { res: AsyncState<ProviderSearchOut>; skeleton: number; empty: ReactNode }) {
  const [queued, setQueued] = useState<Record<string, 'busy' | 'done'>>({});

  const download = async (c: Candidate) => {
    const key = `${c.provider}:${c.provider_id}`;
    setQueued((m) => ({ ...m, [key]: 'busy' }));
    const r = await startDownload({ items: [{ artist: c.artist, title: c.title, explicit: c.explicit ?? null, duration_s: c.duration_s ?? null, candidate: c }] });
    if (r) markQueued(fileRef(c)); // the player shows it too
    setQueued((m) => {
      const next = { ...m };
      if (r) next[key] = 'done';
      else delete next[key];
      return next;
    });
  };

  if (res.loading) return <SkeletonRows n={skeleton} />;
  if (res.error) return <ErrorState text={res.error} onRetry={() => res.reload()} />;
  const data = res.data;
  if (!data) return null;
  return (
    <>
      {data.errors.length > 0 && (
        <div className="px-4 pt-2">
          <Notice>{data.errors.join('. ')}</Notice>
        </div>
      )}
      {!data.candidates.length && empty}
      <ul className="divide-y divide-line/60">
        {data.candidates.map((c) => {
          const key = `${c.provider}:${c.provider_id}`;
          return (
            <li key={key}>
              <CandidateRow c={c} queue={data.candidates} action={<FileAction c={c} st={queued[key]} onDownload={() => download(c)} />} />
            </li>
          );
        })}
      </ul>
    </>
  );
}

/** «Скачать этот файл», or «В очереди» once it is queued (here, or from the player while it played). */
function FileAction({ c, st, onDownload }: { c: Candidate; st?: 'busy' | 'done'; onDownload: () => void }) {
  const dl = useListen((s) => s.downloads.get(fileRef(c)));
  if (st === 'done' || dl)
    return (
      <span className="inline-flex items-center gap-1 text-sm text-accent">
        <Icon name="check" size={16} /> {dl === 'done' ? 'В библиотеке' : 'В очереди'}
      </span>
    );
  return (
    <Button small icon="download" busy={st === 'busy'} onClick={onDownload}>
      Скачать этот файл
    </Button>
  );
}
