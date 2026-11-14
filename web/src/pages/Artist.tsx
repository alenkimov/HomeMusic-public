import { useMemo, useState } from 'react';
import { api } from '../api/client';
import type { AlbumOut, ArtistPageOut } from '../api/types';
import { ReleaseCard, TrackRow } from '../components/catalog';
import { Icon, type IconName } from '../components/Icon';
import { ListBar } from '../components/ListBar';
import { Button, Cover, ErrorState, PageHeader, Sheet, SkeletonRows, Spinner, cx } from '../components/ui';
import { startDownload } from '../lib/actions';
import { setDisliked, useDisliked } from '../lib/dislikes';
import { plural, tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link, enc } from '../lib/router';
import { copyText } from '../lib/share';
import { toggleLike, useLiked } from '../lib/stars';
import { confirmDialog, toast } from '../lib/ui';
import { canWave, startWave } from '../lib/wave';
import { Hero } from './MediaLibrary';

/** An artist's page (`/artist/<ref>`; a library artist the catalog knows opens it too): a list's head — the photo,
 * the name, ▶ and the shuffle of the popular tracks — then «Подписаться» and ⋯ (the wave by the artist, downloading
 * everything, sharing, «Не включать»). Under them: «Вам нравится» — the user's liked tracks the artist is on, when
 * there are any; the ten popular tracks; the latest releases, and the way to all of them («Вся дискография»,
 * `pages/Discography.tsx`).
 *
 * An artist the user does not listen to (`lib/dislikes.ts`) has a grey page that says so and offers to bring them
 * back: nothing of theirs plays by itself, a track tapped still does. */

const POPULAR = 10;
const LATEST = 4;

/** The user's liked tracks this artist is on (credited anywhere, not only first). */
function useLikedBy(name: string | undefined, ref: string | undefined): number {
  const liked = useAsync((s) => api.ndLiked(s), []);
  return useMemo(() => {
    if (!name) return 0;
    const want = name.trim().toLocaleLowerCase('ru');
    return (liked.data?.songs ?? []).filter((t) => (!!ref && (t.artist_ref === ref || !!t.artist_refs?.includes(ref))) || (t.artists?.length ? t.artists : [t.artist]).some((a) => a.trim().toLocaleLowerCase('ru') === want)).length;
  }, [liked.data, name, ref]);
}

export function ArtistPage({ refId }: { refId: string }) {
  const res = useAsync((s) => api.artist(refId, s), [refId]);
  const [menu, setMenu] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const d: ArtistPageOut | undefined = res.data;
  const artist = d?.artist;
  const target = { targetType: 'artist' as const, ref: refId, data: { name: artist?.name ?? '' } };
  const followed = useLiked(target);
  const hidden = useDisliked({ artist_ref: refId });
  const likedBy = useLikedBy(artist?.name, refId);

  if (res.loading && !d) return (<><PageHeader hero title="Исполнитель" back="/search" /><SkeletonRows n={8} /></>);
  if (!d || !artist) return (<><PageHeader hero title="Исполнитель" back="/search" /><ErrorState text={res.error ?? 'Исполнитель не найден'} onRetry={() => res.reload()} /></>);

  const releases = [...d.albums, ...d.singles, ...d.compilations].sort((a, b) => (b.release_date ?? '').localeCompare(a.release_date ?? ''));
  const top = d.top_tracks.slice(0, POPULAR);

  const follow = async () => {
    setBusy('follow');
    try {
      await toggleLike(target);
    } finally {
      setBusy(null);
    }
  };
  const downloadAll = async () => {
    setMenu(false);
    const missing = (xs: AlbumOut[]) => xs.filter((a) => a.owned_status !== 'full');
    const base = [...missing(d.albums), ...missing(d.singles)];
    const tracks = base.reduce((n, a) => n + Math.max(0, (a.nb_tracks ?? 0) - (a.owned_count ?? 0)), 0);
    const comp = missing(d.compilations).length;
    if (!base.length && !comp) {
      toast('Все альбомы и синглы этого исполнителя уже в библиотеке', 'info');
      return;
    }
    const body = `Будет скачано релизов: ${base.length} (альбомы, синглы и EP)` + (tracks ? `, примерно ${tracksWord(tracks)}` : '') + '.\nУже скачанные треки пропускаются.' + (comp ? `\nСборники (${comp}) не включаются.` : '');
    if (!(await confirmDialog({ title: `Скачать всю дискографию ${artist.name}?`, body, okLabel: 'Скачать' }))) return;
    await startDownload({ artist_ref: artist.ref, record_types: ['album', 'single', 'ep'] });
    res.reload(true);
  };
  const share = async () => {
    setMenu(false);
    const url = `${window.location.origin}/#/artist/${enc(artist.ref)}`;
    toast((await copyText(url)) ? 'Ссылка на исполнителя скопирована' : url, 'success');
  };
  const hide = async () => {
    setMenu(false);
    if (hidden) return void setDisliked({ artist_ref: refId, artist: artist.name }, false);
    if (!(await confirmDialog({ title: `Не включать ${artist.name}?`, body: 'Треки этого исполнителя, и те, где он участвует, не будут попадать в волну, миксы и открытия, а в плейлистах будут пропускаться. Нажатый вручную трек сыграет. Вернуть можно здесь же или в «Меню → Скрытое».', okLabel: 'Не включать', danger: true }))) return;
    await setDisliked({ artist_ref: refId, artist: artist.name }, true);
  };
  const deezer = artist.source === 'dz' ? `https://www.deezer.com/artist/${artist.id}` : null;
  const item = (icon: IconName, label: string, run: () => void, danger = false) => (
    <button type="button" onClick={run} className={cx('flex min-h-12 w-full items-center gap-4 rounded-lg px-3 text-left text-[15px] hover:bg-fg/5', danger && 'text-danger')}>
      <Icon name={icon} size={21} className={danger ? undefined : 'text-muted'} />
      {label}
    </button>
  );

  return (
    <div className={hidden ? 'grayscale' : undefined}>
      <ListBar title={artist.name} back="/search" tracks={hidden ? undefined : top} context="artist" />
      <Hero
        kicker="Исполнитель"
        cover={artist.picture_url}
        round
        title={artist.name}
        sub={[artist.nb_fans ? `${artist.nb_fans.toLocaleString('ru-RU')} ${plural(artist.nb_fans, ['поклонник', 'поклонника', 'поклонников'])}` : null, releases.length ? `${releases.length} ${plural(releases.length, ['релиз', 'релиза', 'релизов'])}` : null].filter(Boolean).join(' · ')}
        tracks={hidden ? undefined : top}
        context="artist"
      />

      {hidden ? (
        <div className="mx-4 mb-3 rounded-2xl bg-danger/15 px-4 py-3 text-sm text-fg lg:mx-6">
          <p>Вы не слушаете этого исполнителя. Его треков нет в волне, миксах и открытиях, а в плейлистах они пропускаются.</p>
          <Button small className="mt-3" onClick={() => void setDisliked({ artist_ref: refId, artist: artist.name }, false)}>
            Вернуть
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-2 px-4 pb-3 lg:px-6">
          <button
            type="button"
            onClick={() => void follow()}
            disabled={busy === 'follow'}
            aria-pressed={followed}
            title={followed ? 'Вы подписаны: новые релизы появятся в «Подписках». Нажмите, чтобы отписаться' : 'Подписаться: новые релизы появятся в «Подписках»'}
            className={cx('flex h-9 items-center gap-1.5 rounded-full border px-4 text-sm font-semibold transition active:scale-95', followed ? 'border-accent text-accent' : 'border-fg/40 text-fg hover:border-fg')}
          >
            {busy === 'follow' ? <Spinner size={14} /> : followed && <Icon name="check" size={15} stroke={3} />}
            {followed ? 'Вы подписаны' : 'Подписаться'}
          </button>
          <button type="button" onClick={() => setMenu(true)} aria-label="Ещё" title="Ещё" className="flex h-11 w-11 items-center justify-center rounded-full text-muted hover:text-fg">
            <Icon name="more" size={24} />
          </button>
        </div>
      )}

      <div className="lg:grid lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] lg:gap-x-6">
        <div className="lg:order-2">
          {likedBy > 0 && (
            <section aria-label="Вам нравится" className="px-4 pb-2 lg:px-0 lg:pr-6">
              <h2 className="hidden pb-2 pt-3 text-lg font-bold lg:block">Вам нравится</h2>
              <Link to={`/liked?artist=${enc(artist.name)}${`&ref=${enc(artist.ref)}`}`} className="flex items-center gap-3 rounded-2xl bg-surface px-3 py-2.5 hover:bg-raised">
                <span className="relative shrink-0">
                  <Cover src={artist.picture_small_url || artist.picture_url} size={44} round icon="user" alt="" />
                  <span className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-accent text-on-accent ring-2 ring-surface">
                    <Icon name="heart" size={11} filled />
                  </span>
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block font-semibold lg:hidden">Вам нравится</span>
                  <span className="block text-[13px] text-muted lg:text-[15px] lg:font-semibold lg:text-fg">{tracksWord(likedBy)}</span>
                </span>
                <Icon name="chevron" size={18} className="text-dim" />
              </Link>
            </section>
          )}
          {releases.length > 0 && (
            <section aria-label="Релизы" className="hidden lg:block lg:pr-6">
              <Releases artistRef={artist.ref} releases={releases} />
            </section>
          )}
        </div>

        <div className="lg:order-1">
          {top.length > 0 && (
            <section aria-label="Популярное">
              <h2 className="px-4 pb-1 pt-3 text-lg font-bold lg:px-6">Популярное</h2>
              {top.map((t, i) => (
                <TrackRow key={t.ref} track={t} number={i + 1} queue={top} />
              ))}
            </section>
          )}
        </div>
      </div>

      {releases.length > 0 && (
        <section aria-label="Релизы" className="lg:hidden">
          <Releases artistRef={artist.ref} releases={releases} />
        </section>
      )}

      <Sheet open={menu} onClose={() => setMenu(false)} title={artist.name}>
        <div className="-mx-1">
          {canWave() && item('wave', 'Волна по исполнителю', () => {
            setMenu(false);
            void startWave({ artist_ref: artist.ref }, 'artist_page');
          })}
          {item('download', 'Скачать всё', () => void downloadAll())}
          {item('link', 'Скопировать ссылку', () => void share())}
          {deezer && (
            <a href={deezer} target="_blank" rel="noopener noreferrer" onClick={() => setMenu(false)} className="flex min-h-12 w-full items-center gap-4 rounded-lg px-3 text-[15px] hover:bg-fg/5">
              <Icon name="external" size={21} className="text-muted" />
              Открыть в Deezer
            </a>
          )}
          {item('ban', 'Не включать этого исполнителя', () => void hide(), true)}
        </div>
      </Sheet>
    </div>
  );
}

/** The latest releases, and the way to all of them. */
function Releases({ artistRef, releases }: { artistRef: string; releases: AlbumOut[] }) {
  const to = `/artist/${enc(artistRef)}/discography`;
  return (
    <>
      <div className="flex items-baseline justify-between px-4 pb-2 pt-4 lg:px-0 lg:pt-3">
        <h2 className="text-lg font-bold">Релизы</h2>
        <Link to={to} className="text-sm text-muted hover:text-fg">
          Вся дискография
        </Link>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-5 px-4 min-[520px]:grid-cols-4 lg:grid-cols-2 lg:px-0">
        {releases.slice(0, LATEST).map((a) => (
          <ReleaseCard key={a.ref} album={a} />
        ))}
      </div>
      <div className="px-4 pb-2 pt-4 lg:px-0">
        <Link to={to} className="flex h-11 items-center justify-center rounded-full border border-fg/30 text-sm font-semibold hover:border-fg">
          Вся дискография · {releases.length}
        </Link>
      </div>
    </>
  );
}
