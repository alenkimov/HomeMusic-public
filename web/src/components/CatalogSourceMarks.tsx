import { useEffect, useId, useRef, useState, type MouseEvent } from 'react';
import type { AlbumOut, CatalogSource } from '../api/types';
import { Icon } from './Icon';
import { ServiceBadge, ServiceIcon, serviceName } from './ServiceBadge';
import { cx } from './ui';

const nameOf = (service: string) => service === 'library' ? 'Медиатека' : serviceName(service);
const albumServices: Partial<Record<CatalogSource, string>> = {
  dz: 'deezer', sp: 'spotify', ym: 'yandex', hm: 'hitmoz', mb: 'musicbrainz', nd: 'library',
};

export function AlbumSourceMarks({ album, className }: { album: AlbumOut; className?: string }) {
  const main = albumServices[album.source];
  const services = album.found_in?.length ? album.found_in : main ? [main] : [];
  return <CatalogSourceMarks services={services} label="Источники релиза" className={className} />;
}

function places(positions: number[]) {
  if (positions.length === 1) return `Место ${positions[0]}`;
  const contiguous = positions.every((n, i) => n === positions[0] + i);
  return `Места ${contiguous ? `${positions[0]}–${positions.at(-1)}` : positions.join(', ')}`;
}

export function CatalogSourceMarks({ services, ranked = false, label = ranked ? 'Источники рейтинга' : 'Источники результата', className }: {
  services: readonly string[];
  ranked?: boolean;
  label?: string;
  className?: string;
}) {
  const id = useId();
  const panel = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const close = () => panel.current?.hidePopover();
    window.addEventListener('scroll', close, { passive: true, capture: true });
    window.addEventListener('resize', close);
    return () => {
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', close);
    };
  }, [open]);
  const ordered = [...new Set(services)];
  if (!ordered.length) return null;
  const names = ordered.map(nameOf).join(', ');
  const movePanel = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    const box = event.currentTarget.getBoundingClientRect();
    const menu = panel.current;
    if (!menu) return;
    const width = Math.min(224, window.innerWidth - 16);
    const height = ordered.length * 32 + 18;
    menu.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - width - 8))}px`;
    menu.style.top = `${box.bottom + 6 + height <= window.innerHeight - 8
      ? box.bottom + 6 : Math.max(8, box.top - height - 6)}px`;
  };
  return (
    <>
      <button
        type="button"
        data-source-details
        data-row-actions
        popoverTarget={id}
        aria-expanded={open}
        aria-label={`${label} · ${names}`}
        title={names}
        onClick={movePanel}
        onPointerDown={(event) => event.stopPropagation()}
        className={cx('inline-flex min-h-5 shrink-0 items-center gap-1 rounded-sm px-0.5 align-middle hover:bg-fg/10', ranked && 'min-h-8 gap-1.5 px-1', className)}
      >
        {ordered.map((service) => service === 'library'
          ? <Icon key={service} name="library" size={14} className="text-muted" />
          : <ServiceIcon key={service} service={service} size={ranked ? 17 : 14} />)}
      </button>
      <span
        ref={panel}
        id={id}
        popover="auto"
        onToggle={(event) => setOpen(event.newState === 'open')}
        role="list"
        aria-label={label}
        data-source-details
        onClick={(event) => event.stopPropagation()}
        className="fixed inset-auto m-0 w-56 max-w-[calc(100vw-16px)] rounded-xl border border-line bg-raised p-2 text-left text-sm font-normal text-fg shadow-xl"
      >
        {ordered.map((service) => (
          <span key={service} role="listitem" className="flex h-8 items-center justify-between gap-2 px-2">
            {service === 'library' ? (
              <span className="inline-flex items-center gap-1.5"><Icon name="library" size={14} />Медиатека</span>
            ) : <ServiceBadge service={service} />}
            {ranked && <span className="shrink-0 text-xs text-muted">{places(
              services.flatMap((item, i) => item === service ? [i + 1] : []),
            )}</span>}
          </span>
        ))}
      </span>
    </>
  );
}
