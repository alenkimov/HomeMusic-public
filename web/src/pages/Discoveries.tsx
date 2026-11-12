import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { TrackRow, TrackTableHeader, listenTrack } from '../components/catalog';
import { Button, Empty, ErrorState, Loading, PageHeader } from '../components/ui';
import { STANDALONE } from '../lib/embedded';
import { useHubEvent } from '../lib/events';
import { tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { listen } from '../lib/listen';
import { canPlay, canPlayAll, playTracks } from '../lib/player';
import { attempt } from '../lib/ui';

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
 * («+»). Earlier issues stay and are opened from the list of weeks. */
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
  const playAll = !tracks.length ? null : canPlayAll() ? () => playTracks(tracks) : !canPlay() ? () => listen(listenTrack(tracks[0]), tracks.map(listenTrack)) : null;
  const build = async () => {
    const ok = await attempt(() => api.discoveriesBuild(), 'Собираю — это займёт пару минут');
    if (ok) setAsked(true);
  };
  const latest = d?.issues[0];
  const old = !!d?.issue && !!latest && d.issue.id !== latest.id;

  return (
    <div>
      <PageHeader
        title="Открытия недели"
        back={STANDALONE ? '/home' : '/more'}
        sub={d?.issue ? `${weekTitle(d.issue.week)} · ${tracksWord(tracks.length)}` : 'Исполнители, которых вы ещё не слышали'}
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
          <div className="flex flex-wrap items-center gap-2 px-4 pb-3">
            {playAll && (
              <Button variant="primary" icon="play" onClick={playAll}>
                Слушать
              </Button>
            )}
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
