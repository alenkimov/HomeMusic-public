import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { CatalogArtist } from '../api/types';
import { ArtistMap, ArtistMapPanel, ArtistsViewSwitch, useArtistMap, useArtistsView } from '../components/ArtistMap';
import { Icon } from '../components/Icon';
import { ArtistCard } from '../components/Artist';
import { Button, ErrorState, PageHeader, SkeletonRows } from '../components/ui';
import { useAsync, useDebounced } from '../lib/hooks';
import { navigate, useRoute } from '../lib/router';
import { tastePicked } from '../lib/taste';
import { attempt, toast } from '../lib/ui';
import { startWave } from '../lib/wave';

/** «Кого вы слушаете?» (MIXES.md 6.4): the page where a listener says whom they listen to. A newcomer comes here from
 * the button of «Моя волна» and from «Микс дня» — neither has anything to start from until a few artists are picked
 * (`lib/taste.ts`); anyone may come back and change the picks. The page of «Микс дня» itself is a list of «Для вас»
 * (`pages/ForYou.tsx`). */

const PICK_MIN = 3;
/** How many rows «Похожие на …» stand at once: those of the latest picks. */
const ROWS = 3;

interface Alike {
  ref: string;
  name: string;
  /** not known yet: asked */
  artists?: CatalogArtist[];
}

/** An artist to pick: the round photo with a check on it once picked, the name under it. */
function ArtistTile({ a, on, onToggle, className }: { a: CatalogArtist; on: boolean; onToggle: () => void; className?: string }) {
  return (
    <ArtistCard
      artist={{ name: a.name, photo: a.picture_small_url || a.picture_url }}
      size={88}
      sub={false}
      selected={on}
      onClick={onToggle}
      button={{ 'aria-pressed': on }}
      className={className}
      overlay={on && (
        <span className="absolute -bottom-1 -right-1 flex h-7 w-7 items-center justify-center rounded-full bg-accent text-on-accent ring-2 ring-bg">
          <Icon name="check" size={16} stroke={3} />
        </span>
      )}
    />
  );
}

/** The artists start from those this server listens to; every pick brings a row of artists like the picked one, so
 * the choice widens from what is already chosen. A pick is a follow too (the Hub makes it one). At least three; as
 * many as one likes. `?then=wave` — the wave starts once they are picked, `?then=mix` — the mix's page opens.
 *
 * «Карта» (a server with a Last.fm key; `?view=map` opens with it) is the other way to widen the choice: the
 * artist picked last stands in the middle, those near them around; a press on one goes to them — so one walks
 * from artist to artist — and the one in the middle is picked by «Отметить» on the strip under the map. Before
 * the first pick there is nobody to draw a map around: the list is shown. */
export function TastePage() {
  const route = useRoute();
  const then = route.query.get('then');
  const [view, setView, canMap] = useArtistsView(route.query.get('view') === 'map' ? 'map' : null);
  /** whom the map is drawn around: the artist picked last, or gone to on the map */
  const [center, setCenter] = useState<{ ref: string; name: string } | null>(null);
  const [q, setQ] = useState('');
  const dq = useDebounced(q.trim(), 400);
  const res = useAsync((s) => api.tasteOnboarding(dq || undefined, s), [dq]);
  const [picked, setPicked] = useState<Map<string, string>>(new Map());
  const [alike, setAlike] = useState<Alike[]>([]);
  const [busy, setBusy] = useState(false);
  const [seeded, setSeeded] = useState(false);

  // artists picked earlier come back checked
  useEffect(() => {
    if (seeded || !res.data) return;
    setSeeded(true);
    if (res.data.selected.length) {
      setPicked(new Map(res.data.selected.map((a) => [a.ref, a.name])));
      setCenter(res.data.selected[res.data.selected.length - 1]);
    }
  }, [res.data, seeded]);
  const map = useArtistMap(center?.ref, view === 'map');

  const toggle = (a: { ref: string; name: string }) => {
    if (picked.has(a.ref)) {
      setPicked((prev) => {
        const next = new Map(prev);
        next.delete(a.ref);
        return next;
      });
      setAlike((rows) => rows.filter((r) => r.ref !== a.ref));
      return;
    }
    setPicked((prev) => new Map(prev).set(a.ref, a.name));
    setCenter({ ref: a.ref, name: a.name });
    if (view === 'map') setQ(''); // found and picked: back to the map, now around them
    setAlike((rows) => [{ ref: a.ref, name: a.name }, ...rows.filter((r) => r.ref !== a.ref)]);
    // a row that the catalog has nobody for, or did not answer about, is not shown — the pick itself stands
    api.tasteSimilar(a.ref).then(
      (artists) => setAlike((rows) => rows.map((r) => (r.ref === a.ref ? { ...r, artists } : r))),
      () => setAlike((rows) => rows.filter((r) => r.ref !== a.ref)),
    );
  };

  const submit = async () => {
    setBusy(true);
    const st = await attempt(() => api.pickArtists([...picked.keys()]));
    setBusy(false);
    if (!st) return;
    tastePicked();
    if (then === 'wave') {
      navigate('/home', { replace: true });
      void startWave(null, 'home_card');
    } else if (then === 'mix') {
      toast('Собираем ваш «Микс дня» — пара минут', 'success');
      navigate('/mix', { replace: true });
    } else {
      toast('Сохранено', 'success');
      navigate('/home', { replace: true });
    }
  };

  const left = PICK_MIN - picked.size;
  const mapped = view === 'map' && !!center && !dq; // the map is on screen
  const rows = dq || mapped ? [] : alike.filter((r) => r.artists?.length).slice(0, ROWS);
  const title = res.data?.source === 'server' ? 'Слушают на этом сервере' : res.data?.source === 'chart' ? 'Популярное' : null;
  return (
    <div>
      <PageHeader title="Кого вы слушаете?" back="/home" />
      <p className="px-4 text-sm text-muted">
        Отметьте хотя бы {PICK_MIN} исполнителей — с них начнутся «Моя волна» и «Микс дня», а об их новых релизах вы узнаете первыми. Дальше всё подстроится под то, что
        вы слушаете.
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
              onClick={() => toggle({ ref, name })}
              className="inline-flex min-h-9 shrink-0 items-center gap-1.5 rounded-full bg-accent px-3.5 text-sm font-semibold text-on-accent"
              aria-label={`Убрать ${name}`}
            >
              {name}
              <Icon name="x" size={16} />
            </button>
          ))}
        </div>
      )}

      {canMap && (
        <div className="flex items-center justify-between gap-3 px-4 pt-4">
          <span className="min-w-0 text-[13px] leading-snug text-muted">
            {view === 'map' ? (center ? 'Нажмите на исполнителя, чтобы перейти к нему. Отметить — кнопкой под картой' : 'Отметьте первого исполнителя — карта построится вокруг него') : ''}
          </span>
          <ArtistsViewSwitch view={view} onChange={setView} />
        </div>
      )}

      {mapped && (
        <section aria-label={`Карта: рядом с ${center!.name}`} className="pt-2">
          <ArtistMap data={map.data} loading={map.loading} filling={map.filling} error={map.error} onRetry={map.reload} marked={picked} onArtist={(a) => setCenter({ ref: a.ref, name: a.name })} />
          {map.data && (
            <ArtistMapPanel
              artist={map.data.center}
              action={
                picked.has(map.data.center.ref) ? (
                  <button type="button" onClick={() => toggle(map.data!.center)} title="Снять отметку" className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-full px-3 text-[13px] font-semibold text-accent transition hover:bg-fg/5">
                    <Icon name="check" size={15} stroke={3} />
                    Отмечен
                  </button>
                ) : (
                  <Button small onClick={() => toggle(map.data!.center)}>
                    Отметить
                  </Button>
                )
              }
            />
          )}
        </section>
      )}

      {rows.map((r) => (
        <section key={r.ref} aria-label={`Похожие на ${r.name}`} className="pt-5">
          <h2 className="px-4 text-sm font-semibold text-muted">
            Похожие на <span className="text-fg">{r.name}</span>
          </h2>
          <div className="no-scrollbar flex gap-2 overflow-x-auto px-4 pb-1 pt-3">
            {r.artists!.map((a) => (
              <ArtistTile key={a.ref} a={a} on={picked.has(a.ref)} onToggle={() => toggle(a)} className="w-24 shrink-0" />
            ))}
          </div>
        </section>
      ))}

      {mapped ? null : res.loading && !res.data ? (
        <SkeletonRows n={6} />
      ) : res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !res.data?.suggestions.length ? (
        <p className="px-4 py-8 text-center text-muted">{dq ? 'Никого не нашли — попробуйте другое написание' : 'Найдите исполнителя по имени — строка поиска выше'}</p>
      ) : (
        <section aria-label={title ?? 'Найденные исполнители'} className="pt-5">
          {title && <h2 className="px-4 text-sm font-semibold text-muted">{title}</h2>}
          <ul className="grid grid-cols-3 gap-x-2 gap-y-4 px-4 pb-4 pt-3 min-[560px]:grid-cols-4 min-[800px]:grid-cols-5 min-[1100px]:grid-cols-7">
            {res.data.suggestions.map((a) => (
              <li key={a.ref}>
                <ArtistTile a={a} on={picked.has(a.ref)} onToggle={() => toggle(a)} className="w-full" />
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="sticky bottom-[calc(var(--tabbar-h)+var(--player-h,0px)+env(safe-area-inset-bottom))] z-10 bg-gradient-to-t from-bg via-bg to-transparent px-4 pb-3 pt-6">
        <Button variant="primary" block busy={busy} disabled={left > 0} onClick={submit}>
          {left > 1 ? `Выберите ещё ${left}` : left === 1 ? 'Ещё один, и можно начинать' : then === 'wave' ? 'Начать' : then === 'mix' ? 'Собрать микс' : 'Сохранить'}
        </Button>
      </div>
    </div>
  );
}
