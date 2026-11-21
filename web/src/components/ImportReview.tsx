import { useState } from 'react';
import { api } from '../api/client';
import type { ImportReviewOut, TrackOut } from '../api/types';
import { filePickerTarget, openFindFile } from '../lib/findFile';
import { fmtDuration } from '../lib/format';
import { attempt } from '../lib/ui';
import { Icon } from './Icon';
import { ServiceBadge } from './ServiceBadge';
import { TrackTitle } from './TrackTitle';
import { Button, Cover, Sheet, cx } from './ui';

function differs(a?: string, b?: string): boolean {
  return !!a && !!b && a.trim().toLocaleLowerCase() !== b.trim().toLocaleLowerCase();
}

export function ImportReviewButton({ review, track, playing, onPlay }: {
  review: ImportReviewOut;
  track: TrackOut;
  playing: boolean;
  onPlay: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmed, setConfirmed] = useState<number>();
  if (confirmed === review.id) return null;

  const artist = track.artists?.length ? track.artists.join(', ') : track.artist;
  const titleChanged = differs(review.title, track.title);
  const artistChanged = differs(review.artist, artist);
  const delta = review.duration_s && track.duration_s ? Math.round(track.duration_s - review.duration_s) : 0;
  const differences = [
    titleChanged && 'Другое название',
    artistChanged && 'Другой исполнитель',
    Math.abs(delta) > 3 && `Запись на ${Math.abs(delta)} сек ${delta > 0 ? 'длиннее' : 'короче'}`,
  ].filter(Boolean).join(' · ');
  const target = filePickerTarget(track);
  const label = `Проверить совпадение: «${track.title}»`;

  const confirm = async () => {
    setBusy(true);
    if (await attempt(() => api.confirmImportMatch(review.id))) {
      setOpen(false);
      setConfirmed(review.id);
    }
    setBusy(false);
  };

  return (
    <span data-row-actions className="contents" onClick={(e) => e.stopPropagation()} onPointerDown={(e) => e.stopPropagation()}>
      <button type="button" aria-label={label} title={label} aria-haspopup="dialog" onClick={() => setOpen(true)}
        className="grid h-7 w-6 shrink-0 place-items-center rounded-md text-warn hover:bg-warn/15 focus-visible:bg-warn/15 pointer-coarse:h-11 pointer-coarse:w-11">
        <Icon name="alert" size={17} />
      </button>
      {open && (
        <Sheet open onClose={() => { if (!busy) setOpen(false); }} title="Проверить совпадение" wide footer={
          <div className="flex flex-wrap justify-end gap-2">
            {target && (
              <Button variant={differences ? 'primary' : 'ghost'} icon="search" disabled={busy}
                onClick={() => {
                  setOpen(false);
                  openFindFile({ ...target, has_file: track.owned, import_review: review });
                }}>
                {track.owned ? 'Найти другой файл' : 'Выбрать файл'}
              </Button>
            )}
            <Button variant={differences && target ? 'ghost' : 'primary'} icon="check" busy={busy} onClick={() => void confirm()}>Совпадает</Button>
          </div>
        }>
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="space-y-2 rounded-xl bg-bg/60 p-3">
              <p className="text-xs text-muted">Из <ServiceBadge service={review.source} /></p>
              <div className="flex items-center gap-3">
                <Cover src={review.cover_url} size={44} alt="" />
                <div className="min-w-0">
                  <p className="break-words text-sm font-semibold">{review.title}</p>
                  <p className="break-words text-xs text-muted">{[review.artist, fmtDuration(review.duration_s)].filter(Boolean).join(' · ')}</p>
                </div>
              </div>
            </div>
            <div className="space-y-2 rounded-xl bg-bg/60 p-3">
              <p className="text-xs text-muted">{track.owned ? 'Текущий файл' : 'Найденная запись'}</p>
              <div className="flex items-center gap-3">
                <button type="button" onClick={onPlay} aria-label={playing ? 'Пауза' : 'Прослушать'} className="group relative shrink-0 rounded-md">
                  <Cover src={track.cover_small_url || track.cover_url} size={44} alt="" />
                  <span className={cx('absolute inset-0 grid place-items-center rounded-md bg-black/60 text-white transition-opacity', !playing && 'pointer-fine:opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100')}>
                    <Icon name={playing ? 'pause' : 'play'} size={16} filled />
                  </span>
                </button>
                <div className="min-w-0">
                  <p className={cx('break-words text-sm font-semibold', titleChanged && 'text-warn')}><TrackTitle title={track.title} track={track} /></p>
                  <p className="break-words text-xs text-muted">
                    <span className={artistChanged ? 'text-warn' : undefined}>{artist}</span>
                    {artist && track.duration_s ? ' · ' : ''}
                    <span className={Math.abs(delta) > 3 ? 'text-warn' : undefined}>{fmtDuration(track.duration_s)}</span>
                  </p>
                </div>
              </div>
            </div>
          </div>
          {differences && <p className="mt-3 flex items-center gap-2 text-xs text-warn"><Icon name="alert" size={15} className="shrink-0" />{differences}</p>}
        </Sheet>
      )}
    </span>
  );
}
