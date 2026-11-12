import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { DownloadItemOut } from '../api/types';
import { Icon } from '../components/Icon';
import { Button, Checkbox, Cover, Empty, ErrorState, IconButton, PageHeader, ProgressBar, Segmented, SkeletonRows, StatusChip, cx } from '../components/ui';
import { useDesktop } from '../lib/desktop';
import { useHubConnected, useHubEvent } from '../lib/events';
import { DOWNLOAD_ACTIVE, fmtWhen, plural, providerLabel, warningText } from '../lib/format';
import { useAsync, useInterval } from '../lib/hooks';
import { canPlay, playList } from '../lib/player';
import { Link, navigate } from '../lib/router';
import { attempt, confirmDialog, toast } from '../lib/ui';

type Tab = 'now' | 'problems' | 'done';

const isProblem = (x: DownloadItemOut) => x.status === 'failed' || x.status === 'needs_review';
const tabOf = (x: DownloadItemOut): Tab => (DOWNLOAD_ACTIVE.includes(x.status) ? 'now' : isProblem(x) ? 'problems' : 'done');
const downloadsWord = (n: number) => `${n} ${plural(n, ['загрузку', 'загрузки', 'загрузок'])}`;

/** What went wrong, in a line that says what to do; the source's own words are a click away (`item.error`). */
function reason(item: DownloadItemOut): string {
  const e = item.error ?? '';
  if (!e) return item.status === 'needs_review' ? 'Нужно выбрать файл вручную' : 'Не удалось скачать';
  if (/HTTP Error 403/i.test(e)) return 'Источник не отдал файл — обычно проходит при повторе';
  if (/не совпадает с ожидаемой/i.test(e)) return 'Длительность найденного файла не совпала — выберите файл вручную';
  if (/^Не найдено ни в одном источнике/i.test(e)) return 'Не нашлось ни в одном источнике — нужна своя ссылка';
  if (/^(Ни один кандидат не подошёл|Файл не подошёл)/i.test(e)) return 'Найденные файлы не подошли — выберите вручную или вставьте ссылку';
  return e.length > 90 ? `${e.slice(0, 90)}…` : e;
}

/** «Загрузки»: what is being downloaded now, what needs the user (a file to choose, a link to give), and what is
 * done. A table on a computer, cards on a phone. A download is taken off the list by 🗑 — one that is not finished
 * is cancelled by that; several at once through the ticks. */
export function DownloadsPage() {
  const desktop = useDesktop();
  const res = useAsync((s) => api.downloads(undefined, 5000, s), []);
  const connected = useHubConnected();
  const [tab, setTab] = useState<Tab | null>(null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<number>>(new Set()); // rows whose full error is shown

  // Live updates: upsert the item; unknown items (new batches) are prepended; one taken off the list is dropped.
  useHubEvent('download', (d) => {
    res.setData((prev) => {
      if (!prev) return prev;
      if (d.hidden) return prev.filter((x) => x.id !== d.id);
      const i = prev.findIndex((x) => x.id === d.id);
      if (i < 0) return [d, ...prev];
      const next = prev.slice();
      next[i] = { ...prev[i], ...d, candidates: prev[i].candidates };
      return next;
    });
  });
  useHubEvent('reconnect', () => res.reload(true));

  // downloads queued only for ♥ or by an import are the Hub's own business: not shown (an import has its card); a ♥
  // download that failed comes with `star` false and is shown
  const imports = useAsync((s) => api.imports(s), []);
  useHubEvent('import', () => imports.reload(true));
  const running = (imports.data ?? []).filter((x) => x.status === 'running');
  useInterval(() => imports.reload(true), running.length ? 15_000 : null);
  const items = useMemo(() => (res.data ?? []).filter((x) => !x.star), [res.data]);
  const byTab = useMemo(() => {
    const out: Record<Tab, DownloadItemOut[]> = { now: [], problems: [], done: [] };
    for (const x of items) out[tabOf(x)].push(x);
    return out;
  }, [items]);
  // the first look: what is going on now, else what needs the user, else the finished ones
  const first: Tab = byTab.now.length ? 'now' : byTab.problems.length ? 'problems' : 'done';
  useEffect(() => {
    if (tab === null && res.data) setTab(first); // kept from then on: the page does not jump as downloads finish
  }, [tab, res.data, first]);
  const at: Tab = tab ?? first;
  const shown = byTab[at];
  const chosen = shown.filter((x) => picked.has(x.id));

  const choose = (next: Tab) => {
    setTab(next);
    setPicked(new Set());
  };
  const merge = (r: DownloadItemOut) => res.setData((prev) => prev?.map((x) => (x.id === r.id ? { ...x, ...r, candidates: x.candidates } : x)));
  const one = async (item: DownloadItemOut, kind: 'retry' | 'cancel') => {
    setBusy(`${kind}:${item.id}`);
    const r = await attempt(() => (kind === 'retry' ? api.retry(item.id) : api.cancel(item.id)));
    setBusy(null);
    if (r) merge(r);
  };
  const retry = async (list: DownloadItemOut[], key: string) => {
    if (!list.length) return;
    setBusy(key);
    const r = await attempt(() => api.retryDownloads(list.map((x) => x.id)));
    setBusy(null);
    if (!r) return;
    toast(`Ищу заново: ${downloadsWord(r.queued)}`, 'success');
    setPicked(new Set());
    res.reload(true);
  };
  const remove = async (list: DownloadItemOut[], key: string) => {
    if (!list.length) return;
    const unfinished = list.filter((x) => x.status !== 'done' && x.status !== 'cancelled').length;
    if (list.length > 1 || unfinished) {
      const ok = await confirmDialog({
        title: list.length > 1 ? `Убрать ${downloadsWord(list.length)}?` : `Убрать «${list[0].title}»?`,
        body: unfinished
          ? `${unfinished === list.length ? 'Эти треки' : `${unfinished} из них`} скачаны не будут. Если они ждали места в плейлисте — плейлист соберётся без них.`
          : 'Они исчезнут только из этого списка. Треки останутся в библиотеке.',
        okLabel: 'Убрать',
        danger: unfinished > 0,
      });
      if (!ok) return;
    }
    setBusy(key);
    const ids = new Set(list.map((x) => x.id));
    const r = await attempt(() => api.removeDownloads([...ids]));
    setBusy(null);
    if (!r) return;
    res.setData((prev) => prev?.filter((x) => !ids.has(x.id)));
    setPicked(new Set());
  };

  const pick = (id: number, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  const toggleOpen = (id: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  const play = (item: DownloadItemOut) => playList(byTab.done.filter((x) => x.status === 'done' && x.nd_song_id), (x) => x.nd_song_id, item);

  const row = (item: DownloadItemOut): RowProps => ({
    item,
    picked: picked.has(item.id),
    onPick: (v) => pick(item.id, v),
    busy: busy?.endsWith(`:${item.id}`) ? busy.split(':')[0] : null,
    open: open.has(item.id),
    onOpen: () => toggleOpen(item.id),
    onRetry: () => void one(item, 'retry'),
    onCancel: () => void one(item, 'cancel'),
    onRemove: () => void remove([item], `remove:${item.id}`),
    onPlay: canPlay() && item.status === 'done' && item.nd_song_id ? () => play(item) : undefined,
  });

  const tabs: Array<{ value: Tab; label: string }> = [
    { value: 'now', label: `Сейчас${byTab.now.length ? ` · ${byTab.now.length}` : ''}` },
    { value: 'problems', label: `Проблемы${byTab.problems.length ? ` · ${byTab.problems.length}` : ''}` },
    { value: 'done', label: `Готовые${byTab.done.length ? ` · ${byTab.done.length}` : ''}` },
  ];
  const empty: Record<Tab, [string, string]> = {
    now: ['Сейчас ничего не качается', 'Найдите трек или альбом в поиске и нажмите «Скачать».'],
    problems: ['Проблем нет', 'Здесь появляются загрузки, которым нужен ваш выбор: файл или ссылка.'],
    done: ['Готовых загрузок нет', 'Скачанные треки остаются здесь, пока вы их не уберёте из списка.'],
  };

  return (
    <div className="pb-6">
      <PageHeader
        title="Загрузки"
        sub={
          <span className="inline-flex items-center gap-1.5">
            <span className={cx('h-2 w-2 rounded-full', res.error ? 'bg-danger' : connected ? 'bg-accent' : 'bg-dim')} />
            {res.error ? 'нет связи' : connected ? 'онлайн' : 'переподключаюсь…'}
          </span>
        }
      />
      {running.map((imp) => (
        <Link key={imp.id} to={`/import/${imp.id}`} className="card mx-4 mb-3 flex items-center gap-3 p-3 hover:bg-hover">
          <Icon name="transfer" size={22} className="shrink-0 text-accent" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-semibold">Перенос из Яндекс Музыки</span>
            <span className="block truncate text-xs text-muted">
              {imp.stats.pending ? `сопоставляем · осталось ${imp.stats.pending}` : `скачано ${imp.stats.done} · качается ${imp.stats.downloading}`}
            </span>
          </span>
          <Icon name="chevron" size={18} className="text-dim" />
        </Link>
      ))}
      <div className="flex flex-wrap items-center gap-2 px-4 pb-3">
        <Segmented<Tab> label="Раздел" value={at} onChange={choose} options={tabs} />
        <span className="flex-1" />
        {shown.length > 0 && at === 'problems' && (
          <Button small icon="refresh" busy={busy === 'retry:all'} onClick={() => void retry(shown, 'retry:all')}>
            Повторить все
          </Button>
        )}
        {shown.length > 0 && at !== 'now' && (
          <Button small icon="trash" busy={busy === 'remove:all'} onClick={() => void remove(shown, 'remove:all')}>
            Убрать все
          </Button>
        )}
      </div>

      {res.loading ? (
        <SkeletonRows />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : shown.length === 0 ? (
        <Empty icon="download" title={empty[at][0]} text={empty[at][1]}>
          {at === 'now' && !items.length && (
            <Link to="/search" className="mt-2 rounded-full bg-fg px-5 py-2.5 font-semibold text-bg">
              Перейти к поиску
            </Link>
          )}
        </Empty>
      ) : desktop ? (
        <div className="mx-4 overflow-hidden rounded-xl bg-surface" role="table" aria-label="Загрузки">
          <div role="row" className={cx(GRID, 'h-9 border-b border-line/60 text-xs text-dim')}>
            <Checkbox checked={chosen.length === shown.length} onChange={(v) => setPicked(v ? new Set(shown.map((x) => x.id)) : new Set())} label="Выбрать все" />
            <span>Трек</span>
            <span>Альбом</span>
            <span>Источник</span>
            <span>{at === 'now' ? 'Состояние' : 'Когда'}</span>
            <span />
          </div>
          {shown.map((item) => (
            <TableRow key={item.id} {...row(item)} />
          ))}
        </div>
      ) : (
        <ul className="space-y-2 px-4">
          {shown.map((item) => (
            <Card key={item.id} {...row(item)} />
          ))}
        </ul>
      )}

      {chosen.length > 0 && (
        <div className="sticky bottom-3 z-10 mx-4 mt-3 flex items-center gap-2 rounded-xl border border-line bg-raised px-4 py-2 shadow-lg">
          <span className="text-sm">Выбрано {chosen.length}</span>
          <span className="flex-1" />
          {chosen.some((x) => isProblem(x) || x.status === 'cancelled') && (
            <Button small icon="refresh" busy={busy === 'retry:picked'} onClick={() => void retry(chosen.filter((x) => isProblem(x) || x.status === 'cancelled'), 'retry:picked')}>
              Повторить
            </Button>
          )}
          <Button small icon="trash" busy={busy === 'remove:picked'} onClick={() => void remove(chosen, 'remove:picked')}>
            Убрать
          </Button>
          <IconButton icon="x" size={18} label="Снять выделение" onClick={() => setPicked(new Set())} />
        </div>
      )}
    </div>
  );
}

const GRID = 'grid grid-cols-[2.75rem_minmax(0,2.4fr)_minmax(0,1.4fr)_6rem_9rem_8.5rem] items-center gap-3 pr-2';

interface RowProps {
  item: DownloadItemOut;
  picked: boolean;
  onPick: (v: boolean) => void;
  /** which of its actions is under way: retry / cancel / remove */
  busy: string | null;
  open: boolean;
  onOpen: () => void;
  onRetry: () => void;
  onCancel: () => void;
  onRemove: () => void;
  /** Done and scanned by Navidrome: plays it (and the finished ones around it) in the app's player. */
  onPlay?: () => void;
}

/** The reason of a problem: a line that says what to do; a click shows the source's own words under it. */
function Why({ item, open, onOpen }: { item: DownloadItemOut; open: boolean; onOpen: () => void }) {
  const short = reason(item);
  const full = item.error && item.error !== short ? item.error : null;
  return (
    <div className="min-w-0 text-xs">
      {full ? (
        <button type="button" onClick={onOpen} aria-expanded={open} title={open ? 'Скрыть подробности' : 'Показать подробности'} className="flex max-w-full items-center gap-1 text-left text-warn hover:underline">
          <span className="truncate">{short}</span>
          <Icon name={open ? 'down' : 'chevron'} size={12} className="shrink-0" />
        </button>
      ) : (
        <span className="block truncate text-warn">{short}</span>
      )}
      {open && full && <div className="mt-1 select-text whitespace-pre-wrap break-words rounded-md bg-fg/5 px-2 py-1.5 font-mono text-[11px] text-muted">{full}</div>}
    </div>
  );
}

function Actions({ item, busy, onRetry, onCancel, onRemove, onPlay }: RowProps) {
  const cls = '!h-9 !w-9 text-muted hover:text-fg';
  if (DOWNLOAD_ACTIVE.includes(item.status)) return <IconButton icon="x" size={18} label="Отменить" busy={busy === 'cancel'} onClick={onCancel} className={cls} />;
  return (
    <>
      {onPlay && <IconButton icon="play" size={18} label={`Слушать «${item.title}»`} onClick={onPlay} className="!h-9 !w-9 !text-accent" />}
      {item.status !== 'done' && <IconButton icon="refresh" size={18} label="Повторить поиск" busy={busy === 'retry'} onClick={onRetry} className={cls} />}
      {isProblem(item) && <IconButton icon="search" size={18} label="Выбрать файл или вставить ссылку" onClick={() => navigate(`/downloads/${item.id}`)} className={cls} />}
      <IconButton icon="trash" size={18} label={item.status === 'done' ? 'Убрать из списка' : 'Убрать: не скачивать'} busy={busy === 'remove'} onClick={onRemove} className={cls} />
    </>
  );
}

function Notes({ item }: { item: DownloadItemOut }) {
  const warnings = (item.warnings ?? []).map(warningText);
  if (!warnings.length) return null;
  return (
    <>
      {warnings.map((w, i) => (
        <div key={i} className="flex items-center gap-1 text-xs text-warn">
          <Icon name="alert" size={13} className="shrink-0" />
          <span className="truncate">{w}</span>
        </div>
      ))}
    </>
  );
}

const name = (item: DownloadItemOut): ReactNode => (
  <>
    <span className="block truncate text-sm text-fg">{item.title}</span>
    <span className="block truncate text-xs text-muted">{item.artist}</span>
  </>
);

function TableRow(p: RowProps) {
  const { item } = p;
  const active = DOWNLOAD_ACTIVE.includes(item.status);
  return (
    <div role="row" className={cx('border-b border-line/60 py-1.5 last:border-0', p.picked && 'bg-accent/10')}>
      <div className={GRID}>
        <Checkbox checked={p.picked} onChange={p.onPick} label={`Выбрать «${item.title}»`} />
        <Link to={`/downloads/${item.id}`} className="flex min-w-0 items-center gap-3" aria-label={`Подробности: ${item.artist} — ${item.title}`}>
          <Cover src={item.chosen?.cover_url} size={40} alt="" />
          <span className="min-w-0">{name(item)}</span>
        </Link>
        <span className="truncate text-sm text-muted">{item.album}</span>
        <span className="truncate text-xs text-muted">{item.provider ? providerLabel(item.provider) : '—'}</span>
        <span className="min-w-0 text-xs text-dim">
          {active || item.status === 'cancelled' ? <StatusChip status={item.status} progress={item.progress} /> : fmtWhen(item.updated_at)}
          {item.liked && item.status !== 'done' && <span className="ml-1.5 text-accent" title="♥ поставится после загрузки">♥</span>}
        </span>
        <span className="flex items-center justify-end">
          <Actions {...p} />
        </span>
      </div>
      {(isProblem(item) || item.status === 'downloading' || (item.warnings ?? []).length > 0) && (
        <div className="space-y-1 pb-1 pl-[6.25rem] pr-4">
          {item.status === 'downloading' && <ProgressBar value={item.progress} className="max-w-md" />}
          <Notes item={item} />
          {isProblem(item) && <Why item={item} open={p.open} onOpen={p.onOpen} />}
        </div>
      )}
    </div>
  );
}

function Card(p: RowProps) {
  const { item } = p;
  const active = DOWNLOAD_ACTIVE.includes(item.status);
  return (
    <li className={cx('rounded-xl bg-surface p-3', p.picked && 'ring-1 ring-accent')}>
      <div className="flex items-center gap-3">
        <Link to={`/downloads/${item.id}`} className="flex min-w-0 flex-1 items-center gap-3" aria-label={`Подробности: ${item.artist} — ${item.title}`}>
          <Cover src={item.chosen?.cover_url} size={44} alt="" />
          <span className="min-w-0">
            {name(item)}
            <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11px] text-dim">
              {(active || item.status === 'cancelled') && <StatusChip status={item.status} progress={item.progress} />}
              {item.provider && <span>{providerLabel(item.provider)}</span>}
              <span>{fmtWhen(item.updated_at)}</span>
            </span>
          </span>
        </Link>
        <Checkbox checked={p.picked} onChange={p.onPick} label={`Выбрать «${item.title}»`} />
      </div>
      {item.status === 'downloading' && <ProgressBar value={item.progress} className="mt-2" />}
      <div className="mt-1 space-y-1 empty:hidden">
        <Notes item={item} />
        {isProblem(item) && <Why item={item} open={p.open} onOpen={p.onOpen} />}
      </div>
      <div className="mt-2 flex items-center gap-2">
        {active ? (
          <Button small className="flex-1" icon="x" busy={p.busy === 'cancel'} onClick={p.onCancel}>
            Отменить
          </Button>
        ) : (
          <>
            {p.onPlay && (
              <Button small className="flex-1" icon="play" onClick={p.onPlay}>
                Слушать
              </Button>
            )}
            {item.status !== 'done' && (
              <Button small className="flex-1" icon="refresh" busy={p.busy === 'retry'} onClick={p.onRetry}>
                Повторить
              </Button>
            )}
            {isProblem(item) && (
              <Button small className="flex-1" icon="search" onClick={() => navigate(`/downloads/${item.id}`)}>
                Выбрать файл
              </Button>
            )}
            <IconButton icon="trash" size={18} label={item.status === 'done' ? 'Убрать из списка' : 'Убрать: не скачивать'} busy={p.busy === 'remove'} onClick={p.onRemove} className="!h-9 !w-9 text-muted" />
          </>
        )}
      </div>
    </li>
  );
}
