import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { ReleaseOut } from '../api/types';
import { Cover, Empty, ErrorState, Loading, PageHeader, Segmented } from '../components/ui';
import { STANDALONE } from '../lib/embedded';
import { plural, recordTypeLabel } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { setUnseenReleases } from '../lib/releases';
import { Link, enc } from '../lib/router';

type Kind = 'all' | 'album' | 'ep' | 'single';
const KINDS: Array<{ value: Kind; label: string }> = [
  { value: 'all', label: 'Все' },
  { value: 'album', label: 'Альбомы' },
  { value: 'ep', label: 'EP' },
  { value: 'single', label: 'Синглы' },
];
const KEY = 'hm-releases-kind';

function day(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  const days = Math.round((Date.now() - d.getTime()) / 86400000);
  if (days <= 0) return 'сегодня';
  if (days === 1) return 'вчера';
  if (days < 7) return `${days} ${plural(days, ['день', 'дня', 'дней'])} назад`;
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', ...(d.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {}) });
}

function remembered(): Kind {
  try {
    const v = localStorage.getItem(KEY);
    return KINDS.some((k) => k.value === v) ? (v as Kind) : 'all';
  } catch {
    return 'all';
  }
}

/** «Релизы»: what the artists the user follows (♥ on the artist's page) have put out in the last three months,
 * the newest first. The ones not seen before are marked; opening the page makes them seen. */
export function ReleasesPage() {
  const res = useAsync((s) => api.releases(s), []);
  const [kind, setKind] = useState<Kind>(remembered);
  const items = res.data?.items;
  useEffect(() => {
    if (!res.data) return;
    setUnseenReleases(0);
    if (res.data.unseen) void api.releasesSeen().catch(() => undefined);
  }, [res.data]);
  const shown = useMemo(() => (items ?? []).filter((r) => kind === 'all' || r.record_type === kind), [items, kind]);
  const pick = (k: Kind) => {
    setKind(k);
    try {
      localStorage.setItem(KEY, k);
    } catch {
      /* a private window: the filter lasts while the page is open */
    }
  };
  const fresh = res.data?.unseen ?? 0;

  return (
    <div>
      <PageHeader
        title="Релизы"
        back={STANDALONE ? '/home' : '/more'}
        sub={res.data ? [`${res.data.artists} ${plural(res.data.artists, ['исполнитель', 'исполнителя', 'исполнителей'])}`, fresh ? `новых: ${fresh}` : null].filter(Boolean).join(' · ') : undefined}
      />
      {res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !res.data ? (
        <Loading text="Смотрю, что вышло…" />
      ) : !res.data.artists ? (
        <Empty icon="heart" title="Вы пока ни за кем не следите" text="Откройте страницу исполнителя и нажмите ♥ рядом с его именем — его новые альбомы и синглы будут появляться здесь." />
      ) : (
        <>
          <div className="px-4 pb-3">
            <Segmented value={kind} options={KINDS} onChange={pick} label="Тип релиза" />
          </div>
          {!shown.length ? (
            <Empty icon="disc" title="Пока ничего" text={items?.length ? 'Релизов такого типа за последние три месяца не было.' : 'За последние три месяца у ваших исполнителей ничего не вышло. Хаб проверяет каталог дважды в день.'} />
          ) : (
            <div className="grid grid-cols-2 gap-x-4 gap-y-5 px-4 pb-6 min-[520px]:grid-cols-3 min-[900px]:grid-cols-4 min-[1300px]:grid-cols-6">
              {shown.map((r) => (
                <ReleaseTile key={r.album_ref} r={r} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function ReleaseTile({ r }: { r: ReleaseOut }) {
  return (
    <Link to={`/album/${enc(r.album_ref)}`} className="block min-w-0">
      <span className="relative block">
        <Cover src={r.cover_url} alt={r.title} icon="disc" className="aspect-square w-full" />
        {r.new && <span className="absolute left-2 top-2 rounded-full bg-accent px-2 py-0.5 text-[11px] font-bold text-on-accent">новое</span>}
      </span>
      <span className="mt-2 block line-clamp-2 text-sm font-medium leading-tight">{r.title}</span>
      <span className="mt-0.5 block truncate text-xs text-muted">{r.artist}</span>
      <span className="block truncate text-xs text-dim">{[recordTypeLabel(r.record_type), day(r.release_date)].filter(Boolean).join(' · ')}</span>
    </Link>
  );
}
