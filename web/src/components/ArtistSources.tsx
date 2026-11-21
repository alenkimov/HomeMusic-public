import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { ArtistSourceOut } from '../api/types';
import { useDesktop } from '../lib/desktop';
import { fmtCompactCount, plural } from '../lib/format';
import { imageUrl } from '../lib/img';
import { ServiceBadge } from './ServiceBadge';
import { Icon } from './Icon';
import { Sheet, cx } from './ui';

const num = (n: number) => n.toLocaleString('ru-RU');
const NAMES: Record<string, string> = { deezer: 'Deezer', yandex: 'Яндекс Музыка', spotify: 'Spotify', lastfm: 'Last.fm', catalog: 'Каталог' };
const monthly = (n: number) => `${num(n)} ${plural(n, ['слушатель', 'слушателя', 'слушателей'])} в месяц`;
const people = (s: ArtistSourceOut) =>
  s.monthly_listeners != null ? monthly(s.monthly_listeners)
    : s.followers != null ? `${num(s.followers)} ${plural(s.followers, ['поклонник', 'поклонника', 'поклонников'])}`
    : s.listeners == null ? ''
    : s.service === 'yandex' ? monthly(s.listeners)
    : `${num(s.listeners)} ${plural(s.listeners, ['слушатель', 'слушателя', 'слушателей'])}`;

/** Where an artist is known, as buttons of their page's row — after the other projects: one button a service, with
 * its mark (what tells it from a project, which has a round picture), its name and how many follow or listen there
 * in all.
 *
 * A service that knows the artist by one record opens their page there at a tap. A catalog may keep several
 * records of one artist: then the button opens the list of them — each with its picture, the name the artist has
 * there and its own followers — and a tap on one opens that page. The list hangs under the button on a computer and
 * is a window at the foot of the screen on a phone. */
export function ArtistServiceButtons({ sources }: { sources: ArtistSourceOut[] }) {
  const services = [...new Set(sources.map((s) => s.service))];
  if (!services.length) return null;
  return (
    <>
      {services.map((service) => (
        <ServiceButton key={service} service={service} records={sources.filter((s) => s.service === service)} />
      ))}
    </>
  );
}

function ServiceButton({ service, records }: { service: string; records: ArtistSourceOut[] }) {
  const desktop = useDesktop();
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState<{ left: number; top: number } | null>(null); // where the list hangs (a computer)
  const button = useRef<HTMLButtonElement>(null);
  const total = records.reduce((n, s) => n + (s.followers ?? s.listeners ?? 0), 0);
  const count = service === 'spotify'
    ? records.length === 1 ? records[0].monthly_listeners ?? null : null
    : total > 0 ? total : null;
  const audience = count == null ? '' : records.length === 1 ? people(records[0]) : `${num(count)} суммарно по профилям`;
  const name = NAMES[service] ?? service;
  const box = 'flex h-9 items-center gap-2 rounded-full border border-fg/40 pl-1.5 pr-4 text-sm text-fg transition hover:border-fg active:scale-95';
  const body = (
    <>
      <ServiceBadge service={service} label={name} size={18} />
      {count != null && (
        <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap tabular-nums text-dim">
          <Icon name="headphones" size={14} />
          <span aria-hidden="true">{fmtCompactCount(count)}</span>
          <span className="sr-only">{audience}</span>
        </span>
      )}
    </>
  );

  // the list closes at a tap elsewhere, at Esc, and when the page moves under it
  useEffect(() => {
    if (!open || !desktop) return;
    const close = () => setOpen(false);
    const key = (e: KeyboardEvent) => e.key === 'Escape' && close();
    window.addEventListener('keydown', key);
    window.addEventListener('scroll', close, { passive: true, capture: true });
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('keydown', key);
      window.removeEventListener('scroll', close, { capture: true });
      window.removeEventListener('resize', close);
    };
  }, [open, desktop]);

  if (records.length === 1) {
    const s = records[0];
    const title = [s.name, audience].filter(Boolean).join(' · ');
    return s.url ? (
      <a href={s.url} target="_blank" rel="noopener noreferrer" title={title} className={box}>
        {body}
      </a>
    ) : (
      <span title={title} className={cx(box, 'hover:border-fg/40 active:scale-100')}>{body}</span>
    );
  }

  const show = () => {
    const r = button.current?.getBoundingClientRect();
    if (r) setAt({ left: Math.max(8, Math.min(r.left, window.innerWidth - 328)), top: r.bottom + 8 });
    setOpen((v) => !v);
  };
  const list = records.map((s, i) => {
    const row = (
      <>
        <span className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full bg-fg/15">
          {s.picture_url ? <img src={imageUrl(s.picture_url) as string} alt="" loading="lazy" className="h-full w-full object-cover" /> : <Icon name="user" size={16} />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm text-fg">{s.name}</span>
          <span className="block truncate text-xs text-muted">{people(s)}</span>
        </span>
        {s.url && <Icon name="external" size={16} className="shrink-0 text-dim" />}
      </>
    );
    const cls = 'flex min-h-12 w-full items-center gap-3 rounded-lg px-2 py-1.5 text-left hover:bg-fg/5';
    return s.url ? (
      <a key={s.ref ?? i} href={s.url} target="_blank" rel="noopener noreferrer" onClick={() => setOpen(false)} className={cls}>
        {row}
      </a>
    ) : (
      <span key={s.ref ?? i} className={cls}>
        {row}
      </span>
    );
  });
  return (
    <>
      <button ref={button} type="button" onClick={show} aria-expanded={open} aria-haspopup="menu" title={[`${records.length} ${plural(records.length, ['профиль', 'профиля', 'профилей'])}`, audience].filter(Boolean).join(' · ')} className={cx(box, open && 'border-fg')}>
        {body}
        <Icon name="chevron" size={12} className={cx('-mr-1 text-dim transition-transform', open ? '-rotate-90' : 'rotate-90')} />
      </button>
      {desktop
        ? open &&
          at &&
          createPortal(
            <>
              <button type="button" aria-label="Закрыть" className="fixed inset-0 z-40 cursor-default" onClick={() => setOpen(false)} />
              <div role="menu" aria-label={`Профили в ${name}`} style={{ left: at.left, top: at.top }} className="anim-up fixed z-50 w-80 rounded-2xl bg-raised p-2 shadow-2xl">
                <p className="px-2 pb-1 pt-1 text-xs text-dim">Профили в <ServiceBadge service={service} label={name} /></p>
                {list}
              </div>
            </>,
            document.body,
          )
        : (
          <Sheet open={open} onClose={() => setOpen(false)} title={<>Профили в <ServiceBadge service={service} label={name} size={18} /></>}>
            <div className="-mx-1">{list}</div>
          </Sheet>
        )}
    </>
  );
}
