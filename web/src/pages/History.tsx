import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { HistoryItem, HistoryOut } from '../api/types';
import { listenTrack } from '../components/catalog';
import { SaveButton } from '../components/PlaylistsButton';
import { ListBar } from '../components/ListBar';
import { useSearchField } from '../components/ListSearch';
import { Cover, Empty, ErrorState, IconButton, Segmented, SkeletonRows, Spinner, cx } from '../components/ui';
import { useDesktop } from '../lib/desktop';
import { fmtDuration, fmtLength, tracksWord } from '../lib/format';
import { useDebounced } from '../lib/hooks';
import { listen, useListen, type ListenTrack } from '../lib/listen';
import { Link } from '../lib/router';
import { tagKey } from '../lib/tags';
import { attempt, toast } from '../lib/ui';

/** «История» (from «Меню», under «Статистика»): what was listened to, one listen after another, the newest first —
 * by days, each with its total. A listen says when it was, where it was played from (a link to the list, when the
 * Hub knows which) and how much of the track was heard; one left early is dimmed. A listen of a few seconds is not
 * here: that is leafing through a queue. A row plays its track; × takes the listen out of the history — and of the
 * statistics and of what the recommendations go by. */

type Show = 'all' | 'finished' | 'skipped';
const SHOWS: Array<{ value: Show; label: string }> = [
  { value: 'all', label: 'Всё' },
  { value: 'finished', label: 'Дослушано' },
  { value: 'skipped', label: 'Пропущено' },
];
const PAGE = 100;

const when = (iso: string) => new Date(iso + (/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? '' : 'Z'));
const clock = (iso: string) => when(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });

/** «Сегодня», «Вчера», «12 ноября», and the year when it is not this one. */
function dayTitle(day: string): string {
  const d = new Date(`${day}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const ago = Math.round((today.getTime() - d.getTime()) / 86400000);
  if (ago === 0) return 'Сегодня';
  if (ago === 1) return 'Вчера';
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', ...(d.getFullYear() !== today.getFullYear() ? { year: 'numeric' } : {}) });
}

/** What a row plays: the library's track, a catalog one by its ref, or nothing (a line of another service's history
 * the library has not got). */
const playable = (x: HistoryItem): ListenTrack | null => (x.track ? listenTrack(x.track) : x.ref ? { ref: x.ref, title: x.title, artist: x.artist } : null);

export function HistoryPage() {
  const desktop = useDesktop();
  const [show, setShow] = useState<Show>('all');
  // the same search as a list's: the magnifier of the bar, there while the page is scrolled
  const sf = useSearchField('Название или исполнитель');
  const dq = useDebounced(sf.q.trim(), 350);
  const [data, setData] = useState<HistoryOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [more, setMore] = useState(false);
  const asked = useRef(0);
  const playing = useListen((s) => s.track?.ref ?? null);

  // a filter or a search starts the list over
  useEffect(() => {
    const mine = ++asked.current;
    setData(null);
    setError(null);
    api.playsHistory({ offset: 0, limit: PAGE, show, q: dq })
      .then((r) => mine === asked.current && setData(r))
      .catch((e) => mine === asked.current && setError(e instanceof Error ? e.message : 'Не удалось загрузить историю'));
  }, [show, dq]);

  const loadMore = async () => {
    if (!data || data.next_offset === null || data.next_offset === undefined || more) return;
    const mine = asked.current;
    setMore(true);
    const r = await attempt(() => api.playsHistory({ offset: data.next_offset as number, limit: PAGE, show, q: dq }));
    setMore(false);
    if (r && mine === asked.current) setData((prev) => (prev ? { ...r, items: [...prev.items, ...r.items], days: { ...prev.days, ...r.days } } : r));
  };
  // the next part comes as the end of the list comes into view
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = end.current;
    if (!el) return;
    const io = new IntersectionObserver((xs) => xs.some((x) => x.isIntersecting) && void loadMore(), { rootMargin: '600px' });
    io.observe(el);
    return () => io.disconnect();
  });

  const forget = async (x: HistoryItem) => {
    if (!(await attempt(() => api.forgetListen(x.uid)))) return;
    toast('Убрано из истории', 'success');
    setData((prev) => {
      if (!prev) return prev;
      const day = prev.days[x.day];
      return { ...prev, total: prev.total - 1, items: prev.items.filter((i) => i.uid !== x.uid), days: day ? { ...prev.days, [x.day]: { count: day.count - 1, ms: day.ms - x.ms } } : prev.days };
    });
  };

  const items = data?.items ?? [];
  const tracks = items.map(playable).filter((t): t is ListenTrack => !!t);
  const play = (x: HistoryItem) => {
    const t = playable(x);
    if (t) listen(t, tracks, 'history');
  };
  const cols = 'grid grid-cols-[3rem_minmax(0,1.5fr)_minmax(0,1fr)_7.5rem_5rem] items-center gap-3';

  return (
    <div>
      <ListBar plain title="История" back="/more" search={{ found: null, open: sf.open, alone: sf.alone, show: sf.show, close: sf.close, field: sf.field(data?.total) }} />
      <div className="flex flex-wrap items-center gap-3 px-4 pb-2 lg:px-6">
        <Segmented<Show> label="Что показывать" value={show} onChange={setShow} options={SHOWS} />
        {data && data.total > 0 && <span className="ml-auto text-xs tabular-nums text-dim">{data.total.toLocaleString('ru-RU')}</span>}
      </div>

      {error ? (
        <ErrorState text={error} onRetry={() => setShow((v) => v)} />
      ) : !data ? (
        <SkeletonRows n={8} />
      ) : !items.length ? (
        <Empty icon="history" title={dq || show !== 'all' ? 'Ничего не нашлось' : 'Истории пока нет'} text={dq || show !== 'all' ? 'Попробуйте другой запрос или фильтр.' : 'Здесь появится всё, что вы слушаете, по порядку.'} />
      ) : (
        <div className="pb-4">
          {items.map((x, i) => {
            const head = i === 0 || items[i - 1].day !== x.day;
            const total = data.days[x.day];
            const t = playable(x);
            const on = !!t && t.ref === playing;
            const share = x.duration_ms ? Math.min(1, x.ms / x.duration_ms) : x.finished ? 1 : 0;
            const from = x.from ? (
              x.from_to ? (
                <Link to={x.from_to} onClick={(e) => e.stopPropagation()} className="hover:text-fg hover:underline">
                  {x.from}
                </Link>
              ) : (
                x.from
              )
            ) : null;
            const name = (
              <span className="flex min-w-0 items-center gap-3">
                <Cover src={x.track?.cover_small_url || x.track?.cover_url} size={desktop ? 36 : 44} alt="" />
                <span className="min-w-0">
                  <span className={cx('block truncate', desktop ? 'text-sm' : 'text-[15px]', on && 'text-accent')}>{x.title}</span>
                  <span className="block truncate text-xs text-muted">
                    {x.artist}
                    {!desktop && from && <> · {from}</>}
                  </span>
                </span>
              </span>
            );
            const tools = (
              <span className="flex shrink-0 items-center justify-end" onClick={(e) => e.stopPropagation()}>
                {x.track && <SaveButton songId={x.track.nd_song_id} refId={x.track.ref} listKey={tagKey(x.track)} title={x.title} data={{ ...x.track }} className="!w-9" />}
                <IconButton icon="x" size={16} label="Убрать из истории" onClick={() => void forget(x)} className="!w-9 text-dim hover:text-danger" />
              </span>
            );
            return (
              <div key={x.uid}>
                {head && (
                  <>
                    <div className="flex items-baseline justify-between px-4 pb-1.5 pt-5 lg:px-6">
                      <h2 className="text-[13px] font-semibold uppercase tracking-wide text-muted">{dayTitle(x.day)}</h2>
                      {total && <span className="text-xs text-dim">{tracksWord(total.count)} · {fmtLength(Math.round(total.ms / 1000))}</span>}
                    </div>
                    {desktop && (
                      <div className={cx(cols, 'mx-2 mb-1 h-8 border-b border-line/60 px-4 text-xs text-dim')}>
                        <span>Время</span>
                        <span>Название</span>
                        <span>Откуда</span>
                        <span>Слушал</span>
                        <span />
                      </div>
                    )}
                  </>
                )}
                <div
                  role={t ? 'button' : undefined}
                  tabIndex={t ? 0 : undefined}
                  onClick={() => play(x)}
                  onKeyDown={(e) => e.key === 'Enter' && play(x)}
                  title={t ? undefined : 'Этого трека нет в библиотеке'}
                  className={cx('mx-2 rounded-lg px-2 lg:px-4', t && 'cursor-pointer hover:bg-fg/5', !x.finished && 'opacity-55', desktop ? cx(cols, 'min-h-12') : 'flex min-h-14 items-center gap-3')}
                >
                  <span className={cx('shrink-0 text-xs tabular-nums text-dim', !desktop && 'w-9')}>{clock(x.ts)}</span>
                  {desktop ? name : <span className="min-w-0 flex-1">{name}</span>}
                  {desktop && <span className="truncate text-sm text-muted">{from}</span>}
                  {desktop ? (
                    <span className="flex items-center gap-2 text-xs tabular-nums text-muted" title={x.finished ? 'Дослушан' : 'Пропущен'}>
                      <span className="relative h-1 flex-1 overflow-hidden rounded-full bg-fg/15">
                        <span className={cx('absolute inset-y-0 left-0 rounded-full', x.finished ? 'bg-accent' : 'bg-warn')} style={{ width: `${Math.round(share * 100)}%` }} />
                      </span>
                      {fmtDuration(Math.round(x.ms / 1000))}
                    </span>
                  ) : (
                    !x.finished && <span className="shrink-0 text-[11px] tabular-nums text-warn" title="Пропущен на этой секунде">{fmtDuration(Math.round(x.ms / 1000))}</span>
                  )}
                  {tools}
                </div>
              </div>
            );
          })}
          <div ref={end} className="flex h-12 items-center justify-center text-muted">
            {more && <Spinner size={18} />}
          </div>
        </div>
      )}
    </div>
  );
}
