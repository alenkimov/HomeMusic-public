import type { MouseEvent } from 'react';
import { addToQueue, playNext, removeFromQueue, useListen, type ListenTrack } from '../lib/listen';
import { canPlayAll } from '../lib/player';
import { IconButton, cx } from './ui';

/** The quick queue button of a track row: a click puts the track at the end of the page player's queue, one more
 * click takes it out again; Shift+click or a right click plays it next. Only what the user queued by hand is marked
 * (`ListenTrack.queued`), not the rest of the list that plays. Where the app's own player plays (it keeps its own
 * queue) and on touch screens (the row is narrow; the ⋯ menu has both actions) the button is not drawn.
 * `hover`: the row is a `group/track` — the button shows on hovering it, and always once the track is queued. */
export function QueueButton({ track, hover, className }: { track: ListenTrack; hover?: boolean; className?: string }) {
  const queuedAt = useListen((s) => {
    if (!s.track) return -1;
    for (let i = s.index + 1; i < s.queue.length; i++) if (s.queue[i].queued && s.queue[i].ref === track.ref) return i;
    return -1;
  });
  const current = useListen((s) => s.track?.ref === track.ref);
  if (canPlayAll() || current) return null;
  const queued = queuedAt >= 0;
  const next = (e: MouseEvent) => {
    e.preventDefault();
    if (queued) removeFromQueue(queuedAt);
    playNext([track]);
  };
  return (
    <IconButton
      icon={queued ? 'check' : 'queue'}
      size={20}
      label={queued ? `Убрать из очереди: «${track.title}»` : `В очередь: «${track.title}» (Shift — следующим)`}
      onClick={(e) => (e.shiftKey ? next(e) : queued ? removeFromQueue(queuedAt) : addToQueue([track]))}
      onContextMenu={next}
      className={cx(
        'hidden pointer-fine:inline-flex',
        queued ? 'text-accent' : 'text-muted hover:text-fg',
        hover && !queued && 'opacity-0 focus-visible:opacity-100 group-hover/track:opacity-100',
        className,
      )}
    />
  );
}
