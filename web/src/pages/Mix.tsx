import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { CatalogArtist } from '../api/types';
import { Icon } from '../components/Icon';
import { Button, Cover, ErrorState, PageHeader, SkeletonRows, cx } from '../components/ui';
import { useAsync, useDebounced } from '../lib/hooks';
import { Link, navigate } from '../lib/router';
import { attempt, toast } from '../lib/ui';

/** «Что вы слушаете?» (MIXES.md 6.4): for a user the Hub knows nothing about yet — the prompt on «Главная» and the
 * page where a few artists are picked. The page of «Микс дня» itself is a list of «Для вас» (`pages/ForYou.tsx`). */

const PICK_MIN = 3;
const PICK_MAX = 5;

/** «Что вы слушаете?» on «Главная» of an account the Hub knows nothing about yet: without a few artists picked there
 * is nothing to make «Микс дня» and «Моя волна» of. Nothing for everybody else. */
export function TastePrompt() {
  const res = useAsync((s) => api.mixToday(s), []);
  if (!res.data?.onboarding || res.data.mix || res.data.building) return null;
  return (
    <Link to="/taste" className="mx-4 mt-3 flex items-center gap-3 rounded-xl bg-accent/15 p-4 transition hover:bg-accent/20">
      <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent">
        <Icon name="sparkle" size={24} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="font-bold">Что вы слушаете?</div>
        <div className="text-[13px] text-muted">Выберите 3–5 исполнителей — соберём для вас «Микс дня»</div>
      </div>
      <Icon name="chevron" size={20} className="shrink-0 text-dim" />
    </Link>
  );
}

/** «Что вы слушаете?»: 3–5 artists for a user without history; the first pick builds the first mix at once. */
export function TastePage() {
  const [q, setQ] = useState('');
  const dq = useDebounced(q.trim(), 400);
  const res = useAsync((s) => api.tasteOnboarding(dq || undefined, s), [dq]);
  const [picked, setPicked] = useState<Map<string, string>>(new Map());
  const [busy, setBusy] = useState(false);
  const [seeded, setSeeded] = useState(false);

  // artists picked earlier come back checked
  useEffect(() => {
    if (seeded || !res.data) return;
    setSeeded(true);
    if (res.data.selected.length) setPicked(new Map(res.data.selected.map((a) => [a.ref, a.name])));
  }, [res.data, seeded]);

  const toggle = (a: CatalogArtist) =>
    setPicked((prev) => {
      const next = new Map(prev);
      if (next.has(a.ref)) next.delete(a.ref);
      else if (next.size >= PICK_MAX) toast(`Не больше ${PICK_MAX} исполнителей`, 'info');
      else next.set(a.ref, a.name);
      return next;
    });

  const submit = async () => {
    setBusy(true);
    const st = await attempt(() => api.pickArtists([...picked.keys()]));
    setBusy(false);
    if (!st) return;
    toast('Собираем ваш «Микс дня» — пара минут', 'success');
    navigate('/search', { replace: true });
  };

  const left = PICK_MIN - picked.size;
  return (
    <div>
      <PageHeader title="Что вы слушаете?" back="/search" />
      <p className="px-4 text-sm text-muted">
        Выберите {PICK_MIN}–{PICK_MAX} исполнителей — по ним соберём ваш первый «Микс дня». Дальше он будет учиться на том, что вы слушаете.
      </p>
      <div className="relative px-4 pt-3">
        <Icon name="search" size={20} className="pointer-events-none absolute left-7 top-1/2 mt-1.5 -translate-y-1/2 text-dim" />
        <input
          type="search"
          enterKeyHint="search"
          className="input h-12 rounded-full pl-11"
          placeholder="Найти исполнителя"
          aria-label="Найти исполнителя"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
        />
      </div>
      {picked.size > 0 && (
        <div className="no-scrollbar flex gap-2 overflow-x-auto px-4 pt-3">
          {[...picked].map(([ref, name]) => (
            <button
              key={ref}
              type="button"
              onClick={() => toggle({ ref, name, source: 'dz', id: ref.slice(3) })}
              className="inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-full bg-accent px-3.5 text-sm font-semibold text-on-accent"
              aria-label={`Убрать ${name}`}
            >
              {name}
              <Icon name="x" size={16} />
            </button>
          ))}
        </div>
      )}

      {res.loading && !res.data ? (
        <SkeletonRows n={6} />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !res.data?.suggestions.length ? (
        <p className="px-4 py-8 text-center text-muted">Никого не нашли — попробуйте другое написание</p>
      ) : (
        <ul className="grid grid-cols-3 gap-x-2 gap-y-4 px-4 pb-4 pt-4">
          {res.data.suggestions.map((a) => {
            const on = picked.has(a.ref);
            return (
              <li key={a.ref}>
                <button type="button" onClick={() => toggle(a)} aria-pressed={on} className="flex w-full flex-col items-center gap-2 text-center">
                  <span className="relative">
                    <Cover src={a.picture_small_url || a.picture_url} size={88} round icon="user" alt={a.name} className={cx('transition', on && 'ring-4 ring-accent')} />
                    {on && (
                      <span className="absolute -bottom-1 -right-1 flex h-7 w-7 items-center justify-center rounded-full bg-accent text-on-accent ring-2 ring-bg">
                        <Icon name="check" size={16} stroke={3} />
                      </span>
                    )}
                  </span>
                  <span className={cx('line-clamp-2 text-sm leading-tight', on && 'font-semibold text-accent')}>{a.name}</span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <div className="sticky bottom-[calc(var(--tabbar-h)+var(--player-h,0px)+env(safe-area-inset-bottom))] z-10 bg-gradient-to-t from-bg via-bg to-transparent px-4 pb-3 pt-6">
        <Button variant="primary" block busy={busy} disabled={left > 0} onClick={submit}>
          {left > 0 ? `Выберите ещё ${left}` : 'Собрать микс'}
        </Button>
      </div>
    </div>
  );
}
