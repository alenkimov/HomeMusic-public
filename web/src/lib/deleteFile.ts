import { api } from '../api/client';
import { plural } from './format';
import { setLikes } from './stars';
import { EDITED_EVENT } from './trackEdit';
import { attempt, confirmDialog, toast } from './ui';

/** «Удалить файл» of a track's menu. A file is deleted by the one who alone keeps the track — nobody else has it in
 * a playlist or in «Любимое» (the Hub decides: library/access.py). Where somebody else keeps it the file stays, and
 * the listener is offered what they wanted in the first place: the track out of their own playlists. An admin may
 * delete it anyway, and is told from how many it goes. */
export async function deleteFile(t: { library_track_id: number; title: string; artist: string }): Promise<void> {
  const k = await attempt(() => api.trackKeeping(t.library_track_id));
  if (!k) return;
  const name = `«${t.title}» — ${t.artist}`;
  const people = (n: number) => `${n} ${plural(n, ['слушателя', 'слушателей', 'слушателей'])}`;
  if (!k.can_delete) {
    const lists = k.mine.map((p) => (p.liked ? '«Любимого»' : `«${p.name}»`)).join(', ');
    const ok = await confirmDialog({
      title: 'Файл удалить нельзя',
      body: `${name}\nТрек есть ещё у ${people(k.others)}: с файлом он пропал бы и из их плейлистов.` + (k.mine.length ? `\nМожно убрать его у себя — из ${lists}. Файл останется остальным.` : '\nУ вас его нет ни в одном плейлисте.'),
      okLabel: k.mine.length ? 'Убрать у меня' : 'Понятно',
    });
    if (!ok || !k.mine.length) return;
    const own = k.mine.filter((p) => !p.liked).map((p) => p.id);
    if (own.length && !(await attempt(() => api.playlistMembership({ track: String(t.library_track_id), add: [], remove: own })))) return;
    if (k.mine.some((p) => p.liked)) await setLikes([{ trackId: t.library_track_id }], false);
    toast('Трек убран из ваших плейлистов', 'success');
    window.dispatchEvent(new Event(EDITED_EVENT));
    return;
  }
  const ok = await confirmDialog({
    title: 'Удалить файл с сервера?',
    body: `${name}\n` + (k.others ? `Трек есть ещё у ${people(k.others)} — он пропадёт и из их плейлистов.\n` : k.mine.length ? 'Он пропадёт из ваших плейлистов.\n' : '') + 'Файл уйдёт в корзину: 30 дней его можно вернуть.',
    okLabel: 'Удалить',
    danger: true,
  });
  if (!ok) return;
  if (!(await attempt(() => api.deleteTrack(t.library_track_id)))) return;
  toast('Файл удалён: 30 дней его можно вернуть', 'success', { label: 'Корзина', to: '/trash' });
  window.dispatchEvent(new Event(EDITED_EVENT));
}
