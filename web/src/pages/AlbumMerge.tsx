import { useMemo, useState } from 'react';
import { api, libraryCoverUrl } from '../api/client';
import type { SplitAlbums, TrackOut } from '../api/types';
import { TrackRow, TrackTableHeader } from '../components/catalog';
import { Icon } from '../components/Icon';
import { TrackTable } from '../components/TrackTable';
import { Button, Cover, Empty, ErrorState, Loading, PageHeader, cx } from '../components/ui';
import { fmtLength, tracksWord } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link } from '../lib/router';
import { attempt } from '../lib/ui';
import { PlayPair } from './MediaLibrary';

type Group = SplitAlbums;
type Part = Group['albums'][number];

/** «Объединение альбомов» (from «Меню»): albums of one name that came to the library in pieces — the tracks with a
 * guest filed apart («Три дня дождя» and «Три дня дождя, Роки»). On top: which album, then its parts as cards — a
 * check to keep a part in, a yellow star for the main one (its name, artist and cover are the album's). Under them
 * each part as the album it is now, tracks that play; joined, the one album it became. «Это разные альбомы»: the
 * group is not offered again (`Разные релизы`, where it can be taken back). */
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

/** What a name has beyond the main one's: «(English Version)» of «Watch me! (English Version)» beside «Watch me!»;
 * the whole name when they share no start. Empty: the same name. */
function beyond(name: string, base: string): { head: string; extra: string } {
  if (name.trim().toLowerCase() === base.trim().toLowerCase()) return { head: name, extra: '' };
  if (name.toLowerCase().startsWith(base.toLowerCase())) return { head: name.slice(0, base.length), extra: name.slice(base.length) };
  return { head: '', extra: name };
}

function GroupView({ group, mergedInto, onMerged, onDismissed }: { group: Group; mergedInto?: number; onMerged: (albumId: number) => void; onDismissed: () => void }) {
  const ids = useMemo(() => group.albums.flatMap((a) => a.track_ids), [group]);
  const cards = useAsync(() => (ids.length ? api.trackCards(ids) : Promise.resolve([])), [group.key]);
  const byId = useMemo(() => new Map((cards.data ?? []).map((t) => [t.library_track_id, t as TrackOut])), [cards.data]);
  const [off, setOff] = useState<Set<number>>(new Set());
  const [main, setMain] = useState(group.target_id);
  const [busy, setBusy] = useState<'merge' | 'dismiss' | null>(null);

  const tracksOf = (a: Part) => a.track_ids.map((id) => byId.get(id)).filter((t): t is TrackOut => !!t);
  const inside = group.albums.filter((a) => !off.has(a.id));
  const head = group.albums.find((a) => a.id === main && !off.has(a.id)) ?? inside[0] ?? group.albums[0];
  const total = inside.reduce((n, a) => n + a.track_count, 0);
  // the artists' common part: what a part has beyond it is its guest
  const artistBase = group.albums.map((a) => a.album_artist).reduce((x, y) => (y.length < x.length ? y : x));
  const can = inside.length >= 2 && mergedInto === undefined;

  const toggle = (a: Part) => setOff((s) => { const n = new Set(s); if (n.has(a.id)) n.delete(a.id); else n.add(a.id); return n; });
  const makeMain = (a: Part) => { setMain(a.id); setOff((s) => { const n = new Set(s); n.delete(a.id); return n; }); };

  const merge = async () => {
    if (!can) return;
    setBusy('merge');
    const ok = await attempt(() => api.mergeAlbums({ target_id: head.id, source_ids: inside.filter((a) => a.id !== head.id).map((a) => a.id) }), `«${head.title}» — один альбом`);
    setBusy(null);
    if (ok) onMerged(head.id);
  };
  const dismiss = async () => {
    setBusy('dismiss');
    const albumIds = group.albums.map((a) => a.id);
    const ok = await attempt(() => (group.dismissed ? api.splitRestore(albumIds) : api.splitDismiss(albumIds)),
      group.dismissed ? 'Альбомы снова ждут решения' : 'Отмечено: это разные альбомы');
    setBusy(null);
    if (ok) onDismissed();
  };

  return (
    <>
      <section aria-label="Что объединить" className="mx-4 mt-4 rounded-2xl bg-surface p-4 lg:mx-6 lg:p-5">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-lg font-bold">Что объединить</h2>
          <span className="text-[13px] text-muted">Галочка: часть войдёт в альбом. Звезда: главная часть, от неё альбом получит название, исполнителя и обложку.</span>
        </div>
        <div className="no-scrollbar -mx-1 mt-3 flex gap-3 overflow-x-auto px-1 pb-1">
          {group.albums.map((a) => {
            const isIn = !off.has(a.id);
            const isMain = a.id === head.id && isIn;
            const name = beyond(a.title, head.title);
            const guest = a.album_artist.startsWith(artistBase) ? a.album_artist.slice(artistBase.length) : '';
            return (
              <div key={a.id} className={cx('w-36 shrink-0 rounded-xl bg-raised p-2.5 transition lg:w-48 lg:p-3', !isIn && 'opacity-45')}>
                <div className="relative">
                  <Cover src={a.has_cover ? libraryCoverUrl(a.id, 300) : null} icon="disc" alt="" className="aspect-square w-full" />
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={isIn}
                    aria-label={`Часть «${a.title}» (${a.album_artist}) войдёт в альбом`}
                    onClick={() => toggle(a)}
                    className="absolute left-1 top-1 flex h-10 w-10 items-center justify-center rounded-full bg-black/55"
                  >
                    <span className={cx('flex h-5 w-5 items-center justify-center rounded-md border-2 transition', isIn ? 'border-accent bg-accent text-on-accent' : 'border-white/70')}>
                      {isIn && <Icon name="check" size={13} stroke={3.5} />}
                    </span>
                  </button>
                  <button
                    type="button"
                    aria-pressed={isMain}
                    aria-label={isMain ? 'Главная часть' : `Сделать главной: ${a.title} (${a.album_artist})`}
                    title={isMain ? 'Главная часть' : 'Сделать главной'}
                    onClick={() => makeMain(a)}
                    className={cx('absolute right-1 top-1 flex h-10 w-10 items-center justify-center rounded-full bg-black/55 transition', isMain ? 'text-warn' : 'text-white/80 hover:text-white')}
                  >
                    <Icon name="star" size={19} filled={isMain} />
                  </button>
                </div>
                <div className="mt-2 line-clamp-2 break-words text-sm font-semibold leading-snug lg:text-[15px]" title={a.title}>
                  {name.head}{name.extra && <span className="text-warn">{name.extra}</span>}
                </div>
                <div className="truncate text-[13px] text-muted" title={a.album_artist}>
                  {guest ? <>{artistBase}<span className="text-info">{guest}</span></> : a.album_artist}
                </div>
                <div className="text-[13px] text-muted">{[a.year, tracksWord(a.track_count)].filter(Boolean).join(' · ')}</div>
              </div>
            );
          })}
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3 border-t border-line pt-4">
          <span className="min-w-0 flex-[1_1_20rem] text-[15px] leading-snug">
            {mergedInto !== undefined ? (
              <>Готово: <b>«{head.title}»</b> теперь один альбом. <Link to={`/library/album/${mergedInto}`} className="text-accent hover:underline">Открыть альбом</Link></>
            ) : (
              <>Получится один альбом <b>«{head.title}»</b><span className="text-muted"> · {head.album_artist} · {tracksWord(total)}</span></>
            )}
          </span>
          {mergedInto === undefined && (
            <div className="flex w-full flex-col-reverse gap-2 lg:w-auto lg:flex-row">
              <Button busy={busy === 'dismiss'} disabled={busy !== null} onClick={() => void dismiss()}>
                {group.dismissed ? 'Вернуть к объединению' : 'Это разные альбомы'}
              </Button>
              {!group.dismissed && (
                <Button variant="primary" busy={busy === 'merge'} disabled={!can || busy !== null} onClick={() => void merge()}>
                  {inside.length >= 2 ? `Объединить ${inside.length} ${inside.length < 5 ? 'части' : 'частей'}` : 'Отметьте хотя бы 2 части'}
                </Button>
              )}
            </div>
          )}
        </div>
      </section>

      {cards.loading ? (
        <Loading />
      ) : mergedInto !== undefined ? (
        <PartAlbum album={head} tracks={inside.flatMap(tracksOf)} title={head.title} artist={head.album_artist} />
      ) : (
        group.albums.map((a) => (
          <PartAlbum key={a.id} album={a} tracks={tracksOf(a)} title={a.title} artist={a.album_artist}
            main={a.id === head.id && !off.has(a.id)} out={off.has(a.id)} />
        ))
      )}
    </>
  );
}

/** A part as the album it is: its head (cover, name, artist, ▶) and its tracks as an album page lists them. */
function PartAlbum({ album, tracks, title, artist, main, out }: { album: Part; tracks: TrackOut[]; title: string; artist: string; main?: boolean; out?: boolean }) {
  const length = tracks.reduce((n, t) => n + (t.duration_s ?? 0), 0);
  return (
    <section aria-label={`${title} — ${artist}`} className={cx('mt-8 transition', out && 'opacity-45')}>
      <div className="flex flex-col gap-4 px-4 lg:flex-row lg:items-end lg:gap-6 lg:px-6">
        <Cover src={album.has_cover ? libraryCoverUrl(album.id, 600) : null} icon="disc" alt="" className="aspect-square w-44 shrink-0 self-center shadow-2xl lg:w-40 lg:self-auto" />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted">Альбом</span>
            {main && (
              <span className="inline-flex items-center gap-1 rounded-full bg-warn/15 px-2 py-0.5 text-xs font-semibold text-warn">
                <Icon name="star" size={12} filled />Главная часть
              </span>
            )}
            {out && <span className="rounded-full bg-hover px-2 py-0.5 text-xs font-semibold text-muted">Не войдёт</span>}
          </div>
          <h2 className="mt-1 line-clamp-2 break-words text-2xl font-black leading-tight tracking-tight lg:text-5xl">{title}</h2>
          <div className="mt-2 text-sm text-muted">
            <span className="font-medium text-fg">{artist}</span>
            {[album.year, tracksWord(tracks.length), length ? fmtLength(length) : null].filter(Boolean).map((x) => ` · ${x}`)}
          </div>
        </div>
        {tracks.length > 0 && <div className="self-end"><PlayPair tracks={tracks} context="album" /></div>}
      </div>
      <div className="mt-3">
        <TrackTable tracks={tracks} numbered kind="album">
          <ul>
            {tracks.length > 0 && <li><TrackTableHeader numbered /></li>}
            {tracks.map((t, i) => (
              <li key={`${t.library_track_id}:${i}`}>
                <TrackRow track={t} index={i + 1} queue={tracks} link={false} />
              </li>
            ))}
          </ul>
        </TrackTable>
      </div>
    </section>
  );
}
