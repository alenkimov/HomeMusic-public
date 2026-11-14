import { useEffect, useMemo, useState } from 'react';
import { forYouOpened } from '../lib/forYou';
import { imageUrl, retryImage } from '../lib/img';
import { api } from '../api/client';
import type { ForYouOut, MixMode, TrackOut } from '../api/types';
import type { ListSort } from '../components/catalog';
import { Icon, type IconName } from '../components/Icon';
import { BarWindow, ListBar } from '../components/ListBar';
import { useListSearch } from '../components/ListSearch';
import { TagFilterChips } from '../components/Tags';
import { Button, Cover, Empty, ErrorState, Loading, HeroSkeleton, PageHeader, Segmented, Spinner, cx } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { fmtLength, tracksWord } from '../lib/format';
import { useAsync, useInterval } from '../lib/hooks';
import { Link } from '../lib/router';
import { useSession } from '../lib/session';
import { AUTO_PLAYLISTS } from '../lib/stats';
import { tagKey, type TagFilter } from '../lib/tags';
import { attempt, toast } from '../lib/ui';
import { Hero, SongList, inOrder, useShown } from './MediaLibrary';

/** «Для вас»: a list the Hub makes by itself — «Микс дня» (`mix`), «Открытия недели» (`weekly`), a top or «Давно не
 * слушал» (a kind of `AUTO_PLAYLISTS`). They are one page: the Hub tells every one of them the same way
 * (`/api/for-you/<kind>`: tracks, and what is the list's own beside them), and it is a list like a playlist's — its
 * picture of its tracks' covers, how many tracks, ▶, the tags and the search in its bar. What is a list's own is
 * behind one more mark of the bar: the mode of the mix and «Собрать заново», the issues of the discoveries. */

const MODES: Array<{ value: MixMode; label: string; hint: string }> = [
  { value: 'normal', label: 'Обычный', hint: '40 % знакомого и 60 % нового' },
  { value: 'favorites', label: 'Любимое', hint: 'В основном то, что вы любите: 80 % знакомого' },
  { value: 'discover', label: 'Незнакомое', hint: 'Почти всё новое: 90 % песен, которых вы не слышали' },
];

const TITLES: Record<string, string> = { mix: 'Микс дня', weekly: 'Открытия недели', ...Object.fromEntries(AUTO_PLAYLISTS.map((a) => [a.kind, a.title])) };
const ICONS: Record<string, IconName> = { mix: 'sparkle', weekly: 'wave', ...Object.fromEntries(AUTO_PLAYLISTS.map((a) => [a.kind, a.icon])) };

/** The week an issue is of: «9–15 ноября». */
function weekTitle(iso: string): string {
  const from = new Date(`${iso}T00:00:00`);
  const to = new Date(from.getTime() + 6 * 86400000);
  const month = (d: Date) => d.toLocaleDateString('ru-RU', { month: 'long', day: 'numeric' }).replace(/^\d+\s/, '');
  const year = from.getFullYear() !== new Date().getFullYear() ? ` ${from.getFullYear()}` : '';
  return from.getMonth() === to.getMonth() ? `${from.getDate()}–${to.getDate()} ${month(to)}${year}` : `${from.getDate()} ${month(from)} – ${to.getDate()} ${month(to)}${year}`;
}

/** The picture of a list that makes itself: up to four different covers of its tracks, 2×2; one cover when there
 * are fewer; the list's own mark when there are none. */
function ListCover({ tracks, icon }: { tracks: readonly TrackOut[]; icon: IconName }) {
  const covers = useMemo(() => [...new Set(tracks.map((t) => t.cover_url || t.cover_small_url).filter((c): c is string => !!c))].slice(0, 4), [tracks]);
  if (covers.length >= 4)
    return (
      <div className="grid h-full w-full grid-cols-2 grid-rows-2" aria-hidden>
        {covers.map((c) => (
          <img key={c} src={imageUrl(c)} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={retryImage} className="h-full w-full object-cover" />
        ))}
      </div>
    );
  if (covers.length) return <Cover src={covers[0]} alt="" className="h-full w-full !rounded-none" />;
  return (
    <div className="flex h-full w-full items-center justify-center bg-accent/20 text-accent">
      <Icon name={icon} size={84} />
    </div>
  );
}

export function ForYouPage({ kind, mix }: { kind: string; mix?: number }) {
  const { me } = useSession();
  const [issue, setIssue] = useState<number | null>(null);
  const res = useAsync((s) => api.forYouList(kind, { mix, issue: issue ?? undefined }, s), [kind, mix, issue], true, `for-you:${kind}:${mix ?? ''}:${issue ?? ''}`);
  const d: ForYouOut | undefined = res.data;
  const [busy, setBusy] = useState<string | null>(null);
  // a list being made is asked for again as soon as the Hub says
  useInterval(() => res.reload(true), d?.refresh_s ? d.refresh_s * 1000 : null);
  useHubEvent('library', (e) => {
    if (kind !== 'weekly' || (e as { kind?: string }).kind !== 'discoveries') return;
    if (issue === null) res.reload(true);
    else setIssue(null); // a new issue: it is the one shown
  });
  useHubEvent('reconnect', () => res.reload(true));

  // the latest mix or issue is on screen: it is «новый» no more
  const latest = !!d && mix === undefined && issue === null && d.tracks.length > 0;
  useEffect(() => {
    if (latest) forYouOpened(kind);
  }, [latest, kind]);

  const tracks = useMemo(() => d?.tracks ?? [], [d]);
  const search = useListSearch(tracks);
  const [filter, setFilter] = useState<TagFilter>({});
  const [sort, setSort] = useState<ListSort>(null);
  const shown = useShown(tracks, search.found, filter);
  const songIds = useMemo(() => tracks.map(tagKey), [tracks]);
  const title = d?.title ?? TITLES[kind] ?? 'Для вас';
  const icon = ICONS[kind] ?? 'chart';

  if (res.loading && !d) return (<><PageHeader hero title={title} back="/library" /><HeroSkeleton /></>);
  if (!d) return (<><PageHeader hero title={title} back="/library" /><ErrorState text={res.error ?? 'Список не найден'} onRetry={() => res.reload()} /></>);

  const run = async (key: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(key);
    const r = await attempt(fn);
    setBusy(null);
    if (r === undefined) return;
    if (done) toast(done, 'info');
    res.reload(true);
  };
  const setMode = (mode: MixMode) => {
    if (!d.mix || mode === d.mix.settings.mode) return;
    void run('mode', () => api.mixSettings({ mode }), me.is_admin ? 'Режим сменится в следующем миксе — или соберите заново сейчас' : `Режим сменится в следующем миксе, в ${d.mix.settings.mix_hour}:00`);
  };
  const rebuild = () => run('rebuild', () => api.rebuildMix(), 'Собираем новый микс — пара минут');
  const buildIssue = () => run('issue', () => api.discoveriesBuild(), 'Собираю — это займёт пару минут');

  const list = shown ? shown.map((f) => tracks[f.index]) : tracks;
  const length = tracks.reduce((sum, t) => sum + (t.duration_s ?? 0), 0);
  const playing = inOrder(list, sort);
  const mixBuilding = !!d.mix?.building || busy === 'rebuild';
  const issueBuilding = !!d.weekly?.building || busy === 'issue';

  const extra =
    d.mix?.current ? (
      <BarWindow icon="sliders" label="Режим микса">
        <Segmented<MixMode> label="Режим микса" value={d.mix.settings.mode} onChange={setMode} options={MODES} />
        <p className="mt-2 flex items-center gap-2 text-[13px] text-muted">
          <span>
            {MODES.find((m) => m.value === d.mix!.settings.mode)?.hint}. Новый микс — каждый день в {d.mix.settings.mix_hour}:00.
          </span>
          {busy === 'mode' && <Spinner size={14} />}
        </p>
        {me.is_admin && (
          <Button small icon="refresh" className="mt-3" busy={mixBuilding} onClick={() => void rebuild()}>
            {mixBuilding ? 'Собирается…' : 'Собрать заново'}
          </Button>
        )}
      </BarWindow>
    ) : d.weekly?.issue ? (
      <BarWindow icon="sliders" label="Выпуск">
        {d.weekly.issues.length > 1 && (
          <select aria-label="Выпуск" className="input" value={d.weekly.issue} onChange={(e) => setIssue(Number(e.target.value))}>
            {d.weekly.issues.map((x, i) => (
              <option key={x.id} value={x.id}>
                {i === 0 ? 'Эта неделя: ' : ''}
                {weekTitle(x.week)}
              </option>
            ))}
          </select>
        )}
        <p className="mt-2 text-[13px] text-muted">Новый выпуск — каждый понедельник. Его треки скачиваются сами; что не понравилось, уйдёт со следующим выпуском, а что вы слушали или отметили ♥, останется.</p>
        {d.weekly.latest && (
          <Button small icon="refresh" className="mt-3" busy={issueBuilding} onClick={() => void buildIssue()}>
            {issueBuilding ? 'Собираю…' : 'Собрать заново'}
          </Button>
        )}
      </BarWindow>
    ) : undefined;

  return (
    <div>
      <ListBar
        title={title}
        back="/library"
        tracks={playing}
        context={d.context}
        search={tracks.length ? search : undefined}
        tags={tracks.length ? { songIds, filter, onChange: setFilter, count: list.length } : undefined}
        extra={extra}
        offline={mix === undefined && issue === null ? { key: `foryou:${kind}`, to: kind === 'mix' ? '/mix' : kind === 'weekly' ? '/discoveries' : `/auto/${kind}`, tracks } : undefined}
      />
      {!search.alone && (
        <Hero
          kicker="Для вас"
          title={title}
          cover={tracks[0]?.cover_url}
          art={<ListCover tracks={tracks} icon={icon} />}
          sub={[tracks.length ? tracksWord(tracks.length) : null, length ? fmtLength(length) : null, d.weekly?.week ? weekTitle(d.weekly.week) : null, d.note].filter(Boolean).join(' · ')}
          tracks={playing}
          context={d.context}
        />
      )}
      {tracks.length ? (
        <>
          <TagFilterChips filter={filter} onChange={setFilter} count={list.length} />
          <SongList tracks={tracks} found={shown} sort={sort} onSort={setSort} period={d.plays ? { label: d.plays_label ?? 'Прослушивания', of: d.plays } : undefined} />
        </>
      ) : kind === 'mix' ? (
        mixBuilding ? (
          <Loading text="Собираю микс — это займёт пару минут…" />
        ) : d.mix?.onboarding ? (
          <Link to="/taste" className="mx-4 mt-3 flex items-center gap-3 rounded-2xl bg-accent/15 p-4 transition hover:bg-accent/20">
            <span className={cx('flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent')}>
              <Icon name="sparkle" size={24} />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block font-bold">Что вы слушаете?</span>
              <span className="block text-[13px] text-muted">Выберите 3–5 исполнителей — соберём для вас «Микс дня»</span>
            </span>
            <Icon name="chevron" size={20} className="shrink-0 text-dim" />
          </Link>
        ) : (
          <Empty icon="sparkle" title="Микса пока нет" text={`Он собирается сам каждый день в ${d.mix?.settings.mix_hour ?? 6}:00 — из того, что вы слушаете.`} />
        )
      ) : kind === 'weekly' ? (
        issueBuilding ? (
          <Loading text="Собираю первый выпуск — это займёт пару минут…" />
        ) : (
          <div>
            <Empty icon="wave" title="Выпуска пока нет" text="Каждый понедельник здесь появляется 30 треков исполнителей, которых вы ещё не слышали, — чем менее известен исполнитель, тем выше трек. Нужна история: то, что вы слушали и добавили в «Любимое»." />
            <div className="flex justify-center pt-2">
              <Button variant="primary" icon="wave" onClick={() => void buildIssue()}>
                Собрать сейчас
              </Button>
            </div>
          </div>
        )
      ) : (
        <Empty icon="history" title="Пока пусто" text={kind === 'forgotten' ? 'Здесь появятся треки, которые вы слушали хотя бы три раза и не включали три месяца.' : 'Здесь появятся треки, которые вы слушали за этот период.'} />
      )}
    </div>
  );
}
