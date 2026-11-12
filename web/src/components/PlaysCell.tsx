import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { fmtListened, fmtWhen, plural } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Spinner, cx } from './ui';

const times = (n: number) => `${n} ${plural(n, ['раз', 'раза', 'раз'])}`;

/** The last 8 weeks of a track as small bars (the last one is this week): the taller, the more it was listened to. */
export function Trend({ weeks, className, wide }: { weeks: readonly number[]; className?: string; wide?: boolean }) {
  const max = Math.max(1, ...weeks);
  return (
    <span className={cx('flex items-end', wide ? 'h-14 gap-1.5' : 'h-5 gap-0.5', className)} role="img" aria-label={`По неделям: ${weeks.join(', ')}`}>
      {weeks.map((v, i) => (
        <span
          key={i}
          title={wide ? `${i === weeks.length - 1 ? 'Эта неделя' : `${weeks.length - 1 - i} нед. назад`}: ${times(v)}` : undefined}
          className={cx('rounded-sm', wide ? 'flex-1' : 'w-1', v ? 'bg-accent' : 'bg-fg/15')}
          style={{ height: v ? `${Math.max(12, (v / max) * 100)}%` : 2 }}
        />
      ))}
    </span>
  );
}

/** The «Прослушивания» cell of a track row: the number; a click opens a small window with the track's last 8 weeks,
 * all its listens and time, the first and the last of them. */
export function PlaysCell({ songId, count, title, artist }: { songId?: string | null; count: number; title: string; artist?: string | null }) {
  const [open, setOpen] = useState(false);
  const [up, setUp] = useState(false);
  const [left, setLeft] = useState(false); // no room to the right: the window opens to the left of the number
  const ref = useRef<HTMLSpanElement>(null);
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
  if (!songId || !count) return <span className="text-sm text-dim">—</span>;
  return (
    <span ref={ref} className="relative">
      <button
        type="button"
        aria-expanded={open}
        title="Когда и сколько вы слушали этот трек"
        onClick={(e) => {
          e.stopPropagation();
          const box = e.currentTarget.getBoundingClientRect();
          setUp(box.top > window.innerHeight * 0.55);
          setLeft(box.left + 304 > window.innerWidth);
          setOpen((v) => !v);
        }}
        className={cx('rounded px-1.5 py-0.5 text-sm tabular-nums transition hover:bg-fg/10 hover:text-fg', open ? 'bg-fg/10 text-fg' : 'text-muted')}
      >
        {count}
      </button>
      {open && <Details songId={songId} title={title} artist={artist} up={up} left={left} />}
    </span>
  );
}

function Details({ songId, title, artist, up, left }: { songId: string; title: string; artist?: string | null; up: boolean; left: boolean }) {
  const res = useAsync(() => api.statsTrack(songId), [songId]);
  const d = res.data;
  return (
    <div role="dialog" aria-label={`Прослушивания: ${title}`} onClick={(e) => e.stopPropagation()} className={cx('absolute z-30 w-72 cursor-default rounded-xl border border-line bg-raised p-3 text-left shadow-lg', up ? 'bottom-8' : 'top-8', left ? 'right-0' : 'left-0')}>
      <div className="truncate text-sm font-semibold text-fg">{title}</div>
      {artist && <div className="truncate text-xs text-muted">{artist}</div>}
      {!d ? (
        <div className="flex h-24 items-center justify-center text-muted">{res.error ? <span className="text-xs">{res.error}</span> : <Spinner size={18} />}</div>
      ) : (
        <>
          <Trend weeks={d.weeks} wide className="mt-3" />
          <div className="flex justify-between pb-2 pt-1 text-[11px] text-dim">
            <span>8 недель назад</span>
            <span>эта неделя</span>
          </div>
          <dl className="space-y-1 text-xs">
            <Line label="Всего" value={`${times(d.plays)} · ${fmtListened(d.ms)}`} />
            <Line label="Первый раз" value={d.first_at ? new Date(d.first_at).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' }) : '—'} />
            <Line label="Последний раз" value={d.last_at ? fmtWhen(d.last_at) : '—'} />
          </dl>
        </>
      )}
    </div>
  );
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt className="text-muted">{label}</dt>
      <dd className="text-fg">{value}</dd>
    </div>
  );
}
