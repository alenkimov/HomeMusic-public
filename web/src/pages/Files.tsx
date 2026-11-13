import { useEffect, useState } from 'react';
import { api, libraryCoverUrl, withQuery } from '../api/client';
import type { LibAlbumOut, ReleaseType } from '../api/types';
import { Icon } from '../components/Icon';
import { Button, Cover, Empty, ErrorState, Field, IconButton, Sheet, SkeletonRows } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { recordTypeLabel, toIntOrNull, tracksWord } from '../lib/format';
import { useAsync, useDebounced } from '../lib/hooks';
import { Link, goBack, navigate } from '../lib/router';
import { attempt } from '../lib/ui';

const PAGE = 50;

/** «Мои файлы» (from «Меню»): the albums the Hub keeps the files of — found by a search, opened to be edited, and
 * «+» makes an empty one to move tracks into. */
export function FilesPage({ initialQuery }: { initialQuery: string }) {
  const [q, setQ] = useState(initialQuery);
  const dq = useDebounced(q.trim(), 350);
  const [extra, setExtra] = useState<LibAlbumOut[]>([]);
  const [more, setMore] = useState<{ busy: boolean; done: boolean }>({ busy: false, done: false });
  const [createOpen, setCreateOpen] = useState(false);
  const res = useAsync((s) => api.libAlbums(dq, 0, PAGE, s), [dq]);
  // the banner shows until the first import
  const imports = useAsync((s) => api.imports(s), []);
  const offerImport = imports.data?.length === 0 && !dq;

  useEffect(() => {
    setExtra([]);
    setMore({ busy: false, done: false });
    const target = withQuery('/files', { q: dq });
    if (window.location.hash !== '#' + target) navigate(target, { replace: true });
  }, [dq]);

  useHubEvent('library', () => res.reload(true));

  const albums = [...(res.data ?? []), ...extra];
  const canMore = !more.done && (res.data?.length ?? 0) >= PAGE;

  const loadMore = async () => {
    setMore({ busy: true, done: false });
    const next = await attempt(() => api.libAlbums(dq, albums.length, PAGE));
    setExtra((x) => [...x, ...(next ?? [])]);
    setMore({ busy: false, done: !next || next.length < PAGE });
  };

  return (
    <div>
      <header className="pt-safe sticky top-0 z-20 bg-bg/95 px-4 pb-3 backdrop-blur">
        <div className="flex items-center gap-1 pb-3 pt-4">
          <IconButton icon="back" label="Назад" onClick={() => goBack('/more')} className="-ml-3" />
          <h1 className="min-w-0 flex-1 truncate text-2xl font-bold">Мои файлы</h1>
          <div className="flex gap-1">
            <IconButton icon="upload" label="Загрузить файлы" onClick={() => navigate('/upload')} />
            <IconButton icon="plus" label="Создать свой альбом" onClick={() => setCreateOpen(true)} />
          </div>
        </div>
        <div className="relative">
          <Icon name="search" size={18} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-dim" />
          <input className="input pl-10" type="search" placeholder="Поиск по альбомам" aria-label="Поиск по альбомам" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </header>

      {offerImport && (
        <Link to="/import" className="card mx-4 mb-4 flex items-center gap-3 p-3 hover:bg-hover">
          <Icon name="transfer" size={22} className="shrink-0 text-accent" />
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold">Перенести музыку из Яндекс Музыки</span>
            <span className="block text-xs text-muted">♥, плейлисты, любимые альбомы и исполнители</span>
          </span>
          <Icon name="chevron" size={18} className="shrink-0 text-dim" />
        </Link>
      )}
      {res.loading ? (
        <SkeletonRows />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : albums.length === 0 ? (
        dq ? (
          <Empty icon="search" title="Ничего не найдено" />
        ) : (
          <Empty icon="library" title="Файлов пока нет" text="Скачайте музыку через поиск или загрузите свои файлы.">
            <div className="mt-2 flex gap-2">
              <Button small variant="primary" icon="search" onClick={() => navigate('/search')}>
                Поиск
              </Button>
              <Button small icon="upload" onClick={() => navigate('/upload')}>
                Загрузить
              </Button>
            </div>
          </Empty>
        )
      ) : (
        <>
          <div className="grid grid-cols-2 gap-x-4 gap-y-5 px-4 pt-1 min-[520px]:grid-cols-3">
            {albums.map((a) => (
              <Link key={a.id} to={`/library/album/${a.id}`} className="min-w-0">
                <Cover src={a.has_cover ? libraryCoverUrl(a.id, 300) : null} alt={a.title} className="aspect-square w-full" icon="disc" />
                <div className="mt-2 truncate text-sm font-medium">{a.title}</div>
                <div className="truncate text-xs text-muted">{[a.album_artist, a.year].filter(Boolean).join(' · ')}</div>
                <div className="truncate text-xs text-dim">{[recordTypeLabel(a.release_type), tracksWord(a.track_count)].filter(Boolean).join(' · ')}</div>
              </Link>
            ))}
          </div>
          {canMore && (
            <div className="flex justify-center py-5">
              <Button busy={more.busy} onClick={loadMore}>
                Показать ещё
              </Button>
            </div>
          )}
        </>
      )}

      <CreateAlbumSheet open={createOpen} onClose={() => setCreateOpen(false)} />
    </div>
  );
}

function CreateAlbumSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [title, setTitle] = useState('');
  const [artist, setArtist] = useState('');
  const [year, setYear] = useState('');
  const [genre, setGenre] = useState('');
  const [type, setType] = useState<ReleaseType>('compilation');
  const [busy, setBusy] = useState(false);

  const create = async () => {
    setBusy(true);
    const a = await attempt(
      () => api.createAlbum({ title: title.trim(), album_artist: artist.trim() || 'Various Artists', year: toIntOrNull(year), genre: genre.trim() || null, release_type: type }),
      'Альбом создан',
    );
    setBusy(false);
    if (a) {
      onClose();
      navigate(`/library/album/${a.id}`);
    }
  };

  return (
    <Sheet
      open={open}
      onClose={onClose}
      title="Создать свой альбом"
      footer={
        <Button variant="primary" block busy={busy} disabled={!title.trim()} onClick={create}>
          Создать
        </Button>
      }
    >
      <p className="mb-4 text-sm text-muted">Пустой альбом или подборка: потом перенесите в него треки из других альбомов.</p>
      <div className="space-y-4">
        <Field label="Название">
          <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
        </Field>
        <Field label="Исполнитель альбома">
          <input className="input" value={artist} onChange={(e) => setArtist(e.target.value)} placeholder="Various Artists" />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Год">
            <input className="input" inputMode="numeric" value={year} onChange={(e) => setYear(e.target.value.replace(/\D/g, '').slice(0, 4))} />
          </Field>
          <Field label="Жанр">
            <input className="input" value={genre} onChange={(e) => setGenre(e.target.value)} />
          </Field>
        </div>
        <Field label="Тип">
          <ReleaseTypeSelect value={type} onChange={setType} />
        </Field>
      </div>
    </Sheet>
  );
}

export function ReleaseTypeSelect({ value, onChange }: { value: ReleaseType; onChange: (v: ReleaseType) => void }) {
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value as ReleaseType)}>
      <option value="album">Альбом</option>
      <option value="single">Сингл</option>
      <option value="ep">EP</option>
      <option value="compilation">Сборник</option>
    </select>
  );
}
