import { useState, type ReactNode } from 'react';
import type { TidyProposalOut, TwinAlbum, TwinClue, TwinTrack } from '../api/types';
import { Icon, type IconName } from '../components/Icon';
import { Button, Checkbox, Cover, cx } from '../components/ui';
import { fmtWhen, plural } from '../lib/format';
import { Link, artistPath } from '../lib/router';
import type { TidyDecision } from '../api/client';

const CLUE_ICON: Record<TwinClue['kind'], IconName> = { alias: 'user', sound: 'wave', album: 'disc' };

/** «Двойники» of «Наведение порядка»: one person under two names, with their albums and tracks — one case. Who
 * goes into whom, why they are one (the clues), then the steps in the order they are done: the artist, the albums,
 * the recordings — each one ticked, to be left out; what comes out, and the answers: join it all, his project, two
 * people. Decided (or in «Журнал»), the card is the two names and the answer, to be taken back. */
export function TwinCard({ p, busy, onDecide, journal, lit, actions }: {
  p: TidyProposalOut; busy: boolean; onDecide: (what: TidyDecision, skip?: string[]) => void; journal?: boolean; lit?: boolean;
  /** the answers of a decided card, as every card of the list has them */
  actions: ReactNode;
}) {
  const [skip, setSkip] = useState<Set<string>>(() => new Set());
  const decided = p.state !== 'open';
  const c = p.show.case;
  const on = (key: string) => !skip.has(key);
  const toggle = (keys: string[], value: boolean) => setSkip((was) => {
    const next = new Set(was);
    for (const k of keys) {
      if (value) next.delete(k);
      else next.add(k);
    }
    return next;
  });
  const albumKeys = (c?.albums ?? []).map((a) => `album:${a.source.id}`);
  const trackKeys = (c?.tracks ?? []).map((t) => `track:${t.gone.id}`);
  const count = (line?: string | null) => Number(/\d+/.exec(line ?? '')?.[0] ?? 0);
  const joined = trackKeys.filter(on).length;
  const total = count(p.show.line) + count(p.show.into_line) - joined;
  const nothing = !on('artist') && !albumKeys.some(on) && !joined;
  const into = artistPath({ ref: '', hub_artist_id: p.show.into_id });
  const other = artistPath({ ref: '', hub_artist_id: p.show.artist_id ?? p.subject_id });
  const frame = cx('rounded-2xl bg-raised/60 p-4 transition sm:p-5', lit && 'bg-ai/10 ring-2 ring-ai', decided && !journal && 'opacity-60');

  const said = p.state === 'accepted' ? (p.show.answer === 'project' ? 'Его проект' : 'Объединено')
    : p.state === 'rejected' ? 'Разные люди' : p.state === 'reverted' ? 'Разъединено' : '';
  const who = (
    <div className="flex items-center gap-3 max-sm:flex-col max-sm:items-start sm:gap-5">
      <Link to={other} className="flex min-w-0 flex-1 items-center gap-3">
        <Cover src={p.show.picture} size={56} round icon="user" />
        <span className="min-w-0">
          <span className="block truncate text-[17px] font-bold">{p.show.name || p.was}</span>
          {p.show.line && <span className="block text-[13px] text-muted">{p.show.line}</span>}
        </span>
      </Link>
      <Icon name="chevron" size={26} className="shrink-0 text-ai max-sm:ml-4 max-sm:rotate-90" />
      <Link to={into} className="flex min-w-0 flex-1 items-center gap-3">
        <Cover src={p.show.into_picture} size={56} round icon="user" className="rounded-full ring-2 ring-ai ring-offset-[3px] ring-offset-surface" />
        <span className="min-w-0">
          <span className="block truncate text-[17px] font-bold">{p.show.into_name || p.to}</span>
          {p.show.into_line && <span className="block text-[13px] font-semibold text-ai">{p.show.into_line}</span>}
        </span>
      </Link>
    </div>
  );

  if (decided || journal || !c) {
    return (
      <div className={frame}>
        {who}
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <span className="min-w-0 flex-1 text-[12px] text-dim">
            {[said, p.decided_by, p.decided_at ? fmtWhen(p.decided_at) : null].filter(Boolean).join(' · ') || p.why}
          </span>
          {actions && <div className="shrink-0 max-sm:w-full">{actions}</div>}
        </div>
      </div>
    );
  }

  return (
    <section aria-label={`Двойник: ${p.show.name} и ${p.show.into_name}`} className={frame}>
      {who}

      {!!p.show.clues?.length && (
        <div className="mt-4">
          <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-dim">Почему это один человек</p>
          <ul className="flex flex-wrap gap-2">
            {p.show.clues.map((clue) => (
              <li key={clue.kind} className="flex items-center gap-1.5 rounded-full bg-raised px-3 py-1.5 text-[13px]">
                <Icon name={CLUE_ICON[clue.kind]} size={14} className="text-ai" />
                {clue.text}
              </li>
            ))}
          </ul>
        </div>
      )}

      <ol className="mt-4">
        <Step n={1} last={!c.albums.length && !c.tracks.length} title="Исполнитель" checked={on('artist')} onChange={(v) => toggle(['artist'], v)}>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-xl bg-raised px-4 py-3 text-[14px]">
            <s className="text-muted decoration-muted">{p.show.name}</s>
            <span className="text-dim">→</span>
            <span className="font-semibold">{p.show.into_name}</span>
            <span className="text-[13px] text-dim sm:ml-auto">старое имя останется вторым: по нему он тоже найдётся</span>
          </div>
        </Step>

        {!!c.albums.length && (
          <Step n={2} last={!c.tracks.length} title={`Альбомы · ${c.albums.length}`} checked={albumKeys.some(on)} onChange={(v) => toggle(albumKeys, v)}>
            <ul className="overflow-hidden rounded-xl bg-raised">
              {c.albums.map((a) => (
                <li key={a.source.id} className="flex items-center gap-2 border-line px-2 py-1.5 sm:gap-3 [&+&]:border-t">
                  <Checkbox checked={on(`album:${a.source.id}`)} onChange={(v) => toggle([`album:${a.source.id}`], v)} label={`Объединить альбом «${a.source.title}»`} />
                  <AlbumLine album={a.source} gone />
                  <span className="text-dim">→</span>
                  <AlbumLine album={a.target} />
                </li>
              ))}
            </ul>
          </Step>
        )}

        {!!c.tracks.length && (
          <Step n={c.albums.length ? 3 : 2} last title={`Записаны дважды · ${c.tracks.length}`} checked={trackKeys.some(on)} onChange={(v) => toggle(trackKeys, v)}>
            <ul className="overflow-hidden rounded-xl bg-raised">
              {c.tracks.map((t) => (
                <li key={t.gone.id} className="flex items-center gap-2 border-line px-2 py-1.5 sm:gap-3 [&+&]:border-t">
                  <Checkbox checked={on(`track:${t.gone.id}`)} onChange={(v) => toggle([`track:${t.gone.id}`], v)} label={`Объединить «${t.gone.title}»`} />
                  <TrackLine track={t.gone} gone />
                  <span className="text-dim max-sm:hidden">→</span>
                  <TrackLine track={t.keep} note={moved(t.moves)} />
                </li>
              ))}
            </ul>
          </Step>
        )}
      </ol>

      <div className="mt-4 flex gap-3 rounded-xl bg-accent/10 px-4 py-3 text-[14px]">
        <Icon name="merge" size={20} className="mt-0.5 shrink-0 text-accent" />
        <span className="min-w-0">
          <span className="block font-semibold">
            {nothing ? 'Ничего не выбрано' : on('artist') ? `Получится: ${p.show.into_name} — ${total} ${plural(total, ['трек', 'трека', 'треков'])}` : 'Исполнители останутся двумя'}
          </span>
          <span className="block text-muted">
            Ничего не удаляется: лайки, плейлисты и прослушивания переходят на то, что остаётся. Лишние файлы уходят в корзину, всё дело откатывается одной кнопкой.
          </span>
        </span>
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="min-w-0 flex-1 basis-40 text-[12px] text-dim">{p.sure ? 'Все улики сходятся' : 'Похоже, один человек'}</span>
        <span className="flex flex-wrap gap-2 max-sm:w-full">
          <Button small variant="outline" disabled={busy} onClick={() => onDecide('reject')} className="whitespace-nowrap max-sm:flex-1">Разные люди</Button>
          <Button small variant="outline" disabled={busy} onClick={() => onDecide('project')} className="whitespace-nowrap max-sm:flex-1">Его проект</Button>
          <Button small variant="primary" busy={busy} disabled={nothing} onClick={() => onDecide('accept', [...skip])} className="whitespace-nowrap max-sm:w-full">
            {skip.size ? 'Объединить выбранное' : 'Объединить всё'}
          </Button>
        </span>
      </div>
    </section>
  );
}

function moved(m: { liked: boolean; playlists: number; plays: number }): string {
  const parts = [
    m.liked && '♥',
    m.playlists ? `${m.playlists} ${plural(m.playlists, ['плейлист', 'плейлиста', 'плейлистов'])}` : null,
    m.plays ? `${m.plays} ${plural(m.plays, ['прослушивание', 'прослушивания', 'прослушиваний'])}` : null,
  ].filter(Boolean);
  return parts.length ? `перейдут: ${parts.join(', ')}` : '';
}

/** A step of the case: its number on a line down to the next, its tick, its title, what it does. */
function Step({ n, last, title, checked, onChange, children }: {
  n: number; last?: boolean; title: string; checked: boolean; onChange: (v: boolean) => void; children: ReactNode;
}) {
  return (
    <li className="flex gap-3">
      <div className="flex w-7 shrink-0 flex-col items-center pt-2.5">
        <span className={cx('flex h-7 w-7 items-center justify-center rounded-full text-[13px] font-bold', checked ? 'bg-accent text-bg' : 'bg-raised text-dim')}>{n}</span>
        {!last && <span aria-hidden="true" className="mt-1 w-0.5 flex-1 bg-line" />}
      </div>
      <div className={cx('min-w-0 flex-1', !last && 'pb-4', !checked && 'opacity-50')}>
        <div className="-ml-2 flex items-center">
          <Checkbox checked={checked} onChange={onChange} label={title} />
          <span className="text-[15px] font-semibold">{title}</span>
        </div>
        {children}
      </div>
    </li>
  );
}

function AlbumLine({ album, gone }: { album: TwinAlbum; gone?: boolean }) {
  return (
    <span className="flex min-w-0 flex-1 items-center gap-3">
      <Cover src={album.cover} size={44} icon="disc" />
      <span className="min-w-0">
        <span className={cx('block truncate text-[15px]', gone ? 'text-muted' : 'font-semibold')}>{album.title}</span>
        <span className="block truncate text-[13px] text-dim">
          {album.artist} · {album.tracks} {plural(album.tracks, ['трек', 'трека', 'треков'])}
        </span>
      </span>
    </span>
  );
}

function TrackLine({ track, gone, note }: { track: TwinTrack; gone?: boolean; note?: string }) {
  return (
    <span className={cx('min-w-0 flex-1', gone && 'max-sm:hidden')}>
      <span className={cx('block truncate text-[15px]', gone ? 'text-muted' : 'font-semibold')}>{track.title}</span>
      <span className="block truncate text-[13px] text-dim">{[track.artist, track.file].filter(Boolean).join(' · ')}</span>
      {note && <span className="block text-[12px] text-accent">{note}</span>}
    </span>
  );
}
