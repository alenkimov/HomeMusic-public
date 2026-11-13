import { useEffect, useState } from 'react';
import type { NdSongOut } from '../api/types';
import { useDesktop } from '../lib/desktop';
import { goBack } from '../lib/router';
import { filterActive } from '../lib/tags';
import { playAll } from './catalog';
import { Icon } from './Icon';
import type { ListSearch } from './ListSearch';
import { TagFilterBody, TagFilterChips, type ListTags } from './Tags';
import { Button, IconButton, Sheet, cx } from './ui';

/** The list's head has gone up under the bar: its name (`data-list-title` of `Hero`) is not seen any more. */
function useHeadGone(): boolean {
  const [gone, setGone] = useState(false);
  useEffect(() => {
    const look = () => {
      const name = document.querySelector('[data-list-title]');
      setGone(!!name && name.getBoundingClientRect().bottom < 56);
    };
    look();
    window.addEventListener('scroll', look, { passive: true });
    window.addEventListener('resize', look);
    return () => {
      window.removeEventListener('scroll', look);
      window.removeEventListener('resize', look);
    };
  }, []);
  return gone;
}

/** How much of the screen's bottom the keyboard takes (0: it is not up). */
function useKeyboard(): number {
  const [h, setH] = useState(0);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const look = () => setH(Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop)));
    look();
    vv.addEventListener('resize', look);
    vv.addEventListener('scroll', look);
    return () => {
      vv.removeEventListener('resize', look);
      vv.removeEventListener('scroll', look);
    };
  }, []);
  return h;
}

/** «Закрыть поиск» of a phone, at the right just above the keyboard: the thumb is there, the bar's own «back» is at
 * the far corner. With the keyboard down it stays above the player. */
function SearchDown({ onClose }: { onClose: () => void }) {
  const keyboard = useKeyboard();
  return (
    <button
      type="button"
      aria-label="Закрыть поиск"
      onClick={onClose}
      style={keyboard > 80 ? { bottom: keyboard + 12 } : undefined}
      className="fixed bottom-[calc(var(--tabbar-h)+var(--player-h,0px)+16px+env(safe-area-inset-bottom))] right-3 z-40 flex h-12 w-12 items-center justify-center rounded-full bg-raised text-fg shadow-2xl active:scale-95"
    >
      <Icon name="down" size={24} />
    </button>
  );
}

/** The bar of a list of tracks (a playlist, «Любимое», a selection): it stays at the top of the screen. Over the
 * list's head it is only «back» and, at the right, the tags and the search; once the head has gone up it gets the
 * list's name and ▶. The search opens in the bar itself; the tags — in a window (`TagFilterBody`), and the ones
 * chosen stay in a line under the bar. */
export function ListBar({ title, back, tracks, context, search, tags }: { title: string; back: string; tracks?: readonly NdSongOut[]; context?: string; search?: ListSearch; tags?: ListTags }) {
  const desktop = useDesktop();
  const gone = useHeadGone();
  const [tagsOpen, setTagsOpen] = useState(false);
  const narrowed = !!tags && filterActive(tags.filter);
  const solid = gone || !!search?.alone;
  const play = gone && !!tracks?.length && (
    <button
      type="button"
      aria-label="Слушать"
      title="Слушать"
      onClick={() => playAll(tracks, { context })}
      className="ml-1 mr-2 flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-accent text-on-accent transition hover:scale-105 active:scale-95"
    >
      <Icon name="play" size={20} filled className="translate-x-px" />
    </button>
  );
  // the windows are beside the bar, not in it: the bar is a layer of its own, under the player and the tabs
  return (
    <>
    <header className={cx('pt-safe sticky top-0 z-20 transition-colors', solid ? 'bg-bg' : 'bg-transparent')}>
      <div className="relative flex min-h-14 items-center gap-1 px-2">
        {search?.alone ? (
          <>
            <IconButton icon="back" label="Закрыть поиск" onClick={search.close} />
            <div className="flex min-w-0 flex-1 pr-2">{search.field}</div>
          </>
        ) : (
          <>
            <IconButton icon="back" label="Назад" onClick={() => goBack(back)} className={solid ? undefined : 'ml-1 !h-10 !w-10 bg-bg/40'} />
            <h1 className={cx('min-w-0 flex-1 truncate px-1 text-lg font-bold transition-opacity', gone ? 'opacity-100' : 'opacity-0')} aria-hidden={!gone}>
              {title}
            </h1>
            {tags && <IconButton icon="tag" label={narrowed ? 'Теги: отбор включён' : 'Теги'} aria-pressed={tagsOpen} onClick={() => setTagsOpen((v) => !v)} className={narrowed ? '!text-accent' : undefined} />}
            {search && (search.open ? <div className="flex w-80 pl-1">{search.field}</div> : <IconButton icon="search" label="Поиск по списку (Ctrl+F)" onClick={search.show} />)}
            {play}
          </>
        )}
        {tags && tagsOpen && desktop && (
          <>
            <button type="button" aria-label="Закрыть теги" className="fixed inset-0 z-10 cursor-default" onClick={() => setTagsOpen(false)} />
            <div className="anim-up absolute right-4 top-full z-20 max-h-[70dvh] w-96 overflow-y-auto rounded-2xl bg-raised p-4 shadow-2xl" role="dialog" aria-label="Теги">
              <TagFilterBody {...tags} wrap />
              <div className="flex items-center justify-between pt-3 text-sm text-muted">
                <span className="tabular-nums">{tags.count} из {tags.songIds.length}</span>
                {narrowed && (
                  <button type="button" className="text-fg hover:underline" onClick={() => tags.onChange({})}>
                    Сбросить
                  </button>
                )}
              </div>
            </div>
          </>
        )}
      </div>
      {tags && narrowed && <TagFilterChips filter={tags.filter} onChange={tags.onChange} count={tags.count} />}
    </header>
      {tags && !desktop && (
        <Sheet
          open={tagsOpen}
          onClose={() => setTagsOpen(false)}
          title="Теги"
          footer={
            <div className="flex gap-2">
              <Button className="flex-1" disabled={!narrowed} onClick={() => tags.onChange({})}>
                Сбросить
              </Button>
              <Button variant="primary" className="flex-[2]" onClick={() => setTagsOpen(false)}>
                Показать {tags.count}
              </Button>
            </div>
          }
        >
          <TagFilterBody {...tags} wrap={false} />
        </Sheet>
      )}
      {search?.alone && <SearchDown onClose={search.close} />}
    </>
  );
}
