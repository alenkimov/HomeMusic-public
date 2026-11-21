import { useState } from 'react';
import { api, invalidateCache } from '../api/client';
import type { AlbumEditOut, AlbumEditTarget } from '../api/types';
import { useAsync } from '../lib/hooks';
import { EDITED_EVENT } from '../lib/trackEdit';
import { attempt, toast } from '../lib/ui';
import { Icon, type IconName } from './Icon';
import { OtherTitles } from './OtherTitles';
import { Button, ErrorState, Field, IconButton, Sheet, Spinner } from './ui';

export function LibraryAlbumMenu({ id, title, onSaved, onCover, onMerge }: {
  id: number;
  title: string;
  onSaved: (album: AlbumEditOut) => void;
  onCover?: () => void;
  onMerge?: () => void;
}) {
  const [panel, setPanel] = useState<'menu' | 'rename'>();
  const item = (icon: IconName, label: string, action: () => void) => (
    <button type="button" className="flex min-h-12 w-full items-center gap-4 rounded-lg px-3 text-left text-[15px] hover:bg-fg/5"
      onClick={() => { setPanel(undefined); action(); }}>
      <Icon name={icon} size={21} className="text-muted" />
      {label}
    </button>
  );
  return (
    <>
      <IconButton icon="more" label="Ещё" onClick={() => setPanel('menu')} />
      <Sheet open={panel === 'menu'} onClose={() => setPanel(undefined)} title={title}>
        {item('edit', 'Переименовать', () => setPanel('rename'))}
        {onCover && item('image', 'Сменить обложку', onCover)}
        {onMerge && item('merge', 'Объединить с другим альбомом', onMerge)}
      </Sheet>
      {panel === 'rename' && <AlbumEdit target={{ album_id: id }} onClose={() => setPanel(undefined)} onSaved={onSaved} />}
    </>
  );
}

export function AlbumEdit({ target, onClose, onSaved }: {
  target: AlbumEditTarget;
  onClose: () => void;
  onSaved: (album: AlbumEditOut) => void;
}) {
  const result = useAsync((signal) => api.albumEdit(target, signal), []);
  const [draftTitle, setTitle] = useState<string>();
  const [draftArtist, setArtist] = useState<string>();
  const [busy, setBusy] = useState<'save' | 'names'>();
  const info = result.data;
  const title = draftTitle ?? info?.title ?? '';
  const artist = draftArtist ?? info?.album_artist ?? '';

  const refresh = () => {
    invalidateCache('/api/catalog/');
    invalidateCache('/api/search');
  };
  const save = async (name = title) => {
    if (!info?.can_edit || !name.trim() || !artist.trim() || busy) return;
    setBusy('save');
    const saved = await attempt(() => api.renameAlbum(target, name.trim(), artist.trim()));
    setBusy(undefined);
    if (!saved) return;
    refresh();
    onSaved(saved);
    window.dispatchEvent(new Event(EDITED_EVENT));
    toast('Название изменено у всех', 'success');
    onClose();
  };
  const editNames = async (change: () => Promise<AlbumEditOut>) => {
    setBusy('names');
    const changed = await attempt(change);
    setBusy(undefined);
    if (!changed) return false;
    result.setData(changed);
    refresh();
    return true;
  };

  return (
    <Sheet open onClose={onClose} title="Переименовать альбом" footer={info?.can_edit ? (
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>Отмена</Button>
        <Button busy={busy === 'save'} disabled={!!busy || !title.trim() || !artist.trim()
          || (title.trim() === info.title && artist.trim() === info.album_artist)} onClick={() => void save()}>Сохранить</Button>
      </div>
    ) : undefined}>
      {result.error ? <ErrorState text={result.error} onRetry={() => result.reload()} /> : !info ? (
        <div className="flex justify-center py-10"><Spinner className="text-accent" /></div>
      ) : (
        <div className="space-y-3">
          <Field label="Название">
            <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={512} autoComplete="off" readOnly={!info.can_edit} />
          </Field>
          <Field label="Исполнитель альбома">
            <input className="input" value={artist} onChange={(e) => setArtist(e.target.value)} maxLength={512} autoComplete="off" readOnly={!info.can_edit} />
          </Field>
          <OtherTitles subject="альбом" names={info.names} readOnly={!info.can_edit} busy={!!busy} maxLength={512}
            onChoose={(name) => void save(name)}
            onAdd={(name) => editNames(() => api.addAlbumName(target, name))}
            onRemove={(id) => void editNames(() => api.dropAlbumName(target, id))} />
          <p className="text-[13px] text-dim">
            {info.can_edit ? 'Изменится у всех на этом сервере.' : 'Изменить названия этого альбома может администратор.'}
          </p>
        </div>
      )}
    </Sheet>
  );
}
