import { useEffect, useState } from 'react';
import { tracksWord } from '../lib/format';
import { addToQueue } from '../lib/listen';
import { canPlayAll } from '../lib/player';
import { useRoute } from '../lib/router';
import { clearSelection, selectedTracks, useSelectionCount } from '../lib/selection';
import { openShare } from '../lib/shareTrack';
import { isLiked, toggleLike, useLiked } from '../lib/stars';
import { toast } from '../lib/ui';
import { BulkPlaylists } from './PlaylistsButton';
import { IconButton, cx } from './ui';

/** What to do with the tracks picked in a list (`lib/selection.ts`): a bar over the player while something is
 * picked. Esc and leaving the page drop the selection. */
export function SelectionBar() {
  const count = useSelectionCount();
  const route = useRoute();
  const [liking, setLiking] = useState(false);
  const [lists, setLists] = useState<string[] | null>(null);
  useLiked({}); // the bar follows the ♥ of the picked tracks
  useEffect(() => clearSelection(), [route.path]);
  useEffect(() => {
    if (!count || lists) return; // while the playlists window is open, Esc closes it, not the selection
    const key = (e: KeyboardEvent) => e.key === 'Escape' && clearSelection();
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [count, lists]);
  if (!count) return null;

  const take = () => {
    const list = selectedTracks();
    clearSelection();
    return list;
  };
  const picked = selectedTracks();
  const targets = picked.map((t) => ({ songId: t.nd_song_id, ref: t.ref, data: { ...t.listen } }));
  const allLiked = targets.every(isLiked);
  const owned = picked.map((t) => t.nd_song_id).filter((x): x is string => !!x);
  // ♥ of the whole selection: on for those that lack it; when every one has it — off for all
  const like = async () => {
    setLiking(true);
    const todo = targets.filter((t) => allLiked || !isLiked(t));
    for (const t of todo) await toggleLike(t, true);
    setLiking(false);
    toast(allLiked ? `Убрано из понравившихся: ${tracksWord(todo.length)}` : `В понравившихся: ${tracksWord(todo.length)}`, 'success');
  };
  const btn = '!h-10 !w-10';
  return (
    <div className="pointer-events-none fixed bottom-[calc(var(--tabbar-h)+var(--player-h,0px)+12px+env(safe-area-inset-bottom))] left-(--side-w) right-0 z-40 flex justify-center px-3" role="region" aria-label="Выбранные треки">
      <div className="anim-up pointer-events-auto flex max-w-full items-center gap-1 rounded-2xl border border-line bg-raised py-1.5 pl-4 pr-1.5 shadow-2xl">
        <span className="mr-2 whitespace-nowrap text-sm font-semibold">{tracksWord(count)}</span>
        <IconButton icon="heart" filled={allLiked} size={20} busy={liking} label={allLiked ? 'Убрать все из понравившихся' : 'Все — в понравившиеся'} onClick={() => void like()} className={cx(btn, allLiked ? '!text-accent' : 'text-muted hover:text-fg')} />
        <IconButton
          icon="playlist"
          size={20}
          label="В плейлисты: добавить или убрать"
          onClick={() => (owned.length ? setLists(owned) : toast('Этих треков ещё нет в библиотеке — сначала скачайте их', 'info'))}
          className={cx(btn, 'text-muted hover:text-fg')}
        />
        {!canPlayAll() && (
          <IconButton
            icon="queue"
            size={20}
            label="В очередь"
            onClick={() => {
              const list = take();
              addToQueue(list.map((t) => t.listen));
              toast(`В очереди: ${tracksWord(list.length)}`, 'success');
            }}
            className={cx(btn, 'text-muted hover:text-fg')}
          />
        )}
        <IconButton icon="share" size={20} label="Поделиться" onClick={() => openShare(take())} className={cx(btn, 'text-muted hover:text-fg')} />
        <span className="mx-1 h-6 w-px bg-line" aria-hidden="true" />
        <IconButton icon="x" size={18} label="Снять выделение" onClick={clearSelection} className={cx(btn, 'text-muted hover:text-fg')} />
      </div>
      {lists && (
        <div className="pointer-events-auto">
          <BulkPlaylists songIds={lists} onClose={() => setLists(null)} />
        </div>
      )}
    </div>
  );
}
