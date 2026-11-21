import type { ReactNode } from 'react';
import { addToQueue, manualQueueKey, unqueue, useListen, type ListenTrack } from '../lib/listen';
import { IconButton, cx } from './ui';

/** Puts a track at the end of the account's manual queue, or takes it out again. The row is a
 * `group/track`: the button shows on
 * hovering it, and always — green — once the track is queued. The playing track has it too: it can be queued to play
 * once more.
 * `instead`: what stands in its place while it is not shown — the row's number. `marked`: the button is shown
 * whatever the mouse is over (a drag down the rows passes this one), so the number gives way. */
export function QueueButton({ track, className, instead, marked }: { track: ListenTrack; className?: string; instead?: ReactNode; marked?: boolean }) {
  const key = manualQueueKey(track);
  const queued = useListen((s) => s.manualQueue.some((entry) => manualQueueKey(entry.track) === key));
  const button = (
    <IconButton
      icon="queue"
      size={20}
      label={queued ? `Убрать из очереди: «${track.title}»` : `В очередь: «${track.title}»`}
      onClick={(e) => {
        e.stopPropagation();
        if (queued) void unqueue([track]);
        else void addToQueue([track]);
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
      <span className={cx(queued || marked ? 'opacity-0' : 'group-hover/track:opacity-0')}>{instead}</span>
      {button}
    </span>
  );
}
