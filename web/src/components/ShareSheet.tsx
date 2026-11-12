import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { tracksWord } from '../lib/format';
import { copyText } from '../lib/share';
import { closeShare, useShare } from '../lib/shareTrack';
import { deliverToTelegram, openTelegramSetup, setShareWithLink, shareWithLink, type ShareTrack, type TelegramChat, type TelegramStatus } from '../lib/telegram';
import { attempt, toast } from '../lib/ui';
import { BrandIcon, hasBrand } from './BrandIcon';
import { Icon } from './Icon';
import { Checkbox, Sheet, Spinner, cx } from './ui';

type Links = Awaited<ReturnType<typeof api.links>>;

const KIND: Record<string, string> = { private: 'личный чат', group: 'группа', supergroup: 'группа', channel: 'канал' };

/** «Поделиться»: every way the Hub can share a track, in one window. On top, the chats of the user's own Telegram
 * bot — the private one («Мне») and the groups and channels they added the bot to; a tap sends the file there. Below,
 * for a single track, its links on the music services the Hub knows it on; a tap copies, ↗ opens. */
export function ShareSheet() {
  const tracks = useShare();
  if (!tracks.length) return null;
  return <Body key={tracks.map((t) => t.nd_song_id ?? t.ref).join('|')} tracks={tracks} />;
}

function Body({ tracks }: { tracks: readonly ShareTrack[] }) {
  const one = tracks.length === 1 ? tracks[0] : null;
  const [st, setSt] = useState<TelegramStatus | null>(null);
  const [chats, setChats] = useState<TelegramChat[] | null>(null);
  const [links, setLinks] = useState<Links | null>(null);
  const [withLyrics, setWithLyrics] = useState(false);
  const [withLink, setWithLink] = useState(shareWithLink);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    let alive = true;
    api.telegram()
      .then((r) => {
        if (!alive) return;
        setSt(r);
        setWithLyrics(!!r.with_lyrics);
        if (r.ready) api.telegramChats().then((c) => alive && setChats(c)).catch(() => alive && setChats([]));
      })
      .catch(() => alive && setSt({ connected: false, ready: false }));
    if (one) api.links({ nd_song_id: one.nd_song_id, ref: one.ref }).then((r) => alive && setLinks(r)).catch(() => alive && setLinks({ links: [] }));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const send = (chat: TelegramChat) => {
    closeShare();
    void deliverToTelegram(tracks, withLyrics, chat);
  };
  const refresh = async () => {
    setRefreshing(true);
    const r = await attempt(() => api.telegramChatsRefresh());
    setRefreshing(false);
    if (r) setChats(r);
  };
  const copy = async (title: string, url: string) => {
    const ok = await copyText(url);
    toast(ok ? `Ссылка скопирована: ${title}` : 'Не получилось скопировать ссылку', ok ? 'success' : 'error');
    if (ok) closeShare();
  };
  const rows = links ? [...(links.homemusic ? [{ service: 'homemusic', title: 'HomeMusic', url: links.homemusic }] : []), ...(links.universal ? [{ service: 'universal', title: 'Все площадки сразу', url: links.universal }] : []), ...links.links] : [];
  const label = 'px-4 pb-1 pt-3 text-xs text-muted';

  return (
    <Sheet open onClose={closeShare} title={one ? `${one.artist ? `${one.artist} — ` : ''}${one.title}` : `Поделиться: ${tracksWord(tracks.length)}`}>
      <div className="-mx-4 -my-3 flex flex-col pb-2">
        <div className={label}>Отправить {one ? 'файлом' : 'файлами'} в Telegram</div>
        {!st ? (
          <div className="flex h-24 items-center px-4 text-muted"><Spinner size={16} /></div>
        ) : !st.ready ? (
          <button
            type="button"
            onClick={() => {
              closeShare();
              openTelegramSetup([...tracks], null);
            }}
            className="flex min-h-14 items-center gap-4 px-4 py-2 text-left transition hover:bg-fg/5"
          >
            <Icon name="send" size={22} className="shrink-0 text-accent" />
            <span className="min-w-0 flex-1">
              <span className="block text-[15px]">Подключить своего бота</span>
              <span className="block text-[13px] text-muted">Он отправляет треки вам в личный чат, в группы и каналы</span>
            </span>
          </button>
        ) : (
          <>
            <div className="flex gap-1 overflow-x-auto px-3 pb-1 pt-1">
              {!chats && <div className="flex h-24 items-center px-1 text-muted"><Spinner size={16} /></div>}
              {chats?.map((c) => <ChatTile key={c.id} chat={c} onClick={() => send(c)} />)}
              {chats && (
                <button type="button" onClick={() => void refresh()} disabled={refreshing} title="Обновить список чатов" className="flex w-[4.75rem] shrink-0 flex-col items-center rounded-xl px-1 py-2 text-center transition hover:bg-fg/5">
                  <span className="flex h-[52px] w-[52px] items-center justify-center rounded-full border border-dashed border-line text-muted">
                    {refreshing ? <Spinner size={16} /> : <Icon name="refresh" size={20} />}
                  </span>
                  <span className="mt-1.5 block w-full truncate text-xs text-muted">Обновить</span>
                  <span className="block text-[11px] text-dim">чаты</span>
                </button>
              )}
            </div>
            {chats && chats.length < 2 && (
              <p className="px-4 pb-1 text-xs text-dim">Добавьте @{st.bot_username} в группу или канал сами — чат появится здесь.</p>
            )}
            <label className="flex cursor-pointer items-center justify-between gap-3 px-4 py-2 text-sm">
              <span>
                С текстом песни
                <span className="block text-xs text-muted">Свёрнутой цитатой под треком</span>
              </span>
              <Checkbox
                checked={withLyrics}
                label="С текстом песни"
                onChange={(v) => {
                  setWithLyrics(v);
                  void api.telegramPrefs(v).catch(() => undefined);
                }}
              />
            </label>
            <label className="flex cursor-pointer items-center justify-between gap-3 px-4 py-2 text-sm">
              <span>
                С кнопкой «Открыть в HomeMusic»
                <span className="block text-xs text-muted">Получатель найдёт трек на своём сервере</span>
              </span>
              <Checkbox
                checked={withLink}
                label="С кнопкой «Открыть в HomeMusic»"
                onChange={(v) => {
                  setWithLink(v);
                  setShareWithLink(v);
                }}
              />
            </label>
          </>
        )}

        {one && (
          <>
            <div className={cx(label, 'mt-1 border-t border-line')}>Скопировать ссылку</div>
            {!links && <div className="flex h-14 items-center px-4 text-muted"><Spinner size={16} /></div>}
            {links && !rows.length && <p className="px-4 py-3 text-sm text-muted">Ссылок нет: Хаб не знает этот трек на площадках — он загружен файлом и не встречался в импортах.</p>}
            {rows.map((x) => (
              <div key={x.service} className="flex min-h-14 items-center transition hover:bg-fg/5">
                <button type="button" onClick={() => void copy(x.title, x.url)} className="flex min-w-0 flex-1 items-center gap-4 px-4 py-2 text-left">
                  <span className="flex w-6 shrink-0 justify-center">
                    {hasBrand(x.service) ? <BrandIcon service={x.service} /> : <Icon name={x.service === 'homemusic' ? 'disc' : 'link'} size={22} className={x.service === 'universal' || x.service === 'homemusic' ? 'text-accent' : 'text-muted'} />}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-[15px]">{x.title}</span>
                    {x.service === 'universal' && <span className="block truncate text-[13px] text-muted">song.link — откроется там, где слушает получатель</span>}
                    {x.service === 'homemusic' && <span className="block truncate text-[13px] text-muted">Откроется на сервере получателя</span>}
                  </span>
                </button>
                <a href={x.url} target="_blank" rel="noreferrer" aria-label={`Открыть: ${x.title}`} title="Открыть" className="mr-2 flex h-10 w-10 shrink-0 items-center justify-center rounded-full text-muted transition hover:text-fg">
                  <Icon name="external" size={18} />
                </a>
              </div>
            ))}
          </>
        )}
      </div>
    </Sheet>
  );
}

/** A chat of the bot: its picture (or its letters on a colour of its own), its name, what it is. */
function ChatTile({ chat, onClick }: { chat: TelegramChat; onClick: () => void }) {
  const mine = chat.kind === 'private';
  const letters = chat.title.replace(/@\S+/g, '').trim().split(/\s+/).slice(0, 2).map((w) => [...w][0] ?? '').join('').toLocaleUpperCase('ru') || '?';
  return (
    <button type="button" onClick={onClick} title={`Отправить: ${chat.title}`} className="flex w-[4.75rem] shrink-0 flex-col items-center rounded-xl px-1 py-2 text-center transition hover:bg-fg/5 active:bg-fg/10">
      {chat.photo ? (
        <img src={`/api/telegram/chats/${chat.id}/photo?v=${encodeURIComponent(chat.photo)}`} alt="" className="h-[52px] w-[52px] rounded-full object-cover" />
      ) : (
        <span className="flex h-[52px] w-[52px] items-center justify-center rounded-full text-base font-semibold text-white" style={{ background: `hsl(${(chat.id * 67) % 360} 45% 42%)` }}>
          {letters}
        </span>
      )}
      <span className="mt-1.5 block w-full truncate text-xs">{mine ? 'Мне' : chat.title}</span>
      <span className="block text-[11px] text-dim">{KIND[chat.kind] ?? 'чат'}</span>
    </button>
  );
}
