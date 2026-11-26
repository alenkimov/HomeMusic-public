import { useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { AlbumOut, ArtistPageOut, TrackOut } from '../api/types';
import { ArtistBubble, ReleaseCard, TrackRow, TrackTableHeader } from '../components/catalog';
import { TrackTable, useTrackTable } from '../components/TrackTable';
import { Icon } from '../components/Icon';
import { TidyMark } from '../components/TidyMark';
import { CatalogSourceMarks } from '../components/CatalogSourceMarks';
import { ListBar } from '../components/ListBar';
import { ArtistLooksWindow } from '../components/ArtistLooks';
import { ArtistServiceButtons } from '../components/ArtistSources';
import { SourceStatuses } from '../components/SourceStatuses';
import { ArtistProjects, ArtistWindow, artistHint, useArtistCard } from '../components/ArtistNames';
import { Button, Cover, Empty, ErrorState, HeroSkeleton, PageHeader, Spinner, cx , SectionTitle, Segmented, buttonClass } from '../components/ui';
import { ContextMenu, useContextMenu } from '../components/ContextMenu';
import { startDownload } from '../lib/actions';
import { setDisliked, useDisliked } from '../lib/dislikes';
import { plural, tracksWord } from '../lib/format';
import { useAsync, useRememberScrollX, useSourceRequest } from '../lib/hooks';
import { useHubEvent } from '../lib/events';
import { imageUrl } from '../lib/img';
import { useSoftCover } from '../lib/softCover';
import { Link, RouteBackground, artistPath, enc, navigate, useCanonicalPath } from '../lib/router';
import { copyText } from '../lib/share';
import { useSession } from '../lib/session';
import { toggleLike, useLiked, useLikesKnown } from '../lib/stars';
import { confirmDialog, toast } from '../lib/ui';
import { canWave, startWave } from '../lib/wave';
import { PlayPair } from './MediaLibrary';

function PopularTracks({ tracks }: { tracks: readonly TrackOut[] }) {
  const twoLists = useTrackTable()?.twoLists;
  const middle = Math.ceil(tracks.length / 2);
  const lists = twoLists ? [tracks.slice(0, middle), tracks.slice(middle)] : [tracks];
  return (
    <div className={twoLists ? 'grid grid-cols-2 gap-x-4' : undefined}>
      {lists.map((list, side) => (
        <div key={side} className="min-w-0">
          <TrackTableHeader numbered />
          {list.map((track, i) => <TrackRow key={track.ref} track={track} number={side * middle + i + 1} queue={tracks} />)}
        </div>
      ))}
    </div>
  );
}

/** An artist's page (`/artist/<ref>`; a library artist the catalog knows opens it too): a list's head — the photo,
 * the name, ▶ and the shuffle of the popular tracks — then a row of buttons: «Подписаться», «Вам нравится» (the
 * user's liked tracks the artist is on, when there are any) and ⋯ (the wave by the artist, downloading everything,
 * sharing, «Не включать»). Under them the ten popular tracks (two columns of five in a wide window), then
 * «Дискография»: the releases in a row that scrolls sideways, newest first, by kind, and the way to all of them
 * with their tracks (the heading, `pages/Discography.tsx`).
 *
 * Where the artist has a wide picture (the Hub finds one and keeps it: `catalog/banners.py`) it is the head of the
 * page, the name over it; where there is none — the round photo beside the name, as a list's head.
 *
 * An artist the user does not listen to (`lib/dislikes.ts`) has a grey page that says so and offers to bring them
 * back: nothing of theirs plays by itself, a track tapped still does. */

const POPULAR = 10;
const IN_ROW = 20; // releases in the row of the page; the rest are on «Дискография»

type Kind = 'all' | 'album' | 'single';
const KINDS: Array<{ value: Kind; label: string }> = [
  { value: 'all', label: 'Все' },
  { value: 'album', label: 'Альбомы' },
  { value: 'single', label: 'Синглы и EP' },
];
const kindOf = (a: AlbumOut): Kind => (a.record_type === 'single' || a.record_type === 'ep' ? 'single' : 'album');

/** The user's liked tracks this artist is on (credited anywhere, not only first). */
function useLikedBy(name: string | undefined, ref: string | undefined): number {
  const liked = useAsync((s) => api.ndLiked(s), [], true, 'liked');
  return useMemo(() => {
    if (!name) return 0;
    const want = name.trim().toLocaleLowerCase('ru');
    return (liked.data?.songs ?? []).filter((t) => (!!ref && (t.artist_ref === ref || !!t.artist_refs?.includes(ref))) || (t.artists?.length ? t.artists : [t.artist]).some((a) => a.trim().toLocaleLowerCase('ru') === want)).length;
  }, [liked.data, name, ref]);
}

/** An artist known by the name alone (`/artist-named/<name>`, from a player): the catalog's artist of that name
 * takes the place of this page; when it has none, the search for the name does. */
export function ArtistNamedPage({ name }: { name: string }) {
  const background = useContext(RouteBackground);
  const res = useAsync((s) => api.artistNamed(name, s), [name]);
  useEffect(() => {
    if (!background && res.data) navigate(res.data.ref ? `/artist/${enc(res.data.ref)}` : `/search?q=${enc(name)}`, { replace: true });
  }, [background, res.data, name]);
  if (res.error) return (<><PageHeader hero title={name} back="/search" /><ErrorState text={res.error} onRetry={() => res.reload()} /></>);
  return (<><PageHeader hero title={name} back="/search" /><HeroSkeleton round buttons rows={10} /></>);
}

export function ArtistPage({ refId, initialPath }: { refId: string; initialPath?: string }) {
  const { res, sources } = useSourceRequest(`artist:${refId}`, (s, update) => api.artist(refId, s, false, update));
  // the Hub put this page together anew (it was old, or the first one lacked a catalog): taken without a spinner
  useHubEvent('artist', (e) => {
    const d = res.data;
    if (String(e.id) === refId || e.refs.includes(refId) || (d && (d.artist.hub_artist_id === e.id || (d.profiles ?? []).some((r) => e.refs.includes(r))))) res.reload(true);
  });
  const status = sources.length > 0 && <SourceStatuses sources={sources} label="Статусы источников исполнителя" />;
  const canMap = !!useSession().me.artist_map; // «Карта похожих» is there
  const menu = useContextMenu();
  const [naming, setNaming] = useState<'names' | 'projects' | 'looks' | null>(null); // a window of the artist's ⋯
  const [busy, setBusy] = useState<string | null>(null);
  const d: ArtistPageOut | undefined = res.data;
  const artist = d?.artist;
  const catalogRef = artist?.ref ?? (refId.includes(':') ? refId : `artist:${refId}`);
  const identityRef = artist?.hub_artist_id ? `artist:${artist.hub_artist_id}` : catalogRef;
  const target = { targetType: 'artist' as const, ref: identityRef, data: { name: artist?.name ?? '' } };
  const followed = useLiked(target);
  const likesKnown = useLikesKnown(); // until the likes are read the button is not shown: it may be one to take back
  // «Подписаться» just pressed: the button says so ('said'), then folds away ('folding'), then is gone
  const [leaving, setLeaving] = useState<'said' | 'folding' | null>(null);
  useEffect(() => {
    if (!leaving) return;
    // (the artist is not followed after all — it did not work, or was taken back at once: the button stays)
    const t = window.setTimeout(() => setLeaving(leaving === 'said' && followed ? 'folding' : null), leaving === 'said' ? 1100 : 350);
    return () => window.clearTimeout(t);
  }, [leaving, followed]);
  const hidden = useDisliked({ artist_ref: identityRef });
  const likedBy = useLikedBy(artist?.name, catalogRef);
  useCanonicalPath(initialPath ?? `/artist/${enc(refId)}`, artist?.hub_artist_id ? artistPath(artist) : undefined);
  // the artist as the Hub knows them: the name everyone sees may be another spelling than the catalog's
  const names = useArtistCard(artist?.name, artist ? identityRef : undefined);
  if (res.loading && !d) return (<><PageHeader hero title="Исполнитель" back="/search" right={status} /><HeroSkeleton round buttons rows={10} /></>);
  if (!d || !artist) return (<><PageHeader hero title="Исполнитель" back="/search" right={status} /><ErrorState text={res.error ?? 'Исполнитель не найден'} onRetry={() => res.reload()} /></>);

  const releases = [...d.albums, ...d.singles, ...d.compilations].sort((a, b) => (b.release_date ?? '').localeCompare(a.release_date ?? ''));
  const top = d.top_tracks.slice(0, POPULAR);

  /** «Подписаться»: the button says «Вы подписаны» for a moment, then folds away — it is pressed once (an artist
   * followed has «Отписаться» in the ⋯); a word at the foot of the window takes a slip of the finger back. */
  const follow = async () => {
    setBusy('follow');
    try {
      await toggleLike(target, true);
    } finally {
      setBusy(null);
    }
    setLeaving('said');
    toast(`Вы подписаны: ${shownName}. Новые релизы появятся в «Релизах»`, 'success', { label: 'Отменить', run: () => void toggleLike(target, true) });
  };
  const unfollow = async () => {
    await toggleLike(target, true);
    toast(`Вы отписались: ${shownName}`, 'info', { label: 'Вернуть', run: () => void toggleLike(target, true) });
  };
  const downloadAll = async () => {
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
    await startDownload({ artist_ref: String(artist.hub_artist_id ?? artist.ref), record_types: ['album', 'single', 'ep'] });
    res.reload(true);
  };
  const share = async () => {
    const url = `${window.location.origin}/#${artistPath(artist)}`;
    toast((await copyText(url)) ? 'Ссылка на исполнителя скопирована' : url, 'success');
  };
  const hide = async () => {
    if (hidden) return void setDisliked({ artist_ref: identityRef, artist: artist.name }, false);
    if (!(await confirmDialog({ title: `Не включать ${artist.name}?`, body: 'Треки этого исполнителя, и те, где он участвует, не будут попадать в волну, миксы и открытия, а в плейлистах будут пропускаться. Нажатый вручную трек сыграет. Вернуть можно здесь же или в «Меню → Скрытое».', okLabel: 'Не включать', danger: true }))) return;
    await setDisliked({ artist_ref: identityRef, artist: artist.name }, true);
  };

  const shownName = names.card?.name ?? artist.name;
  const photo = imageUrl(artist.picture_url);

  return (
    <div className={hidden ? 'grayscale' : undefined}>
      {/* the picture over the page: before everything, so it stays at the top of the window while the page goes over
          it. The wide one where the artist has it; else their own photo, out of focus — the round photo stands on it */}
      {(d.banner_url || photo) && <BannerPicture src={(d.banner_url ?? photo) as string} blurred={!d.banner_url} />}
      <ListBar
        title={shownName}
        back="/search"
        tracks={hidden ? undefined : top}
        context="artist"
        end={
          <>
          {(res.loading || sources.some((s) => s.state === 'searching')) && status}
          {/* the search through everything of the artist — their releases and every track: the page of them all */}
          <button type="button" onClick={() => navigate(`${artistPath(artist)}/discography?view=tracks&search=1`)} aria-label="Поиск по трекам и релизам исполнителя" title="Поиск по трекам и релизам" className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-bg/40 text-fg hover:bg-bg/70">
            <Icon name="search" size={20} />
          </button>
          {/* the ⋯ of the artist: at the top right, and there still when the page is scrolled */}
          <button type="button" onClick={menu.fromButton} aria-label="Ещё" title="Ещё" className="relative mr-1 flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-bg/40 text-fg hover:bg-bg/70">
            <Icon name="more" size={22} />
            {/* somebody looks like the same artist under another spelling: «Имя и написания» says who */}
            {artistHint(names.card) && <span aria-hidden="true" className="absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-info" />}
          </button>
          </>
        }
      />
      <ArtistBanner name={shownName} photo={d.banner_url ? undefined : (photo ?? null)} mark={<TidyMark on={artist.hub_artist_id ? { artist: artist.hub_artist_id } : null} size="lg" />} />

      {/* what goes up over the picture has the page's own colour under it */}
      <div className="relative z-[1] min-h-[60dvh] bg-bg">
      {hidden ? (
        <div className="mx-4 mb-3 rounded-2xl bg-danger/15 px-4 py-3 text-sm text-fg lg:mx-6">
          <p>Вы не слушаете этого исполнителя. Его треков нет в волне, миксах и открытиях, а в плейлистах они пропускаются.</p>
          <Button small className="mt-3" onClick={() => void setDisliked({ artist_ref: identityRef, artist: artist.name }, false)}>
            Вернуть
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-2 px-4 pb-3 pt-3 lg:px-6">
          {/* the buttons go sideways where they do not fit; ▶ and the shuffle stay at the right edge, whatever the width */}
          <div className="no-scrollbar -my-1 flex min-w-0 flex-1 items-center gap-2 overflow-x-auto py-1 pr-5 [mask-image:linear-gradient(to_right,#000_calc(100%-1.25rem),transparent)] [&>*]:shrink-0">
          {/* pressed once: an artist followed has no such button — the ⋯ takes it back */}
          {likesKnown && (!followed || leaving) && (
            <button
              type="button"
              onClick={() => void follow()}
              disabled={busy === 'follow' || followed}
              title="Подписаться: новые релизы появятся в «Релизах»"
              className={buttonClass('outline', true, cx(
                'gap-1.5 overflow-hidden whitespace-nowrap transition-all duration-300 ease-out',
                followed && '!border-accent !text-accent',
                leaving === 'folding' ? '-mr-2 max-w-0 !border-transparent !px-0 opacity-0' : 'max-w-56',
              ))}
            >
              {busy === 'follow' ? <Spinner size={14} /> : followed && <Icon name="check" size={15} stroke={3} />}
              {followed ? 'Вы подписаны' : 'Подписаться'}
            </button>
          )}
          {likedBy > 0 && (
            <Link
              to={`/liked?artist=${enc(artist.name)}&ref=${enc(artist.ref)}`}
              title={`Вам нравится: ${tracksWord(likedBy)} этого исполнителя в «Любимом»`}
              className={buttonClass('outline', true, 'gap-1.5')}
            >
              <Icon name="heart" size={15} filled className="text-accent" />
              <span className="hidden lg:inline">Вам нравится ·</span>
              {tracksWord(likedBy)}
            </Link>
          )}
          {/* the other projects of who it is: each leads to its own page */}
          <ArtistProjects card={names.card} />
          <ArtistServiceButtons sources={d.sources ?? []} />
          </div>
          {top.length > 0 && <PlayPair tracks={top} context="artist" />}
        </div>
      )}

      {top.length > 0 && (
        <section aria-label="Популярное">
          <div className="flex items-center gap-2 px-4 pb-1 pt-3 lg:px-6">
            <h2 className="text-lg font-bold">Популярное</h2>
            <CatalogSourceMarks services={d.top_track_sources?.slice(0, top.length) ?? []} ranked />
          </div>
          <TrackTable tracks={top} numbered kind="popular">
            <PopularTracks tracks={top} />
          </TrackTable>
          {/* the rest of what they made: every track of theirs as one list, the most listened first */}
          {releases.length > 0 && (
            <div className="flex justify-center px-4 pt-2 lg:px-6">
              <Link to={`${artistPath(artist)}/discography?view=tracks`} className={buttonClass('outline', true)}>
                Все треки
              </Link>
            </div>
          )}
        </section>
      )}

      {releases.length > 0 && <Discography artistRef={String(artist.hub_artist_id ?? artist.ref)} releases={releases} />}

      {/* a name the catalogs know and no music of: said, not left as an empty page */}
      {!hidden && top.length === 0 && releases.length === 0 && (
        <Empty icon="note" title="Музыки в каталогах нет" text="Каталоги знают этого исполнителя по имени, но ни треков, ни релизов у них нет. Свои файлы можно загрузить в библиотеку." />
      )}

      <Alike artistRef={artist.ref} />

      </div>

      {menu.shown && (
        <ContextMenu
          at={menu.at}
          head={{ title: shownName, sub: 'Исполнитель', cover: <Cover src={photo} size={44} round icon="user" /> }}
          onClose={menu.close}
          entries={[
            canWave() && { icon: 'wave', label: 'Волна по исполнителю', shortcut: 'R', onSelect: () => void startWave({ artist_ref: identityRef }, 'artist_page') },
            canMap && { icon: 'nodes', label: 'Карта похожих', onSelect: () => navigate(`/map/${enc(artist.ref)}`) },
            'sep',
            { icon: 'download', label: 'Скачать всё', shortcut: 'D', onSelect: () => void downloadAll() },
            followed && { icon: 'x', label: 'Отписаться', onSelect: () => void unfollow() },
            'sep',
            names.card && { icon: 'edit', label: 'Имя и написания', shortcut: 'F2', dot: !!artistHint(names.card), onSelect: () => setNaming('names') },
            names.card && { icon: 'user', label: 'Другие проекты', onSelect: () => setNaming('projects') },
            names.card && { icon: 'image', label: 'Фото и обложка', onSelect: () => setNaming('looks') },
            'sep',
            { icon: 'link', label: 'Скопировать ссылку', shortcut: 'C', onSelect: () => void share() },
            'sep',
            { icon: 'ban', label: hidden ? 'Снова включать исполнителя' : 'Не включать исполнителя', active: hidden, done: hidden, danger: !hidden, onSelect: () => void hide() },
          ]}
        />
      )}
      <ArtistWindow
        which={naming === 'looks' ? null : naming}
        card={names.card}
        onChange={names.set}
        onClose={() => setNaming(null)}
        onProfileDetached={() => {
          names.reload();
          res.reload(true);
        }}
      />
      {naming === 'looks' && names.card && <ArtistLooksWindow artistId={names.card.id} onChanged={() => res.reload()} onClose={() => setNaming(null)} />}
    </div>
  );
}

const BANNER_H = 'h-[clamp(15rem,34vw,26rem)]';

/** The artist's wide picture, standing still: a place of no height that sticks to the top of the window, with the
 * picture hanging from it. Nothing moves it — the page's own content, which has the page's colour under it, goes up
 * over it (moved after every scroll instead, it lagged a frame behind and sprang). As the page covers it a shade of
 * the page's colour comes over it too; that is set a frame at a time on the element, and a shade a frame late is
 * not seen. */
export function BannerPicture({ src, blurred }: { src: string; /** not a wide picture but the artist's photo: shown out of focus, as a ground */ blurred?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  // out of focus: blurred once into a tiny picture, the colors a half stronger (`lib/softCover.ts`) — a CSS blur of
  // the whole width was worked out again at every frame of the shade, and held an iPhone
  const soft = useSoftCover(blurred ? imageUrl(src) : null, { vivid: true });
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    let frame = 0;
    const place = () => {
      frame = 0;
      const h = el.offsetHeight || 1;
      el.style.setProperty('--shade', String(Math.min(1, Math.max(0, window.scrollY) / (h * 0.7))));
    };
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(place);
    };
    place();
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', onScroll);
      cancelAnimationFrame(frame);
    };
  }, []);
  return (
    <div aria-hidden="true" className="pointer-events-none sticky top-(--page-top) z-0 h-0">
      <div ref={box} className={cx('absolute inset-x-0 top-0 overflow-hidden', BANNER_H)}>
        {blurred ? (
          soft && <div className="absolute inset-0 bg-cover bg-center" style={{ backgroundImage: `url(${soft})` }}><div className="absolute inset-0 bg-black/45" /></div>
        ) : (
          <img src={imageUrl(src)} alt="" className="h-full w-full object-cover object-[center_25%]" />
        )}
        <div style={{ opacity: 'var(--shade, 0)' }} className="absolute inset-0 bg-bg" />
      </div>
    </div>
  );
}

/** A name kept to one line where it can be, and as large as its place lets it: the page's style says the most (a
 * short name fills the room it has), and a name that does not fit at that is made smaller — down to a third of it,
 * never under 26 px; a name too long even so takes its lines at that least size.
 * Measured anew when the name or the width of its place changes, and once the page's letters have come. */
function useOneLine(what: string) {
  const ref = useRef<HTMLHeadingElement>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const fit = () => {
      el.style.fontSize = '';
      el.style.whiteSpace = 'nowrap';
      const full = parseFloat(getComputedStyle(el).fontSize) || 0;
      const room = el.clientWidth;
      const need = el.scrollWidth;
      if (!full || !room || need <= room) return;
      const least = Math.max(26, full * 0.35);
      const size = Math.floor((full * room) / need) - 1;
      el.style.fontSize = `${Math.max(least, size)}px`;
      if (size < least) el.style.whiteSpace = '';
    };
    fit();
    const watch = new ResizeObserver(fit);
    if (el.parentElement) watch.observe(el.parentElement);
    void document.fonts?.ready.then(fit);
    return () => watch.disconnect();
  }, [what]);
  return ref;
}

/** The head of the page over that picture, reaching up under the bar with «back» and the ⋯: the name in large over
 * its darkened foot — alone, with the whole width (▶ and the shuffle are of the row under the head). Over a wide
 * picture the head is as tall as the picture, and the round photo is not shown — it is the same person twice. Where
 * there is no wide picture (`photo` given: the address, or null for no photo at all) the head is as tall as the
 * round photo, which stands before the name — on a phone and on a computer alike — on the ground made of it.
 */
export function ArtistBanner({ name, photo, sub, mark }: { name: string; photo?: string | null; /** a line under the name */ sub?: string;
  /** after the name: the mark of «Наведение порядка» */ mark?: ReactNode }) {
  const round = 'h-32 w-32 shrink-0 rounded-full shadow-xl sm:h-44 sm:w-44 lg:h-56 lg:w-56';
  const wide = photo === undefined;
  const title = useOneLine(name);
  return (
    <div className={cx('relative isolate z-[1] -mt-[calc(3.5rem+env(safe-area-inset-top))] flex', wide ? cx('items-end', BANNER_H) : 'items-center pt-[calc(4rem+env(safe-area-inset-top))]')}>
      <div aria-hidden="true" className="absolute inset-0 -z-10 bg-gradient-to-t from-bg via-bg/45 to-black/15" />
      <div className={cx('flex w-full gap-4 px-4 pb-4 lg:gap-6 lg:px-6 lg:pb-5', wide ? 'items-end' : 'items-center')}>
        {!wide &&
          (photo ? (
            <img src={imageUrl(photo)} alt="" className={cx(round, 'object-cover')} />
          ) : (
            <span className={cx(round, 'flex items-center justify-center bg-fg/10 text-muted')}>
              <Icon name="user" size={40} />
            </span>
          ))}
        <div className="min-w-0 flex-1">
          <div className="text-xs text-fg/80 [text-shadow:0_1px_8px_rgb(0_0_0/0.6)] lg:text-sm">Исполнитель</div>
          <div className="flex items-center gap-3">
            <h2 ref={title} data-list-title className="line-clamp-2 min-w-0 break-words text-[clamp(2.5rem,12vw,9rem)] font-black leading-[1.05] tracking-tight [text-shadow:0_2px_16px_rgb(0_0_0/0.55)]">
              {name}
            </h2>
            {mark}
          </div>
          {sub && <div className="mt-1 text-sm text-fg/80 [text-shadow:0_1px_8px_rgb(0_0_0/0.6)]">{sub}</div>}
        </div>
      </div>
    </div>
  );
}

/** «Похожие»: the artists the catalog holds to be like this one, in a row; on a server with a Last.fm key the
 * heading leads to «Карта похожих» around them (`pages/ArtistMapPage`). Nothing where the catalog knows nobody. */
function Alike({ artistRef }: { artistRef: string }) {
  const canMap = !!useSession().me.artist_map;
  const list = useAsync((s) => api.tasteSimilar(artistRef, s), [artistRef], true, `alike:${artistRef}`);
  const row = useRememberScrollX(`artist:${artistRef}:alike`);
  if (!list.data?.length) return null;
  return (
    <section aria-label="Похожие" className="pb-4">
      <SectionTitle page right={canMap && (
        <Link to={`/map/${enc(artistRef)}`} className="inline-flex h-8 items-center gap-1.5 rounded-full bg-raised px-3.5 text-[13px] font-normal text-fg transition hover:bg-hover">
          <Icon name="nodes" size={15} />
          На карте
        </Link>
      )}>
        Похожие
      </SectionTitle>
      <div ref={row} className="no-scrollbar flex gap-3 overflow-x-auto px-4 lg:px-6">
        {list.data.map((a) => (
          <ArtistBubble key={a.ref} artist={a} />
        ))}
      </div>
    </section>
  );
}

/** «Дискография»: the releases in a row that scrolls sideways, newest first, by kind; the heading opens the page
 * of all of them with their tracks. */
function Discography({ artistRef, releases }: { artistRef: string; releases: AlbumOut[] }) {
  const [kind, setKind] = useState<Kind>('all');
  const row = useRememberScrollX(`artist:${artistRef}:${kind}`);
  const kinds = KINDS.filter((k) => k.value === 'all' || releases.some((a) => kindOf(a) === k.value));
  const shown = releases.filter((a) => kind === 'all' || kindOf(a) === kind);
  const to = `/artist/${enc(artistRef)}/discography`;
  return (
    <section aria-label="Дискография" className="pb-4">
      {/* the heading itself leads to all the releases with their tracks; the note: how many there are in all — every
          release of every record a catalog keeps of the artist, each once */}
      <SectionTitle page note={`${releases.length} ${plural(releases.length, ['релиз', 'релиза', 'релизов'])}`}>
        <Link to={to} className="hover:underline">Дискография</Link>
      </SectionTitle>
      {kinds.length > 2 && (
        <div className="px-4 pb-3 lg:px-6">
          <Segmented<Kind> label="Какие релизы" value={kind} onChange={setKind} options={kinds} />
        </div>
      )}
      <div ref={row} className="no-scrollbar flex gap-4 overflow-x-auto px-4 pb-1 lg:px-6">
        {shown.slice(0, IN_ROW).map((a) => (
          <div key={a.ref} className="w-36 shrink-0 lg:w-44">
            <ReleaseCard album={a} showSources />
          </div>
        ))}
        {shown.length > IN_ROW && (
          <Link to={to} className="flex aspect-square w-36 shrink-0 flex-col items-center justify-center gap-2 rounded-md bg-fg/5 text-sm font-semibold text-muted hover:bg-fg/10 hover:text-fg lg:w-44">
            <Icon name="chevron" size={22} />
            Ещё {shown.length - IN_ROW}
          </Link>
        )}
      </div>
    </section>
  );
}
