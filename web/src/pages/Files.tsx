import { TrackTitle } from '../components/TrackTitle';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useSession } from '../lib/session';
import { api } from '../api/client';
import type { FileRowOut, ReleaseType, TrashedOut } from '../api/types';
import { TrackRow, TrackTableHeader } from '../components/catalog';
import { TrackTable } from '../components/TrackTable';
import { Icon, type IconName } from '../components/Icon';
import { ServiceIcon } from '../components/ServiceBadge';
import { ListBar } from '../components/ListBar';
import { useListSearch } from '../components/ListSearch';
import { Button, Empty, ErrorState, IconButton, Segmented, SkeletonRows, cx } from '../components/ui';
import { useDesktop } from '../lib/desktop';
import { useHubEvent } from '../lib/events';
import { fmtAdded, parseTime, plural, tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link, navigate } from '../lib/router';
import { EDITED_EVENT } from '../lib/trackEdit';
import { attempt } from '../lib/ui';

type Sort = 'new' | 'old' | 'title' | 'titleZ' | 'artist' | 'artistZ' | 'album' | 'albumZ' | 'quality' | 'qualityBest';
const ALL_SORTS: Sort[] = ['new', 'old', 'title', 'titleZ', 'artist', 'artistZ', 'album', 'albumZ', 'quality', 'qualityBest'];
/** What a click on a column makes of the order: the column's orders one after another, then the list's own (the
 * newest first). */
const after = (sort: Sort, steps: Sort[]): Sort => steps[steps.indexOf(sort) + 1] ?? (steps.includes(sort) ? 'new' : steps[0]);
const SORTS: [Sort, string][] = [
  ['new', 'Сначала новые'],
  ['old', 'Сначала старые'],
  ['title', 'По названию'],
  ['artist', 'По исполнителю'],
  ['album', 'По альбому'],
  ['quality', 'По качеству: худшие сверху'],
];
type Only = 'all' | 'mine' | 'loose' | 'noAlbum' | 'low' | 'doubles' | 'trash';

const LOSSLESS = new Set(['flac', 'alac', 'wav', 'aiff', 'ape', 'wv']);
/** How good a file is, as one number to sort by: what it sounds like when that is less than it says; a file without
 * losses over any with them. */
const quality = (t: FileRowOut): number => (LOSSLESS.has((t.file?.codec ?? '').toLowerCase()) ? 10_000 : (t.file?.sounds_like_kbps ?? t.file?.bitrate_kbps ?? 0));
/** A file that is heard to be poor: an MP3 under 192, the newer codecs (Opus, AAC) under 96. One whose bitrate is
 * not known is not called poor. */
const low = (t: FileRowOut): boolean => {
  const q = quality(t);
  return q > 0 && q < ((t.file?.codec ?? '').toLowerCase() === 'mp3' ? 192 : 96);
};
const SORT_KEY = 'hm-files-sort';
const by = new Intl.Collator('ru', { sensitivity: 'base', numeric: true });

/** The day a file came, as the list's heading says it. */
function dayOf(iso?: string | null): string {
  if (!iso) return 'Когда — неизвестно';
  const d = parseTime(iso);
  const now = new Date();
  const days = Math.round((new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() - new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) / 86_400_000);
  if (days === 0) return 'Сегодня';
  if (days === 1) return 'Вчера';
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', ...(d.getFullYear() === now.getFullYear() ? {} : { year: 'numeric' }) }).replace(/\s*г\.$/, '');
}

/** A button of the bar with a menu under it. */
function BarMenu({ icon, label, active, children }: { icon: IconName; label: string; active?: boolean; children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent ? e.key === 'Escape' : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', close);
    };
  }, [open]);
  return (
    <div ref={ref} className="relative">
      <IconButton icon={icon} label={label} aria-expanded={open} onClick={() => setOpen((v) => !v)} className={active ? '!text-accent' : undefined} />
      {open && (
        <div role="menu" aria-label={label} className="anim-up absolute right-0 top-full z-30 w-64 rounded-2xl bg-raised p-1.5 text-sm shadow-2xl">
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

const menuItem = (icon: IconName | null, label: string, run: () => void, on = false) => (
  <button key={label} type="button" role="menuitem" onClick={run} className={cx('flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-fg/10', on && 'font-semibold text-accent')}>
    {icon ? <Icon name={icon} size={19} /> : <span className="flex w-[19px] justify-center">{on && <Icon name="check" size={16} />}</span>}
    {label}
  </button>
);

/** «Мои файлы» (from «Меню»; «Все файлы» of an admin): every track one brought — an import, an upload, a download
 * (an admin: everybody's) — as one long list of tracks, the newest first and by the days they came. The magnifier
 * of the bar searches it; it is sorted by the bar's menu and sorted out by the marks under the bar: one's own, the
 * tracks that are in no playlist, without an album, of a poor file, and the ones that are one recording twice (told
 * by the sound). The rows are the rows of any list: they play, are picked together, have the track's menu.
 * The trash is one more mark, apart from the rest (they sort out the files there are; it shows the ones there are
 * no more): the deleted tracks in the place of the list, each brought back by its button (`TrashList`). `trash`:
 * the page opens on it (the «Корзина» of the note that follows a deletion). */
export function FilesPage({ trash = false }: { trash?: boolean }) {
  const { me } = useSession();
  const desktop = useDesktop();
  const res = useAsync((s) => api.libFiles(s), [], true, 'files');
  const twice = useAsync((s) => api.fileDoubles(s), []);
  const gone = useAsync((s) => api.trash(s), []);
  // the banner shows until the first import
  const imports = useAsync((s) => api.imports(s), []);
  const [sort, setSortState] = useState<Sort>(() => {
    try {
      const v = localStorage.getItem(SORT_KEY) as Sort | null;
      return v && ALL_SORTS.includes(v) ? v : 'new';
    } catch {
      return 'new';
    }
  });
  const setSort = (v: Sort) => {
    setSortState(v);
    try { localStorage.setItem(SORT_KEY, v); } catch { /* it lasts for this page then */ }
  };
  const [only, setOnly] = useState<Only>(trash ? 'trash' : 'all');

  useHubEvent('library', () => {
    res.reload(true);
    twice.reload(true);
    gone.reload(true);
  });

  const all = useMemo(() => res.data ?? [], [res.data]);
  // the group of doubles a track is in (its place among the groups)
  const doubleOf = useMemo(() => {
    const m = new Map<number, number>();
    (twice.data ?? []).forEach((g, at) => g.forEach((id) => m.set(id, at)));
    return m;
  }, [twice.data]);
  const isDouble = (t: FileRowOut) => t.library_track_id != null && doubleOf.has(t.library_track_id);
  const counts = useMemo(() => ({
    mine: all.filter((t) => t.mine).length,
    loose: all.filter((t) => !t.kept).length,
    noAlbum: all.filter((t) => t.no_album).length,
    low: all.filter(low).length,
    doubles: all.filter(isDouble).length,
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [all, doubleOf]);

  const listed = useMemo(() => {
    const rows = all.filter((t) => (only === 'mine' ? t.mine : only === 'loose' ? !t.kept : only === 'noAlbum' ? t.no_album : only === 'low' ? low(t) : only === 'doubles' ? isDouble(t) : true));
    const at = (t: FileRowOut) => (t.added_at ? parseTime(t.added_at).getTime() : 0);
    const artist = (a: FileRowOut, b: FileRowOut) => by.compare(a.artist, b.artist) || by.compare(a.album_title ?? '', b.album_title ?? '') || (a.track_no ?? 0) - (b.track_no ?? 0);
    const album = (a: FileRowOut, b: FileRowOut) => by.compare(a.album_title ?? '\uffff', b.album_title ?? '\uffff') || (a.disc_no ?? 0) - (b.disc_no ?? 0) || (a.track_no ?? 0) - (b.track_no ?? 0);
    const cmp: Record<Sort, (a: FileRowOut, b: FileRowOut) => number> = {
      new: (a, b) => at(b) - at(a),
      old: (a, b) => at(a) - at(b),
      title: (a, b) => by.compare(a.title, b.title),
      titleZ: (a, b) => by.compare(b.title, a.title),
      artist,
      artistZ: (a, b) => artist(b, a),
      album,
      albumZ: (a, b) => album(b, a),
      qualityBest: (a, b) => quality(b) - quality(a),
      quality: (a, b) => quality(a) - quality(b),
    };
    // the doubles stand next to each other, whatever the order of the rest
    if (only === 'doubles') return [...rows].sort((a, b) => (doubleOf.get(a.library_track_id ?? -1) ?? 0) - (doubleOf.get(b.library_track_id ?? -1) ?? 0) || quality(b) - quality(a));
    return [...rows].sort(cmp[sort]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [all, only, sort, doubleOf]);

  // the trash is searched by the same field: its rows stand in for the list's (a name, an artist, an album)
  const deleted = useMemo(() => gone.data ?? [], [gone.data]);
  const inTrash = only === 'trash';
  const deletedRows = useMemo(() => deleted.map((d) => ({ ref: `trash:${d.id}`, title: d.title, artist: d.artist, album_title: d.album }) as unknown as FileRowOut), [deleted]);
  const search = useListSearch(inTrash ? deletedRows : listed, inTrash ? 'Название, исполнитель или альбом' : 'Название, исполнитель, альбом или строчка');
  const deletedShown = useMemo(() => (inTrash && search.found ? search.found.map((f) => deleted[f.index]).filter(Boolean) : deleted), [inTrash, search.found, deleted]);
  const shown = useMemo(() => (search.found ? search.found.map((f) => listed[f.index]) : listed), [search.found, listed]);
  // by the days the files came: only while the list is in that order and whole
  const byDays = (sort === 'new' || sort === 'old') && only !== 'doubles' && !search.found;
  const days = useMemo(() => {
    const m = new Map<string, number>();
    if (byDays) for (const t of shown) m.set(dayOf(t.added_at), (m.get(dayOf(t.added_at)) ?? 0) + 1);
    return m;
  }, [shown, byDays]);

  const chips: [Only, string, number | null][] = [
    ['all', 'Все', null],
    ...(me.is_admin && counts.mine < all.length ? [['mine', 'Добавил я', counts.mine] as [Only, string, number]] : []),
    ['loose', 'Не в плейлистах', counts.loose],
    ['noAlbum', 'Без альбома', counts.noAlbum],
    ['low', 'Низкое качество', counts.low],
    ['doubles', 'Дубли', counts.doubles],
  ];
  const offerImport = imports.data?.length === 0 && only === 'all' && !search.found;
  const title = me.is_admin ? 'Все файлы' : 'Мои файлы';
  const filters = (all.length > 0 || deleted.length > 0) && (
    <div className="flex items-center gap-2 pb-2 pl-4 lg:pl-6">
      <div className="min-w-0 flex-1 pr-4">
        <Segmented<Only>
          label="Какие файлы показывать"
          value={only}
          onChange={(id) => setOnly(only === id ? 'all' : id)}
          options={chips.filter(([id, , n]) => id === 'all' || id === only || (n ?? 0) > 0).map(([id, label, n]) => ({ value: id, label, count: n }))}
          after={<>
        {(deleted.length > 0 || inTrash) && (
          <>
            <span className="mx-1 my-1.5 w-px shrink-0 self-stretch bg-line" aria-hidden="true" />
            <button type="button" aria-pressed={inTrash} onClick={() => setOnly(inTrash ? 'all' : 'trash')} title="Удалённые файлы: 30 дней их можно вернуть" className={cx('flex min-h-9 shrink-0 items-center gap-1.5 rounded-full px-4 text-sm transition', inTrash ? 'bg-danger font-semibold text-white' : 'bg-raised text-fg hover:bg-hover')}>
              <Icon name="trash" size={14} />
              Корзина
              <span className={cx('tabular-nums', !inTrash && 'text-dim')}>{deleted.length}</span>
            </button>
          </>
        )}
          </>}
        />
      </div>
      {!inTrash && <span className="hidden shrink-0 pr-4 text-xs tabular-nums text-dim sm:block lg:pr-6">{shown.length === all.length ? tracksWord(all.length) : `${shown.length} из ${all.length}`}</span>}
    </div>
  );

  return (
    <div>
      <ListBar
        plain
        title={title}
        back="/more"
        search={search}
        toolbar={filters}
        extra={
          <>
            {/* a computer sorts by a click on a column's name; a phone has no columns */}
            {!inTrash && !desktop && <BarMenu icon="sliders" label={`Сортировка: ${SORTS.find(([id]) => id === sort)?.[1] ?? ''}`} active={sort !== 'new'}>
              {(close) => SORTS.map(([id, label]) => menuItem(null, label, () => { setSort(id); close(); }, sort === id))}
            </BarMenu>}
            <IconButton icon="upload" label="Загрузить файлы" onClick={() => navigate('/upload')} />
          </>
        }
      />

      {offerImport && (
        <Link to="/import" className="card mx-4 mb-4 flex items-center gap-3 p-3 hover:bg-hover lg:mx-6">
          <ServiceIcon service="yandex" size={22} />
          <span className="min-w-0 flex-1">
            <span className="block text-sm font-semibold">Перенести музыку из Яндекс Музыки</span>
            <span className="block text-xs text-muted">♥, плейлисты, любимые альбомы и исполнители</span>
          </span>
          <Icon name="chevron" size={18} className="shrink-0 text-dim" />
        </Link>
      )}
      {only === 'low' && shown.length > 0 && (
        <p className="px-4 pb-2 text-xs text-muted lg:px-6">
          Файлы, которые слышно: MP3 ниже 192 кбит/с и подобные.{' '}
          <Link to="/quality" className="text-info hover:underline">
            Поискать для них лучшее качество
          </Link>
        </p>
      )}
      {only === 'doubles' && shown.length > 0 && (
        <p className="px-4 pb-2 text-xs text-muted lg:px-6">Одна запись в двух файлах — определено по звучанию. Рядом стоят пары, лучший файл выше; лишний удалите через меню трека («Удалить файл»).</p>
      )}

      {inTrash ? (
        <TrashList
          items={deletedShown}
          searching={!!search.found}
          onBack={() => {
            window.dispatchEvent(new Event(EDITED_EVENT));
            gone.reload(true);
            res.reload(true);
          }}
        />
      ) : res.loading ? (
        <SkeletonRows n={10} />
      ) : res.error && !all.length ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : all.length === 0 ? (
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
      ) : shown.length === 0 ? (
        <Empty icon="search" title="Ничего не найдено" text={search.found ? 'Попробуйте другой запрос.' : 'Под эту метку ничего не подходит.'} />
      ) : (
        <TrackTable tracks={shown} numbered album added>
          <ul className="pb-6">
            <li>
              <TrackTableHeader
                numbered
                album
                added
                sort={sort === 'new' || sort === 'old' ? sort : null}
                onSort={(next) => setSort(next === 'old' ? 'old' : 'new')}
                heads={{
                  title: {
                    label: sort === 'artist' || sort === 'artistZ' ? 'Исполнитель' : 'Название',
                    mark: sort === 'title' || sort === 'artist' ? '▲' : sort === 'titleZ' || sort === 'artistZ' ? '▼' : null,
                    title: 'Сортировать: по названию, потом по исполнителю',
                    onClick: () => setSort(after(sort, ['title', 'titleZ', 'artist', 'artistZ'])),
                  },
                  album: { mark: sort === 'album' ? '▲' : sort === 'albumZ' ? '▼' : null, title: 'Сортировать по альбому', onClick: () => setSort(after(sort, ['album', 'albumZ'])) },
                  file: { mark: sort === 'quality' ? '▲' : sort === 'qualityBest' ? '▼' : null, title: 'Сортировать по качеству файла: сначала худшие, потом лучшие', onClick: () => setSort(after(sort, ['quality', 'qualityBest'])) },
                }}
              />
            </li>
            {shown.map((t, i) => {
              const day = byDays ? dayOf(t.added_at) : null;
              const head = day !== null && (i === 0 || dayOf(shown[i - 1].added_at) !== day);
              return (
                <li key={`${t.ref}:${t.library_track_id}`}>
                  {head && (
                    <h2 className="px-4 pb-1 pt-4 text-xs font-semibold text-muted lg:px-6">
                      {day} <span className="font-normal text-dim">· {tracksWord(days.get(day as string) ?? 0)}</span>
                    </h2>
                  )}
                  <TrackRow track={t} queue={shown} link={false} showAlbum showAdded index={i + 1} />
                </li>
              );
            })}
          </ul>
        </TrackTable>
      )}

    </div>
  );
}

/** The trash, in the place of the list: the tracks whose files were deleted lately, the last deleted first. A file
 * is kept for thirty days; until then «Вернуть» brings the track back as it was — into its album and the playlists
 * it stood in. The library is everybody's, and so is the trash. A deleted track does not play and is not picked. */
function TrashList({ items, searching, onBack }: { items: TrashedOut[]; searching: boolean; onBack: () => void }) {
  const desktop = useDesktop();
  const [busy, setBusy] = useState<number | null>(null);
  const restore = async (id: number) => {
    setBusy(id);
    const r = await attempt(() => api.trashRestore(id), 'Трек возвращён');
    setBusy(null);
    if (r) onBack();
  };
  if (!items.length)
    return searching ? (
      <Empty icon="search" title="Ничего не найдено" text="В корзине такого нет." />
    ) : (
      <Empty icon="trash" title="Корзина пуста" text="Удалённый файл лежит здесь 30 дней — за это время трек можно вернуть вместе с его местом в плейлистах." />
    );
  const left = (t: TrashedOut) => `${t.days_left} ${plural(t.days_left, ['день', 'дня', 'дней'])}`;
  const was = (t: TrashedOut) => (t.playlists ? `был в ${t.playlists} ${plural(t.playlists, ['плейлисте', 'плейлистах', 'плейлистах'])}` : null);
  const grid = { gridTemplateColumns: '2.5rem minmax(0,2fr) minmax(0,1.3fr) minmax(0,10rem) 5.5rem 6.5rem' };
  return (
    <div className="pb-6">
      <p className="px-4 pb-2 text-xs text-muted lg:px-6">Удалённые файлы хранятся 30 дней, потом стираются насовсем.</p>
      {desktop && (
        <div className="mx-2 mb-1 grid h-9 items-center gap-3 border-b border-line/60 px-2 text-xs text-dim" style={grid}>
          <span />
          <span>Название</span>
          <span>Альбом</span>
          <span>Удалил</span>
          <span>Осталось</span>
          <span />
        </div>
      )}
      <ul>
        {items.map((t) => {
          const soon = t.days_left <= 3;
          const back = (
            <Button small variant="ghost" busy={busy === t.id} onClick={() => void restore(t.id)}>
              Вернуть
            </Button>
          );
          const mark = (
            <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-fg/5 text-dim">
              <Icon name="note" size={18} />
            </span>
          );
          return desktop ? (
            <li key={t.id} className="mx-2 grid min-h-14 items-center gap-3 rounded-lg px-2 hover:bg-fg/5" style={grid}>
              {mark}
              <span className="min-w-0">
                <span className="block truncate text-[15px]"><TrackTitle title={t.title} label={t.version_label ?? null} /></span>
                <span className="block truncate text-[13px] text-muted">{[t.artist, was(t)].filter(Boolean).join(' · ')}</span>
              </span>
              <span className="truncate text-sm text-muted">{t.album || '—'}</span>
              <span className="truncate text-sm text-muted">{[t.deleted_by ?? 'сервер', fmtAdded(t.deleted_at)].filter(Boolean).join(' · ')}</span>
              <span className={cx('text-sm tabular-nums', soon ? 'text-danger' : 'text-muted')}>{left(t)}</span>
              <span className="flex justify-end">{back}</span>
            </li>
          ) : (
            <li key={t.id} className="flex min-h-16 items-center gap-3 pl-4 pr-2">
              {mark}
              <span className="min-w-0 flex-1 py-2">
                <span className="block truncate text-[15px]"><TrackTitle title={t.title} label={t.version_label ?? null} /></span>
                <span className="block truncate text-[13px] text-muted">{t.artist}</span>
                <span className={cx('block truncate text-xs', soon ? 'text-danger' : 'text-dim')}>{['осталось ' + left(t), was(t)].filter(Boolean).join(' · ')}</span>
              </span>
              {back}
            </li>
          );
        })}
      </ul>
    </div>
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
