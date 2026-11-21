import { useEffect, useId, useMemo, useRef, useState, type MouseEvent } from 'react';
import { api } from '../api/client';
import type { FollowOut, ReleaseOut } from '../api/types';
import { TrackRow, playAll } from '../components/catalog';
import { Icon } from '../components/Icon';
import { Cover, Empty, ErrorState, IconButton, Loading, PageHeader, Segmented, cx } from '../components/ui';
import { STANDALONE } from '../lib/embedded';
import { useHubEvent } from '../lib/events';
import { plural, recordTypeLabel, tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { setUnseenReleases } from '../lib/releases';
import { Link, enc } from '../lib/router';
import { toggleLike, useLiked, useLikesKnown } from '../lib/stars';

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
  const res = useAsync((s) => api.releases(s), [], true, 'releases');
  useHubEvent('library', (e) => (e as { kind?: string }).kind === 'releases' && res.reload(true));
  const [kind, setKind] = useState<Kind>('all');
  const [only, setOnly] = useState<string | null>(null); // the releases of this artist alone
  const [list, setList] = useState(false); // a phone: the list of the artists in place of the feed
  const d = res.data;
  const who = d?.following.find((f) => f.ref === only);
  useEffect(() => {
    if (!d) return;
    setUnseenReleases(0);
    if (d.unseen) void api.releasesSeen().catch(() => undefined);
  }, [d]);
  const artistRef = who?.ref;
  const shown = useMemo(() => (d?.items ?? []).filter((r) => (kind === 'all' || kindOf(r) === kind) && (!artistRef || r.artist_ref === artistRef)), [d, kind, artistRef]);
  const back = STANDALONE ? '/home' : '/library';

  if (res.error && !d) return (<><PageHeader title="Релизы" back={back} /><ErrorState text={res.error} onRetry={() => res.reload()} /></>);
  if (!d) return (<><PageHeader title="Релизы" back={back} /><Loading text="Смотрю, что вышло…" /></>);
  if (!d.artists)
    return (
      <>
        <PageHeader title="Релизы" back={back} />
        <Empty icon="user" title="Вы пока ни на кого не подписаны" text="Откройте страницу исполнителя и нажмите «Подписаться» — его новые альбомы и синглы будут появляться здесь, сразу треками." />
      </>
    );

  const pick = (ref: string) => {
    setOnly((cur) => (cur === ref ? null : ref));
    setList(false);
  };
  const feed = (
    <section aria-label="Лента релизов" className={cx('min-w-0 @min-[46rem]/releases:block', list && 'hidden')}>
      <div className="flex min-w-0 flex-wrap items-center gap-2 px-2 pb-1">
        <Segmented value={kind} options={KINDS} onChange={setKind} label="Тип релиза" />
        {who && (
          <button type="button" onClick={() => setOnly(null)} aria-label={`Снять фильтр · ${who.name}`} className="flex min-h-8 max-w-full items-center gap-1.5 rounded-full bg-accent/15 py-1 pl-3 pr-2 text-left text-sm font-medium text-accent">
            <span className="min-w-0 wrap-anywhere">{who.name}</span>
            <Icon name="x" size={14} className="shrink-0" />
          </button>
        )}
      </div>
      {!shown.length ? (
        <Empty icon="disc" title="Пока ничего" text={d.items.length ? 'Таких релизов за последние три месяца не было.' : 'За последние три месяца у ваших исполнителей ничего не выходило.'} />
      ) : (
        <Feed items={shown} />
      )}
    </section>
  );

  return (
    <div className="@container/releases">
      <PageHeader title="Релизы" back={back} sub={[artistsWord(d.artists), d.unseen ? `новых релизов: ${d.unseen}` : null].filter(Boolean).join(' · ')} />
      <div className="no-scrollbar flex items-start gap-1 overflow-x-auto px-3 pt-1 pb-3 @min-[46rem]/releases:hidden" aria-label="Фильтр по исполнителю">
        <button type="button" onClick={() => setList((v) => !v)} aria-pressed={list} className="w-14 shrink-0 text-center">
          <span className={cx('mx-auto flex size-11 items-center justify-center rounded-full border', list ? 'border-accent bg-accent/15 text-accent' : 'border-fg/20 bg-raised text-muted')}>
            <Icon name="user" size={20} />
          </span>
          <span className="mt-1 block text-[11px] text-muted">Все</span>
        </button>
        {d.following.map((f) => (
          <button key={f.ref} type="button" onClick={() => pick(f.ref)} aria-pressed={!list && artistRef === f.ref} title={f.name} className="w-14 shrink-0 text-center">
            <span className={cx('mx-auto block size-11 rounded-full p-0.5', !list && artistRef === f.ref ? 'ring-2 ring-fg' : f.new ? 'ring-2 ring-accent' : undefined)}>
              <Cover src={f.picture_url} round icon="user" alt="" className="h-full w-full" />
            </span>
            <span className={cx('mt-1 line-clamp-2 wrap-anywhere text-[11px]', !list && artistRef === f.ref ? 'font-semibold text-fg' : 'text-muted')}>{f.name}</span>
          </button>
        ))}
      </div>
      <div className="px-2 pt-1 @min-[46rem]/releases:grid @min-[46rem]/releases:grid-cols-[minmax(0,1fr)_minmax(15rem,28%)] @min-[46rem]/releases:gap-x-5 @min-[46rem]/releases:px-4">
        {feed}
        <section aria-label="Исполнители" className={cx('min-w-0 @min-[46rem]/releases:block @min-[46rem]/releases:border-l @min-[46rem]/releases:border-line @min-[46rem]/releases:pl-3', !list && 'hidden')}>
          <h2 className="px-2 pt-1 pb-2 text-lg font-bold">Исполнители <span className="text-sm font-normal text-muted">· {d.artists}</span></h2>
          <ul className="pb-4">
            {d.following.map((f) => <ArtistLine key={f.ref} f={f} on={artistRef === f.ref} onPick={() => pick(f.ref)} />)}
          </ul>
        </section>
      </div>
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
            {head && <h3 className="px-2 pb-1 pt-4 text-xs font-semibold uppercase tracking-wide text-dim">{label}</h3>}
            <ReleaseBlock r={r} />
          </div>
        );
      })}
    </div>
  );
}

/** The first three tracks of an album are visible by default. */
function ReleaseBlock({ r }: { r: ReleaseOut }) {
  const [all, setAll] = useState(false);
  const [expanded, setExpanded] = useState(true);
  const tracks = r.tracks ?? [];
  const mark = r.new && <span aria-label="новое" title="Вы этого ещё не видели" className="pointer-events-none absolute -left-1 top-1/2 z-1 h-1.5 w-1.5 -translate-y-1/2 rounded-full bg-accent" />;
  if (tracks.length === 1)
    return (
      <div className="relative">
        {mark}
        <TrackRow track={tracks[0]} queue={tracks} releaseFeed />
      </div>
    );
  const to = `/album/${enc(r.album_ref)}`;
  const n = tracks.length || r.nb_tracks || 0;
  const visible = all ? tracks : tracks.slice(0, 3);
  const rest = tracks.length - visible.length;
  return (
    <div>
      <div className="relative flex min-h-14 items-center gap-2 border-t border-line px-2 py-1">
        {mark}
        <Link to={to} className="shrink-0">
          <Cover src={r.cover_url} size={44} icon="disc" alt="" />
        </Link>
        <Link to={to} className="min-w-0 flex-1">
          <span className="line-clamp-2 wrap-anywhere font-medium hover:underline" title={r.title}>{r.title}</span>
          <span className="line-clamp-2 wrap-anywhere text-[13px] text-muted">{[r.artist, recordTypeLabel(r.record_type), n ? tracksWord(n) : null].filter(Boolean).join(' · ')}</span>
        </Link>
        {tracks.length > 0 && (
          <>
            <button type="button" aria-label={`Слушать «${r.title}»`} title="Слушать" onClick={() => playAll(tracks, { context: 'album' })} className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-fg hover:bg-fg/10">
              <Icon name="play" size={20} filled />
            </button>
            <button type="button" aria-expanded={expanded} aria-label={expanded ? 'Свернуть треки альбома' : 'Показать треки альбома'} onClick={() => setExpanded((v) => !v)} className="flex h-11 w-9 shrink-0 items-center justify-center rounded-full text-muted hover:bg-fg/10 hover:text-fg">
              <Icon name="down" size={18} className={cx('transition-transform', expanded && 'rotate-180')} />
            </button>
          </>
        )}
      </div>
      {expanded && visible.length > 0 && (
        <div className="ml-3 min-w-0 border-l border-line pl-1">
          {visible.map((t, i) => (
            <TrackRow key={t.ref} track={t} number={t.track_no ?? i + 1} queue={tracks} releaseFeed />
          ))}
        </div>
      )}
      {expanded && rest > 0 && (
        <button type="button" onClick={() => setAll(true)} className="ml-[4.5rem] py-1.5 text-[13px] text-muted hover:text-fg">
          Ещё {tracksWord(rest)}
        </button>
      )}
    </div>
  );
}

/** The avatar filters the feed and the artist name opens their page. */
function ArtistLine({ f, on, onPick }: { f: FollowOut; on: boolean; onPick: () => void }) {
  const target = { targetType: 'artist' as const, ref: f.like_ref ?? (f.nd_id ? undefined : f.ref), ndId: f.nd_id ?? undefined, data: { name: f.name } };
  const followed = useLiked(target);
  const known = useLikesKnown();
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const id = useId();
  const menu = useRef<HTMLDivElement>(null);
  const unfollow = followed || !known;

  useEffect(() => {
    if (!open) return;
    const close = () => menu.current?.hidePopover();
    window.addEventListener('scroll', close, { passive: true, capture: true });
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [open]);

  const positionMenu = (event: MouseEvent<HTMLButtonElement>) => {
    if (!menu.current) return;
    const button = event.currentTarget.getBoundingClientRect();
    menu.current.style.left = `${Math.max(8, button.right - 184)}px`;
    menu.current.style.top = `${button.bottom + 60 <= window.innerHeight ? button.bottom : Math.max(8, button.top - 56)}px`;
  };
  const flip = async () => {
    menu.current?.hidePopover();
    setBusy(true);
    try {
      await toggleLike(target);
    } finally {
      setBusy(false);
    }
  };
  return (
    <li className={cx('flex min-h-15 items-center gap-2 rounded-lg px-1', on && 'bg-accent/10')}>
      <button type="button" onClick={onPick} aria-pressed={on} aria-label={on ? 'Показать релизы всех' : `Релизы · ${f.name}`} className={cx('shrink-0 rounded-full p-0.5', f.new > 0 && 'ring-2 ring-accent')}>
        <Cover src={f.picture_url} size={40} round icon="user" alt="" />
      </button>
      <Link to={`/artist/${enc(f.ref)}`} className="min-w-0 flex-1 py-1.5">
        <span className="block wrap-anywhere font-medium hover:underline">{f.name}</span>
        <span className={cx('line-clamp-2 wrap-anywhere text-[13px]', unfollow && f.new ? 'text-accent' : 'text-muted')}>
          {!unfollow ? 'Подписка снята' : f.new ? `Новых релизов: ${f.new}` : f.last_release_date ? `${f.last_release_title ?? 'Последний релиз'} · ${day(f.last_release_date)}` : 'Релизов пока не видно'}
        </span>
      </Link>
      <IconButton icon="more" size={18} label={`Действия · ${f.name}`} popoverTarget={id} onClick={positionMenu}
        aria-expanded={open} busy={busy} className="text-muted hover:bg-fg/5 hover:text-fg" />
      <div id={id} ref={menu} popover="auto" onToggle={(event) => setOpen(event.newState === 'open')}
        className="fixed inset-auto m-0 w-[184px] rounded-xl border border-line bg-raised p-1 shadow-xl"
      >
        <button type="button" onClick={() => void flip()} disabled={busy || !known}
          className={cx('flex min-h-11 w-full items-center gap-2 rounded-lg px-3 text-left text-sm hover:bg-fg/5 disabled:opacity-50', unfollow ? 'text-danger' : 'text-fg')}>
          <Icon name={unfollow ? 'x' : 'bell'} size={17} />
          {unfollow ? 'Отписаться' : 'Подписаться'}
        </button>
      </div>
    </li>
  );
}
