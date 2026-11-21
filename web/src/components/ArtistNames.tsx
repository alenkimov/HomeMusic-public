import { useState } from 'react';
import { api } from '../api/client';
import type { ArtistCardOut, ArtistOtherIn, ArtistPickOut, ArtistProfileOut } from '../api/types';
import { forgetSavedPages, useAsync } from '../lib/hooks';
import { plural } from '../lib/format';
import { imageUrl } from '../lib/img';
import { Link } from '../lib/router';
import { attempt, confirmDialog, toast } from '../lib/ui';
import { Icon } from './Icon';
import { ServiceBadge, serviceName } from './ServiceBadge';
import { Button, Sheet, cx } from './ui';

/** An artist as the Hub knows them — the one name, the other spellings, the other projects — for a page of theirs:
 * by what the catalog calls its own artist (`ref`) or by the name. The page shows `card.name` as its title. */
export function useArtistCard(name: string | null | undefined, ref?: string | null) {
  const res = useAsync((s) => (name || ref ? api.artistOf({ name: name ?? undefined, ref: ref ?? undefined }, s) : Promise.resolve(null)), [name, ref], true, `artist-card:${ref ?? name}`);
  const [said, setSaid] = useState<ArtistCardOut | null>(null); // what a change in a window answered
  const reload = () => {
    setSaid(null);
    res.reload(true);
  };
  return { card: said ?? res.data ?? null, set: setSaid, reload };
}

const SOURCE: Record<string, string> = { catalog: 'каталог', library: 'из файлов', manual: 'вручную' };
const enc = encodeURIComponent;

/** An artist's round picture in a button: the catalog's or an album's cover — else the first letter of the name. */
function Avatar({ name, src }: { name: string; src?: string | null }) {
  return (
    <span className="flex h-7 w-7 shrink-0 items-center justify-center overflow-hidden rounded-full bg-fg/15 text-xs font-semibold text-muted">
      {src ? <img src={imageUrl(src) as string} alt="" loading="lazy" className="h-full w-full object-cover" /> : name.trim().charAt(0).toUpperCase()}
    </span>
  );
}

/** The other projects of who the artist is, as buttons of the page's row — each a round picture and the name,
 * leading to the project's own page. */
export function ArtistProjects({ card }: { card: ArtistCardOut | null }) {
  return (
    <>
      {(card?.projects ?? []).map((p) => (
        <Link
          key={p.id}
          to={p.ref ? `/artist/${enc(p.ref)}` : `/artist-named/${enc(p.name)}`}
          title={`Другой проект: ${[p.name, ...p.also].join(', ')}`}
          className="flex h-9 min-w-0 max-w-full items-center gap-2 rounded-full border border-fg/40 pl-1 pr-4 text-sm text-fg transition hover:border-fg active:scale-95"
        >
          <Avatar name={p.name} src={p.picture_url} />
          <span className="truncate">{p.name}</span>
        </Link>
      ))}
    </>
  );
}

/** Somebody looks like the same artist under another spelling: the page's ⋯ says so with a dot. */
export const artistHint = (card: ArtistCardOut | null) => (card?.suggestions.length ?? 0) > 0;

/** The two windows of an artist's ⋯: «Имя и написания» and «Другие проекты». */
type OnProfileDetached = (ref: string, card: ArtistCardOut) => void;

export function ArtistWindow({ which, card, onChange, onClose, onProfileDetached }: {
  which: 'names' | 'projects' | null;
  card: ArtistCardOut | null;
  onChange: (next: ArtistCardOut) => void;
  onClose: () => void;
  onProfileDetached?: OnProfileDetached;
}) {
  if (!which || !card) return null;
  return which === 'names'
    ? <NamesWindow card={card} onChange={onChange} onClose={onClose} onProfileDetached={onProfileDetached} />
    : <ProjectsWindow card={card} onChange={onChange} onClose={onClose} />;
}

const REWRITE = 'Библиотека переписывается: треки, альбомы и теги файлов. Это займёт немного времени';
const chip = 'inline-flex max-w-full items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-sm';
const mini = 'flex h-5 w-5 items-center justify-center rounded-full text-dim hover:bg-fg/10 hover:text-fg';

/** What a window does to the artist: said to the page, with a word when it worked. */
function useRun(onChange: (next: ArtistCardOut) => void) {
  const [busy, setBusy] = useState(false);
  const run = async (what: () => Promise<ArtistCardOut>, said?: string, after = onChange) => {
    setBusy(true);
    const next = await attempt(what);
    setBusy(false);
    if (!next) return false;
    after(next);
    if (said) toast(said, 'success');
    return true;
  };
  return { busy, run };
}

/** Another artist, looked for as the name is typed: the ones the Hub knows, and the name itself when nobody is
 * called so yet. */
function OtherPicker({ card, placeholder, action, onPick }: { card: ArtistCardOut; placeholder: string; action: string; onPick: (who: ArtistOtherIn, name: string) => Promise<boolean> }) {
  const [text, setText] = useState('');
  const typed = text.trim();
  const picks = useAsync((s) => (typed.length > 1 ? api.artistFind(typed, s) : Promise.resolve([] as ArtistPickOut[])), [typed]);
  const taken = new Set([card.id, ...card.projects.map((p) => p.id)]);
  const found = (picks.data ?? []).filter((x) => !taken.has(x.id));
  const rows = [...found.map((x) => ({ who: { other_id: x.id } as ArtistOtherIn, name: x.name, known: true })), ...(found.some((x) => x.name.toLocaleLowerCase() === typed.toLocaleLowerCase()) ? [] : [{ who: { name: typed } as ArtistOtherIn, name: typed, known: false }])];
  return (
    <>
      <input className="input w-full" placeholder={placeholder} aria-label={placeholder} value={text} onChange={(e) => setText(e.target.value)} maxLength={300} autoComplete="off" />
      {typed.length > 1 && (
        <div className="space-y-1 pt-2 text-[13px]">
          {rows.map((x) => (
            <p key={`${x.known}:${x.name}`} className="flex flex-wrap items-center gap-x-3 rounded-lg px-2 py-1 hover:bg-fg/5">
              <span className="min-w-0 flex-1 truncate text-sm">
                {x.name}
                {!x.known && <span className="text-dim"> — пока нет в библиотеке</span>}
              </span>
              <button type="button" className="text-info hover:underline" onClick={() => void onPick(x.who, x.name).then((ok) => ok && setText(''))}>
                {action}
              </button>
            </p>
          ))}
        </div>
      )}
    </>
  );
}

/** «Имя и написания»: the artist's one name among their spellings, and the artists that are the same one. Whatever
 * is changed here is changed for everyone; a new name, or an artist joined, rewrites the library — its tracks,
 * albums and the files' tags — which takes the Hub a little while. */
function NamesWindow({ card, onChange, onClose, onProfileDetached }: {
  card: ArtistCardOut;
  onChange: (next: ArtistCardOut) => void;
  onClose: () => void;
  onProfileDetached?: OnProfileDetached;
}) {
  const { busy, run } = useRun(onChange);
  const [spelling, setSpelling] = useState('');
  const makeMain = async (id: number, name: string) => {
    const ok = await confirmDialog({ title: `Основное имя — «${name}»?`, body: `Все увидят исполнителя под этим именем. Треки и альбомы, где он записан как «${card.name}», будут переписаны, в том числе теги в файлах.`, okLabel: 'Сделать основным' });
    if (ok) await run(() => api.artistMain(card.id, id), REWRITE);
  };
  const dropName = async (id: number, name: string, source: string) => {
    const ok = source === 'manual' || (await confirmDialog({ title: `«${name}» — другой исполнитель?`, body: 'Написание станет отдельным исполнителем: музыка, которая придёт под ним дальше, будет его. Треки, уже переписанные под основным именем, останутся как есть.', okLabel: 'Отделить' }));
    if (ok) await run(() => api.artistDropName(card.id, id));
  };
  const detach = async (profile: ArtistProfileOut) => {
    const label = serviceName(profile.service);
    const ok = await confirmDialog({
      title: `Отвязать профиль «${profile.name}»?`,
      body: `${label} · ${profile.ref}. Профиль получит отдельную карточку с релизами каталога. Операция действует для всех пользователей Хаба.`,
      okLabel: 'Отвязать',
      danger: true,
    });
    if (!ok) return;
    await run(async () => {
      const next = await api.artistDetachProfile(card.id, profile.ref);
      forgetSavedPages([`artist-card:${card.name}`, ...(card.profiles ?? []).flatMap((ref) =>
        [`artist:${ref}`, `artist-card:${ref}`, `discography:${ref}`],
      )]);
      return next;
    }, 'Профиль отвязан', (next) => {
      onChange(next);
      onProfileDetached?.(profile.ref, next);
    });
  };
  const merge = async (who: ArtistOtherIn, name: string) => {
    const ok = await confirmDialog({ title: `«${name}» — это ${card.name}?`, body: `Они станут одним исполнителем под именем «${card.name}». Треки и альбомы «${name}» будут переписаны под этим именем, в том числе теги в файлах. Отдельный проект того же человека со своими релизами добавляется иначе — в «Другие проекты».`, okLabel: 'Объединить' });
    return ok ? run(() => api.artistMerge(card.id, who), REWRITE) : false;
  };
  const addSpelling = async () => {
    const name = spelling.trim();
    if (!name) return;
    // a spelling that is somebody's: the two are joined, by the listener's word
    const theirs = (await api.artistFind(name).catch(() => [] as ArtistPickOut[])).find((x) => x.id !== card.id && x.name.trim().toLocaleLowerCase() === name.toLocaleLowerCase());
    if (theirs ? await merge({ other_id: theirs.id }, theirs.name) : await run(() => api.artistAddName(card.id, name))) setSpelling('');
  };
  return (
    <Sheet open onClose={onClose} title="Имя и написания">
      <div className={cx('space-y-4 text-fg', busy && 'pointer-events-none opacity-60')}>
        {card.suggestions.length > 0 && (
          <section className="rounded-xl bg-info/10 px-3 py-2 text-[13px]">
            <p className="pb-1 text-muted">Похоже на того же исполнителя:</p>
            {card.suggestions.map((x) => (
              <p key={x.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1">
                {/* who it is: their own page, to look before the two are joined */}
                <Link to={x.ref ? `/artist/${enc(x.ref)}` : `/artist-named/${enc(x.name)}`} onClick={onClose} title="Открыть страницу этого исполнителя" className="flex h-9 min-w-0 max-w-full items-center gap-2 rounded-full border border-fg/40 pl-1 pr-3 text-sm text-fg transition hover:border-fg">
                  <Avatar name={x.name} src={x.picture_url} />
                  <span className="truncate">{x.name}</span>
                  {x.nb_fans ? <span className="shrink-0 text-[12px] text-dim">{x.nb_fans.toLocaleString('ru-RU')} в каталоге</span> : null}
                </Link>
                <button type="button" className="text-info hover:underline" onClick={() => void merge({ other_id: x.id }, x.name)}>объединить</button>
                <button type="button" className="text-info hover:underline" onClick={() => void run(() => api.artistAddProject(card.id, { other_id: x.id }))}>это его проект</button>
                <button type="button" className="text-muted hover:underline" onClick={() => void run(() => api.artistApart(card.id, { other_id: x.id }))}>это другой</button>
              </p>
            ))}
          </section>
        )}
        <section>
          <p className="pb-1.5 text-[13px] text-muted">Имя, под которым исполнителя видят все, и другие написания — по ним он тоже находится.</p>
          <div className="flex flex-wrap gap-1.5">
            {card.names.map((n) => (
              <span key={n.id ?? n.name} className={cx(chip, n.main && 'border-accent text-accent')}>
                <span className="truncate">{n.name}</span>
                <span className={cx('text-[11px]', n.main ? 'text-accent' : 'text-dim')}>{n.main ? 'основное' : (SOURCE[n.source] ?? n.source)}</span>
                {!n.main && n.id != null && (
                  <>
                    <button type="button" className="rounded-full px-1.5 text-[11px] text-info hover:bg-fg/10 hover:underline" onClick={() => void makeMain(n.id as number, n.name)}>
                      сделать основным
                    </button>
                    <button type="button" className={mini} aria-label={`Убрать написание: ${n.name}`} title={n.source === 'manual' ? 'Убрать' : 'Это другой исполнитель — отделить'} onClick={() => void dropName(n.id as number, n.name, n.source)}>
                      <Icon name="x" size={12} />
                    </button>
                  </>
                )}
              </span>
            ))}
          </div>
          {card.held_by && (
            <p className="pt-2 text-[12px] text-dim">
              Так же пишется «{card.held_by}». Музыка из каталога приходит к своему исполнителю, а файл, в котором указано только это имя, считается музыкой «{card.held_by}».
            </p>
          )}
          <div className="flex gap-2 pt-2">
            <input className="input min-w-0 flex-1" placeholder="Ещё написание: LSP" aria-label="Ещё написание" value={spelling} onChange={(e) => setSpelling(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && void addSpelling()} maxLength={300} autoComplete="off" />
            <Button small disabled={!spelling.trim()} onClick={() => void addSpelling()}>
              Добавить
            </Button>
          </div>
        </section>
        <LinkedProfiles card={card} onDetach={(profile) => void detach(profile)} />
        <section>
          <p className="pb-1.5 text-[13px] text-muted">Тот же исполнитель есть под другим именем — объединить их в одного.</p>
          <OtherPicker card={card} placeholder="Имя другого исполнителя" action="это тот же исполнитель" onPick={merge} />
        </section>
        <p className="text-[12px] text-dim">Изменения видят все на этом сервере. Основное имя записывается и в файлы — так же исполнитель называется в Navidrome и других плеерах.</p>
      </div>
    </Sheet>
  );
}

function LinkedProfiles({ card, onDetach }: { card: ArtistCardOut; onDetach: (profile: ArtistProfileOut) => void }) {
  const refs = card.profiles ?? [];
  const res = useAsync((signal) => api.artistProfiles(card.id, signal), [card.id, refs.join(',')], refs.length > 0);
  const profiles = (res.data ?? []).filter((p) => refs.includes(p.ref));
  return (
    <section aria-label="Привязанные профили">
      <h3 className="pb-1 text-sm font-semibold">Привязанные профили</h3>
      {!refs.length && <p className="text-sm text-muted">Привязанных профилей нет</p>}
      {res.loading && !profiles.length && <p className="text-sm text-muted" role="status">Загружаю профили…</p>}
      {res.error && <Button small variant="ghost" onClick={() => res.reload()}>Повторить загрузку</Button>}
      <ul className="divide-y divide-line">
        {profiles.map((profile) => {
          const details = (
            <>
              <Avatar name={profile.name} src={profile.picture_url} />
              <span className="min-w-0">
                <span className="block truncate text-sm font-medium">{profile.name}</span>
                <span className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-xs text-muted">
                  <ServiceBadge service={profile.service} size={13} />
                  {profile.followers != null
                    ? <span>{profile.followers.toLocaleString('ru-RU')} {plural(profile.followers, ['поклонник', 'поклонника', 'поклонников'])}</span>
                    : <span>{profile.ref}</span>}
                </span>
              </span>
            </>
          );
          const layout = 'flex min-w-0 flex-1 items-center gap-2 rounded-lg py-1.5';
          return (
            <li key={profile.ref} className="flex min-h-12 items-center gap-2">
              {profile.url ? <a href={profile.url} target="_blank" rel="noreferrer" className={cx(layout, 'hover:bg-fg/5')} title={profile.ref}>
                {details}<Icon name="external" size={14} className="shrink-0 text-dim" />
              </a> : <span className={layout}>{details}</span>}
              <Button small variant="ghost" icon="unlink" onClick={() => onDetach(profile)}>Отвязать</Button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** «Другие проекты»: the other artists that are the same person — each with releases and a page of its own. The
 * music is not mixed; the pages name one another. Named on one page, a project is on the pages of all. */
function ProjectsWindow({ card, onChange, onClose }: { card: ArtistCardOut; onChange: (next: ArtistCardOut) => void; onClose: () => void }) {
  const { busy, run } = useRun(onChange);
  return (
    <Sheet open onClose={onClose} title="Другие проекты">
      <div className={cx('space-y-3 text-fg', busy && 'pointer-events-none opacity-60')}>
        <p className="text-[13px] text-muted">Другие проекты того же человека. У проекта свои релизы и своя страница — музыка не смешивается, страницы ссылаются друг на друга.</p>
        {card.projects.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {card.projects.map((p) => (
              <span key={p.id} className={chip}>
                <span className="truncate">{p.name}</span>
                {p.also.length > 0 && <span className="truncate text-[11px] text-dim">{p.also.join(', ')}</span>}
                <button type="button" className={mini} aria-label={`Убрать проект: ${p.name}`} title="Больше не проект" onClick={() => void run(() => api.artistDropProject(card.id, p.id))}>
                  <Icon name="x" size={12} />
                </button>
              </span>
            ))}
          </div>
        )}
        <OtherPicker card={card} placeholder="Имя проекта" action="добавить проект" onPick={(who) => run(() => api.artistAddProject(card.id, who))} />
        <p className="text-[12px] text-dim">Изменения видят все на этом сервере. Проект появится на страницах всех проектов этого человека.</p>
      </div>
    </Sheet>
  );
}
