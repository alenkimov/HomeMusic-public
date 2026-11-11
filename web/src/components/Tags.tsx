import { useMemo, useState } from 'react';
import { api } from '../api/client';
import { tracksWord } from '../lib/format';
import { UNTAGGED, closeTagsSheet, setTagsData, useTags, useTagsSheet, type Tag, type TagFilter } from '../lib/tags';
import { attempt, toast } from '../lib/ui';
import { Icon } from './Icon';
import { Button, Loading, Sheet, cx } from './ui';

type Mark = 'all' | 'some' | 'none';

/** «Теги»: the tags of one track or of several picked ones, by group. A tag is on all of them (✓), on some (–) or
 * on none; a tap goes round «put on all» → «take off all» → «leave as it is». Typing narrows the tags, and a name
 * that is not there yet can be made a tag of one's own. Nothing changes until «Готово». */
export function TagsSheet() {
  const open = useTagsSheet();
  if (!open) return null;
  return <Body key={open.songIds.join('|')} songIds={open.songIds} title={open.title} />;
}

function Body({ songIds, title }: { songIds: string[]; title: string }) {
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
  const was = (t: Tag): Mark => ((counts.get(t.id) ?? 0) >= n ? 'all' : counts.get(t.id) ? 'some' : 'none');
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
    if (!add.length && !remove.length) return closeTagsSheet();
    setBusy(true);
    const data = await attempt(() => api.tagsAssign({ nd_song_ids: songIds, add: add.map((t) => t.id), remove: remove.map((t) => t.id) }));
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
      title={n === 1 ? `Теги: ${title}` : `${tracksWord(n)} — теги`}
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
          <p className="text-[11px] text-dim">Язык и жанр Хаб ставит сам: язык — по тексту песни, жанр — из каталога. Снятый вами тег он не вернёт.</p>
        </div>
      )}
    </Sheet>
  );
}

/** The tags a list is narrowed by, above it: only the tags its tracks have. A tap on a tag: wanted → left out →
 * off. Of every group with wanted tags a track needs one of them; a tag left out removes the track. */
export function TagFilterBar({ songIds, filter, onChange }: { songIds: readonly (string | null | undefined)[]; filter: TagFilter; onChange: (next: TagFilter) => void }) {
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
  if (!tags.loaded) return null;
  const groups = tags.groups.map((g) => ({ ...g, tags: g.tags.filter((t) => present.counts.has(t.id) || filter[t.id]) })).filter((g) => g.tags.length || g.id === 'own');
  if (!groups.some((g) => g.tags.length)) return null;
  const tap = (key: string) => {
    const next = { ...filter };
    if (!next[key]) next[key] = 'yes';
    else if (next[key] === 'yes') next[key] = 'no';
    else delete next[key];
    onChange(next);
  };
  const chip = (key: string, name: string, count: number) => {
    const mode = filter[key];
    return (
      <button
        key={key}
        type="button"
        onClick={() => tap(key)}
        aria-pressed={!!mode}
        title={mode === 'yes' ? `Нужен «${name}» — нажмите, чтобы исключить` : mode === 'no' ? `Без «${name}» — нажмите, чтобы сбросить` : `${name}: ${tracksWord(count)}`}
        className={cx('flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs transition', mode === 'yes' ? 'bg-accent text-on-accent' : mode === 'no' ? 'bg-danger/20 text-danger line-through' : 'bg-fg/10 text-muted hover:text-fg')}
      >
        {mode && <Icon name={mode === 'yes' ? 'check' : 'minus'} size={11} stroke={3} />}
        {name}
      </button>
    );
  };
  const active = Object.keys(filter).length > 0;
  return (
    <div className="space-y-1.5 px-6 pb-3" role="group" aria-label="Фильтр по тегам">
      {groups.map((g) => (
        <div key={g.id} className="flex items-start gap-2">
          <span className="w-12 shrink-0 pt-0.5 text-xs text-dim">{g.title}</span>
          <div className="flex flex-wrap gap-1.5">
            {g.tags.map((t) => chip(String(t.id), t.name, present.counts.get(t.id) ?? 0))}
            {g.id === 'own' && present.untagged > 0 && chip(UNTAGGED, 'без своих тегов', present.untagged)}
          </div>
        </div>
      ))}
      {active && (
        <button type="button" onClick={() => onChange({})} className="ml-14 text-xs text-muted underline hover:text-fg">
          Сбросить фильтр
        </button>
      )}
    </div>
  );
}
