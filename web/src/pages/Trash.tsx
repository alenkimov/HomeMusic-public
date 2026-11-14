import { useState } from 'react';
import { api } from '../api/client';
import { Icon } from '../components/Icon';
import { Button, Empty, ErrorState, Loading, PageHeader } from '../components/ui';
import { fmtAdded, plural } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { EDITED_EVENT } from '../lib/trackEdit';
import { attempt } from '../lib/ui';

/** «Корзина» (from «Меню»): the tracks whose files were deleted lately. A file is kept for thirty days; until then
 * the track can be brought back as it was — into its album and into the playlists it stood in. The library is
 * everybody's, and so is the trash: anybody may bring a track back. */
export function TrashPage() {
  const res = useAsync((s) => api.trash(s), []);
  const [busy, setBusy] = useState<number | null>(null);
  const restore = async (id: number) => {
    setBusy(id);
    const r = await attempt(() => api.trashRestore(id), 'Трек возвращён');
    setBusy(null);
    if (!r) return;
    window.dispatchEvent(new Event(EDITED_EVENT));
    res.reload(true);
  };
  const items = res.data ?? [];
  return (
    <div>
      <PageHeader title="Корзина" back="/more" sub={items.length ? 'Файл хранится 30 дней после удаления' : undefined} />
      {res.error && !res.data ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !res.data ? (
        <Loading />
      ) : !items.length ? (
        <Empty icon="trash" title="Корзина пуста" text="Удалённый файл лежит здесь 30 дней — за это время трек можно вернуть вместе с его местом в плейлистах." />
      ) : (
        <ul className="mx-4 mb-6 overflow-hidden rounded-2xl bg-surface">
          {items.map((t) => (
            <li key={t.id} className="flex min-h-16 items-center gap-3 border-b border-fg/5 pl-4 pr-2 last:border-0">
              <Icon name="note" size={20} className="shrink-0 text-muted" />
              <span className="min-w-0 flex-1 py-2">
                <span className="block truncate">{t.title}</span>
                <span className="block truncate text-[13px] text-muted">{t.artist}</span>
                <span className="block truncate text-xs text-dim">
                  {[
                    `удалил ${t.deleted_by ?? 'сервер'}, ${fmtAdded(t.deleted_at)}`,
                    t.playlists ? `был в ${t.playlists} ${plural(t.playlists, ['плейлисте', 'плейлистах', 'плейлистах'])}` : null,
                    `осталось ${t.days_left} ${plural(t.days_left, ['день', 'дня', 'дней'])}`,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                </span>
              </span>
              <Button small variant="ghost" busy={busy === t.id} onClick={() => void restore(t.id)}>
                Вернуть
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
