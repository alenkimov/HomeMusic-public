import { useEffect, useMemo, useRef, useState } from 'react';
import { api, errorMessage, invalidateCache, libraryCoverUrl } from '../api/client';
import type { CoverInfo, CoverOption } from '../api/types';
import { canEditAlbumArtwork, closeCoverPicker, openAlbumArtwork, useCoverPicker } from '../lib/coverPicker';
import { pictureWords } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { EDITED_EVENT } from '../lib/trackEdit';
import { toast } from '../lib/ui';
import { titleWithVersion, useVersionLabel } from '../lib/versionLabels';
import { Icon } from './Icon';
import { CoverView } from './PlaylistMenu';
import { ServiceBadge } from './ServiceBadge';
import { TrackTitle } from './TrackTitle';
import { Button, Cover, ErrorState, IconButton, Segmented, Sheet, Spinner, cx } from './ui';

type Target = NonNullable<ReturnType<typeof useCoverPicker>>['target'];
type Picture = { file: File; info: CoverInfo };
type SearchMode = 'name' | 'tracks';
type Search = { q: string; mode: SearchMode };
type Results = { items: CoverOption[]; completed: number; total: number; incomplete: boolean };
const PAGE = 18;
const trackQuery = ({ artist, title }: { artist: string; title: string }) =>
  [artist, title].map((part) => part.trim()).filter(Boolean).join(' - ').slice(0, 512);
const specs = (info: CoverInfo) => pictureWords(info).filter(Boolean).join(' · ');

export function CoverPicker() {
  const window = useCoverPicker();
  if (!window) return null;
  const { target, view } = window;
  if (view === 'large' && target.kind === 'album') {
    return <CoverView src={target.src} title={target.title} onClose={closeCoverPicker}
      onChange={canEditAlbumArtwork(target) ? () => openAlbumArtwork(target, 'edit') : undefined} />;
  }
  if (target.kind === 'album' && !canEditAlbumArtwork(target)) return null;
  return <Editor key={target.kind === 'album' ? `album:${target.albumId ?? target.catalogRef}` : `track:${target.library_track_id ?? target.ref ?? target.nd_song_id}`} target={target} />;
}

function usePictureUrl(file?: File) {
  const [src, setSrc] = useState<string>();
  useEffect(() => {
    if (!file) {
      setSrc(undefined);
      return;
    }
    const url = URL.createObjectURL(file);
    setSrc(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  return src;
}

function useSearchCovers(queries: string[], album: boolean) {
  const [results, setResults] = useState<Results>({ items: [], completed: 0, total: queries.length, incomplete: false });
  useEffect(() => {
    const ctl = new AbortController();
    setResults({ items: [], completed: 0, total: queries.length, incomplete: false });
    const collect = async () => {
      const found = new Map<string, CoverOption>();
      let incomplete = false;
      for (let i = 0; i < queries.length && !ctl.signal.aborted; i += 2) {
        const responses = await Promise.allSettled(queries.slice(i, i + 2).map((q) => api.searchCovers(q, album, ctl.signal)));
        if (ctl.signal.aborted) return;
        for (const result of responses) {
          if (result.status === 'fulfilled') {
            for (const option of result.value.covers) if (!found.has(option.key)) found.set(option.key, option);
            incomplete ||= result.value.incomplete;
          } else incomplete = true;
        }
        setResults({ items: [...found.values()], completed: Math.min(i + 2, queries.length), total: queries.length, incomplete });
      }
    };
    void collect();
    return () => ctl.abort();
  }, [queries, album]);
  return results;
}

function Option({ option, selected, onSelect }: { option: CoverOption; selected: boolean; onSelect: (p: Picture) => void }) {
  const card = useRef<HTMLButtonElement>(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (visible || !card.current) return;
    if (typeof IntersectionObserver === 'undefined') { setVisible(true); return; }
    const io = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) { setVisible(true); io.disconnect(); }
    }, { rootMargin: '120px' });
    io.observe(card.current);
    return () => io.disconnect();
  }, [visible]);
  const image = useAsync((signal) => api.coverImage(option.key, signal), [option.key], visible);
  const src = usePictureUrl(image.data?.file);
  return (
    <button ref={card} type="button" aria-pressed={selected} disabled={!image.data}
      onClick={() => image.data && onSelect(image.data)}
      className={cx('relative flex min-w-0 flex-col gap-1 rounded-xl border p-1.5 text-left',
        selected ? 'border-accent bg-accent/10' : 'border-line enabled:hover:bg-hover')}>
      <Cover src={src} alt="" icon="image" className="aspect-square w-full" />
      {image.loading && <span className="absolute left-1/2 top-6 -translate-x-1/2"><Spinner size={16} /></span>}
      {selected && <span className="absolute right-2 top-2 flex h-6 w-6 items-center justify-center rounded-full bg-accent text-on-accent"><Icon name="check" size={14} /></span>}
      <span className="w-full truncate px-0.5 text-sm" title={option.title}><TrackTitle title={option.title} track={option} /></span>
      {option.artist && <span className="w-full truncate px-0.5 text-xs text-muted" title={option.artist}>{option.artist}</span>}
      <ServiceBadge service={option.source} className="px-0.5 text-xs text-muted" />
      <span className="px-0.5 text-xs text-dim">{image.data ? specs(image.data.info) : image.error ? 'Обложка недоступна' : '…'}</span>
    </button>
  );
}

function Editor({ target }: { target: Target }) {
  const album = target.kind === 'album';
  const info = useAsync((signal) => target.kind === 'track'
    ? api.trackEdit({ track_id: target.library_track_id ?? undefined, ref: target.ref ?? undefined,
        nd_song_id: target.nd_song_id ?? undefined }, signal)
    : Promise.resolve(null), [target], !album);
  const [input, setInput] = useState(() => album ? target.title : trackQuery(target));
  const [search, setSearch] = useState<Search>({ q: input, mode: 'name' });
  const [limit, setLimit] = useState(PAGE);
  const [chosen, choose] = useState<(Picture & { key: string }) | null>(null);
  const [preview, setPreview] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reading, setReading] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const chosenSrc = usePictureUrl(chosen?.file);
  const queries = useMemo(() => {
    if (target.kind === 'album' && search.mode === 'tracks') return [...new Set(target.tracks.map(trackQuery).filter(Boolean))];
    return [search.q.trim().slice(0, 512)].filter(Boolean);
  }, [target, search]);
  const results = useSearchCovers(queries, album && search.mode === 'name');
  const close = () => {
    if (busy) return;
    if (target.kind === 'album') openAlbumArtwork(target);
    else closeCoverPicker();
  };
  const submit = (q: string, mode: SearchMode) => {
    setLimit(PAGE);
    setSearch({ q, mode });
  };
  const apply = async (picture?: Picture) => {
    if (busy || (target.kind === 'track' && !info.data)) return;
    setBusy(true);
    try {
      let savedAlbum: { id: number; cover_url?: string | null } | null = null;
      if (target.kind === 'album') {
        if (!picture) return;
        if (target.albumId != null) savedAlbum = await api.setAlbumCover(target.albumId, picture.file);
        else if (target.catalogRef) savedAlbum = await api.setCatalogAlbumCover(target.catalogRef, picture.file);
      } else if (info.data) {
        if (picture) await api.setTrackCover(info.data.track_id, picture.file);
        else await api.dropTrackCover(info.data.track_id);
      }
      invalidateCache('/api/catalog/');
      invalidateCache('/api/search');
      window.dispatchEvent(new Event(EDITED_EVENT));
      toast('Обложка обновлена', 'success');
      if (target.kind === 'album' && savedAlbum) openAlbumArtwork({ ...target, albumId: savedAlbum.id,
        src: savedAlbum.cover_url || libraryCoverUrl(savedAlbum.id, 1000, Date.now()) });
      else closeCoverPicker();
    } catch (e) {
      toast(errorMessage(e), 'error');
    } finally {
      setBusy(false);
    }
  };
  const read = async (file: File) => {
    if (file.size > 20 * 1024 * 1024) { toast('Картинка слишком большая', 'error'); return; }
    setReading(true);
    try {
      const image = await createImageBitmap(file);
      const format = file.type.split('/')[1]?.toUpperCase();
      choose({ key: 'upload', file, info: { w: image.width, h: image.height, size: file.size, format } });
      image.close();
    } catch {
      toast('Не удалось прочитать картинку', 'error');
    } finally {
      setReading(false);
    }
  };

  const title = info.data?.title ?? target.title;
  const identity = target.kind === 'track' ? (info.data ? { ...target, track_id: info.data.track_id } : target) : null;
  const label = useVersionLabel(identity);
  const src = chosenSrc || info.data?.cover_url || target.src;
  const loading = results.completed < results.total;
  if (preview) return <CoverView src={src} title={titleWithVersion(title, identity, label)} onClose={() => setPreview(false)} />;
  return (
    <Sheet open wide onClose={close} title={album ? 'Обложка альбома' : 'Обложка трека'} footer={
      <div className="flex flex-wrap items-center justify-between gap-2">
        <input ref={fileInput} type="file" accept="image/*" hidden onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) void read(file);
        }} />
        <Button small variant="secondary" icon="upload" busy={reading} disabled={busy} onClick={() => fileInput.current?.click()}>Загрузить свою</Button>
        <div className="ml-auto flex gap-2">
          <Button small variant="ghost" disabled={busy} onClick={close}>Отмена</Button>
          <Button small variant="primary" busy={busy} disabled={!chosen || reading || (!album && !info.data)} onClick={() => void apply(chosen ?? undefined)}>Сохранить</Button>
        </div>
      </div>
    }>
      <p className="mb-3 truncate text-sm text-muted"><TrackTitle title={title} label={label} /> · {info.data?.artist ?? target.artist}</p>
      {info.error && <ErrorState text={info.error} onRetry={info.reload} />}
      <div className="grid gap-4 sm:grid-cols-[112px_minmax(0,1fr)]">
        <aside className="flex items-center gap-3 sm:block">
          <button type="button" aria-label="Открыть обложку крупно" onClick={() => setPreview(true)} className="w-16 shrink-0 cursor-pointer sm:w-full">
            <Cover src={src} icon="image" className="aspect-square w-full" />
          </button>
          <div className="text-xs sm:mt-2" aria-live="polite">
            <p className="font-semibold">{chosen ? 'Новая обложка' : 'Текущая обложка'}</p>
            {chosen && <p className="mt-1 text-dim">{specs(chosen.info)}</p>}
            {info.data?.own_cover && <button type="button" disabled={busy} className="mt-2 cursor-pointer text-muted hover:text-fg hover:underline" onClick={() => void apply()}>
              {info.data.album_id ? 'Вернуть обложку альбома' : 'Убрать свою обложку'}
            </button>}
          </div>
        </aside>
        <div className="min-w-0 space-y-3">
          {album && <Segmented<SearchMode> value={search.mode} label="Поиск обложек альбома"
            options={[{ value: 'name', label: 'По названию альбома' }, { value: 'tracks', label: 'По трекам альбома' }]}
            onChange={(mode) => submit(input, mode)} />}
          {search.mode === 'name' && <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); submit(input, 'name'); }}>
            <input type="search" className="input min-w-0" aria-label="Запрос для поиска обложек" value={input} maxLength={512} onChange={(e) => setInput(e.target.value)} />
            <IconButton type="submit" icon="search" label="Найти обложки" />
          </form>}
          <h3 className="text-sm font-semibold">{search.mode === 'tracks' ? 'Обложки по трекам альбома' : 'Найденные обложки'}</h3>
          {loading && <p className="flex items-center gap-2 text-xs text-muted" role="status">
            <Spinner size={14} />{search.mode === 'tracks' ? `Треки проверены: ${results.completed} из ${results.total}` : 'Ищем обложки…'}
          </p>}
          {!loading && !results.items.length && <p className="text-sm text-muted">{results.incomplete ? 'Источники не ответили. Повторите поиск.' : 'Обложки не найдены'}</p>}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {results.items.slice(0, limit).map((option) => <Option key={option.key} option={option} selected={chosen?.key === option.key} onSelect={(picture) => choose({ ...picture, key: option.key })} />)}
          </div>
          {results.items.length > limit && <Button small variant="ghost" onClick={() => setLimit(limit + PAGE)}>Показать ещё</Button>}
          {!loading && !!results.items.length && results.incomplete && <p className="text-xs text-dim">Некоторые источники не ответили</p>}
          {!loading && search.mode === 'tracks' && results.incomplete && <Button small variant="ghost" icon="refresh" onClick={() => submit(input, 'tracks')}>Повторить поиск</Button>}
        </div>
      </div>
    </Sheet>
  );
}
