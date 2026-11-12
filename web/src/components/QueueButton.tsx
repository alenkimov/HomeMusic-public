import type { ReactNode } from 'react';
import { addToQueue, removeFromQueue, useListen, type ListenTrack } from '../lib/listen';
import { IconButton, cx } from './ui';

/** The quick queue button of a track row of the table (a computer): a click puts the track at the end of the page
 * player's queue, one more click takes it out again. Only what the user queued by hand is marked
 * (`ListenTrack.queued`), not the rest of the list that plays. The row is a `group/track`: the button shows on
 * hovering it, and always — green — once the track is queued. The playing track has it too: it can be queued to play
 * once more.
 * `instead`: what stands in its place while it is not shown — the row's number. */
export function QueueButton({ track, className, instead }: { track: ListenTrack; className?: string; instead?: ReactNode }) {
  const queuedAt = useListen((s) => {
    if (!s.track) return -1;
    for (let i = s.index + 1; i < s.queue.length; i++) if (s.queue[i].queued && s.queue[i].ref === track.ref) return i;
    return -1;
  });
  const queued = queuedAt >= 0;
  const button = (
    <IconButton
      icon="queue"
      size={20}
      label={queued ? `Убрать из очереди: «${track.title}»` : `В очередь: «${track.title}»`}
      onClick={(e) => {
        e.stopPropagation();
        if (queued) removeFromQueue(queuedAt);
        else addToQueue([track]);
      }}
      className={cx(
        queued ? '!text-accent' : 'text-muted opacity-0 hover:text-fg focus-visible:opacity-100 group-hover/track:opacity-100',
        instead !== undefined && '!absolute inset-0 !h-full !w-full',
        className,
      )}
    />
  );
  if (instead === undefined) return button;
  return (
    <span className="relative flex h-10 w-8 items-center justify-center text-sm tabular-nums text-dim">
      <span className={cx(queued ? 'opacity-0' : 'group-hover/track:opacity-0')}>{instead}</span>
      {button}
    </span>
  );
}
