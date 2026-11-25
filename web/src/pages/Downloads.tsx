import { titleWithVersion } from '../lib/versionLabels';
import { TrackTitle } from '../components/TrackTitle';
import { TrackCell } from '../components/Track';
import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api } from '../api/client';
import type { DownloadGroupOut, DownloadItemOut } from '../api/types';
import { Icon, type IconName } from '../components/Icon';
import { ServiceBadge, ServiceIcon, ServiceText } from '../components/ServiceBadge';
import { ListBar } from '../components/ListBar';
import { useSearchField } from '../components/ListSearch';
import { Button, Checkbox, Cover, Empty, ErrorState, IconButton, ProgressBar, Segmented, SkeletonRows, StatusChip, cx } from '../components/ui';
import { useDesktop } from '../lib/desktop';
import { SOURCES } from './Import';
import { useHubConnected, useHubEvent } from '../lib/events';
import { DOWNLOAD_ACTIVE, fmtWhen, plural, warningText } from '../lib/format';
import { useAsync, useInterval } from '../lib/hooks';
import { canPlay, playList } from '../lib/player';
import { Link, navigate, useRoute } from '../lib/router';
import { attempt, confirmDialog, toast } from '../lib/ui';

type Tab = 'now' | 'review' | 'done';

const isProblem = (x: DownloadItemOut) => x.status === 'failed' || x.status === 'needs_review';
const tabOf = (x: DownloadItemOut): Tab => (DOWNLOAD_ACTIVE.includes(x.status) ? 'now' : isProblem(x) ? 'review' : 'done');
const TAB_STATUS: Record<Tab, string> = { now: DOWNLOAD_ACTIVE.join(','), review: 'failed,needs_review', done: 'done,cancelled' };
const TABS: Tab[] = ['now', 'review', 'done'];
const GROUP_PAGE = 5; // a group opens with so many of its downloads; «Показать ещё» brings more
const MORE_PAGE = 50;
const downloadsWord = (n: number) => `${n} ${plural(n, ['загрузку', 'загрузки', 'загрузок'])}`;
const tracksWord = (n: number) => `${n.toLocaleString('ru')} ${plural(n, ['трек', 'трека', 'треков'])}`;

/** What went wrong, in a line that says what to do; the source's own words are a click away (`item.error`). */
function reason(item: DownloadItemOut): string {
  const e = item.error ?? '';
  if (!e) return item.status === 'needs_review' ? 'Хаб не уверен в найденном — выберите файл' : 'Не удалось скачать';
  if (/HTTP Error 403/i.test(e)) return 'Источник не отдал файл — обычно проходит при повторе';
  if (/не совпадает с ожидаемой/i.test(e)) return 'Длительность найденного файла не совпала — выберите файл';
  if (/^Не найдено ни в одном источнике/i.test(e)) return 'Не нашлось ни в одном источнике — нужна своя ссылка';
  if (/^(Ни один кандидат не подошёл|Файл не подошёл)/i.test(e)) return 'Найденные файлы не подошли — выберите файл или вставьте ссылку';
  if (/все источники недоступны/i.test(e)) return 'Источники не отвечали — повторите позже';
  return e.length > 90 ? `${e.slice(0, 90)}…` : e;
}

const HINTS: Record<Tab, string> = {
  now: 'Всё, что хаб качает прямо сейчас: ваши загрузки, переносы и миксы. Делать ничего не нужно.',
  review: 'Без вас это не скачается: выберите файл, вставьте ссылку или повторите позже.',
  done: 'Что скачалось. Строки можно убрать — треки останутся в медиатеке.',
};

/** «Загрузки»: one place for everything the Hub downloads. Three sections — under way, waiting for the user, done.
 * A transfer, a mix, the wave and the better files are a group each (a transfer alone has thousands of downloads:
 * the group shows its counts and opens on a few, by pages); the user's own downloads are a list under them, a table
 * on a computer and cards on a phone, with ticks for several at once. `?import=<id>` shows one transfer's group
 * alone (its page links here), `?tab=` opens a section. */
export function DownloadsPage() {
  const desktop = useDesktop();
  const route = useRoute();
  const onlyImport = Number(route.query.get('import')) || null;
  const asked = route.query.get('tab');
  const res = useAsync((s) => api.downloads(undefined, 5000, s, true), []);
  const overview = useAsync((s) => api.downloadsOverview(s), []);
  const connected = useHubConnected();
  const [tab, setTab] = useState<Tab | null>(TABS.includes(asked as Tab) ? (asked as Tab) : null);
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState<Set<number>>(new Set()); // rows whose full error is shown

  // Live updates: upsert the item; unknown items (new batches) are prepended; one taken off the list is dropped.
  // The groups' counts follow by a fresh overview, at most once a second and a half.
  const dirty = useRef(false);
  useHubEvent('download', (d) => {
    dirty.current = true;
    if (d.quiet) return;
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
  useHubEvent('import', () => (dirty.current = true));
  useHubEvent('reconnect', () => {
    res.reload(true);
    dirty.current = true;
  });
  useInterval(() => {
    if (!dirty.current || overview.loading || document.visibilityState === 'hidden') return;
    dirty.current = false;
    overview.reload(true);
  }, 1500);

  // the bar's search: by the track's name, its artist and its album — the user's own downloads
  const sf = useSearchField('Название, исполнитель или альбом');
  const needle = sf.q.trim().toLocaleLowerCase('ru');
  const items = useMemo(
    () => (res.data ?? []).filter((x) => !x.quiet && (!needle || `${x.title} ${x.artist} ${x.album ?? ''}`.toLocaleLowerCase('ru').includes(needle))),
    [res.data, needle],
  );
  const byTab = useMemo(() => {
    const out: Record<Tab, DownloadItemOut[]> = { now: [], review: [], done: [] };
    for (const x of onlyImport ? [] : items) out[tabOf(x)].push(x);
    return out;
  }, [items, onlyImport]);
  const groups = useMemo(
    () => (overview.data?.groups ?? []).filter((g) => g.key !== 'own' && (!onlyImport || g.import_id === onlyImport)),
    [overview.data, onlyImport],
  );
  const count = (t: Tab) => byTab[t].length + (needle ? 0 : groups.reduce((n, g) => n + g[t], 0));
  // the first look: what needs the user, else what is going on now, else the finished ones
  const loaded = !!res.data && !!overview.data;
  const first: Tab = count('review') ? 'review' : count('now') ? 'now' : 'done';
  useEffect(() => {
    if (tab === null && loaded) setTab(first); // kept from then on: the page does not jump as downloads finish
  }, [tab, loaded, first]);
  const at: Tab = tab ?? first;
  const shown = byTab[at];
  const shownGroups = needle ? [] : groups.filter((g) => g[at] > 0);
  const chosen = shown.filter((x) => picked.has(x.id));

  const choose = (next: Tab) => {
    setTab(next);
    setPicked(new Set());
  };
  const merge = (r: DownloadItemOut) => res.setData((prev) => prev?.map((x) => (x.id === r.id ? { ...x, ...r, candidates: x.candidates } : x)));
  const one = async (item: DownloadItemOut, kind: 'retry' | 'cancel', done: (r: DownloadItemOut) => void) => {
    setBusy(`${kind}:${item.id}`);
    const r = await attempt(() => (kind === 'retry' ? api.retry(item.id) : api.cancel(item.id)));
    setBusy(null);
    if (r) done(r);
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
  const retryGroup = async (key: string) => {
    setBusy(`group:${key}`);
    const r = await attempt(() => api.retryDownloadGroup(key));
    setBusy(null);
    if (!r) return;
    toast(`Ищу заново: ${downloadsWord(r.queued)}`, 'success');
    overview.reload(true);
  };
  const remove = async (list: DownloadItemOut[], key: string) => {
    if (!list.length) return;
    const unfinished = list.filter((x) => x.status !== 'done' && x.status !== 'cancelled').length;
    if (list.length > 1 || unfinished) {
      const ok = await confirmDialog({
        title: list.length > 1 ? `Убрать ${downloadsWord(list.length)}?` : `Убрать «${titleWithVersion(list[0].title, list[0])}»?`,
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

  /** A row's props; `among`: the list it plays with, `update`: where the answer of its action goes. */
  const rowFor = (item: DownloadItemOut, among: DownloadItemOut[], update: (r: DownloadItemOut) => void, pickable: boolean): RowProps => ({
    item,
    picked: pickable ? picked.has(item.id) : undefined,
    onPick: pickable ? (v) => pick(item.id, v) : undefined,
    busy: busy?.endsWith(`:${item.id}`) ? busy.split(':')[0] : null,
    open: open.has(item.id),
    onOpen: () => toggleOpen(item.id),
    onRetry: () => void one(item, 'retry', update),
    onCancel: () => void one(item, 'cancel', update),
    onRemove: () => void remove([item], `remove:${item.id}`),
    onPlay: canPlay() && item.status === 'done' && item.nd_song_id
      ? () => playList(among.filter((x) => x.status === 'done' && x.nd_song_id), (x) => x.nd_song_id, item)
      : undefined,
  });

  const tabs: Array<{ value: Tab; label: string }> = TABS.map((t) => {
    const n = count(t);
    const label = { now: 'Сейчас', review: 'Нужен выбор', done: 'Готовые' }[t];
    return { value: t, label: n ? `${label} · ${n.toLocaleString('ru')}` : label };
  });
  const empty: Record<Tab, [string, string]> = {
    now: ['Сейчас ничего не качается', 'Найдите трек или альбом в поиске и нажмите «Скачать».'],
    review: ['Ничего не ждёт вашего выбора', 'Здесь появляются загрузки, которым нужен ваш выбор: файл или ссылка.'],
    done: ['Готовых загрузок нет', 'Скачанные треки остаются здесь, пока вы их не уберёте из списка.'],
  };
  const filteredGroup = onlyImport ? (overview.data?.groups ?? []).find((g) => g.import_id === onlyImport) : null;

  return (
    <div className="pb-6">
      <ListBar plain title="Загрузки" back="/more" search={{ found: null, open: sf.open, alone: sf.alone, show: sf.show, close: sf.close, field: sf.field(needle ? items.length : null) }} />
      {/* the list keeps itself up to date over a live line; said only when the line is down */}
      {!connected && !res.error && !res.loading && <p className="px-4 pb-2 text-xs text-muted lg:px-6">Нет связи с сервером — переподключаюсь, список может отставать.</p>}
      <div className="flex flex-wrap items-center gap-2 px-4 pb-1">
        <Segmented<Tab> label="Раздел" value={at} onChange={choose} options={tabs} />
        {onlyImport && (
          <button
            type="button"
            onClick={() => navigate(`/downloads?tab=${at}`, { replace: true })}
            className="flex min-h-9 items-center gap-1.5 rounded-full border border-line px-3.5 text-sm hover:bg-hover"
            title="Показать все загрузки"
          >
            Только {groupTitle(filteredGroup ?? null).toLocaleLowerCase('ru')}
            <Icon name="x" size={16} />
          </button>
        )}
      </div>
      <p className="px-4 pb-3 pt-1 text-xs text-muted">{HINTS[at]}</p>

      {res.loading || overview.loading ? (
        <SkeletonRows />
      ) : res.error || overview.error ? (
        <ErrorState text={res.error ?? overview.error ?? ''} onRetry={() => { res.reload(); overview.reload(); }} />
      ) : shown.length === 0 && !shownGroups.length && needle ? (
        <Empty icon="search" title="Ничего не найдено" text="В этом разделе таких загрузок нет — посмотрите в соседних." />
      ) : shown.length === 0 && !shownGroups.length ? (
        <Empty icon="download" title={empty[at][0]} text={empty[at][1]}>
          {at === 'now' && !items.length && (
            <Link to="/search" className="mt-2 rounded-full bg-fg px-5 py-2.5 font-semibold text-bg">
              Перейти к поиску
            </Link>
          )}
        </Empty>
      ) : (
        <div className="space-y-3">
          {shownGroups.map((g) => (
            <Group
              key={`${at}:${g.key}`}
              group={g}
              tab={at}
              desktop={desktop}
              rowFor={rowFor}
              retrying={busy === `group:${g.key}`}
              onRetryAll={() => void retryGroup(g.key)}
            />
          ))}
          {shown.length > 0 && (
            <section aria-label="Вы скачивали сами">
              <div className="flex flex-wrap items-center gap-2 px-4 pb-2">
                {shownGroups.length > 0 && <h2 className="font-semibold">Вы скачивали сами</h2>}
                <span className="flex-1" />
                {at === 'review' && (
                  <Button small icon="refresh" busy={busy === 'retry:all'} onClick={() => void retry(shown, 'retry:all')}>
                    Повторить все
                  </Button>
                )}
                {at !== 'now' && (
                  <Button small icon="trash" busy={busy === 'remove:all'} onClick={() => void remove(shown, 'remove:all')}>
                    Убрать все
                  </Button>
                )}
              </div>
              {desktop ? (
                <div className="mx-4 overflow-hidden rounded-2xl bg-surface" role="table" aria-label="Ваши загрузки">
                  <div role="row" className={cx(GRID, 'h-9 border-b border-line/60 text-xs text-dim')}>
                    <Checkbox checked={chosen.length === shown.length} onChange={(v) => setPicked(v ? new Set(shown.map((x) => x.id)) : new Set())} label="Выбрать все" />
                    <span>Трек</span>
                    <span>Альбом</span>
                    <span>Источник</span>
                    <span>{at === 'now' ? 'Состояние' : 'Когда'}</span>
                    <span />
                  </div>
                  {shown.map((item) => (
                    <TableRow key={item.id} {...rowFor(item, shown, merge, true)} />
                  ))}
                </div>
              ) : (
                <ul className="space-y-2 px-4">
                  {shown.map((item) => (
                    <Card key={item.id} {...rowFor(item, shown, merge, true)} />
                  ))}
                </ul>
              )}
            </section>
          )}
        </div>
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

function groupTitle(g: DownloadGroupOut | null): string {
  if (!g || g.origin === 'import') {
    const page = g?.source ? SOURCES[g.source]?.page : null;
    return page ? `${page}${g?.title ? ` · ${g.title}` : ''}` : 'Перенос музыки';
  }
  return { mix: 'Микс дня', wave: 'Моя волна', upgrade: 'Замена файлов на лучшие' }[g.origin ?? 'mix'] ?? 'Загрузки хаба';
}

const GROUP_ICON: Record<string, IconName> = { import: 'transfer', mix: 'sparkle', wave: 'wave', upgrade: 'refresh' };

/** A group of «Загрузки» in one section: its counts in the head, its downloads under it by pages. Kept up to date
 * by the live line: a row changes in place and leaves when it leaves the section; a new count brings the first page
 * again (as many as are open). */
function Group({ group, tab, desktop, rowFor, retrying, onRetryAll }: {
  group: DownloadGroupOut;
  tab: Tab;
  desktop: boolean;
  rowFor: (item: DownloadItemOut, among: DownloadItemOut[], update: (r: DownloadItemOut) => void, pickable: boolean) => RowProps;
  retrying: boolean;
  onRetryAll: () => void;
}) {
  const [open, setOpen] = useState(tab !== 'done');
  const [rows, setRows] = useState<DownloadItemOut[] | null>(null);
  const [loading, setLoading] = useState(false);
  const total = group[tab];
  const want = useRef(GROUP_PAGE);

  useEffect(() => {
    if (!open) return;
    const ctl = new AbortController();
    setLoading(true);
    api.downloadGroup(group.key, TAB_STATUS[tab], want.current, undefined, ctl.signal)
      .then((list) => setRows(list))
      .catch(() => undefined)
      .finally(() => !ctl.signal.aborted && setLoading(false));
    return () => ctl.abort();
  }, [open, group.key, tab, total]);

  useHubEvent('download', (d) => {
    setRows((prev) => {
      if (!prev) return prev;
      const i = prev.findIndex((x) => x.id === d.id);
      if (i < 0) return prev;
      if (d.hidden || tabOf(d) !== tab) return prev.filter((x) => x.id !== d.id);
      const next = prev.slice();
      next[i] = { ...prev[i], ...d };
      return next;
    });
  });

  const more = async () => {
    if (!rows?.length) return;
    setLoading(true);
    const next = await attempt(() => api.downloadGroup(group.key, TAB_STATUS[tab], MORE_PAGE, rows[rows.length - 1].id));
    setLoading(false);
    if (!next) return;
    setRows((prev) => [...(prev ?? []), ...next]);
    want.current += next.length;
  };
  const update = (r: DownloadItemOut) => setRows((prev) => prev?.map((x) => (x.id === r.id ? { ...x, ...r } : x)) ?? prev);

  const all = group.now + group.review + group.done;
  const sub = group.origin === 'import'
    ? tab === 'now'
      ? `скачано ${group.done.toLocaleString('ru')} из ${all.toLocaleString('ru')} · качается и ждёт очереди ${group.now.toLocaleString('ru')}`
      : tab === 'review'
        ? `скачано ${group.done.toLocaleString('ru')} из ${all.toLocaleString('ru')}`
        : tracksWord(group.done)
    : `${tracksWord(total)}${tab === 'now' ? ' в работе' : ''}`;
  const left = total - (rows?.length ?? 0);

  return (
    <section aria-label={groupTitle(group)} className="mx-4 overflow-hidden rounded-2xl bg-surface max-sm:mx-2">
      <div className="flex flex-wrap items-center gap-3 p-3 sm:px-4">
        <button type="button" onClick={() => setOpen((v) => !v)} aria-expanded={open} className="flex min-w-0 flex-1 basis-60 items-center gap-3 text-left">
          <span className={cx('flex h-11 w-11 shrink-0 items-center justify-center rounded-xl', group.origin === 'import' ? 'bg-accent/15 text-accent' : 'bg-fg/10 text-muted')}>
            {group.origin === 'import' && group.source ? <ServiceIcon service={group.source} size={22} /> : <Icon name={GROUP_ICON[group.origin ?? 'mix'] ?? 'download'} size={22} />}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-semibold">{groupTitle(group)}</span>
            <span className="block text-sm text-muted">
              {sub}
              {tab === 'review' && <span className="text-warn"> · не скачалось {group.review.toLocaleString('ru')}</span>}
            </span>
            {group.origin === 'import' && tab === 'now' && all > 0 && <ProgressBar label={`Перенос: скачано ${group.done} из ${all}`} value={group.done / all} className="mt-2 max-w-md" />}
          </span>
          <Icon name="down" size={18} className={cx('shrink-0 text-dim transition-transform', open && 'rotate-180')} />
        </button>
        {(tab === 'review' || group.import_id) && (
          <span className="flex items-center gap-2 max-sm:w-full">
            {tab === 'review' && (
              <Button small icon="refresh" busy={retrying} onClick={onRetryAll} className="max-sm:flex-1">
                Повторить все
              </Button>
            )}
            {group.import_id && (
              <Link to={`/import/${group.import_id}`} className="flex min-h-9 items-center rounded-full px-3 text-sm text-muted hover:bg-hover hover:text-fg">
                К переносу
              </Link>
            )}
          </span>
        )}
      </div>
      {open && (
        <div className="border-t border-line/60">
          {rows === null ? (
            <SkeletonRows />
          ) : desktop ? (
            <div role="table" aria-label={groupTitle(group)}>
              {rows.map((item) => <TableRow key={item.id} {...rowFor(item, rows, update, false)} />)}
            </div>
          ) : (
            <ul className="space-y-1.5 p-1.5">
              {rows.map((item) => <Card key={item.id} flat {...rowFor(item, rows, update, false)} />)}
            </ul>
          )}
          {rows !== null && left > 0 && (
            <div className="flex items-center gap-3 px-4 pb-3 pt-1 text-sm text-muted">
              <span>Ещё {left.toLocaleString('ru')}{tab === 'now' && group.origin === 'import' ? ' — пойдут по порядку' : ''}</span>
              <Button small busy={loading} onClick={() => void more()}>
                Показать ещё
              </Button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

const GRID = 'grid grid-cols-[2.75rem_minmax(0,2.4fr)_minmax(0,1.4fr)_6rem_9rem_8.5rem] items-center gap-3 pr-2';

interface RowProps {
  item: DownloadItemOut;
  /** a row of the user's own list can be ticked; a group's cannot */
  picked?: boolean;
  onPick?: (v: boolean) => void;
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
          <span className="truncate"><ServiceText text={short} /></span>
          <Icon name={open ? 'down' : 'chevron'} size={12} className="shrink-0" />
        </button>
      ) : (
        <span className="block truncate text-warn"><ServiceText text={short} /></span>
      )}
      {open && full && <div className="mt-1 select-text whitespace-pre-wrap break-words rounded-md bg-fg/5 px-2 py-1.5 font-mono text-[11px] text-muted"><ServiceText text={full} /></div>}
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
          <span className="truncate"><ServiceText text={w} /></span>
        </div>
      ))}
    </>
  );
}

const name = (item: DownloadItemOut): ReactNode => (
  <>
    <span className="block truncate text-sm text-fg"><TrackTitle title={item.title} track={{ ...item, page_url: item.chosen?.page_url }} /></span>
    <span className="block truncate text-xs text-muted">{item.artist}</span>
  </>
);

function TableRow(p: RowProps) {
  const { item } = p;
  const active = DOWNLOAD_ACTIVE.includes(item.status);
  return (
    <div role="row" className={cx('border-b border-line/60 py-1.5 last:border-0', p.picked && 'bg-accent/10')}>
      <div className={GRID}>
        {p.onPick ? <Checkbox checked={!!p.picked} onChange={p.onPick} label={`Выбрать «${item.title}»`} /> : <span />}
        <Link to={`/downloads/${item.id}`} className="flex min-w-0 items-center gap-3" aria-label={`Подробности: ${item.artist} — ${item.title}`}>
          <TrackCell track={{ title: item.title, artist: item.artist, cover: item.chosen?.cover_url, identity: { ...item, page_url: item.chosen?.page_url } }} size={40} />
        </Link>
        <span className="truncate text-sm text-muted">{item.album}</span>
        <span className="truncate text-xs text-muted">{item.provider ? <ServiceBadge service={item.provider} /> : '—'}</span>
        <span className="min-w-0 text-xs text-dim">
          {active || item.status === 'cancelled' ? <StatusChip status={item.status} progress={item.progress} /> : fmtWhen(item.updated_at)}
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

function Card(p: RowProps & { flat?: boolean }) {
  const { item } = p;
  const active = DOWNLOAD_ACTIVE.includes(item.status);
  return (
    <li className={cx('rounded-2xl p-3', p.flat ? 'bg-fg/[0.04]' : 'bg-surface', p.picked && 'ring-1 ring-accent')}>
      <div className="flex items-center gap-3">
        <Link to={`/downloads/${item.id}`} className="flex min-w-0 flex-1 items-center gap-3" aria-label={`Подробности: ${item.artist} — ${item.title}`}>
          <Cover src={item.chosen?.cover_url} size={48} alt="" />
          <span className="min-w-0">
            {name(item)}
            <span className="mt-0.5 flex flex-wrap items-center gap-x-1.5 text-[11px] text-dim">
              {(active || item.status === 'cancelled') && <StatusChip status={item.status} progress={item.progress} />}
              {item.provider && <ServiceBadge service={item.provider} />}
              <span>{fmtWhen(item.updated_at)}</span>
            </span>
          </span>
        </Link>
        {p.onPick && <Checkbox checked={!!p.picked} onChange={p.onPick} label={`Выбрать «${item.title}»`} />}
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
              <Button small className="flex-1 whitespace-nowrap" icon="search" onClick={() => navigate(`/downloads/${item.id}`)}>
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
