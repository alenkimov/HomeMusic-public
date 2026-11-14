import { useEffect, useMemo } from 'react';
import { api } from '../api/client';
import type { NamesakeOut } from '../api/types';
import { Icon } from '../components/Icon';
import { Button, Cover, Empty, PageHeader } from '../components/ui';
import { refreshDislikes, setDisliked, useDislikes } from '../lib/dislikes';
import { recordTypeLabel } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link, enc } from '../lib/router';
import { attempt } from '../lib/ui';

/** «Скрытое» (from «Меню»): the artists the user does not listen to, the tracks marked «Не нравится», and the
 * releases said to be a namesake's (those are the whole server's, `catalog/namesakes.py` of the Hub) — each can
 * be brought back here. An artist is hidden from their page or a track's menu; nothing of theirs plays by itself
 * after that (`lib/dislikes.ts`). */
export function HiddenPage() {
  const rows = useDislikes();
  useEffect(() => {
    void refreshDislikes(true);
  }, []);
  // releases said to be a namesake's, by the artist they were taken from
  const others = useAsync((s) => api.namesakes(s), []);
  const byArtist = useMemo(() => {
    const m = new Map<string, NamesakeOut[]>();
    for (const n of others.data ?? []) m.set(n.artist_ref, [...(m.get(n.artist_ref) ?? []), n]);
    return [...m.entries()];
  }, [others.data]);
  const bringBack = async (artistRef: string, albumRefs?: string[]) => {
    if (await attempt(() => api.namesakesRestore(artistRef, albumRefs), albumRefs ? 'Релиз возвращён исполнителю' : 'Релизы возвращены исполнителю')) others.reload(true);
  };
  const artists = rows.filter((d) => d.artist_ref);
  const tracks = rows.filter((d) => !d.artist_ref);
  return (
    <div>
      <PageHeader title="Скрытое" back="/more" />
      <div className="space-y-5 px-4 pb-6">
        {!rows.length && !byArtist.length && <Empty icon="ban" title="Ничего не скрыто" text="Исполнителя можно скрыть на его странице, за тремя точками: его треки перестанут включаться сами." />}
        {artists.length > 0 && (
          <section aria-label="Исполнители">
            <h2 className="px-1 pb-2 text-[13px] font-semibold uppercase tracking-wide text-muted">Исполнители · {artists.length}</h2>
            <ul className="overflow-hidden rounded-2xl bg-surface">
              {artists.map((d) => (
                <li key={`a:${d.artist_ref}`} className="flex min-h-14 items-center gap-3 border-b border-fg/5 pl-4 pr-2 last:border-0">
                  <Icon name="ban" size={20} className="shrink-0 text-muted" />
                  {d.artist_ref && !d.artist_ref.startsWith('nd:') ? (
                    <Link to={`/artist/${enc(d.artist_ref)}`} className="min-w-0 flex-1 truncate py-2 hover:underline">
                      {d.artist || 'Исполнитель'}
                    </Link>
                  ) : (
                    <span className="min-w-0 flex-1 truncate py-2">{d.artist || 'Исполнитель'}</span>
                  )}
                  <Button small variant="ghost" onClick={() => void setDisliked({ artist_ref: d.artist_ref, artist: d.artist }, false)}>
                    Вернуть
                  </Button>
                </li>
              ))}
            </ul>
            <p className="px-1 pt-2 text-xs text-dim">Треков этих исполнителей нет в волне, миксах и открытиях, а в плейлистах они пропускаются. Нажатый вручную трек играет.</p>
          </section>
        )}
        {byArtist.map(([artistRef, list]) => (
          <section key={artistRef} aria-label={`Чужие релизы: ${list[0].artist}`}>
            <div className="flex items-baseline justify-between px-1 pb-2">
              <h2 className="text-[13px] font-semibold uppercase tracking-wide text-muted">
                Чужие релизы ·{' '}
                <Link to={`/artist/${enc(artistRef)}`} className="hover:text-fg hover:underline">
                  {list[0].artist}
                </Link>{' '}
                · {list.length}
              </h2>
              <button type="button" onClick={() => void bringBack(artistRef)} className="text-xs text-muted hover:text-fg">
                Вернуть все
              </button>
            </div>
            <ul className="overflow-hidden rounded-2xl bg-surface">
              {list.map((n) => (
                <li key={n.album_ref} className="flex min-h-14 items-center gap-3 border-b border-fg/5 pl-3 pr-2 last:border-0">
                  <Cover src={n.cover_url} size={40} icon="disc" alt="" />
                  <Link to={`/album/${enc(n.album_ref)}`} className="min-w-0 flex-1 py-2">
                    <span className="block truncate hover:underline">{n.title}</span>
                    <span className="block truncate text-[13px] text-muted">{[n.year, recordTypeLabel(n.record_type)].filter(Boolean).join(' · ')}</span>
                  </Link>
                  <Button small variant="ghost" onClick={() => void bringBack(artistRef, [n.album_ref])}>
                    Вернуть
                  </Button>
                </li>
              ))}
            </ul>
            <p className="px-1 pt-2 text-xs text-dim">Каталог держит тёзок под одним исполнителем. Этих релизов нет на его странице, в дискографии и в «Подписках» — у всех на этом сервере.</p>
          </section>
        ))}
        {tracks.length > 0 && (
          <section aria-label="Треки">
            <h2 className="px-1 pb-2 text-[13px] font-semibold uppercase tracking-wide text-muted">Треки · {tracks.length}</h2>
            <ul className="overflow-hidden rounded-2xl bg-surface">
              {tracks.map((d) => (
                <li key={`t:${d.id}:${d.ref ?? d.nd_song_id}`} className="flex min-h-14 items-center gap-3 border-b border-fg/5 pl-3 pr-2 last:border-0">
                  {/* a track of the library has its cover by its id; one known by the catalog alone has none kept */}
                  <Cover src={d.nd_song_id ? `/api/nd/cover/${enc(d.nd_song_id)}?size=96` : null} size={40} alt="" />
                  <span className="min-w-0 flex-1 py-2">
                    <span className="block truncate">{d.title || 'Трек'}</span>
                    {d.artist && <span className="block truncate text-[13px] text-muted">{d.artist}</span>}
                  </span>
                  <Button small variant="ghost" onClick={() => void setDisliked({ track_id: d.track_id, ref: d.ref, nd_song_id: d.nd_song_id, artist: d.artist, title: d.title }, false)}>
                    Вернуть
                  </Button>
                </li>
              ))}
            </ul>
            <p className="px-1 pt-2 text-xs text-dim">Эти треки не попадают в миксы и «Мою волну».</p>
          </section>
        )}
      </div>
    </div>
  );
}
