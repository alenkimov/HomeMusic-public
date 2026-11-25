import { useMemo, useState } from 'react';
import { api, libraryCoverUrl } from '../api/client';
import type { SplitAlbums, TrackOut } from '../api/types';
import { TrackRow, TrackTableHeader } from '../components/catalog';
import { Icon } from '../components/Icon';
import { TrackTable } from '../components/TrackTable';
import { Button, Cover, Empty, ErrorState, Loading, PageHeader, cx } from '../components/ui';
import { AlbumCard, AlbumHero } from '../components/Album';
import { ServiceIcon, serviceName } from '../components/ServiceBadge';
import { fmtLength, tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link, artistPath } from '../lib/router';
import { attempt } from '../lib/ui';

type Group = SplitAlbums;
type Part = Group['albums'][number];

/** «Объединение альбомов» (from «Меню»): albums of one name that came to the library in pieces — the tracks with a
 * guest filed apart («Три дня дождя» and «Три дня дождя, Роки»). On top: which album; then its parts as cards — a
 * tick to keep a part in, where each came from (a transfer, a download, an upload); then the name, the artist and the
 * cover of what comes out; under them the album it will be, tracks that play. «Это разные альбомы»: the group is not
 * offered again (`Разные релизы`, where it can be taken back). */
export function AlbumMergePage() {
  const res = useAsync((s) => api.splitAlbums(s), [], true, 'album-merge');
  const [tab, setTab] = useState<'pending' | 'separate'>('pending');
  const [picked, setPicked] = useState<string | null>(null);
  const [merged, setMerged] = useState<Record<string, number>>({}); // group → the album it became

  if (res.loading) return (<><PageHeader title="Объединение альбомов" back="/more" /><Loading /></>);
  if (!res.data) return (<><PageHeader title="Объединение альбомов" back="/more" /><ErrorState text={res.error ?? 'Не удалось загрузить'} onRetry={() => res.reload()} /></>);
  const all = res.data;
  const pending = all.filter((g) => !g.dismissed);
  const separate = all.filter((g) => g.dismissed);
  const shown = tab === 'pending' ? pending : separate;
  const group = shown.find((g) => g.key === picked) ?? shown[0];
  const waiting = pending.filter((g) => merged[g.key] === undefined).length;

  const chip = (on: boolean) => cx('flex h-9 shrink-0 items-center gap-2 rounded-full px-4 text-sm font-semibold transition', on ? 'bg-fg text-bg' : 'bg-raised hover:bg-hover');
  const after = (next: Group[]) => {
    const rest = next.filter((g) => g.key !== group?.key);
    setPicked(rest[0]?.key ?? null);
    res.reload(true);
  };

  return (
    <div className="pb-8">
      <PageHeader title="Объединение альбомов" back="/more" />
      <p className="max-w-3xl px-4 text-[15px] leading-relaxed text-muted lg:px-6">
        Альбом попал в библиотеку несколькими частями: треки с гостями записались отдельно. Проверьте части и объедините их. Если это разные релизы, отметьте это.
      </p>
      <div className="flex gap-2 px-4 pt-4 lg:px-6">
        <button type="button" onClick={() => { setTab('pending'); setPicked(null); }} className={chip(tab === 'pending')}>Ждут решения · {waiting}</button>
        <button type="button" onClick={() => { setTab('separate'); setPicked(null); }} className={chip(tab === 'separate')}>Разные релизы · {separate.length}</button>
      </div>

      {!group ? (
        <Empty
          icon="merge"
          title={tab === 'pending' ? 'Все альбомы целые' : 'Здесь пусто'}
          text={tab === 'pending' ? 'Когда альбом окажется разбит на части, он появится здесь.' : 'Здесь будут альбомы, которые вы отметили как разные релизы.'}
        />
      ) : (
        <>
          {shown.length > 1 && (
            <div className="no-scrollbar flex gap-2 overflow-x-auto px-4 pt-3 lg:px-6">
              {shown.map((g) => (
                <button key={g.key} type="button" onClick={() => setPicked(g.key)} className={cx(chip(g.key === group.key), 'max-w-72 font-medium')}>
                  {differs(g) && merged[g.key] === undefined && <span aria-label="названия отличаются" className="h-2 w-2 shrink-0 rounded-full bg-warn" />}
                  <span className="truncate">{g.title}</span>
                  {merged[g.key] !== undefined && <Icon name="check" size={14} className="shrink-0 text-accent" />}
                </button>
              ))}
            </div>
          )}
          <GroupView
            key={group.key}
            group={group}
            mergedInto={merged[group.key]}
            onMerged={(id) => setMerged((m) => ({ ...m, [group.key]: id }))}
            onDismissed={() => after(shown)}
          />
        </>
      )}
    </div>
  );
}

/** Do the names of the group's albums differ beyond letter case («Watch me!» and «Watch me! (English Version)»)? */
function differs(g: Group): boolean {
  return new Set(g.albums.map((a) => a.title.trim().toLowerCase())).size > 1;
}

type Source = NonNullable<Part['sources']>[number];

/** Where a part came from, in words: «Перенос из Spotify», «Скачано: YouTube», «Загружено вами». */
function sourceLabel(x: Source): string {
  if (x.kind === 'import') return `Перенос из ${serviceName(x.service)}`;
  if (x.service === 'upload') return 'Загружено вами';
  if (x.service === 'manual') return 'Добавлено вручную';
  return `Скачано: ${serviceName(x.service)}`;
}

function SourceLine({ x }: { x: Source }) {
  return (
    <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted">
      <ServiceIcon service={x.service} size={14} />
      <span className="truncate">{sourceLabel(x)}</span>
    </span>
  );
}

const unique = (xs: string[]) => [...new Map(xs.map((x) => [x.trim().toLowerCase(), x.trim()])).values()];

function GroupView({ group, mergedInto, onMerged, onDismissed }: { group: Group; mergedInto?: number; onMerged: (albumId: number) => void; onDismissed: () => void }) {
  const ids = useMemo(() => group.albums.flatMap((a) => a.track_ids), [group]);
  const cards = useAsync(() => (ids.length ? api.trackCards(ids) : Promise.resolve([])), [group.key]);
  const byId = useMemo(() => new Map((cards.data ?? []).map((t) => [t.library_track_id, t as TrackOut])), [cards.data]);
  const [off, setOff] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<'merge' | 'dismiss' | null>(null);

  const first = group.albums.find((a) => a.id === group.target_id) ?? group.albums[0];
  const titles = useMemo(() => unique([first.title, ...group.albums.map((a) => a.title)]), [group, first]);
  // the album's artist: the shortest credit first — a guest's part names the album's artist and the guest
  const artists = useMemo(() => unique([...group.albums.map((a) => a.album_artist)].sort((x, y) => x.length - y.length)), [group]);
  const withCover = group.albums.filter((a) => a.has_cover);
  const [title, setTitle] = useState(0); // an index of `titles`; -1: the user's own
  const [own, setOwn] = useState('');
  const [artist, setArtist] = useState(0);
  const [coverFrom, setCoverFrom] = useState<number | null>((first.has_cover ? first : withCover[0])?.id ?? null);

  const tracksOf = (a: Part) => a.track_ids.map((id) => byId.get(id)).filter((t): t is TrackOut => !!t);
  const inside = group.albums.filter((a) => !off.has(a.id));
  const target = inside.find((a) => a.id === first.id) ?? inside[0] ?? first;
  const finalTitle = title === -1 ? own.trim() || first.title : titles[title];
  const finalArtist = artists[artist];
  const cover = coverFrom !== null && !off.has(coverFrom) ? coverFrom : withCover.find((a) => !off.has(a.id))?.id ?? null;
  const tracks = inside.flatMap((a) => tracksOf(a).map((t) => ({ t, from: a.sources?.[0] })));
  const length = tracks.reduce((n, x) => n + (x.t.duration_s ?? 0), 0);
  const year = inside.map((a) => a.year).find((y) => y);
  // each credit of the album's artist with its page, where a part knows it
  const known = new Map(group.albums.flatMap((a) => a.artists ?? []).filter((x) => x.id).map((x) => [x.name.toLowerCase(), x.id]));
  const credits = finalArtist.split(/,\s*/).filter(Boolean).map((name) => {
    const id = known.get(name.toLowerCase());
    return { name, to: id ? artistPath({ ref: '', hub_artist_id: id }) : null };
  });
  const can = inside.length >= 2 && mergedInto === undefined && finalTitle.length > 0;

  const toggle = (a: Part) => setOff((s) => { const n = new Set(s); if (n.has(a.id)) n.delete(a.id); else n.add(a.id); return n; });

  const merge = async () => {
    if (!can) return;
    setBusy('merge');
    const ok = await attempt(() => api.mergeAlbums({
      target_id: target.id, source_ids: inside.filter((a) => a.id !== target.id).map((a) => a.id),
      title: finalTitle, album_artist: finalArtist, cover_from: cover ?? undefined,
    }), `«${finalTitle}» — один альбом`);
    setBusy(null);
    if (ok) onMerged(target.id);
  };
  const dismiss = async () => {
    setBusy('dismiss');
    const albumIds = group.albums.map((a) => a.id);
    const ok = await attempt(() => (group.dismissed ? api.splitRestore(albumIds) : api.splitDismiss(albumIds)),
      group.dismissed ? 'Альбомы снова ждут решения' : 'Отмечено: это разные альбомы');
    setBusy(null);
    if (ok) onDismissed();
  };

  const legend = 'mb-2 text-[13px] text-muted';
  const radio = 'flex min-h-10 cursor-pointer items-center gap-2.5 text-[15px]';
  const done = mergedInto !== undefined;
  return (
    <>
      <section aria-label="Какие части объединить" className="mx-4 mt-4 rounded-2xl bg-surface p-4 lg:mx-6 lg:p-5">
        <h2 className="text-lg font-bold">1. Какие части объединить</h2>
        <p className="mt-0.5 text-[13px] text-muted">Под каждой частью — откуда она пришла. Снимите галочку, если часть — другой релиз.</p>
        <div className="no-scrollbar -mx-1 mt-3 flex gap-4 overflow-x-auto px-1 pb-1">
          {group.albums.map((a) => {
            const isIn = !off.has(a.id);
            return (
              <AlbumCard
                key={a.id}
                className={cx('w-36 shrink-0 transition lg:w-44', !isIn && 'opacity-40')}
                album={{ title: a.title, cover: a.has_cover ? libraryCoverUrl(a.id, 300) : null, artist: a.album_artist, year: a.year }}
                overlay={!done && (
                  <button type="button" role="checkbox" aria-checked={isIn} aria-label={`Часть «${a.title}» (${a.album_artist}) войдёт в альбом`}
                    onClick={() => toggle(a)} className="absolute left-1 top-1 flex h-10 w-10 items-center justify-center rounded-full bg-black/55">
                    <span className={cx('flex h-5 w-5 items-center justify-center rounded-md border-2 transition', isIn ? 'border-accent bg-accent text-on-accent' : 'border-white/70')}>
                      {isIn && <Icon name="check" size={13} stroke={3.5} />}
                    </span>
                  </button>
                )}
                extra={
                  <div className="mt-1.5 space-y-1">
                    <span className="block text-xs text-muted">{tracksWord(a.track_count)}</span>
                    {(a.sources ?? []).slice(0, 3).map((x) => <SourceLine key={`${x.kind}:${x.service}`} x={x} />)}
                  </div>
                }
              />
            );
          })}
        </div>
      </section>

      {!done && (
        <section aria-label="Каким будет альбом" className="mx-4 mt-3 rounded-2xl bg-surface p-4 lg:mx-6 lg:p-5">
          <h2 className="mb-3 text-lg font-bold">2. Каким будет альбом</h2>
          <div className="grid gap-5 lg:grid-cols-3">
            <fieldset className="min-w-0">
              <legend className={legend}>Название</legend>
              {titles.map((t, i) => (
                <label key={t} className={radio}>
                  <input type="radio" name={`title:${group.key}`} checked={title === i} onChange={() => setTitle(i)} className="h-4.5 w-4.5 shrink-0 accent-accent" />
                  <span className="min-w-0 break-words">{t}</span>
                </label>
              ))}
              <label className={radio}>
                <input type="radio" name={`title:${group.key}`} checked={title === -1} onChange={() => setTitle(-1)} aria-label="Своё название" className="h-4.5 w-4.5 shrink-0 accent-accent" />
                <input type="text" value={own} placeholder="Своё название" onFocus={() => setTitle(-1)} onChange={(e) => { setOwn(e.target.value); setTitle(-1); }}
                  className="h-9 min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 text-sm outline-none focus:border-fg/40" />
              </label>
            </fieldset>
            <fieldset className="min-w-0">
              <legend className={legend}>Исполнитель альбома</legend>
              {artists.map((t, i) => (
                <label key={t} className={radio}>
                  <input type="radio" name={`artist:${group.key}`} checked={artist === i} onChange={() => setArtist(i)} className="h-4.5 w-4.5 shrink-0 accent-accent" />
                  <span className="min-w-0 break-words">{t}</span>
                </label>
              ))}
              <p className="mt-1 text-xs text-dim">Гости останутся у своих треков.</p>
            </fieldset>
            <fieldset className="min-w-0">
              <legend className={legend}>Обложка</legend>
              {withCover.length ? (
                <div className="flex flex-wrap gap-2.5">
                  {withCover.map((a) => (
                    <button key={a.id} type="button" aria-pressed={cover === a.id} disabled={off.has(a.id)} onClick={() => setCoverFrom(a.id)}
                      aria-label={`Обложка части «${a.title}» (${a.album_artist})`}
                      className={cx('rounded-lg transition disabled:opacity-40', cover === a.id ? 'ring-3 ring-accent' : 'hover:ring-2 hover:ring-fg/30')}>
                      <Cover src={libraryCoverUrl(a.id, 200)} size={72} icon="disc" alt="" />
                    </button>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted">Ни у одной части нет обложки — её можно поставить на странице альбома после объединения.</p>
              )}
            </fieldset>
          </div>
          <div className="mt-5 flex flex-col-reverse gap-2 border-t border-line pt-4 lg:flex-row lg:justify-end">
            <Button busy={busy === 'dismiss'} disabled={busy !== null} onClick={() => void dismiss()}>
              {group.dismissed ? 'Вернуть к объединению' : 'Это разные альбомы'}
            </Button>
            {!group.dismissed && (
              <Button variant="primary" busy={busy === 'merge'} disabled={!can || busy !== null} onClick={() => void merge()}>
                {inside.length >= 2 ? `Объединить ${inside.length} ${inside.length < 5 ? 'части' : 'частей'}` : 'Отметьте хотя бы 2 части'}
              </Button>
            )}
          </div>
        </section>
      )}

      {/* the preview's light stays inside it: it does not reach up over the choices */}
      <section aria-label="Так будет выглядеть альбом" className="relative isolate mt-6 overflow-hidden pt-2">
        <p className="px-4 pb-2 text-xs font-bold uppercase tracking-wider text-dim lg:px-6">
          {done ? <>Готово — <Link to={`/library/album/${mergedInto}`} className="text-accent normal-case tracking-normal hover:underline">открыть альбом</Link></> : '3. Так будет выглядеть альбом'}
        </p>
        <AlbumHero
          album={{ title: finalTitle, cover: cover !== null ? libraryCoverUrl(cover, 600) : null }}
          artists={credits}
          facts={[year, tracksWord(tracks.length), length ? fmtLength(length) : null]}
          tracks={tracks.map((x) => x.t)}
        />
        {cards.loading ? <Loading /> : (
          <TrackTable tracks={tracks.map((x) => x.t)} numbered kind="album">
            <ul>
              {tracks.length > 0 && <li><TrackTableHeader numbered /></li>}
              {tracks.map(({ t, from }, i) => (
                <li key={`${t.library_track_id}:${i}`}>
                  <TrackRow track={t} index={i + 1} queue={tracks.map((x) => x.t)} link={false} note={from && <SourceLine x={from} />} />
                </li>
              ))}
            </ul>
          </TrackTable>
        )}
      </section>
    </>
  );
}
