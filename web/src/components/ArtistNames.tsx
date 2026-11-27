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
function Avatar({ name, src, big }: { name: string; src?: string | null; big?: boolean }) {
  return (
    <span className={cx(big ? 'h-10 w-10 text-sm' : 'h-7 w-7 text-xs', 'flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-fg/15 font-semibold text-muted')}>
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

type OnProfileDetached = (ref: string, card: ArtistCardOut) => void;

const REWRITE = 'Библиотека переписывается: треки, альбомы и теги файлов. Это займёт немного времени';
const heading = 'pb-1 text-[12px] font-semibold uppercase tracking-wider text-muted';
const cross = 'flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-dim hover:bg-fg/10 hover:text-fg';
const pill = 'min-h-9 rounded-full px-3 text-[13px] font-semibold transition active:scale-95';
/** a catalog's record -> its service (`app/catalog/models.py` CATALOG_SERVICES) */
const SERVICE_OF: Record<string, string> = { dz: 'deezer', ym: 'yandex', sp: 'spotify', hm: 'hitmoz', mb: 'musicbrainz' };
const fans = (n: number) => `${n.toLocaleString('ru-RU')} ${plural(n, ['поклонник', 'поклонника', 'поклонников'])}`;
const pageOf = (x: { ref?: string | null; name: string }) => (x.ref ? `/artist/${enc(x.ref)}` : `/artist-named/${enc(x.name)}`);

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

/** «Соцсети, проекты и псевдонимы» of an artist's ⋯: who the artist is, in one window — the services' profiles tied
 * to them (and, marked as «Наведение порядка» marks what it offers, the ones that look like the same artist), the
 * other projects of the same person, the names with a star on the one everybody sees, and one field that adds a
 * name, a project or the same artist under another name. Whatever is changed here is changed for everyone; a new
 * main name, or an artist joined, rewrites the library — its tracks, albums and the files' tags. */
export function ArtistWindow({ open, card, onChange, onClose, onProfileDetached }: {
  open: boolean;
  card: ArtistCardOut | null;
  onChange: (next: ArtistCardOut) => void;
  onClose: () => void;
  onProfileDetached?: OnProfileDetached;
}) {
  if (!open || !card) return null;
  return <Window card={card} onChange={onChange} onClose={onClose} onProfileDetached={onProfileDetached} />;
}

function Window({ card, onChange, onClose, onProfileDetached }: {
  card: ArtistCardOut;
  onChange: (next: ArtistCardOut) => void;
  onClose: () => void;
  onProfileDetached?: OnProfileDetached;
}) {
  const { busy, run } = useRun(onChange);
  const makeMain = async (id: number, name: string) => {
    const ok = await confirmDialog({ title: `Основное имя — «${name}»?`, body: `Все увидят исполнителя под этим именем. Треки и альбомы, где он записан как «${card.name}», будут переписаны, в том числе теги в файлах.`, okLabel: 'Сделать основным' });
    if (ok) await run(() => api.artistMain(card.id, id), REWRITE);
  };
  const dropName = async (id: number, name: string, source: string) => {
    const ok = source === 'manual' || (await confirmDialog({ title: `«${name}» — другой исполнитель?`, body: 'Имя станет отдельным исполнителем: музыка, которая придёт под ним дальше, будет его. Треки, уже переписанные под основным именем, останутся как есть.', okLabel: 'Отделить' }));
    if (ok) await run(() => api.artistDropName(card.id, id));
  };
  const detach = async (profile: ArtistProfileOut) => {
    const ok = await confirmDialog({
      title: `Отвязать профиль «${profile.name}»?`,
      body: `${serviceName(profile.service)} · ${profile.ref}. Профиль получит отдельную карточку с релизами каталога. Операция действует для всех пользователей Хаба.`,
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
    const ok = await confirmDialog({ title: `«${name}» — это ${card.name}?`, body: `Они станут одним исполнителем под именем «${card.name}». Треки и альбомы «${name}» будут переписаны под этим именем, в том числе теги в файлах. Если это отдельный проект того же человека со своими релизами — выберите «Его проект».`, okLabel: 'Объединить' });
    return ok ? run(() => api.artistMerge(card.id, who), REWRITE) : false;
  };
  const project = (who: ArtistOtherIn) => run(() => api.artistAddProject(card.id, who), 'Проект добавлен');
  const addName = async (name: string) => {
    // a name that is somebody's: the two are joined, by the listener's word
    const theirs = (await api.artistFind(name).catch(() => [] as ArtistPickOut[])).find((x) => x.id !== card.id && x.name.trim().toLocaleLowerCase() === name.toLocaleLowerCase());
    return theirs ? merge({ other_id: theirs.id }, theirs.name) : run(() => api.artistAddName(card.id, name));
  };

  return (
    <Sheet open onClose={onClose} title="Соцсети, проекты и псевдонимы">
      <div className={cx('space-y-6 text-fg', busy && 'pointer-events-none opacity-60')}>
        <section aria-labelledby="artist-profiles">
          <h3 id="artist-profiles" className={heading}>Соцсети и профили</h3>
          <LinkedProfiles card={card} onDetach={(profile) => void detach(profile)} />
          {card.suggestions.map((x) => (
            <div key={x.id} className="mt-2 space-y-2 rounded-2xl bg-ai/10 px-3 pb-3 pt-2.5 ring-1 ring-ai/35">
              <p className="flex items-center gap-1.5 text-[12px] font-semibold text-ai">
                <Icon name="sparkle" size={13} filled stroke={0} />
                Похоже на того же исполнителя
              </p>
              {/* who it is: their own page, to look before the two are joined */}
              <Link to={pageOf(x)} onClick={onClose} title="Открыть страницу этого исполнителя" className="flex min-w-0 items-center gap-3 rounded-lg hover:bg-fg/5">
                <Avatar name={x.name} src={x.picture_url} big />
                <span className="min-w-0">
                  <span className="block truncate text-[15px] font-semibold">{x.name}</span>
                  <span className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-xs text-muted">
                    {x.ref && SERVICE_OF[x.ref.split(':')[0]] ? <ServiceBadge service={SERVICE_OF[x.ref.split(':')[0]]} size={13} /> : <span>В библиотеке</span>}
                    {x.nb_fans ? <span>· {fans(x.nb_fans)}</span> : null}
                  </span>
                </span>
              </Link>
              <div className="flex gap-1.5">
                <button type="button" className={cx(pill, 'flex-1 bg-ai text-bg hover:opacity-90')} onClick={() => void merge({ other_id: x.id }, x.name)}>Привязать</button>
                <button type="button" className={cx(pill, 'flex-1 whitespace-nowrap bg-ai/15 text-ai hover:bg-ai/25')} onClick={() => void project({ other_id: x.id })}>Это его проект</button>
                <button type="button" className={cx(pill, 'flex-1 whitespace-nowrap bg-ai/15 text-ai hover:bg-ai/25')} onClick={() => void run(() => api.artistApart(card.id, { other_id: x.id }))}>Это другой</button>
              </div>
            </div>
          ))}
        </section>

        {card.projects.length > 0 && (
          <section aria-labelledby="artist-projects">
            <h3 id="artist-projects" className={heading}>Другие проекты</h3>
            <ul>
              {card.projects.map((p) => (
                <li key={p.id} className="flex min-h-14 items-center gap-2">
                  <Link to={pageOf(p)} onClick={onClose} className="flex min-w-0 flex-1 items-center gap-3 rounded-lg py-1.5 hover:bg-fg/5">
                    <Avatar name={p.name} src={p.picture_url} big />
                    <span className="min-w-0">
                      <span className="block truncate text-[15px] font-semibold">{p.name}</span>
                      <span className="block truncate text-xs text-muted">{p.also.length ? p.also.join(', ') : 'Своя страница и свои релизы'}</span>
                    </span>
                  </Link>
                  <button type="button" className={cross} aria-label={`Больше не проект: ${p.name}`} title="Больше не проект" onClick={() => void run(() => api.artistDropProject(card.id, p.id))}>
                    <Icon name="x" size={16} />
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}

        <section aria-labelledby="artist-names">
          <h3 id="artist-names" className={heading}>Имена</h3>
          <ul>
            {card.names.map((n) => (
              <li key={n.id ?? n.name} className="flex min-h-12 items-center gap-1">
                <button
                  type="button"
                  aria-pressed={n.main}
                  disabled={n.main || n.id == null}
                  aria-label={n.main ? `Основное имя: ${n.name}` : `Сделать основным: ${n.name}`}
                  title={n.main ? 'Основное имя' : 'Сделать основным'}
                  onClick={() => void makeMain(n.id as number, n.name)}
                  className={cx('flex h-10 w-10 shrink-0 items-center justify-center rounded-full', n.main ? 'text-warn' : 'text-dim hover:bg-fg/10 hover:text-warn')}
                >
                  <Icon name="star" size={20} filled={n.main} />
                </button>
                <span className={cx('min-w-0 flex-1 truncate text-[15px]', n.main ? 'font-bold' : 'font-medium')}>{n.name}</span>
                <span className={cx('shrink-0 text-[12px]', n.main ? 'text-warn' : 'text-dim')}>{n.main ? 'основное' : (SOURCE[n.source] ?? n.source)}</span>
                {!n.main && n.id != null ? (
                  <button type="button" className={cross} aria-label={`Убрать имя: ${n.name}`} title={n.source === 'manual' ? 'Убрать' : 'Это другой исполнитель — отделить'} onClick={() => void dropName(n.id as number, n.name, n.source)}>
                    <Icon name="x" size={14} />
                  </button>
                ) : <span className="w-9 shrink-0" />}
              </li>
            ))}
          </ul>
          <p className="pt-1 text-[12px] text-dim">Звезда — имя, под которым исполнителя видят все и которое пишется в файлы. По остальным он тоже находится.</p>
          {card.held_by && (
            <p className="pt-2 text-[12px] text-dim">
              Так же пишется «{card.held_by}». Музыка из каталога приходит к своему исполнителю, а файл, в котором указано только это имя, считается музыкой «{card.held_by}».
            </p>
          )}
        </section>

        <AddField card={card} onMerge={merge} onProject={project} onName={addName} />

        <p className="text-[12px] text-dim">Изменения видят все на этом сервере. Основное имя записывается и в файлы — так же исполнитель называется в Navidrome и других плеерах.</p>
      </div>
    </Sheet>
  );
}

/** The one field: a name typed is looked for among the artists at once — each found one the same artist or a project
 * of theirs — and can be added as one more name of this one. */
function AddField({ card, onMerge, onProject, onName }: {
  card: ArtistCardOut;
  onMerge: (who: ArtistOtherIn, name: string) => Promise<boolean>;
  onProject: (who: ArtistOtherIn) => Promise<boolean>;
  onName: (name: string) => Promise<boolean>;
}) {
  const [text, setText] = useState('');
  const typed = text.trim();
  const picks = useAsync((s) => (typed.length > 1 ? api.artistFind(typed, s) : Promise.resolve([] as ArtistPickOut[])), [typed]);
  const taken = new Set([card.id, ...card.projects.map((p) => p.id)]);
  const found = (picks.data ?? []).filter((x) => !taken.has(x.id));
  const known = card.names.some((n) => n.name.toLocaleLowerCase() === typed.toLocaleLowerCase());
  const done = (ok: boolean) => ok && setText('');
  const btn = 'min-h-9 rounded-full border border-line px-3 text-[13px] font-semibold hover:border-fg/60 active:scale-95';
  return (
    <section>
      <label htmlFor="artist-add" className={cx(heading, 'block')}>Добавить</label>
      <input
        id="artist-add"
        className="input w-full"
        placeholder="Имя, другое написание или проект"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && typed && !known && void onName(typed).then(done)}
        maxLength={300}
        autoComplete="off"
      />
      {typed.length > 1 && (
        <div className="space-y-1.5 pt-2">
          {found.length > 0 && <p className="px-1 text-[12px] text-dim">Есть в библиотеке</p>}
          {found.map((x) => (
            <div key={x.id} className="space-y-2 rounded-xl bg-raised px-3 py-2.5">
              <p className="truncate text-sm font-semibold">{x.name}</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" className={btn} onClick={() => void onMerge({ other_id: x.id }, x.name).then(done)}>Это он — объединить</button>
                <button type="button" className={btn} onClick={() => void onProject({ other_id: x.id }).then(done)}>Его проект</button>
              </div>
            </div>
          ))}
          {!known && (
            <button type="button" className="flex min-h-11 w-full items-center gap-2.5 rounded-xl px-3 text-left text-sm hover:bg-fg/5" onClick={() => void onName(typed).then(done)}>
              <Icon name="plus" size={16} className="shrink-0 text-muted" />
              <span className="min-w-0 truncate">Добавить «{typed}» как имя</span>
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function LinkedProfiles({ card, onDetach }: { card: ArtistCardOut; onDetach: (profile: ArtistProfileOut) => void }) {
  const refs = card.profiles ?? [];
  const res = useAsync((signal) => api.artistProfiles(card.id, signal), [card.id, refs.join(',')], refs.length > 0);
  const profiles = (res.data ?? []).filter((p) => refs.includes(p.ref));
  return (
    <>
      {!refs.length && <p className="text-sm text-muted">Привязанных профилей нет</p>}
      {res.loading && !profiles.length && <p className="text-sm text-muted" role="status">Загружаю профили…</p>}
      {res.error && <Button small variant="ghost" onClick={() => res.reload()}>Повторить загрузку</Button>}
      <ul>
        {profiles.map((profile) => {
          const details = (
            <>
              <Avatar name={profile.name} src={profile.picture_url} big />
              <span className="min-w-0">
                <span className="block truncate text-[15px] font-semibold">{profile.name}</span>
                <span className="flex min-w-0 flex-wrap items-center gap-x-1.5 text-xs text-muted">
                  <ServiceBadge service={profile.service} size={13} />
                  {profile.followers != null ? <span>· {fans(profile.followers)}</span> : <span>· {profile.ref}</span>}
                </span>
              </span>
            </>
          );
          const layout = 'flex min-w-0 flex-1 items-center gap-3 rounded-lg py-1.5';
          return (
            <li key={profile.ref} className="flex min-h-14 items-center gap-2">
              {profile.url ? <a href={profile.url} target="_blank" rel="noreferrer" className={cx(layout, 'hover:bg-fg/5')} title={profile.ref}>
                {details}<Icon name="external" size={14} className="shrink-0 text-dim" />
              </a> : <span className={layout}>{details}</span>}
              <Button small variant="ghost" onClick={() => onDetach(profile)}>Отвязать</Button>
            </li>
          );
        })}
      </ul>
    </>
  );
}
