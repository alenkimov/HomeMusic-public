import { useEffect } from 'react';
import { tracksWord } from '../lib/format';
import { addToQueue } from '../lib/listen';
import { canPlayAll } from '../lib/player';
import { useRoute } from '../lib/router';
import { clearSelection, selectedTracks, useSelectionCount } from '../lib/selection';
import { sendToTelegram } from '../lib/telegram';
import { toast } from '../lib/ui';
import { Button, IconButton } from './ui';

/** What to do with the tracks picked in a list (`lib/selection.ts`): a bar over the player while something is
 * picked. Esc and leaving the page drop the selection. */
export function SelectionBar() {
  const count = useSelectionCount();
  const route = useRoute();
  useEffect(() => clearSelection(), [route.path]);
  useEffect(() => {
    if (!count) return;
    const key = (e: KeyboardEvent) => e.key === 'Escape' && clearSelection();
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, [count]);
  if (!count) return null;

  const take = () => {
    const list = selectedTracks();
    clearSelection();
    return list;
  };
  return (
    <div className="pointer-events-none fixed bottom-[calc(var(--tabbar-h)+var(--player-h,0px)+12px+env(safe-area-inset-bottom))] left-(--side-w) right-0 z-40 flex justify-center px-3" role="region" aria-label="Выбранные треки">
      <div className="anim-up pointer-events-auto flex max-w-full flex-wrap items-center gap-2 rounded-2xl border border-line bg-raised py-2 pl-4 pr-2 shadow-2xl">
        <span className="text-sm font-semibold">Выбрано: {tracksWord(count)}</span>
        <Button small variant="primary" icon="send" onClick={() => void sendToTelegram(take())}>
          В Telegram
        </Button>
        {!canPlayAll() && (
          <Button
            small
            icon="queue"
            onClick={() => {
              const list = take();
              addToQueue(list.map((t) => t.listen));
              toast(`В очереди: ${tracksWord(list.length)}`, 'success');
            }}
          >
            В очередь
          </Button>
        )}
        <IconButton icon="x" size={18} label="Снять выделение" onClick={clearSelection} className="!h-9 !w-9 text-muted hover:text-fg" />
      </div>
    </div>
  );
}
