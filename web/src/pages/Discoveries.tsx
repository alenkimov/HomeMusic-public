import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { TrackRow, TrackTableHeader } from '../components/catalog';
import { Icon } from '../components/Icon';
import { Button, Empty, ErrorState, Loading, PageHeader } from '../components/ui';
import { STANDALONE } from '../lib/embedded';
import { useHubEvent } from '../lib/events';
import { fmtLength, tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { attempt } from '../lib/ui';
import { Hero } from './MediaLibrary';

/** The week an issue is of: «9–15 ноября». */
function weekTitle(iso: string): string {
  const from = new Date(`${iso}T00:00:00`);
  const to = new Date(from.getTime() + 6 * 86400000);
  const month = (d: Date) => d.toLocaleDateString('ru-RU', { month: 'long', day: 'numeric' }).replace(/^\d+\s/, '');
  const year = from.getFullYear() !== new Date().getFullYear() ? ` ${from.getFullYear()}` : '';
  return from.getMonth() === to.getMonth() ? `${from.getDate()}–${to.getDate()} ${month(to)}${year}` : `${from.getDate()} ${month(from)} – ${to.getDate()} ${month(to)}${year}`;
}

/** «Открытия недели»: tracks of artists the user has never heard, little-known ones first — a new issue every Monday
 * (app/mixes/discover.py). The tracks are the catalog's: they play at once and reach the library only when kept
 * («+»). Earlier issues stay and are opened from the list of weeks. The page is a list like any other: its picture
 * (the covers of its first tracks), how many tracks and how long, ▶ and the shuffle. */
export function DiscoveriesPage() {
  const [issueId, setIssueId] = useState<number | null>(null);
  const res = useAsync((s) => api.discoveries(issueId, s), [issueId]);
  const [asked, setAsked] = useState(false);
  useHubEvent('library', (e) => {
    if ((e as { kind?: string }).kind !== 'discoveries') return;
    setAsked(false);
    if (issueId === null) res.reload();
    else setIssueId(null);
  });
  const d = res.data;
  const building = asked || !!d?.building;
  // while an issue is being built the Hub says nothing until it is ready: look again now and then
  useEffect(() => {
    if (!building) return;
    const timer = window.setInterval(() => res.reload(), 20_000);
    return () => window.clearInterval(timer);
  }, [building, res.reload]);
  const tracks = d?.issue?.tracks ?? [];
  const length = tracks.reduce((sum, t) => sum + (t.duration_s ?? 0), 0);
  const covers = [...new Set(tracks.map((t) => t.cover_url || t.cover_small_url).filter((c): c is string => !!c))].slice(0, 4);
  const build = async () => {
    const ok = await attempt(() => api.discoveriesBuild(), 'Собираю — это займёт пару минут');
    if (ok) setAsked(true);
  };
  const latest = d?.issues[0];
  const old = !!d?.issue && !!latest && d.issue.id !== latest.id;

  return (
    <div>
      <PageHeader hero title="Открытия недели" back={STANDALONE ? '/home' : '/more'} />
      <Hero
        kicker="Автоплейлист"
        title="Открытия недели"
        art={
          covers.length >= 4 ? (
            <div className="grid h-full w-full grid-cols-2 grid-rows-2">
              {covers.map((c) => (
                <img key={c} src={c} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" className="h-full w-full object-cover" />
              ))}
            </div>
          ) : covers.length ? (
            <img src={covers[0]} alt="" referrerPolicy="no-referrer" className="h-full w-full object-cover" />
          ) : (
            <div className="flex h-full w-full items-center justify-center bg-accent/20 text-accent">
              <Icon name="wave" size={84} />
            </div>
          )
        }
        sub={d?.issue
          ? [tracksWord(tracks.length), length ? fmtLength(length) : null, weekTitle(d.issue.week)].filter(Boolean).join(' · ')
          : 'Исполнители, которых вы ещё не слышали'}
        tracks={tracks}
      />
      {res.error ? (
        <ErrorState text={res.error} onRetry={() => res.reload()} />
      ) : !d ? (
        <Loading />
      ) : !d.issue ? (
        building ? (
          <Loading text="Собираю первый выпуск — это займёт пару минут…" />
        ) : (
          <div>
            <Empty icon="wave" title="Выпуска пока нет" text="Каждый понедельник здесь появляется 30 треков исполнителей, которых вы ещё не слышали, — чем менее известен исполнитель, тем выше трек. Нужна история: то, что вы слушали и добавили в «Любимое»." />
            <div className="flex justify-center pt-2">
              <Button variant="primary" icon="wave" onClick={() => void build()}>
                Собрать сейчас
              </Button>
            </div>
          </div>
        )
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 px-4 pb-3 lg:px-6">
            {d.issues.length > 1 && (
              <select aria-label="Выпуск" className="input !w-auto" value={d.issue.id} onChange={(e) => setIssueId(Number(e.target.value))}>
                {d.issues.map((x, i) => (
                  <option key={x.id} value={x.id}>
                    {i === 0 ? 'Эта неделя: ' : ''}
                    {weekTitle(x.week)}
                  </option>
                ))}
              </select>
            )}
            {!old && (
              <Button variant="ghost" small busy={building} onClick={() => void build()}>
                {building ? 'Собираю…' : 'Собрать заново'}
              </Button>
            )}
          </div>
          <p className="px-4 pb-2 text-xs text-dim">Треки играют из каталога и не занимают места. Понравился — нажмите «+»: трек скачается и попадёт в «Любимое».</p>
          <TrackTableHeader album />
          {tracks.map((t, i) => (
            <TrackRow key={t.ref} track={t} showAlbum queue={tracks} index={i + 1} />
          ))}
        </>
      )}
    </div>
  );
}
