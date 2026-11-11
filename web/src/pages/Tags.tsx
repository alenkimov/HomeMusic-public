import { useState } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { Button, ErrorState, Field, IconButton, Loading, PageHeader, Sheet, cx } from '../components/ui';
import { tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link } from '../lib/router';
import { UNTAGGED, filterWords, selectionsChanged, setTagsData, useTags, type Tag } from '../lib/tags';
import { attempt, confirmDialog } from '../lib/ui';

const GROUP_NOTE: Record<string, string> = {
  lang: 'ставит Хаб — по тексту песни; у песни может быть несколько языков',
  genre: 'ставит Хаб — жанр из каталога, пришедший с треком',
  own: 'ваши собственные',
};
const SCOPE: Record<string, string> = { liked: 'Любимое', library: 'Вся библиотека' };

/** «Теги» (`/tags`): every tag by group with the number of its tracks — a click opens those tracks; one's own tags
 * are renamed, merged and deleted here, and new ones are made. Above them, the saved selections. */
export function TagsPage() {
  const tags = useTags();
  const sels = useAsync((s) => api.tagSelections(s), []);
  const [name, setName] = useState('');
  const [editing, setEditing] = useState<Tag | null>(null);
  const [busy, setBusy] = useState(false);
  const own = tags.groups.find((g) => g.id === 'own')?.tags ?? [];

  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    if (await attempt(() => api.tagCreate(name.trim()), 'Тег создан')) {
      const data = await attempt(() => api.tags());
      if (data) setTagsData(data);
      setName('');
    }
    setBusy(false);
  };
  const drop = async (t: Tag) => {
    if (!(await confirmDialog({ title: `Удалить тег «${t.name}»?`, body: t.count ? `Он снимется с ${tracksWord(t.count)}. Сами треки останутся.` : 'На треках его нет.', okLabel: 'Удалить', danger: true }))) return;
    const data = await attempt(() => api.tagDelete(t.id), 'Тег удалён');
    if (data) {
      setTagsData(data);
      selectionsChanged();
      sels.reload(true);
    }
  };

  return (
    <div className="mx-auto max-w-3xl pb-10">
      <PageHeader title="Теги" back="/library" />
      {!tags.loaded ? (
        <Loading />
      ) : (
        <>
          <p className="px-4 pb-3 text-sm text-muted">
            По тегам фильтруются списки: над «Любимым» и любым плейлистом есть строка тегов — нажмите тег, чтобы оставить только его треки, ещё раз — чтобы исключить их. Ставятся теги через «⋯» трека или сразу нескольким выделенным.
          </p>

          <h3 className="px-4 pb-1 pt-2 text-base font-bold">Подборки</h3>
          {sels.error ? (
            <ErrorState text={sels.error} onRetry={() => sels.reload()} />
          ) : !sels.data?.length ? (
            <p className="px-4 pb-2 text-sm text-dim">Пока нет. Выберите теги в строке над списком и нажмите «Сохранить как подборку».</p>
          ) : (
            <ul className="px-2">
              {sels.data.map((x) => (
                <li key={x.id}>
                  <Link to={`/selection/${x.id}`} className="flex min-h-12 items-center gap-3 rounded-lg px-2 hover:bg-fg/5">
                    <Icon name="filter" size={18} className="shrink-0 text-accent" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[15px]">{x.name}</span>
                      <span className="block truncate text-xs text-muted">
                        {SCOPE[x.scope] ?? 'Плейлист'} · {filterWords(x.filter, tags.byId)}
                      </span>
                    </span>
                    <Icon name="chevron" size={16} className="shrink-0 text-dim" />
                  </Link>
                </li>
              ))}
            </ul>
          )}

          {tags.groups.map((g) => (
            <section key={g.id} aria-label={g.title}>
              <h3 className="px-4 pb-0.5 pt-5 text-base font-bold">{g.title}</h3>
              <p className="px-4 pb-1 text-xs text-dim">{GROUP_NOTE[g.id]}</p>
              {g.id === 'own' && (
                <form
                  className="flex items-center gap-2 px-4 py-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void create();
                  }}
                >
                  <input className="input flex-1" value={name} onChange={(e) => setName(e.target.value)} placeholder="Новый тег: chill, для дороги…" aria-label="Название нового тега" maxLength={64} />
                  <Button type="submit" icon="plus" disabled={!name.trim()} busy={busy}>
                    Создать
                  </Button>
                </form>
              )}
              <ul className="px-2">
                {g.tags.map((t) => (
                  <li key={t.id} className="flex min-h-11 items-center gap-1 rounded-lg px-2 hover:bg-fg/5">
                    <Link to={`/selection/library?yes=${t.id}`} className="flex min-w-0 flex-1 items-center gap-3 py-1.5" title="Показать треки с этим тегом">
                      <Icon name="tag" size={16} className="shrink-0 text-muted" />
                      <span className="min-w-0 flex-1 truncate text-[15px]">{t.name}</span>
                      <span className="shrink-0 text-xs tabular-nums text-muted">{t.count}</span>
                    </Link>
                    {g.id === 'own' && (
                      <>
                        <IconButton icon="edit" size={16} label={`Переименовать или слить: ${t.name}`} onClick={() => setEditing(t)} className="!h-9 !w-9 text-muted hover:text-fg" />
                        <IconButton icon="trash" size={16} label={`Удалить тег: ${t.name}`} onClick={() => void drop(t)} className="!h-9 !w-9 text-muted hover:text-danger" />
                      </>
                    )}
                  </li>
                ))}
                {g.id === 'own' && (
                  <li>
                    <Link to={`/selection/library?yes=${UNTAGGED}`} className="flex min-h-11 items-center gap-3 rounded-lg px-2 text-muted hover:bg-fg/5 hover:text-fg">
                      <Icon name="search" size={16} className="shrink-0" />
                      <span className="flex-1 text-[15px]">Треки без своих тегов</span>
                      <Icon name="chevron" size={16} className="shrink-0 text-dim" />
                    </Link>
                  </li>
                )}
              </ul>
              {!g.tags.length && g.id !== 'own' && <p className="px-4 py-2 text-sm text-dim">Пока ни одного.</p>}
            </section>
          ))}
        </>
      )}
      {editing && (
        <EditTag
          tag={editing}
          others={own.filter((t) => t.id !== editing.id)}
          onClose={() => setEditing(null)}
          onDone={() => {
            setEditing(null);
            selectionsChanged();
            sels.reload(true);
          }}
        />
      )}
    </div>
  );
}

/** A tag of one's own: another name, or its tracks handed over to another tag. */
function EditTag({ tag, others, onClose, onDone }: { tag: Tag; others: Tag[]; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState(tag.name);
  const [busy, setBusy] = useState(false);
  const rename = async () => {
    if (!name.trim() || name.trim() === tag.name) return onClose();
    setBusy(true);
    const data = await attempt(() => api.tagRename(tag.id, name.trim()), 'Тег переименован');
    setBusy(false);
    if (data) {
      setTagsData(data);
      onDone();
    }
  };
  const merge = async (into: Tag) => {
    if (!(await confirmDialog({ title: `Слить «${tag.name}» с «${into.name}»?`, body: `Все треки «${tag.name}» получат тег «${into.name}», а «${tag.name}» исчезнет. Отменить это нельзя.`, okLabel: 'Слить', danger: true }))) return;
    setBusy(true);
    const data = await attempt(() => api.tagMerge(tag.id, into.id), 'Теги слиты');
    setBusy(false);
    if (data) {
      setTagsData(data);
      onDone();
    }
  };
  return (
    <Sheet
      open
      onClose={onClose}
      title={`Тег «${tag.name}»`}
      footer={
        <div className="flex justify-end gap-2">
          <Button onClick={onClose}>Закрыть</Button>
          <Button variant="primary" busy={busy} disabled={!name.trim()} onClick={() => void rename()}>
            Сохранить название
          </Button>
        </div>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void rename();
        }}
      >
        <Field label="Название">
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={64} autoFocus />
        </Field>
      </form>
      {others.length > 0 && (
        <div className="pt-4">
          <div className="pb-1.5 text-xs text-muted">Слить с другим тегом — его получат все треки «{tag.name}»</div>
          <div className="flex flex-wrap gap-1.5">
            {others.map((t) => (
              <button key={t.id} type="button" disabled={busy} onClick={() => void merge(t)} className={cx('flex items-center gap-1 rounded-full bg-fg/10 px-3 py-1 text-sm text-muted hover:text-fg')}>
                <Icon name="merge" size={13} />
                {t.name}
              </button>
            ))}
          </div>
        </div>
      )}
    </Sheet>
  );
}
