import { titleWithVersion } from './versionLabels';
import { api } from '../api/client';
import { plural, tracksWord } from './format';
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
  const name = `«${titleWithVersion(t.title, t)}» — ${t.artist}`;
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

/** The same for the tracks picked in a list (the bar of the picked): the Hub is asked first what it would delete —
 * a track somebody else keeps stays (an admin's goes anyway, and the admin is told) — then, after a yes, deletes.
 * True: files were deleted. */
export async function deleteFiles(ids: number[]): Promise<boolean> {
  const k = await attempt(() => api.deleteTracks(ids, true));
  if (!k) return false;
  const others = (n: number) => `${tracksWord(n)} ${plural(n, ['есть', 'есть', 'есть'])} ещё у других слушателей`;
  if (!k.deleted) {
    await confirmDialog({
      title: 'Удалять нечего',
      body: k.kept ? `${others(k.kept)}: с файлом ${plural(k.kept, ['он пропал', 'они пропали', 'они пропали'])} бы и из их плейлистов.` : 'У выбранных треков нет файлов на сервере.',
      okLabel: 'Понятно',
    });
    return false;
  }
  const ok = await confirmDialog({
    title: `Удалить с сервера ${plural(k.deleted, ['файл', 'файлы', 'файлы'])}: ${tracksWord(k.deleted)}?`,
    body: [
      k.kept ? `${others(k.kept)} — ${plural(k.kept, ['он останется', 'они останутся', 'они останутся'])}.` : null,
      k.others ? `${others(k.others)} — ${plural(k.others, ['он пропадёт', 'они пропадут', 'они пропадут'])} и из их плейлистов.` : null,
      k.mine ? `${tracksWord(k.mine)} ${plural(k.mine, ['пропадёт', 'пропадут', 'пропадут'])} из ваших плейлистов и «Любимого».` : null,
      'Файлы уйдут в корзину: 30 дней их можно вернуть.',
    ].filter(Boolean).join('\n'),
    okLabel: 'Удалить',
    danger: true,
  });
  if (!ok) return false;
  const r = await attempt(() => api.deleteTracks(ids));
  if (!r) return false;
  toast(`Удалено: ${tracksWord(r.deleted)}. 30 дней их можно вернуть`, 'success', { label: 'Корзина', to: '/trash' });
  window.dispatchEvent(new Event(EDITED_EVENT));
  return r.deleted > 0;
}
