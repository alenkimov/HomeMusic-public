import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { closePlaylistMenu, usePlaylistMenu, type MenuPlaylist } from '../lib/playlistMenu';
import { navigate } from '../lib/router';
import { attempt, confirmDialog } from '../lib/ui';
import { ContextMenu } from './ContextMenu';
import { Icon } from './Icon';
import { Button, Cover, Field, Sheet } from './ui';

/** The picture of a playlist is the user's own, not the one made of its albums' covers. */
export const ownCover = (url: string | null | undefined): boolean => !!url && url.startsWith('/api/playlists/');

/** The menu of a playlist one keeps (`lib/playlistMenu.ts`): its name, its picture, and deleting it. What it changes
 * the Hub tells every page that lists playlists (the «library» event). */
export function PlaylistMenu() {
  const open = usePlaylistMenu();
  const [renaming, setRenaming] = useState<MenuPlaylist | null>(null);
  const file = useRef<HTMLInputElement>(null);
  const target = useRef<MenuPlaylist | null>(null);

  const p = open?.playlist;
  const drop = async (x: MenuPlaylist) => {
    if (!(await confirmDialog({ title: `Удалить плейлист «${x.name}»?`, body: 'Песни останутся в библиотеке.', okLabel: 'Удалить', danger: true }))) return;
    if (!(await attempt(() => api.deletePlaylist(x.id), 'Плейлист удалён'))) return;
    if (decodeURIComponent(window.location.hash).includes(`/playlist/${x.id}`)) navigate('/library', { replace: true });
  };
  return (
    <>
      <input
        ref={file}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          const x = target.current;
          e.target.value = '';
          if (f && x) void attempt(() => api.setPlaylistCover(x.id, f), 'Обложка обновлена');
        }}
      />
      {p && (
        <ContextMenu
          at={open.at}
          head={{ title: p.name, sub: 'Плейлист', cover: <Cover src={p.cover_url ? `${p.cover_url}&size=160` : null} size={44} icon="playlist" /> }}
          onClose={closePlaylistMenu}
          entries={[
            { icon: 'edit', label: 'Переименовать', shortcut: 'F2', onSelect: () => setRenaming(p) },
            { icon: 'image', label: 'Сменить обложку', onSelect: () => {
              target.current = p;
              file.current?.click();
            } },
            ownCover(p.cover_url) && { icon: 'refresh', label: 'Вернуть коллаж', onSelect: () => void attempt(() => api.dropPlaylistCover(p.id), 'Своя обложка убрана') },
            'sep',
            { icon: 'trash', label: 'Удалить плейлист', shortcut: 'Del', danger: true, onSelect: () => void drop(p) },
          ]}
        />
      )}
      {renaming && <Rename key={renaming.id} playlist={renaming} onClose={() => setRenaming(null)} />}
    </>
  );
}

function Rename({ playlist, onClose }: { playlist: MenuPlaylist; onClose: () => void }) {
  const [name, setName] = useState(playlist.name);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    const next = name.trim();
    if (!next || next === playlist.name) return onClose();
    setBusy(true);
    const r = await attempt(() => api.renamePlaylist(playlist.id, next), 'Плейлист переименован');
    setBusy(false);
    if (r) onClose();
  };
  return (
    <Sheet
      open
      onClose={onClose}
      title="Переименовать плейлист"
      footer={
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Отмена</Button>
          <Button variant="primary" busy={busy} disabled={!name.trim()} onClick={() => void save()}>
            Сохранить
          </Button>
        </div>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Field label="Название">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={200} autoFocus />
        </Field>
      </form>
    </Sheet>
  );
}

/** A list's picture, large, over the whole screen. Of a playlist one keeps (`playlistId`) it can be changed here:
 * «Выбрать другую обложку», and «Вернуть коллаж» when the picture is the user's own. */
export function CoverView({ src, title, playlistId, own, onClose, onChange }: { src: string | null | undefined; title: string; playlistId?: string; own?: boolean; onClose: () => void; onChange?: () => void }) {
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', key);
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      window.removeEventListener('keydown', key);
      document.body.style.overflow = prev;
    };
  }, [onClose]);
  const run = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    await attempt(fn, done);
    setBusy(false);
  };
  return (
    <div className="pt-safe pb-safe fixed inset-0 z-50 flex flex-col items-center justify-center gap-5 bg-bg/95 px-6" role="dialog" aria-modal="true" aria-label={`Обложка: ${title}`}>
      <button type="button" aria-label="Закрыть" className="absolute inset-0 cursor-default" onClick={onClose} />
      <button type="button" aria-label="Закрыть" onClick={onClose} className="absolute left-3 top-[calc(env(safe-area-inset-top)+8px)] flex h-11 w-11 items-center justify-center rounded-full text-fg hover:bg-fg/10">
        <Icon name="x" size={24} />
      </button>
      <Cover src={src} alt={title} icon={playlistId ? 'playlist' : 'disc'} className="relative aspect-square w-[min(86vw,58dvh)] shadow-2xl" />
      <div className="relative max-w-full truncate text-lg font-bold">{title}</div>
      {onChange && <Button icon="image" onClick={onChange} className="relative">Выбрать другую обложку</Button>}
      {playlistId && (
        <div className="relative flex flex-col items-center gap-3">
          <label className={`inline-flex min-h-12 cursor-pointer items-center gap-2 rounded-full bg-fg px-6 font-semibold text-bg transition hover:scale-105 ${busy ? 'pointer-events-none opacity-60' : ''}`}>
            <Icon name="image" size={18} />
            Выбрать другую обложку
            <input
              type="file"
              accept="image/*"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = '';
                if (f) void run(() => api.setPlaylistCover(playlistId, f), 'Обложка обновлена');
              }}
            />
          </label>
          {own && (
            <button type="button" disabled={busy} className="text-sm text-muted hover:text-fg" onClick={() => void run(() => api.dropPlaylistCover(playlistId), 'Своя обложка убрана')}>
              Вернуть коллаж
            </button>
          )}
        </div>
      )}
    </div>
  );
}
