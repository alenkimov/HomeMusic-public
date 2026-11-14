import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { FollowOut, ReleaseOut } from '../api/types';
import { TrackRow, playAll } from '../components/catalog';
import { Icon } from '../components/Icon';
import { Cover, Empty, ErrorState, Loading, PageHeader, SectionTitle, Segmented, Spinner, cx } from '../components/ui';
import { useDesktop } from '../lib/desktop';
import { STANDALONE } from '../lib/embedded';
import { useHubEvent } from '../lib/events';
import { plural, recordTypeLabel, tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { setUnseenReleases } from '../lib/releases';
import { Link, enc } from '../lib/router';
import { toggleLike, useLiked } from '../lib/stars';

/** «Подписки» (`/subscriptions`): the artists the user follows («Подписаться» on an artist's page) and what they
 * have released in the last three months — as tracks, not as covers: a single is a track to play at once, an album
 * shows its first tracks and opens to the rest. A phone has the artists as a strip of pictures over the feed (a tap
 * leaves only that artist's releases; «Все» opens the list of them, where one unfollows); a computer has the list
 * beside the feed. What was not seen before is marked; opening the page makes it seen. */

type Kind = 'all' | 'album' | 'single';
const KINDS: Array<{ value: Kind; label: string }> = [
  { value: 'all', label: 'Все' },
  { value: 'album', label: 'Альбомы' },
  { value: 'single', label: 'Синглы и EP' },
];
const kindOf = (r: ReleaseOut): Kind => (r.record_type === 'single' || r.record_type === 'ep' ? 'single' : 'album');
const artistsWord = (n: number) => `${n} ${plural(n, ['исполнитель', 'исполнителя', 'исполнителей'])}`;

export function day(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  const days = Math.round((Date.now() - d.getTime()) / 86400000);
  if (days <= 0) return 'сегодня';
  if (days === 1) return 'вчера';
  if (days < 7) return `${days} ${plural(days, ['день', 'дня', 'дней'])} назад`;
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', ...(d.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {}) });
}

export function SubscriptionsPage() {
  const res = useAsync((s) => api.releases(s), []);
  useHubEvent('library', (e) => (e as { kind?: string }).kind === 'releases' && res.reload(true));
  const desktop = useDesktop();
  const [kind, setKind] = useState<Kind>('all');
  const [only, setOnly] = useState<string | null>(null); // the releases of this artist alone
  const [list, setList] = useState(false); // a phone: the list of the artists in place of the feed
  const d = res.data;
  useEffect(() => {
    if (!d) return;
    setUnseenReleases(0);
    if (d.unseen) void api.releasesSeen().catch(() => undefined);
  }, [d]);
  const shown = useMemo(() => (d?.items ?? []).filter((r) => (kind === 'all' || kindOf(r) === kind) && (!only || r.artist_ref === only)), [d, kind, only]);
  const back = STANDALONE ? '/home' : '/library';

  if (res.error && !d) return (<><PageHeader title="Подписки" back={back} /><ErrorState text={res.error} onRetry={() => res.reload()} /></>);
  if (!d) return (<><PageHeader title="Подписки" back={back} /><Loading text="Смотрю, что вышло…" /></>);
  if (!d.artists)
    return (
      <>
        <PageHeader title="Подписки" back={back} />
        <Empty icon="user" title="Вы пока ни на кого не подписаны" text="Откройте страницу исполнителя и нажмите «Подписаться» — его новые альбомы и синглы будут появляться здесь, сразу треками." />
      </>
    );

  const who = only ? d.following.find((f) => f.ref === only) : null;
  const pick = (ref: string) => {
    setOnly((cur) => (cur === ref ? null : ref));
    setList(false);
  };
  const feed = (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-2 px-4 pb-1 lg:px-6">
        <Segmented value={kind} options={KINDS} onChange={setKind} label="Тип релиза" />
        {who && (
          <button type="button" onClick={() => setOnly(null)} className="flex h-8 items-center gap-1.5 rounded-full bg-accent/15 pl-3 pr-2 text-sm font-medium text-accent">
            {who.name}
            <Icon name="x" size={14} />
          </button>
        )}
      </div>
      {!shown.length ? (
        <Empty icon="disc" title="Пока ничего" text={d.items.length ? 'Таких релизов за последние три месяца не было.' : 'За последние три месяца у ваших исполнителей ничего не выходило.'} />
      ) : (
        <Feed items={shown} />
      )}
    </div>
  );
  const artists = (
    <ul className="px-2 pb-4 lg:px-0 lg:pr-4">
      {d.following.map((f) => (
        <ArtistLine key={f.ref} f={f} on={only === f.ref} onPick={() => pick(f.ref)} />
      ))}
    </ul>
  );

  return (
    <div>
      <PageHeader title="Подписки" back={back} sub={[artistsWord(d.artists), d.unseen ? `новых релизов: ${d.unseen}` : null].filter(Boolean).join(' · ')} />
      {desktop ? (
        <div className="grid grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)] gap-x-4">
          {feed}
          <section aria-label="Исполнители">
            <h2 className="px-2 pb-2 text-lg font-bold">Исполнители · {d.artists}</h2>
            {artists}
          </section>
        </div>
      ) : (
        <>
          <div className="no-scrollbar flex gap-1 overflow-x-auto px-3 pb-3">
            <button type="button" onClick={() => setList((v) => !v)} aria-pressed={list} className="w-[68px] shrink-0 text-center">
              <span className={cx('mx-auto flex h-14 w-14 items-center justify-center rounded-full border', list ? 'border-accent bg-accent/15 text-accent' : 'border-fg/25 text-muted')}>
                <Icon name="user" size={22} />
              </span>
              <span className="mt-1 block truncate text-[11px] text-muted">Все {d.artists}</span>
            </button>
            {d.following.map((f) => (
              <button key={f.ref} type="button" onClick={() => pick(f.ref)} aria-pressed={only === f.ref} title={f.name} className="w-[68px] shrink-0 text-center">
                <span className={cx('mx-auto block h-14 w-14 rounded-full p-0.5', only === f.ref ? 'ring-2 ring-fg' : f.new ? 'ring-2 ring-accent' : undefined)}>
                  <Cover src={f.picture_url} round icon="user" alt="" className="h-full w-full" />
                </span>
                <span className={cx('mt-1 block truncate text-[11px]', only === f.ref ? 'font-semibold text-fg' : 'text-muted')}>{f.name}</span>
              </button>
            ))}
          </div>
          {list ? artists : feed}
        </>
      )}
    </div>
  );
}

/** The releases under the days they came out on. */
function Feed({ items }: { items: ReleaseOut[] }) {
  let last = '';
  return (
    <div className="pb-6">
      {items.map((r) => {
        const label = day(r.release_date);
        const head = label !== last;
        last = label;
        return (
          <div key={r.album_ref}>
            {head && <h3 className="px-4 pb-1 pt-4 text-xs font-semibold uppercase tracking-wide text-dim lg:px-6">{label}</h3>}
            <ReleaseBlock r={r} open={3} />
          </div>
        );
      })}
    </div>
  );
}

/** A release as its tracks. One track: the track itself, to play at once. Several: the release's line (▶ plays all
 * of it) and its first `open` tracks, the rest behind «Ещё N». `date`: say when it came out (a list with no days). */
export function ReleaseBlock({ r, open, date = false }: { r: ReleaseOut; open: number; date?: boolean }) {
  const [all, setAll] = useState(false);
  const tracks = r.tracks ?? [];
  const mark = r.new && <span aria-label="новое" title="Вы этого ещё не видели" className="absolute left-1 top-1/2 h-1.5 w-1.5 -translate-y-1/2 rounded-full bg-accent lg:left-2.5" />;
  if (tracks.length === 1)
    return (
      <div className="relative">
        {mark}
        <TrackRow track={tracks[0]} queue={tracks} />
      </div>
    );
  const to = `/album/${enc(r.album_ref)}`;
  const n = tracks.length || r.nb_tracks || 0;
  const visible = all ? tracks : tracks.slice(0, open);
  const rest = tracks.length - visible.length;
  return (
    <div>
      <div className="relative flex min-h-14 items-center gap-3 px-4 py-1 lg:px-6">
        {mark}
        <Link to={to} className="shrink-0">
          <Cover src={r.cover_url} size={44} icon="disc" alt="" />
        </Link>
        <Link to={to} className="min-w-0 flex-1">
          <span className="block truncate font-medium hover:underline">{r.title}</span>
          <span className="block truncate text-[13px] text-muted">{[r.artist, recordTypeLabel(r.record_type), n ? tracksWord(n) : null, date ? day(r.release_date) : null].filter(Boolean).join(' · ')}</span>
        </Link>
        {tracks.length > 0 && (
          <>
            <button type="button" aria-label={`Слушать «${r.title}»`} title="Слушать" onClick={() => playAll(tracks, { context: 'album' })} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-fg hover:bg-fg/10">
              <Icon name="play" size={20} filled />
            </button>
            {(open === 0 || rest > 0 || all) && (
              <button type="button" aria-expanded={all} aria-label={all ? 'Свернуть' : 'Показать треки'} title={all ? 'Свернуть' : 'Показать треки'} onClick={() => setAll((v) => !v)} className="-ml-2 flex h-10 w-8 shrink-0 items-center justify-center text-muted hover:text-fg">
                <Icon name="down" size={18} className={cx('transition-transform', all && 'rotate-180')} />
              </button>
            )}
          </>
        )}
      </div>
      {visible.length > 0 && (
        <div className="pl-6">
          {visible.map((t, i) => (
            <TrackRow key={t.ref} track={t} number={t.track_no ?? i + 1} showCover={false} queue={tracks} />
          ))}
        </div>
      )}
      {rest > 0 && open > 0 && (
        <button type="button" onClick={() => setAll(true)} className="ml-[4.5rem] py-1.5 text-[13px] text-muted hover:text-fg">
          Ещё {tracksWord(rest)}
        </button>
      )}
    </div>
  );
}

/** A followed artist: the picture leaves only their releases in the feed, the name opens their page, the button
 * unfollows — and follows again while the page is open, should it have been a slip. */
function ArtistLine({ f, on, onPick }: { f: FollowOut; on: boolean; onPick: () => void }) {
  const target = { targetType: 'artist' as const, ref: f.like_ref ?? (f.nd_id ? undefined : f.ref), ndId: f.nd_id ?? undefined, data: { name: f.name } };
  const followed = useLiked(target);
  const [busy, setBusy] = useState(false);
  const flip = async () => {
    setBusy(true);
    try {
      await toggleLike(target);
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className={cx('flex min-h-14 items-center gap-3 rounded-lg px-2', on && 'bg-fg/10')}>
      <button type="button" onClick={onPick} aria-pressed={on} title={on ? 'Показать релизы всех' : 'Только релизы этого исполнителя'} className={cx('shrink-0 rounded-full p-0.5', f.new > 0 && 'ring-2 ring-accent')}>
        <Cover src={f.picture_url} size={40} round icon="user" alt="" />
      </button>
      <Link to={`/artist/${enc(f.ref)}`} className="min-w-0 flex-1 py-1.5">
        <span className="block truncate font-medium hover:underline">{f.name}</span>
        <span className={cx('block truncate text-[13px]', f.new ? 'text-accent' : 'text-muted')}>
          {f.new ? `Новых релизов: ${f.new}` : f.last_release_date ? `${f.last_release_title ?? 'Последний релиз'} · ${day(f.last_release_date)}` : 'Релизов пока не видно'}
        </span>
      </Link>
      <button
        type="button"
        onClick={() => void flip()}
        disabled={busy}
        aria-pressed={followed}
        className={cx('flex h-8 shrink-0 items-center gap-1 rounded-full border px-3 text-[13px] font-semibold transition active:scale-95', followed ? 'border-accent text-accent' : 'border-fg/40 text-fg hover:border-fg')}
      >
        {busy && <Spinner size={12} />}
        {followed ? 'Вы подписаны' : 'Подписаться'}
      </button>
    </li>
  );
}

const ON_HOME = 5;

/** «Новое от подписок» of «Главная»: the latest releases, each one line — a single plays at once, an album opens
 * to its tracks. Nothing is shown when nothing came out; what is new stays marked until «Подписки» is opened. */
export function NewFromFollowed() {
  const res = useAsync((s) => api.releases(s), []);
  useHubEvent('library', (e) => (e as { kind?: string }).kind === 'releases' && res.reload(true));
  const items = res.data?.items.slice(0, ON_HOME) ?? [];
  if (!items.length) return null;
  return (
    <section aria-label="Новое от подписок">
      <SectionTitle right={<Link to="/subscriptions" className="text-sm text-muted hover:text-fg">Все подписки</Link>}>Новое от подписок</SectionTitle>
      {items.map((r) => (
        <ReleaseBlock key={r.album_ref} r={r} open={0} date />
      ))}
    </section>
  );
}
