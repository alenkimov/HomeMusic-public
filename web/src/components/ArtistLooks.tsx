import { useRef, useState } from 'react';
import { api } from '../api/client';
import type { ArtistLooksOut } from '../api/types';
import { useAsync } from '../lib/hooks';
import { imageUrl } from '../lib/img';
import { attempt, toast } from '../lib/ui';
import { Icon } from './Icon';
import { Sheet, Spinner, cx } from './ui';

/** «Фото и обложка» of an artist's ⋯: how the artist is shown, for everyone here.
 *
 * The round photo is one of those the catalog keeps with its records of the artist, the cover of an album of
 * theirs, or a picture of one's own. The wide picture over the page is found by a name, so every spelling of the
 * artist gives its own (a namesake's page may answer too): each is offered with the name it was found by and its
 * size, beside «без обложки» and a picture of one's own. «Как было» takes the choice back. `onChanged`: the page
 * is drawn anew. */
export function ArtistLooksWindow({ artistId, onChanged, onClose }: { artistId: number; onChanged: () => void; onClose: () => void }) {
  const res = useAsync((s) => api.artistLooks(artistId, s), [artistId]);
  const [said, setSaid] = useState<ArtistLooksOut | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [sizes, setSizes] = useState<Record<string, string>>({}); // a wide picture's size, once it is loaded
  const [none, setNone] = useState<Set<string>>(new Set()); // the spellings nothing was found by
  const photoFile = useRef<HTMLInputElement>(null);
  const bannerFile = useRef<HTMLInputElement>(null);
  const d = said ?? res.data;
  const run = async (key: string, what: () => Promise<ArtistLooksOut>) => {
    setBusy(key);
    const next = await attempt(what);
    setBusy(null);
    if (!next) return;
    setSaid(next);
    onChanged();
    toast('Изменено у всех', 'success');
  };
  const ring = (on: boolean) => (on ? 'ring-2 ring-accent ring-offset-2 ring-offset-surface' : 'opacity-70 hover:opacity-100');
  const label = 'pt-1.5 text-center text-[11px] leading-tight text-dim';
  const upload = (onClick: () => void) => (
    <button type="button" onClick={onClick} className="flex h-full w-full items-center justify-center rounded-[inherit] border border-dashed border-fg/30 text-muted hover:border-fg hover:text-fg">
      <Icon name="upload" size={20} />
    </button>
  );

  return (
    <Sheet open onClose={onClose} title="Фото и обложка">
      {!d ? (
        <div className="flex justify-center py-10">
          <Spinner className="text-accent" />
        </div>
      ) : (
        <div className={cx('space-y-5 text-fg', busy && 'pointer-events-none')}>
          <section>
            <p className="pb-3 text-[13px] text-muted">Фото — кружок в поиске, списках и на странице без обложки.</p>
            <div className="flex flex-wrap gap-3">
              {d.photos.map((p) => (
                <button key={p.choice} type="button" aria-pressed={d.photo === p.choice} onClick={() => void run(p.choice, () => api.artistPhoto(artistId, p.choice))} className="w-20">
                  <span className={cx('relative block h-[4.5rem] w-[4.5rem] overflow-hidden rounded-full bg-fg/10 transition mx-auto', ring(d.photo === p.choice))}>
                    <img src={imageUrl(p.url) as string} alt="" loading="lazy" className="h-full w-full object-cover" />
                    {busy === p.choice && <span className="absolute inset-0 flex items-center justify-center bg-black/50"><Spinner size={16} /></span>}
                  </span>
                  <span className={cx('block', label)}>
                    {p.label}
                    <br />
                    {p.note}
                  </span>
                </button>
              ))}
              <div className="w-20">
                <span className="mx-auto block h-[4.5rem] w-[4.5rem] rounded-full">{upload(() => photoFile.current?.click())}</span>
                <span className={cx('block', label)}>своё…</span>
              </div>
            </div>
            {d.photo && (
              <button type="button" className="pt-2 text-[12px] text-info hover:underline" onClick={() => void run('photo:default', () => api.artistPhoto(artistId, null))}>
                как было — фото из каталога
              </button>
            )}
          </section>

          <section>
            <p className="pb-3 text-[13px] text-muted">Обложка страницы — широкая картинка над именем. Ищется по имени, поэтому у каждого написания своя; проверьте, что на ней тот исполнитель.</p>
            <div className="grid grid-cols-2 gap-3">
              {d.banners.filter((b) => !none.has(b.choice)).map((b) => (
                <button key={b.choice} type="button" aria-pressed={d.banner === b.choice} onClick={() => void run(b.choice, () => api.artistBanner(artistId, b.choice))} className="min-w-0 text-left">
                  <span className={cx('relative block aspect-[12/5] overflow-hidden rounded-lg bg-fg/10 transition', ring(d.banner === b.choice))}>
                    <img
                      src={imageUrl(b.url)}
                      alt=""
                      loading="lazy"
                      className="h-full w-full object-cover"
                      onLoad={(e) => {
                        const { naturalWidth: w, naturalHeight: h } = e.currentTarget;
                        setSizes((prev) => ({ ...prev, [b.choice]: `${w}×${h}` }));
                      }}
                      onError={() => setNone((prev) => new Set(prev).add(b.choice))}
                    />
                    {busy === b.choice && <span className="absolute inset-0 flex items-center justify-center bg-black/50"><Spinner size={16} /></span>}
                  </span>
                  <span className={cx('block truncate', label)}>
                    {b.choice === 'own' ? 'своя' : b.choice.startsWith('profile:') ? b.label : `по имени «${b.label}»`}
                    {sizes[b.choice] ? ` · ${sizes[b.choice]}` : ''}
                  </span>
                </button>
              ))}
              <button type="button" aria-pressed={d.banner === 'none'} onClick={() => void run('none', () => api.artistBanner(artistId, 'none'))} className="min-w-0">
                <span className={cx('flex aspect-[12/5] items-center justify-center rounded-lg border border-fg/30 text-[13px] text-muted transition', ring(d.banner === 'none'))}>без обложки</span>
                <span className={cx('block', label)}>страница с круглым фото</span>
              </button>
              <div className="min-w-0">
                <span className="block aspect-[12/5] rounded-lg">{upload(() => bannerFile.current?.click())}</span>
                <span className={cx('block', label)}>своя…</span>
              </div>
            </div>
            {none.size > 0 && <p className="pt-2 text-[12px] text-dim">Не нашлось по именам: {d.banners.filter((b) => none.has(b.choice)).map((b) => `«${b.label}»`).join(', ')}.</p>}
            {d.banner && (
              <button type="button" className="pt-2 text-[12px] text-info hover:underline" onClick={() => void run('banner:default', () => api.artistBanner(artistId, null))}>
                как было — подбирать автоматически
              </button>
            )}
          </section>
          <p className="text-[12px] text-dim">Выбор видят все на этом сервере.</p>
          <input ref={photoFile} type="file" accept="image/*" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void run('own', () => api.artistPhotoUpload(artistId, f)); }} />
          <input ref={bannerFile} type="file" accept="image/*" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void run('own', () => api.artistBannerUpload(artistId, f)); }} />
        </div>
      )}
    </Sheet>
  );
}
