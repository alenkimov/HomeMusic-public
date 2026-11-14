import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api/client';
import type { AlbumOut, TrackOut } from '../api/types';
import { TrackRow, playAll } from '../components/catalog';
import { Icon } from '../components/Icon';
import { ListBar } from '../components/ListBar';
import { useSearchField } from '../components/ListSearch';
import { Cover, Empty, ErrorState, Loading, PageHeader, Segmented } from '../components/ui';
import { tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link, enc } from '../lib/router';

/** «Дискография» (`/artist/<ref>/discography`): everything an artist released, newest first — each release with its
 * tracks, so it is also «all the tracks of the artist». Filtered by the kind of release; the search of the bar looks
 * through every release at once and shows what it found as one list, each track with its release and year. The Hub
 * gives it whole (`/api/catalog/artist/<ref>/discography`): the first opening of a long one takes a few seconds. */

type Kind = 'all' | 'album' | 'single' | 'compile';
const KINDS: Array<{ value: Kind; label: string }> = [
  { value: 'all', label: 'Всё' },
  { value: 'album', label: 'Альбомы' },
  { value: 'single', label: 'Синглы и EP' },
  { value: 'compile', label: 'Сборники' },
];
const KIND_NAME: Record<string, string> = { album: 'Альбом', single: 'Сингл', ep: 'EP', compile: 'Сборник' };
const STEP = 8; // releases drawn at once; more as the end comes into view

const kindOf = (a: AlbumOut): Kind => (a.record_type === 'ep' ? 'single' : a.record_type === 'single' || a.record_type === 'compile' ? a.record_type : 'album');
const yearOf = (a: AlbumOut) => a.year ?? (a.release_date ? Number(a.release_date.slice(0, 4)) : null);

export function DiscographyPage({ refId }: { refId: string }) {
  const res = useAsync((s) => api.discography(refId, s), [refId], true, `discography:${refId}`);
  const [kind, setKind] = useState<Kind>('all');
  const [shown, setShown] = useState(STEP);
  const sf = useSearchField('Трек или релиз');
  const needle = sf.q.trim().toLocaleLowerCase('ru');
  const d = res.data;
  const back = `/artist/${enc(refId)}`;

  const albums = useMemo(() => (d?.albums ?? []).filter((a) => kind === 'all' || kindOf(a) === kind), [d, kind]);
  // every track of every release, with the release it is of: what the search looks through
  const found = useMemo(() => {
    if (!needle) return null;
    const out: { t: TrackOut; a: AlbumOut }[] = [];
    for (const a of albums) {
      const whole = a.title.toLocaleLowerCase('ru').includes(needle);
      for (const t of a.tracks ?? []) if (whole || `${t.title} ${t.artists?.join(' ') ?? t.artist}`.toLocaleLowerCase('ru').includes(needle)) out.push({ t, a });
    }
    return out;
  }, [albums, needle]);
  useEffect(() => setShown(STEP), [kind, refId]);

  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = end.current;
    if (!el) return;
    const io = new IntersectionObserver((xs) => xs.some((x) => x.isIntersecting) && setShown((n) => n + STEP), { rootMargin: '800px' });
    io.observe(el);
    return () => io.disconnect();
  }, [albums.length, !!found]);

  if (res.loading && !d) return (<><PageHeader title="Дискография" back={back} /><Loading text="Собираю дискографию — у исполнителя с десятками релизов это несколько секунд…" /></>);
  if (!d) return (<><PageHeader title="Дискография" back={back} /><ErrorState text={res.error ?? 'Не удалось получить дискографию'} onRetry={() => res.reload()} /></>);

  const total = albums.reduce((n, a) => n + (a.tracks?.length ?? 0), 0);
  return (
    <div>
      <ListBar plain title={d.artist.name} back={back} search={{ found: null, open: sf.open, alone: sf.alone, show: sf.show, close: sf.close, field: sf.field(found?.length) }} />
      <div className="flex flex-wrap items-center gap-3 px-4 pb-2 lg:px-6">
        <Segmented<Kind> label="Какие релизы" value={kind} onChange={setKind} options={KINDS.filter((k) => k.value === 'all' || (d.albums ?? []).some((a) => kindOf(a) === k.value))} />
        <span className="ml-auto text-xs tabular-nums text-dim">{tracksWord(total)}</span>
      </div>

      {found ? (
        found.length ? (
          <div className="pb-4">
            {found.slice(0, 300).map(({ t, a }) => (
              <div key={`${a.ref}:${t.ref}`}>
                <TrackRow track={{ ...t, album_title: `${a.title}${yearOf(a) ? ` · ${yearOf(a)}` : ''}` }} showAlbum queue={found.map((x) => x.t)} />
              </div>
            ))}
          </div>
        ) : (
          <Empty icon="search" title="Ничего не нашлось" text="Ни в одном релизе этого исполнителя такого нет." />
        )
      ) : !albums.length ? (
        <Empty icon="disc" title="Релизов нет" text="Каталог не знает релизов этого исполнителя." />
      ) : (
        <div className="pb-4">
          {albums.slice(0, shown).map((a) => {
            const tracks = a.tracks ?? [];
            return (
              <section key={a.ref} aria-label={a.title} className="pt-4">
                <div className="flex items-center gap-3 px-4 pb-2 lg:px-6">
                  <Link to={`/album/${enc(a.ref)}`} className="shrink-0">
                    <Cover src={a.cover_small_url || a.cover_url} size={64} alt="" />
                  </Link>
                  <div className="min-w-0 flex-1">
                    <Link to={`/album/${enc(a.ref)}`} className="block truncate text-lg font-bold hover:underline">
                      {a.title}
                    </Link>
                    <div className="truncate text-sm text-muted">
                      {[KIND_NAME[a.record_type] ?? 'Релиз', yearOf(a), tracks.length ? tracksWord(tracks.length) : a.nb_tracks ? tracksWord(a.nb_tracks) : null].filter(Boolean).join(' · ')}
                    </div>
                  </div>
                  {tracks.length > 0 && (
                    <button
                      type="button"
                      aria-label={`Слушать «${a.title}»`}
                      title="Слушать"
                      onClick={() => playAll(tracks, { context: 'album' })}
                      className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition hover:scale-105 active:scale-95"
                    >
                      <Icon name="play" size={20} filled className="translate-x-px" />
                    </button>
                  )}
                </div>
                {tracks.map((t, i) => (
                  <TrackRow key={t.ref} track={t} number={t.track_no ?? i + 1} showCover={false} queue={tracks} />
                ))}
                {!tracks.length && <p className="px-4 pb-2 text-sm text-dim lg:px-6">Каталог не отдал треки этого релиза — откройте его страницу.</p>}
              </section>
            );
          })}
          <div ref={end} className="h-8" />
        </div>
      )}
    </div>
  );
}
