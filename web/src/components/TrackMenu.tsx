import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { api } from '../api/client';
import { setDisliked, useDisliked, type DislikeTarget } from '../lib/dislikes';
import { STANDALONE } from '../lib/embedded';
import { addToQueue, next, playNext, stopListening, useListen, type ListenTrack } from '../lib/listen';
import { canPlayAll } from '../lib/player';
import { enc, navigate } from '../lib/router';
import { copyText } from '../lib/share';
import { attempt, toast } from '../lib/ui';
import { sendToTelegram } from '../lib/telegram';
import { artistSeed, canWave, startWave, trackSeed } from '../lib/wave';
import { Icon, type IconName } from './Icon';
import { PlaylistPicker, type PlaylistPick } from './pickers';
import { IconButton, Sheet, cx } from './ui';

export interface MenuTrack {
  title: string;
  artist: string;
  ref?: string | null;
  nd_song_id?: string | null;
  artist_ref?: string | null;
  album_ref?: string | null;
  /** the song's Navidrome album and artist (the library pages of a browser) */
  nd_album_id?: string | null;
  nd_artist_id?: string | null;
  /** the track for the page player's queue («Играть следующим», «В очередь»); none — not offered */
  listen?: ListenTrack;
}

/** An action a screen adds to the menu (a playlist's «Убрать из плейлиста»). */
export interface MenuExtra {
  icon: IconName;
  label: string;
  hint?: string;
  onClick: () => void;
}

/** ⋯ of a track row (catalog or library): «Играть следующим» / «В очередь» where the page's own player plays (a
 * browser; inside the app its player has them), «В плейлист», «К альбому» / «К исполнителю», «Волна по треку» /
 * «по исполнителю» (MIXES.md 7) where the wave can play, and «Не нравится» for the track and for its artist
 * (MIXES.md 3.2) — it keeps them out of mixes and «Моя волна»; set again, it is taken off. A track disliked while
 * the page's player plays it is skipped. */
export function TrackMenuButton({ track, extra, className, inPlayer }: { track: MenuTrack; extra?: MenuExtra[]; className?: string; inPlayer?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <IconButton icon="more" label={`Ещё: «${track.title}»`} onClick={() => setOpen(true)} className={cx('text-muted hover:text-fg', className)} />
      {open && createPortal(<TrackMenu track={track} extra={extra} inPlayer={inPlayer} onClose={() => setOpen(false)} />, document.body)}
    </>
  );
}

/** Library songs go into the playlist at once; a catalog track is downloaded into it (it appears once imported). */
export async function addToPlaylist(track: MenuTrack, pick: PlaylistPick): Promise<boolean> {
  const name = pick.kind === 'existing' ? pick.playlist.name : pick.name;
  if (track.nd_song_id) {
    const r = await attempt(async () => {
      const id = pick.kind === 'existing' ? pick.playlist.id : (await api.createPlaylist(pick.name)).id;
      return api.addToPlaylist(id, { song_ids: [track.nd_song_id as string] });
    }, `Добавлено в «${name}»`);
    return !!r;
  }
  if (!track.ref) return false;
  const r = await attempt(
    () =>
      api.download({
        track_refs: [track.ref as string],
        playlist_id: pick.kind === 'existing' ? pick.playlist.id : null,
        new_playlist_name: pick.kind === 'new' ? pick.name : null,
      }),
    `Скачиваем — трек появится в «${name}»`,
  );
  return !!r;
}

/** `inPlayer`: opened from a player (the mini player, `/player` and its queue): a wave started here is the player's. */
function TrackMenu({ track, extra, inPlayer, onClose }: { track: MenuTrack; extra?: MenuExtra[]; inPlayer?: boolean; onClose: () => void }) {
  const [picking, setPicking] = useState(false);
  const [linking, setLinking] = useState(false);
  const [busy, setBusy] = useState(false);
  const trackWave = trackSeed(track);
  const artistWave = artistSeed(track);
  const target: DislikeTarget = { ref: track.ref, nd_song_id: track.nd_song_id, artist: track.artist, title: track.title };
  const artist: DislikeTarget | null = track.artist_ref ? { artist_ref: track.artist_ref, artist: track.artist } : null;
  const disliked = useDisliked(target);
  const artistDisliked = useDisliked(artist ?? { artist_ref: '\u0000' });
  const playing = useListen((s) => !!track.ref && s.track?.ref === track.ref);
  const hasQueue = useListen((s) => !!s.track);
  // the page's own player plays here (a browser, an app without `playQueue`)
  const queueable = !!track.listen && !canPlayAll() && !playing;
  const albumTo = track.album_ref ? `/album/${enc(track.album_ref)}` : STANDALONE && track.nd_album_id ? `/lib/album/${enc(track.nd_album_id)}` : null;
  const artistTo = track.artist_ref ? `/artist/${enc(track.artist_ref)}` : STANDALONE && track.nd_artist_id ? `/lib/artist/${enc(track.nd_artist_id)}` : null;
  const go = (to: string) => {
    onClose();
    navigate(to);
  };

  const toggle = (t: DislikeTarget, on: boolean) => {
    onClose();
    void setDisliked(t, !on).then((ok) => {
      if (ok && !on && playing && !next()) stopListening();
    });
  };

  if (picking)
    return (
      <PlaylistPicker
        open
        busy={busy}
        onClose={onClose}
        onPick={async (pick) => {
          setBusy(true);
          const ok = await addToPlaylist(track, pick);
          setBusy(false);
          if (ok) onClose();
        }}
      />
    );

  if (linking) return <TrackLinks track={track} onClose={onClose} />;

  return (
    <Sheet open onClose={onClose} title={`${track.artist} — ${track.title}`}>
      <div className="-mx-4 -my-3 flex flex-col py-1">
        {queueable && (
          <>
            <MenuItem
              icon="queue"
              label="Играть следующим"
              onClick={() => {
                onClose();
                playNext([track.listen as ListenTrack]);
              }}
            />
            <MenuItem
              icon="plus"
              label="Добавить в очередь"
              hint={hasQueue ? undefined : 'Сейчас ничего не играет — начнёт играть'}
              onClick={() => {
                onClose();
                addToQueue([track.listen as ListenTrack]);
              }}
            />
          </>
        )}
        {(track.nd_song_id || track.ref) && (
          <MenuItem
            icon="playlist"
            label="Добавить в плейлист"
            hint={track.nd_song_id ? undefined : 'Трек скачается в библиотеку'}
            onClick={() => setPicking(true)}
          />
        )}
        {(track.nd_song_id || track.ref) && (
          <MenuItem
            icon="send"
            label="Отправить в Telegram"
            hint="Вашему боту, файлом из библиотеки"
            onClick={() => {
              onClose();
              void sendToTelegram(track);
            }}
          />
        )}
        {(track.nd_song_id || track.ref) && (
          <MenuItem
            icon="lyrics"
            label="Отправить в Telegram с текстом"
            hint="Текст песни придёт свёрнутой цитатой"
            onClick={() => {
              onClose();
              void sendToTelegram(track, true);
            }}
          />
        )}
        {(track.nd_song_id || track.ref) && (
          <MenuItem icon="link" label="Поделиться ссылкой" hint="Spotify, Яндекс Музыка, YouTube и другие" onClick={() => setLinking(true)} />
        )}
        {extra?.map((x) => (
          <MenuItem
            key={x.label}
            icon={x.icon}
            label={x.label}
            hint={x.hint}
            onClick={() => {
              onClose();
              x.onClick();
            }}
          />
        ))}
        {albumTo && <MenuItem icon="disc" label="Перейти к альбому" onClick={() => go(albumTo)} />}
        {artistTo && <MenuItem icon="user" label="Перейти к исполнителю" hint={track.artist} onClick={() => go(artistTo)} />}
        {canWave() && trackWave && (
          <MenuItem
            icon="wave"
            label="Волна по треку"
            hint="Бесконечный поток похожего, начиная с этого трека"
            onClick={() => {
              onClose();
              void startWave(trackWave, inPlayer ? 'player' : trackWave.ref?.startsWith('file:') ? 'search_file' : 'track_menu');
            }}
          />
        )}
        {canWave() && artistWave && (
          <MenuItem
            icon="wave"
            label="Волна по исполнителю"
            hint={track.artist}
            onClick={() => {
              onClose();
              void startWave(artistWave, inPlayer ? 'player' : 'artist_menu');
            }}
          />
        )}
        <MenuItem
          icon="ban"
          active={disliked}
          label={disliked ? 'Убрать «Не нравится»' : 'Не нравится'}
          hint={disliked ? 'Трек снова может попадать в миксы' : 'Трек не будет попадать в миксы и «Мою волну»'}
          onClick={() => toggle(target, disliked)}
        />
        {artist && (
          <MenuItem
            icon="user"
            active={artistDisliked}
            label={artistDisliked ? `Убрать «Не нравится» с исполнителя` : 'Не нравится исполнитель'}
            hint={track.artist}
            onClick={() => toggle(artist, artistDisliked)}
          />
        )}
      </div>
    </Sheet>
  );
}

type Links = Awaited<ReturnType<typeof api.links>>;

/** «Поделиться ссылкой»: the track on the services the Hub knows its id of. A tap copies the link; ↗ opens it. */
function TrackLinks({ track, onClose }: { track: MenuTrack; onClose: () => void }) {
  const [data, setData] = useState<Links | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let alive = true;
    api.links({ nd_song_id: track.nd_song_id, ref: track.ref })
      .then((r) => alive && setData(r))
      .catch(() => alive && setFailed(true));
    return () => {
      alive = false;
    };
  }, [track.nd_song_id, track.ref]);
  const copy = async (title: string, url: string) => {
    const ok = await copyText(url);
    toast(ok ? `Ссылка скопирована: ${title}` : 'Не получилось скопировать ссылку', ok ? 'success' : 'error');
    if (ok) onClose();
  };
  const rows = data ? [...(data.universal ? [{ service: 'universal', title: 'Все площадки сразу', url: data.universal }] : []), ...data.links] : [];
  return (
    <Sheet open onClose={onClose} title={`${track.artist} — ${track.title}`}>
      <div className="-mx-4 -my-3 flex flex-col py-1">
        {!data && !failed && <p className="px-4 py-6 text-sm text-muted">Собираем ссылки…</p>}
        {failed && <p className="px-4 py-6 text-sm text-muted">Не получилось получить ссылки.</p>}
        {data && !rows.length && (
          <p className="px-4 py-6 text-sm text-muted">Хаб не знает этот трек ни на одной площадке: он загружен файлом и не встречался в импортах.</p>
        )}
        {rows.map((x) => (
          <div key={x.service} className="flex min-h-14 items-center transition hover:bg-fg/5">
            <button type="button" onClick={() => void copy(x.title, x.url)} className="flex min-w-0 flex-1 items-center gap-4 px-4 py-2 text-left">
              <Icon name="link" size={22} className={cx('shrink-0', x.service === 'universal' ? 'text-accent' : 'text-muted')} />
              <span className="min-w-0 flex-1">
                <span className="block text-[15px]">{x.title}</span>
                <span className="block truncate text-[13px] text-muted">{x.service === 'universal' ? 'song.link — откроется там, где слушает получатель' : x.url.replace(/^https?:\/\/(www\.)?/, '')}</span>
              </span>
            </button>
            <a href={x.url} target="_blank" rel="noreferrer" aria-label={`Открыть: ${x.title}`} title="Открыть" className="mr-2 flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted transition hover:text-fg">
              <Icon name="external" size={18} />
            </a>
          </div>
        ))}
      </div>
    </Sheet>
  );
}

function MenuItem({ icon, label, hint, active, onClick }: { icon: IconName; label: string; hint?: string; active?: boolean; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="flex min-h-14 items-center gap-4 px-4 py-2 text-left transition hover:bg-fg/5 active:bg-fg/10">
      <Icon name={icon} size={22} className={cx('shrink-0', active ? 'text-accent' : 'text-muted')} />
      <span className="min-w-0 flex-1">
        <span className="block text-[15px]">{label}</span>
        {hint && <span className="block text-[13px] text-muted">{hint}</span>}
      </span>
    </button>
  );
}
