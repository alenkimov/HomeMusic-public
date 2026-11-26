import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';
import { api } from '../api/client';
import type { ArtistMapOut, CatalogArtist } from '../api/types';
import { useAsync } from '../lib/hooks';
import { Link, enc } from '../lib/router';
import { useSession } from '../lib/session';
import { toggleLike, useLiked, useLikesKnown } from '../lib/stars';
import { attempt, toast } from '../lib/ui';
import { playAll } from './catalog';
import { Icon } from './Icon';
import { ServiceText } from './ServiceBadge';
import { Button, Cover, ErrorState, Spinner, cx } from './ui';

/** «Карта похожих»: an artist in the middle and those Last.fm holds to be near them around — the nearer the closer
 * and the bigger, each with how near in percent (the share of common listeners, as Last.fm counts it). Up to fifty
 * stand around; the far ones are beyond the edge of the window, and the map is moved and zoomed to see them: a
 * finger drags it and two pinch it, a mouse drags it and its wheel zooms.
 *
 * A press on an artist goes to them — they become the middle (`onArtist`); nothing else happens to them. What can be
 * done with the artist in the middle is on the strip under the map (`ArtistMapPanel`): their page, their popular
 * tracks, and a button — to follow them («Карта похожих» itself), or to pick them («Кого вы слушаете?»).
 *
 * The map is Last.fm's word, so a server without a Last.fm key has no map (`MeOut.artist_map`). */
export type ArtistsView = 'list' | 'map';

const KEY = 'hm-artists-view';

function remembered(): ArtistsView {
  try {
    return localStorage.getItem(KEY) === 'map' ? 'map' : 'list';
  } catch {
    return 'list';
  }
}

/** «Список» or «Карта» on the page where artists are picked: the view to show, the way to change it, and whether
 * there is a choice. Remembered on this device. `first`: the view to open with this once, whatever is remembered. */
export function useArtistsView(first?: ArtistsView | null): [ArtistsView, (v: ArtistsView) => void, boolean] {
  const can = !!useSession().me.artist_map;
  const [view, setView] = useState<ArtistsView>(() => first ?? remembered());
  const set = (v: ArtistsView) => {
    setView(v);
    try {
      localStorage.setItem(KEY, v);
    } catch {
      /* not remembered, then */
    }
  };
  return [can ? view : 'list', set, can];
}

/** «Список | Карта»: two words in one pill. */
export function ArtistsViewSwitch({ view, onChange }: { view: ArtistsView; onChange: (v: ArtistsView) => void }) {
  const one = (v: ArtistsView, label: string) => (
    <button
      type="button"
      role="tab"
      aria-selected={view === v}
      onClick={() => onChange(v)}
      className={cx('inline-flex h-8 items-center rounded-full px-3.5 text-[13px] transition', view === v ? 'bg-fg font-semibold text-bg' : 'text-muted hover:text-fg')}
    >
      {label}
    </button>
  );
  return (
    <div role="tablist" aria-label="Как показывать исполнителей" className="inline-flex shrink-0 rounded-full bg-raised p-0.5">
      {one('list', 'Список')}
      {one('map', 'Карта')}
    </div>
  );
}

const NEAREST = 15; // asked for first: they are on screen at once
const ALL = 50;

/** The map around `ref`: the nearest few at once, then everybody (each is looked up in the catalog, which takes a
 * while the first time). `filling`: the rest are still being looked up. */
export function useArtistMap(ref: string | null | undefined, enabled = true) {
  const quick = useAsync((s) => api.artistMap(ref!, NEAREST, s), [ref], enabled && !!ref);
  const full = useAsync((s) => api.artistMap(ref!, ALL, s), [ref], enabled && !!ref && !!quick.data?.more);
  const data = !ref ? undefined : full.data?.center && quick.data && full.data.around.length >= quick.data.around.length ? full.data : quick.data;
  return { data, loading: quick.loading, filling: !!quick.data?.more && !full.data && !full.error, error: quick.data ? undefined : quick.error, reload: () => quick.reload() };
}

const GOLDEN = (137.508 * Math.PI) / 180; // between neighbours: nobody stands behind anybody
const STEP = 64; // how far apart they stand, px of the map
const FIRST = 3.1; // the nearest one stands clear of the one in the middle
const radius = (i: number) => STEP * Math.sqrt(i + FIRST);
const SEEN = radius(11) + 56; // what the window shows at first: a dozen around the middle
const ZOOM_MAX = 1.8;

interface View {
  x: number;
  y: number;
  k: number;
}

export function ArtistMap({
  data,
  loading,
  filling,
  error,
  onRetry,
  onArtist,
  marked,
  className,
}: {
  data?: ArtistMapOut | null;
  loading?: boolean;
  /** the far ones are still being looked up */
  filling?: boolean;
  error?: string;
  onRetry?: () => void;
  onArtist: (a: CatalogArtist) => void;
  /** those shown with a check (picked, on «Кого вы слушаете?») */
  marked?: ReadonlySet<string> | ReadonlyMap<string, unknown>;
  /** the size of the window the map is seen through */
  className?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const [view, setView] = useState<View>({ x: 0, y: 0, k: 1 });
  const at = useRef(view);
  at.current = view;
  const n = data?.around.length ?? 0;
  const fit = size ? Math.min(1, Math.min(size.w, size.h) / 2 / SEEN) : 1; // a dozen in sight
  const least = size ? Math.min(fit, Math.min(size.w, size.h) / 2 / (radius(Math.max(n, 12)) + 60)) : fit; // everybody in sight
  const clamp = (k: number) => Math.max(least, Math.min(ZOOM_MAX, k));

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const watch = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    watch.observe(el);
    return () => watch.disconnect();
  }, []);
  // a new artist in the middle: the map is seen from the middle again
  const center = data?.center.ref;
  useEffect(() => setView({ x: 0, y: 0, k: fit }), [center, fit]);

  /** zoom to `k`, the point of the window at (px, py) from its middle staying where it is */
  const zoom = (k: number, px = 0, py = 0) => {
    const v = at.current;
    const to = clamp(k);
    setView({ k: to, x: px - ((px - v.x) * to) / v.k, y: py - ((py - v.y) * to) / v.k });
  };
  const zoomRef = useRef(zoom);
  zoomRef.current = zoom;
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const wheel = (e: WheelEvent) => {
      e.preventDefault(); // the wheel is the map's here, not the page's
      const r = el.getBoundingClientRect();
      zoomRef.current(at.current.k * Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015)), e.clientX - r.left - r.width / 2, e.clientY - r.top - r.height / 2);
    };
    el.addEventListener('wheel', wheel, { passive: false });
    return () => el.removeEventListener('wheel', wheel);
  }, []);

  // fingers (and the mouse) on the map: one drags it, two pinch it
  const touch = useRef(new Map<number, { x: number; y: number }>());
  const dragged = useRef(false);
  const went = useRef(0);
  const down = (e: ReactPointerEvent) => {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    if (!touch.current.size) {
      dragged.current = false;
      went.current = 0;
    }
    touch.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
  };
  const move = (e: ReactPointerEvent) => {
    const was = touch.current.get(e.pointerId);
    const el = box.current;
    if (!was || !el) return;
    const now = { x: e.clientX, y: e.clientY };
    const v = at.current;
    if (touch.current.size === 1) {
      went.current += Math.abs(now.x - was.x) + Math.abs(now.y - was.y);
      if (!dragged.current && went.current < 7) return; // a press that trembles is still a press
      if (!dragged.current) {
        dragged.current = true;
        el.setPointerCapture(e.pointerId);
      }
      touch.current.set(e.pointerId, now);
      setView({ ...v, x: v.x + now.x - was.x, y: v.y + now.y - was.y });
      return;
    }
    dragged.current = true;
    const other = [...touch.current.entries()].find(([id]) => id !== e.pointerId)?.[1];
    touch.current.set(e.pointerId, now);
    if (!other) return;
    const r = el.getBoundingClientRect();
    const before = Math.hypot(was.x - other.x, was.y - other.y) || 1;
    const after = Math.hypot(now.x - other.x, now.y - other.y) || 1;
    const mx = (now.x + other.x) / 2 - r.left - r.width / 2;
    const my = (now.y + other.y) / 2 - r.top - r.height / 2;
    const k = clamp((v.k * after) / before);
    // the middle of the two fingers stays under them, and goes with them
    setView({ k, x: mx - ((mx - v.x) * k) / v.k + (now.x - was.x) / 2, y: my - ((my - v.y) * k) / v.k + (now.y - was.y) / 2 });
  };
  const up = (e: ReactPointerEvent) => {
    touch.current.delete(e.pointerId);
  };

  const places = useMemo(
    () =>
      (data?.around ?? []).map((x, i) => {
        const r = radius(i);
        const a = -Math.PI / 2 + i * GOLDEN;
        return { ...x, x: r * Math.cos(a), y: r * Math.sin(a) };
      }),
    [data],
  );
  const best = Math.max(...(data?.around ?? []).map((x) => x.match), 0.01);
  const on = (ref: string) => !!marked?.has(ref);
  const node = (a: CatalogArtist, size_: number, x: number, y: number, press: (() => void) | undefined, caption: string | null, key: string) => (
    <button
      key={key}
      type="button"
      onClick={press}
      disabled={!press}
      aria-label={caption ? `${a.name}, близость ${caption}` : a.name}
      title={a.name}
      className="absolute flex w-[5rem] -translate-x-1/2 flex-col items-center text-center enabled:hover:z-10"
      style={{ left: x, top: y - size_ / 2 }}
    >
      <span className="relative">
        <Cover src={a.picture_small_url || a.picture_url} size={size_} round icon="user" alt="" className={cx('shadow-lg transition', !press ? 'ring-[3px] ring-fg/80' : on(a.ref) && 'ring-[3px] ring-accent')} />
        {on(a.ref) && (
          <span className="absolute -bottom-0.5 -right-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-accent text-on-accent ring-2 ring-bg">
            <Icon name="check" size={12} stroke={3} />
          </span>
        )}
      </span>
      <span className={cx('mt-1 w-full truncate text-xs leading-tight', !press ? 'font-semibold text-fg' : on(a.ref) ? 'font-semibold text-accent' : 'text-fg')}>{a.name}</span>
      {caption && <span className="text-[11px] leading-tight text-dim">{caption}</span>}
    </button>
  );
  const tool = (icon: 'plus' | 'minus' | 'target', label: string, run: () => void) => (
    <button type="button" aria-label={label} title={label} onClick={run} className="flex h-9 w-9 items-center justify-center text-muted transition hover:text-fg">
      <Icon name={icon} size={18} />
    </button>
  );

  return (
    <div
      ref={box}
      onPointerDown={down}
      onPointerMove={move}
      onPointerUp={up}
      onPointerCancel={up}
      onClickCapture={(e) => {
        // the map was dragged, not pressed
        if (dragged.current) {
          e.stopPropagation();
          e.preventDefault();
          dragged.current = false;
        }
      }}
      data-pull-ignore
      className={cx('relative w-full touch-none select-none overflow-hidden', data && 'cursor-grab active:cursor-grabbing', className ?? 'h-[min(62dvh,36rem)]')}
    >
      {error ? (
        <div className="flex h-full items-center justify-center">
          <ErrorState text={error} onRetry={onRetry} />
        </div>
      ) : !data || !size ? (
        <div className="flex h-full items-center justify-center">
          <Spinner size={28} />
        </div>
      ) : (
        <>
          <div className={cx('absolute left-0 top-0 transition-opacity', loading && 'opacity-50')} style={{ transform: `translate(${size.w / 2 + view.x}px, ${size.h / 2 + view.y}px) scale(${view.k})`, transformOrigin: '0 0' }}>
            {/* how far is how near: rings to read the distance by */}
            {[4, 14, 30, 49].filter((i) => i === 4 || i < n).map((i) => (
              <span key={i} aria-hidden="true" className="pointer-events-none absolute rounded-full border border-fg/[0.07]" style={{ width: radius(i) * 2, height: radius(i) * 2, left: -radius(i), top: -radius(i) }} />
            ))}
            {places.map((x) => node(x.artist, Math.round(38 + 22 * (x.match / best)), x.x, x.y, () => onArtist(x.artist), `${Math.max(1, Math.round(x.match * 100))}%`, x.artist.ref))}
            {node(data.center, 80, 0, 0, undefined, null, `center:${data.center.ref}`)}
          </div>
          <div className="absolute right-2 top-2 flex flex-col divide-y divide-fg/10 rounded-lg bg-raised/90 backdrop-blur">
            {tool('plus', 'Приблизить', () => zoom(view.k * 1.35))}
            {tool('minus', 'Отдалить', () => zoom(view.k / 1.35))}
            {tool('target', 'Вернуться к середине', () => setView({ x: 0, y: 0, k: fit }))}
          </div>
          <p className="pointer-events-none absolute bottom-1.5 left-3 right-3 text-center text-[11px] text-dim">
            {n === 0 ? <><ServiceText text="Last.fm никого не знает рядом с" /> {data.center.name}</> : filling ? 'Ищем остальных…' : <ServiceText text="Чем ближе к середине, тем больше общих слушателей — по Last.fm" />}
          </p>
        </>
      )}
    </div>
  );
}

/** «Подписаться» / «Вы подписаны» for the artist of the strip: followed with one press, unfollowed with another
 * (a word at the foot of the window takes either back). Not shown until the follows are read. */
export function ArtistFollowButton({ artist }: { artist: CatalogArtist }) {
  const target = { targetType: 'artist' as const, ref: artist.ref, data: { name: artist.name } };
  const followed = useLiked(target);
  const known = useLikesKnown();
  const [busy, setBusy] = useState(false);
  if (!known) return null;
  const press = async () => {
    setBusy(true);
    try {
      await toggleLike(target, true);
    } finally {
      setBusy(false);
    }
    toast(followed ? `Вы отписались: ${artist.name}` : `Вы подписаны: ${artist.name}. Новые релизы появятся в «Релизах»`, followed ? 'info' : 'success', {
      label: followed ? 'Вернуть' : 'Отменить',
      run: () => void toggleLike(target, true),
    });
  };
  return followed ? (
    <button type="button" onClick={() => void press()} disabled={busy} title="Отписаться" className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full px-3 text-[13px] font-semibold text-accent transition hover:bg-fg/5">
      <Icon name="check" size={15} stroke={3} />
      Вы подписаны
    </button>
  ) : (
    <Button small busy={busy} onClick={() => void press()}>
      Подписаться
    </Button>
  );
}

/** The strip under the map, of the artist in the middle: who they are (a press opens their page), `action` — what
 * this place does with an artist — and ▶, their popular tracks (what ▶ of their page plays). */
export function ArtistMapPanel({ artist, action }: { artist: CatalogArtist; action?: ReactNode }) {
  const [busy, setBusy] = useState(false);
  const play = async () => {
    setBusy(true);
    const page = await attempt(() => api.artist(artist.ref));
    setBusy(false);
    if (!page) return;
    const top = page.top_tracks.slice(0, 10);
    if (top.length) playAll(top, { context: 'artist' });
    else toast(`У ${artist.name} нет популярных треков в каталоге`, 'info');
  };
  return (
    <div className="flex items-center gap-3 border-t border-fg/[0.07] bg-raised/60 px-4 py-3">
      <Link to={`/artist/${enc(artist.ref)}`} className="flex min-w-0 flex-1 items-center gap-3">
        <Cover src={artist.picture_small_url || artist.picture_url} size={48} round icon="user" alt="" />
        <span className="min-w-0">
          <span className="block truncate font-semibold">{artist.name}</span>
          <span className="flex items-center gap-0.5 text-xs text-muted">
            Открыть страницу
            <Icon name="chevron" size={13} />
          </span>
        </span>
      </Link>
      {action}
      <button
        type="button"
        aria-label={`Слушать популярное: ${artist.name}`}
        title="Слушать популярное"
        onClick={() => void play()}
        disabled={busy}
        className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition hover:scale-105 active:scale-95"
      >
        <Icon name="play" size={20} filled className="translate-x-0.5" />
      </button>
    </div>
  );
}
