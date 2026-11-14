import { useEffect } from 'react';
import { Icon } from '../components/Icon';
import { Button, Cover, Empty, PageHeader } from '../components/ui';
import { refreshDislikes, setDisliked, useDislikes } from '../lib/dislikes';
import { Link, enc } from '../lib/router';

/** «Скрытое» (from «Меню»): the artists the user does not listen to, and the tracks marked «Не нравится» — each can
 * be brought back here. An artist is hidden from their page or a track's menu; nothing of theirs plays by itself
 * after that (`lib/dislikes.ts`). */
export function HiddenPage() {
  const rows = useDislikes();
  useEffect(() => {
    void refreshDislikes(true);
  }, []);
  const artists = rows.filter((d) => d.artist_ref);
  const tracks = rows.filter((d) => !d.artist_ref);
  return (
    <div>
      <PageHeader title="Скрытое" back="/more" />
      <div className="space-y-5 px-4 pb-6">
        {!rows.length && <Empty icon="ban" title="Ничего не скрыто" text="Исполнителя можно скрыть на его странице, за тремя точками: его треки перестанут включаться сами." />}
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
