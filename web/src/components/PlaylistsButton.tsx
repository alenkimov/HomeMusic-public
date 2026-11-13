import { tagKey } from '../lib/tags';
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { PlaylistOfSongOut, PlaylistOfSongsOut } from '../api/types';
import { tracksWord } from '../lib/format';
import { noteInPlaylists, useInPlaylists } from '../lib/inPlaylists';
import { isLiked, setLikes, toggleLike, useLiked, type LikeTarget } from '../lib/stars';
import { attempt, toast } from '../lib/ui';
import { Icon } from './Icon';
import { Button, Cover, Loading, Sheet, cx } from './ui';

/** The one button of a track, in a row and in the players: where the track is kept. «Любимое» is a playlist among
 * the others — a special one, the first in the list — and has no button of its own.
 *   +  the track is nowhere: a click puts it into «Любимое» at once, and the line that says so offers «Изменить»;
 *   ♥  in a green circle: it is in «Любимое» (and maybe in playlists too);
 *   ✓  in a green circle: it is in some playlist, but not in «Любимое».
 * A click on ♥ or ✓ opens the playlists with ticks (`TrackPlaylists`). A catalog track the library has not got is
 * downloaded when it is put anywhere. */
export function SaveButton({ songId, refId, listKey, title, data, size = 20, className }: { songId?: string | null; refId?: string | null; /** what the track is called in the list of tracks kept in playlists (`lib/tags.ts` `tagKey`): a track with no file yet has no `songId` */ listKey?: string | null; title: string; data?: Record<string, unknown>; size?: number; className?: string }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const target: LikeTarget = { targetType: 'track', trackId: listKey ? Number(listKey) : null, songId, ref: refId, data };
  const liked = useLiked(target);
  const listed = useInPlaylists(listKey);
  if (!songId && !refId) return null;
  const kept = liked || listed;
  const label = liked ? `«${title}» в любимом — изменить` : listed ? `«${title}» в плейлистах — изменить` : `«${title}» — в любимое`;
  const click = async () => {
    if (kept) return setOpen(true);
    setBusy(true);
    try {
      await toggleLike(target, false, () => setOpen(true));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button type="button" disabled={busy} onClick={() => void click()} aria-label={label} title={liked ? 'В любимом — изменить' : listed ? 'В плейлистах — изменить' : 'В любимое'} className={cx('flex h-11 w-11 shrink-0 items-center justify-center rounded-full transition hover:scale-110 disabled:opacity-60', kept ? 'text-accent' : 'text-muted hover:text-fg', className)}>
        <SaveMark state={liked ? 'liked' : listed ? 'listed' : 'none'} size={size} />
      </button>
      {open && <TrackPlaylists track={{ title, nd_song_id: songId, ref: refId, data, key: listKey }} onClose={() => setOpen(false)} />}
    </>
  );
}

/** The mark of the button: «+» in a ring, or ♥ / ✓ in a green circle. */
export function SaveMark({ state, size = 20 }: { state: 'liked' | 'listed' | 'none'; size?: number }) {
  return (
    <span className={cx('flex items-center justify-center rounded-full', state === 'none' ? 'border-2 border-current' : 'bg-accent text-on-accent')} style={{ width: size + 2, height: size + 2 }}>
      {state === 'liked' ? <Icon name="heart" size={Math.round(size * 0.6)} filled /> : <Icon name={state === 'listed' ? 'check' : 'plus'} size={size - 6} stroke={3} />}
    </span>
  );
}

/** The row of «Любимое» on top of a list of playlists. */
function LikedRow({ mark, sub, changed, onClick }: { mark: Mark; sub: string; changed?: boolean; onClick: () => void }) {
  return (
    <button type="button" role="checkbox" aria-checked={mark === 'all' ? true : mark === 'some' ? 'mixed' : false} onClick={onClick} className="flex min-h-14 w-full items-center gap-3 px-4 py-1.5 text-left transition hover:bg-fg/5">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-accent/20 text-accent">
        <Icon name="heart" size={20} filled />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[15px]">Любимое</span>
        <span className={cx('block text-xs', changed ? 'text-accent' : 'text-muted')}>{sub}</span>
      </span>
      <Tick mark={mark} />
    </button>
  );
}

function Tick({ mark }: { mark: Mark }) {
  return (
    <span className={cx('flex h-6 w-6 shrink-0 items-center justify-center rounded-full', mark === 'all' ? 'bg-accent text-on-accent' : mark === 'some' ? 'bg-warn text-bg' : 'border-2 border-line')}>
      {mark !== 'none' && <Icon name={mark === 'all' ? 'check' : 'minus'} size={14} stroke={3} />}
    </span>
  );
}

function NewPlaylist({ busy, onCreate }: { busy: boolean; onCreate: (name: string) => Promise<boolean> }) {
  const [name, setName] = useState('');
  return (
    <form
      className="flex items-center gap-2 px-4 py-3"
      onSubmit={(e) => {
        e.preventDefault();
        const n = name.trim();
        if (n) void onCreate(n).then((ok) => ok && setName(''));
      }}
    >
      <input className="input flex-1" value={name} onChange={(e) => setName(e.target.value)} placeholder="Новый плейлист" aria-label="Название нового плейлиста" />
      <Button type="submit" icon="plus" disabled={!name.trim() || busy}>
        Создать
      </Button>
    </form>
  );
}

function Footer({ text, busy, onClose, onDone, disabled }: { text: ReactNode; busy?: boolean; onClose: () => void; onDone: () => void; disabled?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <span className="min-w-0 flex-1 truncate text-xs text-muted">{text}</span>
      <Button onClick={onClose}>Отмена</Button>
      <Button variant="primary" busy={busy} disabled={disabled} onClick={onDone}>
        Готово
      </Button>
    </div>
  );
}

const names = (list: { name: string }[]) => list.map((p) => `«${p.name}»`).join(', ');
const LIKED = { name: 'Любимое' };

/** Where one track is kept: «Любимое» first, then the user's playlists — the ones the track is in on top, then the
 * rest, the ones changed last first. It is ticked into some and out of others; nothing changes until «Готово».
 * A library song changes places at once; a catalog track the library has not got is downloaded and goes into the
 * chosen playlists as it arrives. */
export function TrackPlaylists({ track, onClose }: { track: { title: string; nd_song_id?: string | null; ref?: string | null; data?: Record<string, unknown>; /** what the Hub calls the track (`lib/tags.ts` `tagKey`), if it knows it */ key?: string | null; library_track_id?: number | null }; onClose: () => void }) {
  // what the Hub knows the track by: its own id of it, file or no file. None: a catalog track the Hub has not met
  // — putting it anywhere downloads it
  const songId = tagKey(track);
  const target: LikeTarget = { targetType: 'track', trackId: songId ? Number(songId) : null, songId: track.nd_song_id, ref: track.ref, data: track.data };
  const [wasLiked] = useState(() => isLiked(target));
  const [liked, setLiked] = useState(wasLiked);
  // the order of the opening: a tick does not make a row jump
  const [rows, setRows] = useState<PlaylistOfSongOut[] | null>(null);
  const [want, setWant] = useState<Record<string, boolean>>({});
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    (songId ? api.playlistsOfTrack(songId) : api.playlists().then((r) => r.map((p) => ({ ...p, has: false }))))
      .then((r) => alive && setRows(r))
      .catch(() => {
        if (alive) {
          toast('Не удалось получить плейлисты', 'error');
          onClose();
        }
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [songId]);

  const has = (p: PlaylistOfSongOut) => want[p.id] ?? p.has;
  const add = rows?.filter((p) => !p.has && has(p)) ?? [];
  const remove = rows?.filter((p) => p.has && !has(p)) ?? [];
  const needle = q.trim().toLocaleLowerCase('ru');
  const shown = useMemo(() => (rows ?? []).filter((p) => !needle || p.name.toLocaleLowerCase('ru').includes(needle)), [rows, needle]);
  const inside = shown.filter((p) => p.has);
  const rest = shown.filter((p) => !p.has);
  const into = [...(liked && !wasLiked ? [LIKED] : []), ...add];
  const out = [...(!liked && wasLiked ? [LIKED] : []), ...remove];

  const create = async (name: string) => {
    setBusy(true);
    const p = await attempt(() => api.createPlaylist(name));
    setBusy(false);
    if (!p) return false;
    setRows((list) => [{ ...p, has: false }, ...(list ?? [])]);
    setWant((w) => ({ ...w, [p.id]: true }));
    return true;
  };
  const save = async () => {
    if (!into.length && !out.length) return onClose();
    setBusy(true);
    try {
      if (liked !== wasLiked && isLiked(target) !== liked) await toggleLike(target, true);
      if (songId && (add.length || remove.length)) {
        const r = await attempt(() => api.playlistMembership({ track: songId, add: add.map((p) => p.id), remove: remove.map((p) => p.id) }));
        if (!r) return;
        noteInPlaylists([songId], r.some((p) => p.has));
      } else if (!songId && add.length && track.ref) {
        const r = await attempt(() => api.download({ track_refs: [track.ref as string], playlist_ids: add.map((p) => p.id) }));
        if (!r) return;
      }
    } finally {
      setBusy(false);
    }
    toast([into.length ? `${songId ? 'Добавлено в' : 'Скачиваем — трек появится в'}: ${names(into)}` : '', out.length ? `Убрано из: ${names(out)}` : ''].filter(Boolean).join('. '), 'success');
    onClose();
  };
  const row = (p: PlaylistOfSongOut) => {
    const on = has(p);
    return (
      <li key={p.id}>
        <button type="button" role="checkbox" aria-checked={on} onClick={() => setWant((w) => ({ ...w, [p.id]: !on }))} className="flex min-h-14 w-full items-center gap-3 px-4 py-1.5 text-left transition hover:bg-fg/5">
          <Cover src={p.cover_url ? `${p.cover_url}&size=96` : null} size={40} icon="playlist" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[15px]">{p.name}</span>
            <span className="block text-xs text-muted">{tracksWord(p.song_count + (p.pending_count ?? 0))}</span>
          </span>
          <Tick mark={on ? 'all' : 'none'} />
        </button>
      </li>
    );
  };
  const label = 'px-4 pb-1 pt-3 text-xs text-muted';
  const plan = [into.length ? `добавить в ${into.length}` : '', out.length ? `убрать из ${out.length}` : ''].filter(Boolean).join(', ');

  return (
    <Sheet open onClose={onClose} title={track.title} footer={<Footer text={plan || 'Изменений нет'} busy={busy} onClose={onClose} onDone={() => void save()} />}>
      {!rows ? (
        <Loading />
      ) : (
        <div className="-mx-4 -my-3">
          {!songId && <p className="px-4 pt-3 text-xs text-muted">Трека ещё нет в библиотеке: он скачается и появится там, куда вы его добавите.</p>}
          {songId && !track.nd_song_id && <p className="px-4 pt-3 text-xs text-muted">Файла трека ещё нет: он скачается, когда вы его послушаете. В плейлистах он стоит уже сейчас.</p>}
          <div className="pt-1.5">
            <LikedRow mark={liked ? 'all' : 'none'} sub={liked !== wasLiked ? (liked ? 'добавить' : 'убрать') : liked ? 'трек здесь' : 'трека здесь нет'} changed={liked !== wasLiked} onClick={() => setLiked((v) => !v)} />
          </div>
          {rows.length > 6 && (
            <div className="px-4 pt-3">
              <input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти плейлист" aria-label="Найти плейлист" />
            </div>
          )}
          <NewPlaylist busy={busy} onCreate={create} />
          {inside.length > 0 && <div className={label}>Трек уже здесь</div>}
          <ul>{inside.map(row)}</ul>
          {rest.length > 0 && <div className={label}>{inside.length ? 'Недавно менялись' : 'Плейлисты'}</div>}
          <ul>{rest.map(row)}</ul>
          {!shown.length && <p className="px-4 py-6 text-center text-sm text-muted">{rows.length ? 'Ничего не нашлось' : 'Плейлистов пока нет — создайте первый.'}</p>}
        </div>
      )}
    </Sheet>
  );
}

type Mark = 'all' | 'some' | 'none';

/** One of several picked tracks: a library song has `nd_song_id`, a catalog track only its `ref`. */
export interface PickedTrack {
  ref: string;
  nd_song_id?: string | null;
  /** what the Hub calls the track (`lib/tags.ts` `tagKey`), if it knows it */
  key?: string | null;
  data?: Record<string, unknown>;
}

/** Where several picked tracks are kept (the selection bar): «Любимое» first, then the playlists. Each has all of
 * them (✓), some (–) or none; a tap goes round «add the missing ones» → «take them all out» → «leave as it is» (for
 * one that had some), and the row says what will happen. Nothing changes until «Готово». A picked catalog track the
 * library has not got is downloaded when it is added somewhere. */
export function BulkPlaylists({ tracks, onClose }: { tracks: PickedTrack[]; onClose: () => void }) {
  const n = tracks.length;
  const targets = useMemo<LikeTarget[]>(() => tracks.map((t) => ({ targetType: 'track' as const, trackId: t.key ? Number(t.key) : null, songId: t.nd_song_id, ref: t.ref, data: t.data })), [tracks]);
  const songIds = useMemo(() => tracks.map((t) => t.key).filter((x): x is string => !!x), [tracks]);
  const [likedCount] = useState(() => targets.filter(isLiked).length);
  const [rows, setRows] = useState<PlaylistOfSongsOut[] | null>(null);
  const [want, setWant] = useState<Record<string, Mark>>({});
  const [likedWant, setLikedWant] = useState<Mark | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    (songIds.length ? api.playlistsOfTracks(songIds) : api.playlists().then((r) => r.map((p) => ({ ...p, count: 0 }))))
      .then((r) => alive && setRows(r))
      .catch(() => {
        if (alive) {
          toast('Не удалось получить плейлисты', 'error');
          onClose();
        }
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const mark = (count: number): Mark => (count >= n ? 'all' : count > 0 ? 'some' : 'none');
  const was = (p: PlaylistOfSongsOut): Mark => mark(p.count);
  const now = (p: PlaylistOfSongsOut): Mark => want[p.id] ?? was(p);
  const likedWas = mark(likedCount);
  const likedNow = likedWant ?? likedWas;
  const next = (at: Mark, before: Mark): Mark => (at === 'all' ? 'none' : at === 'none' && before === 'some' ? 'some' : 'all');
  const add = rows?.filter((p) => now(p) === 'all' && was(p) !== 'all') ?? [];
  const remove = rows?.filter((p) => now(p) === 'none' && was(p) !== 'none') ?? [];
  const like = likedNow !== likedWas && likedNow !== 'some' ? likedNow === 'all' : null;
  const sub = (at: Mark, before: Mark, count: number, idle: string) => (at !== before ? (at === 'all' ? `добавить ${n - count}` : `убрать ${count}`) : at === 'all' ? `здесь все ${n}` : at === 'some' ? `здесь ${count} из ${n}` : idle);

  const create = async (name: string) => {
    setBusy(true);
    const p = await attempt(() => api.createPlaylist(name));
    setBusy(false);
    if (!p) return false;
    setRows((list) => [{ ...p, count: 0 }, ...(list ?? [])]);
    setWant((w) => ({ ...w, [p.id]: 'all' }));
    return true;
  };
  const save = async () => {
    if (!add.length && !remove.length && like === null) return onClose();
    setBusy(true);
    try {
      if (like !== null) await setLikes(targets, like);
      if (songIds.length && (add.length || remove.length)) {
        const r = await attempt(() => api.playlistMembershipMany({ tracks: songIds, add: add.map((p) => p.id), remove: remove.map((p) => p.id) }));
        if (!r) return;
        noteInPlaylists(songIds, add.length > 0);
      }
      const fresh = tracks.filter((t) => !t.key && !t.ref.startsWith('nd:')).map((t) => t.ref);
      if (fresh.length && add.length) {
        const r = await attempt(() => api.download({ track_refs: fresh, playlist_ids: add.map((p) => p.id) }));
        if (!r) return;
      }
    } finally {
      setBusy(false);
    }
    const into = [...(like === true ? [LIKED] : []), ...add];
    const out = [...(like === false ? [LIKED] : []), ...remove];
    toast([into.length ? `Добавлено в: ${names(into)}` : '', out.length ? `Убрано из: ${names(out)}` : ''].filter(Boolean).join('. '), 'success');
    onClose();
  };
  const plan = [...(like === true ? [`+${n - likedCount} Любимое`] : like === false ? [`−${likedCount} Любимое`] : []), ...add.map((p) => `+${n - p.count} ${p.name}`), ...remove.map((p) => `−${p.count} ${p.name}`)].join(', ');

  return (
    <Sheet open onClose={onClose} title={`${tracksWord(n)} — в плейлисты`} footer={<Footer text={plan || 'Изменений нет'} busy={busy} onClose={onClose} onDone={() => void save()} />}>
      {!rows ? (
        <Loading />
      ) : (
        <div className="-mx-4 -my-3">
          <div className="pt-1.5">
            <LikedRow mark={likedNow} sub={sub(likedNow, likedWas, likedCount, 'этих треков здесь нет')} changed={likedNow !== likedWas} onClick={() => setLikedWant(next(likedNow, likedWas))} />
          </div>
          <NewPlaylist busy={busy} onCreate={create} />
          <ul>
            {rows.map((p) => {
              const at = now(p);
              return (
                <li key={p.id}>
                  <button type="button" role="checkbox" aria-checked={at === 'all' ? true : at === 'some' ? 'mixed' : false} onClick={() => setWant((w) => ({ ...w, [p.id]: next(at, was(p)) }))} className="flex min-h-14 w-full items-center gap-3 px-4 py-1.5 text-left transition hover:bg-fg/5">
                    <Cover src={p.cover_url ? `${p.cover_url}&size=96` : null} size={40} icon="playlist" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[15px]">{p.name}</span>
                      <span className={cx('block text-xs', at !== was(p) ? 'text-accent' : 'text-muted')}>{sub(at, was(p), p.count, tracksWord(p.song_count + (p.pending_count ?? 0)))}</span>
                    </span>
                    <Tick mark={at} />
                  </button>
                </li>
              );
            })}
          </ul>
          {!rows.length && <p className="px-4 pb-6 text-center text-sm text-muted">Плейлистов пока нет — создайте первый.</p>}
        </div>
      )}
    </Sheet>
  );
}

/** The same list, to choose where tracks will go (files once they are imported, a catalog album as it is
 * downloaded): nothing is changed here, «Готово» gives the choice back. `liked`: «Любимое» is offered as the first
 * row, ticked or not — its tick comes back as the second argument. `single`: one playlist only — a tick takes the
 * place of the one before. `note`: a line above the list. */
export function PlaylistsPick({ title, picked, onClose, onDone, single, note, liked }: { title: string; picked: { id: string; name: string }[]; onClose: () => void; onDone: (list: { id: string; name: string }[], liked: boolean) => void; single?: boolean; note?: string; liked?: boolean }) {
  const [rows, setRows] = useState<{ id: string; name: string; song_count: number; pending_count?: number; cover_url?: string | null }[] | null>(null);
  const [want, setWant] = useState<Record<string, boolean>>(() => Object.fromEntries(picked.map((p) => [p.id, true])));
  const [like, setLike] = useState(!!liked);
  const [busy, setBusy] = useState(false);
  const tick = (id: string, on: boolean) => setWant((w) => (single ? { [id]: on } : { ...w, [id]: on }));
  useEffect(() => {
    let alive = true;
    api.playlists()
      .then((r) => alive && setRows(r))
      .catch(() => {
        if (alive) {
          toast('Не удалось получить плейлисты', 'error');
          onClose();
        }
      });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const create = async (name: string) => {
    setBusy(true);
    const p = await attempt(() => api.createPlaylist(name));
    setBusy(false);
    if (!p) return false;
    setRows((list) => [p, ...(list ?? [])]);
    tick(p.id, true);
    return true;
  };
  const chosen = (rows ?? []).filter((p) => want[p.id]);
  const said = [...(like ? ['Любимое'] : []), ...chosen.map((p) => p.name)];
  return (
    <Sheet open onClose={onClose} title={title} footer={<Footer text={said.length ? said.join(', ') : single ? 'Плейлист не выбран' : 'Плейлисты не выбраны'} disabled={!rows} onClose={onClose} onDone={() => onDone(chosen.map((p) => ({ id: p.id, name: p.name })), like)} />}>
      {!rows ? (
        <Loading />
      ) : (
        <div className="-mx-4 -my-3">
          {note && <p className="px-4 pt-3 text-xs text-muted">{note}</p>}
          {liked !== undefined && (
            <div className="pt-1.5">
              <LikedRow mark={like ? 'all' : 'none'} sub="Особый плейлист: то, что вам нравится" onClick={() => setLike((v) => !v)} />
            </div>
          )}
          <NewPlaylist busy={busy} onCreate={create} />
          <ul>
            {rows.map((p) => {
              const on = !!want[p.id];
              return (
                <li key={p.id}>
                  <button type="button" role={single ? 'radio' : 'checkbox'} aria-checked={on} onClick={() => tick(p.id, !on)} className="flex min-h-14 w-full items-center gap-3 px-4 py-1.5 text-left transition hover:bg-fg/5">
                    <Cover src={p.cover_url ? `${p.cover_url}&size=96` : null} size={40} icon="playlist" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[15px]">{p.name}</span>
                      <span className="block text-xs text-muted">{tracksWord(p.song_count + (p.pending_count ?? 0))}</span>
                    </span>
                    <Tick mark={on ? 'all' : 'none'} />
                  </button>
                </li>
              );
            })}
          </ul>
          {!rows.length && <p className="px-4 pb-6 text-center text-sm text-muted">Плейлистов пока нет — создайте первый.</p>}
        </div>
      )}
    </Sheet>
  );
}
