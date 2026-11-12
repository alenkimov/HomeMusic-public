import { useEffect, useState } from 'react';
import type { PickedTrack } from './PlaylistsButton';
import { tracksWord } from '../lib/format';
import { addToQueue } from '../lib/listen';
import { canPlayAll } from '../lib/player';
import { useRoute } from '../lib/router';
import { clearSelection, selectedTracks, useSelectionCount } from '../lib/selection';
import { openShare } from '../lib/shareTrack';
import { openTagsSheet } from '../lib/tags';
import { toast } from '../lib/ui';
import { inPlaylists, useInPlaylists } from '../lib/inPlaylists';
import { isLiked, useLiked } from '../lib/stars';
import { BulkPlaylists, SaveMark } from './PlaylistsButton';
import { IconButton, cx } from './ui';

/** What to do with the tracks picked in a list (`lib/selection.ts`): a bar over the player while something is
 * picked. Esc and leaving the page drop the selection. */
export function SelectionBar() {
  const count = useSelectionCount();
  const route = useRoute();
  const [lists, setLists] = useState<PickedTrack[] | null>(null);
  useLiked({}); // the bar follows where the picked tracks are kept
  useInPlaylists(null);
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
  const likes = picked.map((t) => isLiked({ songId: t.nd_song_id, ref: t.ref }));
  // as a row's button, for them all: ♥ when every one is in «Любимое», ✓ when every one is kept somewhere, else «+»
  const kept = likes.every(Boolean) ? 'liked' : picked.every((t, i) => likes[i] || inPlaylists(t.nd_song_id)) ? 'listed' : 'none';
  const tagKeys = picked.map((t) => t.tag_key).filter((x): x is string => !!x);
  const btn = '!h-10 !w-10';
  return (
    <div className="pointer-events-none fixed bottom-[calc(var(--tabbar-h)+var(--player-h,0px)+12px+env(safe-area-inset-bottom))] left-(--side-w) right-0 z-40 flex justify-center px-3" role="region" aria-label="Выбранные треки">
      <div className="anim-up pointer-events-auto flex max-w-full items-center gap-1 rounded-2xl border border-line bg-raised py-1.5 pl-4 pr-1.5 shadow-2xl">
        <span className="mr-2 whitespace-nowrap text-sm font-semibold">{tracksWord(count)}</span>
        <button
          type="button"
          aria-label="В «Любимое» и плейлисты: добавить или убрать"
          title="В «Любимое» и плейлисты"
          onClick={() => setLists(picked.map((t) => ({ ref: t.ref, nd_song_id: t.nd_song_id, key: t.tag_key, data: { ...t.listen } })))}
          className={cx('flex h-10 w-10 shrink-0 items-center justify-center rounded-full transition hover:scale-110', kept === 'none' ? 'text-muted hover:text-fg' : 'text-accent')}
        >
          <SaveMark state={kept} size={18} />
        </button>
        <IconButton
          icon="tag"
          size={20}
          label="Теги: поставить или снять"
          onClick={() => (tagKeys.length ? openTagsSheet(tagKeys, '') : toast('Хаб ещё не знает эти треки — добавьте их в плейлист или «Любимое»', 'info'))}
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
          <BulkPlaylists tracks={lists} onClose={() => setLists(null)} />
        </div>
      )}
    </div>
  );
}
