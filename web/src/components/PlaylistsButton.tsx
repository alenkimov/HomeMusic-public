import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { PlaylistOfSongOut } from '../api/types';
import { useHubEvent } from '../lib/events';
import { tracksWord } from '../lib/format';
import { attempt, toast } from '../lib/ui';
import { Icon } from './Icon';
import { Button, Cover, Loading, Sheet, cx } from './ui';

/** «В плейлистах» of a library song, beside its ♥: a green ✓ when it is in some playlist, a + when in none. It opens
 * the list of the user's playlists — the ones the song is in on top, then the rest, the ones changed last first — where
 * it is ticked into some and out of others; nothing changes until «Готово». */
export function PlaylistsButton({ songId, title, size = 18, className }: { songId: string; title: string; size?: number; className?: string }) {
  const [lists, setLists] = useState<PlaylistOfSongOut[] | null>(null);
  const [open, setOpen] = useState(false);
  const [round, setRound] = useState(0);
  useEffect(() => {
    let alive = true;
    setLists(null);
    api.playlistsOfSong(songId).then((r) => alive && setLists(r)).catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [songId, round]);
  useHubEvent('library', () => setRound((n) => n + 1));
  const inSome = lists?.filter((p) => p.has).length ?? 0;
  const label = inSome ? `В плейлистах: ${inSome} — изменить` : 'Добавить в плейлист';
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} aria-label={`${label}: «${title}»`} title={label} className={cx('flex h-9 w-9 shrink-0 items-center justify-center rounded-full transition hover:scale-115', inSome ? 'text-accent' : 'text-muted hover:text-fg', className)}>
        <span className={cx('flex items-center justify-center rounded-full', inSome ? 'bg-accent text-on-accent' : 'border-2 border-current')} style={{ width: size + 2, height: size + 2 }}>
          <Icon name={inSome ? 'check' : 'plus'} size={size - 5} stroke={3} />
        </span>
      </button>
      {open && (
        <Picker
          songId={songId}
          title={title}
          lists={lists}
          onClose={() => setOpen(false)}
          onSaved={(r) => {
            setLists(r);
            setOpen(false);
          }}
        />
      )}
    </>
  );
}

function Picker({ songId, title, lists, onClose, onSaved }: { songId: string; title: string; lists: PlaylistOfSongOut[] | null; onClose: () => void; onSaved: (r: PlaylistOfSongOut[]) => void }) {
  // the order of the opening: a tick does not make a row jump
  const [rows, setRows] = useState<PlaylistOfSongOut[] | null>(lists);
  const [want, setWant] = useState<Record<string, boolean>>({});
  const [q, setQ] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!rows && lists) setRows(lists);
  }, [lists, rows]);

  const has = (p: PlaylistOfSongOut) => want[p.id] ?? p.has;
  const add = rows?.filter((p) => !p.has && has(p)) ?? [];
  const remove = rows?.filter((p) => p.has && !has(p)) ?? [];
  const needle = q.trim().toLocaleLowerCase('ru');
  const shown = useMemo(() => (rows ?? []).filter((p) => !needle || p.name.toLocaleLowerCase('ru').includes(needle)), [rows, needle]);
  const inside = shown.filter((p) => p.has);
  const rest = shown.filter((p) => !p.has);

  const create = async () => {
    const n = name.trim();
    if (!n) return;
    setBusy(true);
    const p = await attempt(() => api.createPlaylist(n));
    setBusy(false);
    if (!p) return;
    setRows((list) => [{ ...p, has: false }, ...(list ?? [])]);
    setWant((w) => ({ ...w, [p.id]: true }));
    setName('');
  };
  const save = async () => {
    if (!add.length && !remove.length) return onClose();
    setBusy(true);
    const r = await attempt(() => api.playlistMembership({ nd_song_id: songId, add: add.map((p) => p.id), remove: remove.map((p) => p.id) }));
    setBusy(false);
    if (!r) return;
    toast([add.length ? `Добавлено в: ${add.map((p) => `«${p.name}»`).join(', ')}` : '', remove.length ? `Убрано из: ${remove.map((p) => `«${p.name}»`).join(', ')}` : ''].filter(Boolean).join('. '), 'success');
    onSaved(r);
  };
  const row = (p: PlaylistOfSongOut) => {
    const on = has(p);
    return (
      <li key={p.id}>
        <button type="button" role="checkbox" aria-checked={on} onClick={() => setWant((w) => ({ ...w, [p.id]: !on }))} className="flex min-h-14 w-full items-center gap-3 px-4 py-1.5 text-left transition hover:bg-fg/5">
          <Cover src={p.cover_art && p.song_count ? `/api/nd/cover/${encodeURIComponent(p.cover_art)}?size=96&v=${p.song_count}` : null} size={40} icon="playlist" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[15px]">{p.name}</span>
            <span className="block text-xs text-muted">{tracksWord(p.song_count)}</span>
          </span>
          <span className={cx('flex h-6 w-6 shrink-0 items-center justify-center rounded-full', on ? 'bg-accent text-on-accent' : 'border-2 border-line')}>{on && <Icon name="check" size={14} stroke={3} />}</span>
        </button>
      </li>
    );
  };
  const label = 'px-4 pb-1 pt-3 text-xs text-muted';

  return (
    <Sheet
      open
      onClose={onClose}
      title={title}
      footer={
        <div className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-xs text-muted">
            {add.length || remove.length ? [add.length ? `добавить в ${add.length}` : '', remove.length ? `убрать из ${remove.length}` : ''].filter(Boolean).join(', ') : 'Изменений нет'}
          </span>
          <Button onClick={onClose}>Отмена</Button>
          <Button variant="primary" busy={busy} onClick={() => void save()}>
            Готово
          </Button>
        </div>
      }
    >
      {!rows ? (
        <Loading />
      ) : (
        <div className="-mx-4 -my-3">
          {rows.length > 6 && (
            <div className="px-4 pt-3">
              <input className="input" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти плейлист" aria-label="Найти плейлист" />
            </div>
          )}
          <form
            className="flex items-center gap-2 px-4 pt-3"
            onSubmit={(e) => {
              e.preventDefault();
              void create();
            }}
          >
            <input className="input flex-1" value={name} onChange={(e) => setName(e.target.value)} placeholder="Новый плейлист" aria-label="Название нового плейлиста" />
            <Button type="submit" icon="plus" disabled={!name.trim() || busy}>
              Создать
            </Button>
          </form>
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
