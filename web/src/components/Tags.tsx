import { useMemo, useState } from 'react';
import { selectedTracks, useSelectionCount } from '../lib/selection';
import { api } from '../api/client';
import { tracksWord } from '../lib/format';
import { UNTAGGED, closeTagsSheet, filterActive, filterWords, selectionsChanged, setTagsData, useTags, useTagsSheet, type Tag, type TagFilter, type TagSelection } from '../lib/tags';
import { attempt, toast } from '../lib/ui';
import { Icon } from './Icon';
import { Button, Field, Loading, Sheet, cx } from './ui';

type Mark = 'all' | 'some' | 'none';

/** «Теги»: the tags of one track or of several picked ones, by group. A tag is on all of them (✓), on some (–) or
 * on none; a tap goes round «put on all» → «take off all» → «leave as it is». Typing narrows the tags, and a name
 * that is not there yet can be made a tag of one's own. Nothing changes until «Готово». */
export function TagsSheet() {
  const open = useTagsSheet();
  if (!open) return null;
  return <Body key={open.songIds.join('|')} songIds={open.songIds} title={open.title} pick={open.pick} />;
}

function Body({ songIds, title, pick }: { songIds: string[]; title: string; pick?: { ids: number[]; onDone: (ids: number[]) => void } }) {
  const tags = useTags();
  const n = songIds.length;
  const [want, setWant] = useState<Record<number, Mark>>({});
  const [q, setQ] = useState('');
  const [busy, setBusy] = useState(false);

  const counts = useMemo(() => {
    const out = new Map<number, number>();
    for (const id of songIds) for (const t of tags.map[id] ?? []) out.set(t, (out.get(t) ?? 0) + 1);
    return out;
  }, [songIds, tags.map]);
  const was = (t: Tag): Mark => pick ? (pick.ids.includes(t.id) ? 'all' : 'none') : ((counts.get(t.id) ?? 0) >= n ? 'all' : counts.get(t.id) ? 'some' : 'none');
  const now = (t: Tag): Mark => want[t.id] ?? was(t);
  const all = tags.groups.flatMap((g) => g.tags);
  const add = all.filter((t) => now(t) === 'all' && was(t) !== 'all');
  const remove = all.filter((t) => now(t) === 'none' && was(t) !== 'none');
  const needle = q.trim().toLocaleLowerCase('ru');
  const exact = all.some((t) => t.name.toLocaleLowerCase('ru') === needle);

  const tap = (t: Tag) => {
    const at = now(t);
    setWant((w) => ({ ...w, [t.id]: at === 'all' ? 'none' : at === 'none' && was(t) === 'some' ? 'some' : 'all' }));
  };
  const create = async () => {
    const name = q.trim();
    if (!name) return;
    setBusy(true);
    const t = await attempt(() => api.tagCreate(name));
    if (t) {
      const data = await attempt(() => api.tags());
      if (data) setTagsData(data);
      setWant((w) => ({ ...w, [t.id]: 'all' }));
      setQ('');
    }
    setBusy(false);
  };
  const save = async () => {
    if (pick) {
      pick.onDone(all.filter((t) => now(t) === 'all').map((t) => t.id));
      return closeTagsSheet();
    }
    if (!add.length && !remove.length) return closeTagsSheet();
    setBusy(true);
    const data = await attempt(() => api.tagsAssign({ tracks: songIds, add: add.map((t) => t.id), remove: remove.map((t) => t.id) }));
    setBusy(false);
    if (!data) return;
    setTagsData(data);
    toast([add.length ? `Поставлено: ${add.map((t) => t.name).join(', ')}` : '', remove.length ? `Снято: ${remove.map((t) => t.name).join(', ')}` : ''].filter(Boolean).join('. '), 'success');
    closeTagsSheet();
  };

  return (
    <Sheet
      open
      onClose={closeTagsSheet}
      title={pick ? title : n === 1 ? `Теги: ${title}` : `${tracksWord(n)} — теги`}
      footer={
        <div className="flex items-center gap-2">
          <span className="min-w-0 flex-1 truncate text-xs text-muted">
            {add.length || remove.length ? [...add.map((t) => `+${t.name}`), ...remove.map((t) => `−${t.name}`)].join(', ') : 'Изменений нет'}
          </span>
          <Button onClick={closeTagsSheet}>Отмена</Button>
          <Button variant="primary" busy={busy} onClick={() => void save()}>
            Готово
          </Button>
        </div>
      }
    >
      {!tags.loaded ? (
        <Loading />
      ) : (
        <div className="space-y-3">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (needle && !exact) void create();
            }}
          >
            <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти тег или создать свой" aria-label="Найти тег или создать свой" maxLength={64} />
            {needle && !exact && (
              <button type="submit" disabled={busy} className="mt-1.5 flex w-full items-center gap-2 rounded-lg bg-accent/15 px-3 py-2 text-left text-sm text-accent hover:bg-accent/20">
                <Icon name="plus" size={16} />
                Создать тег «{q.trim()}»
              </button>
            )}
          </form>
          {tags.groups.map((g) => {
            const shown = g.tags.filter((t) => !needle || t.name.toLocaleLowerCase('ru').includes(needle));
            if (!shown.length && (g.id !== 'own' || needle)) return null;
            return (
              <div key={g.id}>
                <div className="pb-1.5 text-xs text-muted">{g.title}</div>
                <div className="flex flex-wrap gap-1.5">
                  {shown.map((t) => {
                    const at = now(t);
                    return (
                      <button
                        key={t.id}
                        type="button"
                        role="checkbox"
                        aria-checked={at === 'all' ? true : at === 'some' ? 'mixed' : false}
                        onClick={() => tap(t)}
                        className={cx('flex items-center gap-1 rounded-full px-3 py-1 text-sm transition', at === 'all' ? 'bg-accent text-on-accent' : at === 'some' ? 'bg-warn/20 text-warn' : 'bg-fg/10 text-muted hover:text-fg')}
                      >
                        {at !== 'none' && <Icon name={at === 'all' ? 'check' : 'minus'} size={13} stroke={3} />}
                        {t.name}
                        {at === 'some' && n > 1 && <span className="text-xs">· {counts.get(t.id)} из {n}</span>}
                      </button>
                    );
                  })}
                  {!shown.length && <span className="text-xs text-dim">Своих тегов пока нет — впишите название выше.</span>}
                </div>
              </div>
            );
          })}
          <p className="text-[11px] text-dim">
            {pick ? 'Язык и жанр Хаб поставит сам после импорта: язык — по тексту песни, жанр — из файла.' : 'Язык и жанр Хаб ставит сам: язык — по тексту песни, жанр — из каталога. Снятый вами тег он не вернёт.'} «Без слов» — для инструментальной музыки: текст для такого трека Хаб не ищет.
          </p>
        </div>
      )}
    </Sheet>
  );
}

/** What the tags' window of a list needs to know of it. */
export interface ListTags {
  songIds: readonly (string | null | undefined)[];
  filter: TagFilter;
  onChange: (next: TagFilter) => void;
  /** «Сохранить как подборку» */
  onSave?: () => void;
  /** tracks the list shows now */
  count: number;
}

const FIRST = 8; // tags of a group a computer shows before «ещё»

/** The tags a list is narrowed by, in a window of their own (a sheet of a phone, a small window by the tag mark of a
 * computer): only the tags its tracks have, the ones chosen first, then by how many tracks have them. A tap on a tag:
 * wanted → left out → off. Of every group with wanted tags a track needs one of them; a tag left out removes the
 * track. However many tags there are the window stays its size: a group is one line a phone scrolls sideways
 * (`wrap` off), or its first tags and «ещё» (a computer). The list changes as the tags are tapped. */
export function TagFilterBody({ songIds, filter, onChange, onSave, wrap }: ListTags & { wrap: boolean }) {
  const tags = useTags();
  const present = useMemo(() => {
    const out = new Map<number, number>();
    let untagged = 0;
    for (const id of songIds) {
      const mine = id ? (tags.map[id] ?? []) : [];
      for (const t of mine) out.set(t, (out.get(t) ?? 0) + 1);
      if (id && !mine.some((t) => tags.byId.get(t)?.group === 'own')) untagged++;
    }
    return { counts: out, untagged };
  }, [songIds, tags.map, tags.byId]);
  const [fresh, setFresh] = useState<number[]>([]); // tags made here just now: shown though no track of the list has them yet
  const [q, setQ] = useState('');
  const [whole, setWhole] = useState<string[]>([]); // groups opened by «ещё»
  if (!tags.loaded) return <Loading />;
  const needle = q.trim().toLocaleLowerCase('ru');
  const count = (t: Tag) => present.counts.get(t.id) ?? 0;
  const groups = tags.groups
    .map((g) => ({
      ...g,
      tags: g.tags
        .filter((t) => (present.counts.has(t.id) || filter[t.id] || fresh.includes(t.id)) && (!needle || t.name.toLocaleLowerCase('ru').includes(needle)))
        .sort((a, b) => Number(!!filter[b.id]) - Number(!!filter[a.id]) || count(b) - count(a) || a.name.localeCompare(b.name, 'ru')),
    }))
    .filter((g) => g.tags.length || (g.id === 'own' && !needle));
  const tap = (key: string) => {
    const next = { ...filter };
    if (!next[key]) next[key] = 'yes';
    else if (next[key] === 'yes') next[key] = 'no';
    else delete next[key];
    onChange(next);
  };
  const chip = (key: string, name: string, n: number) => {
    const mode = filter[key];
    return (
      <button
        key={key}
        type="button"
        onClick={() => tap(key)}
        aria-pressed={!!mode}
        title={mode === 'yes' ? `Нужен «${name}» — нажмите, чтобы исключить` : mode === 'no' ? `Без «${name}» — нажмите, чтобы сбросить` : `${name}: ${tracksWord(n)}`}
        className={cx('flex h-8 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full px-3 text-sm transition', mode === 'yes' ? 'bg-accent text-on-accent' : mode === 'no' ? 'bg-danger/20 text-danger line-through' : 'bg-fg/10 text-fg hover:bg-fg/15')}
      >
        {mode && <Icon name={mode === 'yes' ? 'check' : 'minus'} size={12} stroke={3} />}
        {name}
        {n > 0 && <span className={cx('text-xs tabular-nums', mode ? 'opacity-70' : 'text-dim')}>{n}</span>}
      </button>
    );
  };
  return (
    <div className="space-y-3" role="group" aria-label="Фильтр по тегам">
      <div className="flex h-10 items-center gap-2 rounded-xl bg-fg/10 px-3">
        <Icon name="search" size={16} className="shrink-0 text-muted" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Найти тег" aria-label="Найти тег" maxLength={64} className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-dim" />
      </div>
      {!groups.some((g) => g.tags.length) && <p className="text-sm text-muted">{needle ? 'Такого тега у треков этого списка нет.' : 'У треков этого списка пока нет тегов.'}</p>}
      {groups.map((g) => {
        const all = wrap && (needle || whole.includes(g.id) || g.tags.length <= FIRST + 1) ? g.tags : wrap ? g.tags.slice(0, Math.max(FIRST, g.tags.filter((t) => filter[t.id]).length)) : g.tags;
        const rest = g.tags.length - all.length;
        return (
          <div key={g.id}>
            <div className="pb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
              {g.title}
              {g.tags.length > 5 && <span className="font-normal"> · {g.tags.length}</span>}
            </div>
            <div className={wrap ? 'flex flex-wrap gap-1.5' : 'no-scrollbar -mx-4 flex gap-1.5 overflow-x-auto px-4'}>
              {all.map((t) => chip(String(t.id), t.name, count(t)))}
              {rest > 0 && (
                <button type="button" onClick={() => setWhole((w) => [...w, g.id])} className="flex h-8 shrink-0 items-center rounded-full px-3 text-sm text-muted hover:text-fg">
                  ещё {rest}
                </button>
              )}
              {g.id === 'own' && !needle && present.untagged > 0 && chip(UNTAGGED, 'без своих тегов', present.untagged)}
              {g.id === 'own' && !needle && <NewTag onMade={(id) => setFresh((list) => [...list, id])} />}
            </div>
          </div>
        );
      })}
      {filterActive(filter) && onSave && (
        <button type="button" onClick={onSave} className="flex items-center gap-1.5 text-sm text-accent hover:underline">
          <Icon name="filter" size={15} />
          Сохранить как подборку
        </button>
      )}
    </div>
  );
}

/** The tags a list is narrowed by now, in a line above its tracks: × takes one off. Scrolled up, the line stays
 * under the list's bar (`ListBar`: 56px and the screen's own top). */
export function TagFilterChips({ filter, onChange, count }: { filter: TagFilter; onChange: (next: TagFilter) => void; count: number }) {
  const tags = useTags();
  const keys = Object.keys(filter);
  if (!keys.length) return null;
  return (
    <div className="sticky top-[calc(env(safe-area-inset-top)+56px+var(--page-top))] z-10 flex items-center gap-2 bg-bg px-4 py-2 lg:px-6">
      <div className="no-scrollbar flex min-w-0 flex-1 gap-1.5 overflow-x-auto">
        {keys.map((k) => {
          const name = k === UNTAGGED ? 'без своих тегов' : (tags.byId.get(Number(k))?.name ?? '?');
          return (
            <button
              key={k}
              type="button"
              title={`Убрать «${name}» из отбора`}
              onClick={() => {
                const next = { ...filter };
                delete next[k];
                onChange(next);
              }}
              className={cx('flex h-7 shrink-0 items-center gap-1 whitespace-nowrap rounded-full pl-2.5 pr-1.5 text-xs', filter[k] === 'no' ? 'bg-danger/20 text-danger line-through' : 'bg-fg/10 text-fg')}
            >
              {name}
              <Icon name="x" size={12} className="opacity-60" />
            </button>
          );
        })}
      </div>
      <span className="shrink-0 text-xs tabular-nums text-muted">{tracksWord(count)}</span>
    </div>
  );
}

/** «+» after the last tag of «Теги»: a new tag of one's own, made right here. With tracks picked in the list it is put
 * on them at once; without — it is only made, to be put on tracks later (the 🏷 of the selection bar, a track's menu). */
function NewTag({ onMade }: { onMade: (id: number) => void }) {
  const picked = useSelectionCount();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const close = () => {
    setOpen(false);
    setName('');
  };
  const make = async () => {
    const title = name.trim();
    if (!title || busy) return;
    setBusy(true);
    const tag = await attempt(() => api.tagCreate(title));
    if (tag) {
      const songs = selectedTracks().map((x) => x.tag_key).filter((x): x is string => !!x);
      const data = await attempt(() => (songs.length ? api.tagsAssign({ tracks: songs, add: [tag.id], remove: [] }) : api.tags()));
      if (data) setTagsData(data);
      onMade(tag.id);
      toast(songs.length ? `Тег «${tag.name}» поставлен: ${tracksWord(songs.length)}` : `Тег «${tag.name}» создан — выделите треки и поставьте его кнопкой 🏷`, 'success');
      close();
    }
    setBusy(false);
  };
  if (!open)
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        title={picked ? `Новый тег — сразу на выделенные: ${tracksWord(picked)}` : 'Новый тег'}
        aria-label="Новый тег"
        className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-fg/10 text-muted transition hover:bg-accent hover:text-on-accent"
      >
        <Icon name="plus" size={13} stroke={2.6} />
      </button>
    );
  return (
    <form
      className="flex shrink-0 items-center gap-1"
      onSubmit={(e) => {
        e.preventDefault();
        void make();
      }}
    >
      <input
        autoFocus
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === 'Escape' && close()}
        onBlur={() => !name.trim() && close()}
        placeholder={picked ? `тег для ${tracksWord(picked)}` : 'новый тег'}
        aria-label="Название нового тега"
        maxLength={64}
        disabled={busy}
        className="h-8 w-40 shrink-0 rounded-full bg-fg/10 px-3 text-sm text-fg outline-none ring-1 ring-accent placeholder:text-dim"
      />
      <button type="submit" disabled={!name.trim() || busy} aria-label="Создать тег" title="Создать (Enter)" className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent disabled:opacity-40">
        <Icon name="check" size={13} stroke={3} />
      </button>
    </form>
  );
}

/** «Сохранить как подборку»: a name for the filter that is on; it appears in the sidebar. */
export function SaveSelection({ scope, filter, onClose, onSaved }: { scope: string; filter: TagFilter; onClose: () => void; onSaved: (s: TagSelection) => void }) {
  const tags = useTags();
  const [name, setName] = useState(() => filterWords(filter, tags.byId).slice(0, 60));
  const [busy, setBusy] = useState(false);
  const save = async () => {
    if (!name.trim()) return;
    setBusy(true);
    const r = await attempt(() => api.tagSelectionCreate({ name: name.trim(), scope, filter }), 'Подборка сохранена');
    setBusy(false);
    if (!r) return;
    selectionsChanged();
    onSaved(r);
  };
  return (
    <Sheet
      open
      onClose={onClose}
      title="Подборка по тегам"
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
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <Field label="Название">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={128} autoFocus />
        </Field>
        <p className="text-sm text-muted">{filterWords(filter, tags.byId)}</p>
        <p className="text-xs text-dim">Подборка хранит не треки, а сам фильтр: список собирается заново при каждом открытии, так что только что размеченный трек уже в ней.</p>
      </form>
    </Sheet>
  );
}
