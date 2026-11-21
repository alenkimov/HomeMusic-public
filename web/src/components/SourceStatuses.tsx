import { useEffect, useId, useRef, useState } from 'react';
import type { SourceStatus } from '../api/types';
import { Icon } from './Icon';
import { ServiceBadge, ServiceIcon, serviceName } from './ServiceBadge';
import { cx } from './ui';

const stateLabel = { searching: 'Ищет', ready: 'Ответил', empty: 'Нет совпадений', error: 'Ошибка запроса' };
const stateColor = { searching: 'text-info', ready: 'text-accent', empty: 'text-muted', error: 'text-danger' };

function Mark({ state }: Pick<SourceStatus, 'state'>) {
  if (state === 'searching') return <span className="hm-spin h-2 w-2 rounded-full border-[1.5px] border-current border-r-transparent motion-reduce:animate-none" />;
  return <Icon name={state === 'ready' ? 'check' : state === 'empty' ? 'minus' : 'alert'} size={9} stroke={2.8} />;
}

export function SourceStatuses({ sources, label, labels = stateLabel }: {
  sources: readonly SourceStatus[];
  label: string;
  labels?: Record<SourceStatus['state'], string>;
}) {
  const [open, setOpen] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const details = useId();

  useEffect(() => {
    if (!open) return;
    const close = (event: PointerEvent) => {
      if (event.target instanceof Node && !wrapper.current?.contains(event.target)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      button.current?.focus();
    };
    document.addEventListener('pointerdown', close);
    document.addEventListener('keydown', escape);
    return () => {
      document.removeEventListener('pointerdown', close);
      document.removeEventListener('keydown', escape);
    };
  }, [open]);

  return (
    <div ref={wrapper} className="shrink-0">
      <button
        ref={button}
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={details}
        onClick={() => setOpen((show) => !show)}
        className="flex h-11 items-center gap-0.5 rounded-lg px-1 hover:bg-fg/5 min-[360px]:gap-1 sm:gap-1.5"
      >
        {sources.map(({ service, state }) => (
          <span
            key={service}
            aria-hidden="true"
            title={`${service === 'library' ? 'Медиатека' : serviceName(service)} · ${labels[state]}`}
            className={cx('relative flex h-5 w-5 items-center justify-center rounded-full border bg-bg/70 sm:h-6 sm:w-6',
              state === 'searching' ? 'border-dashed border-info/60' : 'border-line')}
          >
            {service === 'library' ? <Icon name="library" size={12} className="text-muted" /> : <ServiceIcon service={service} size={12} />}
            <span className={cx('absolute -bottom-0.5 -right-0.5 flex h-3 w-3 items-center justify-center rounded-full bg-raised', stateColor[state])}>
              <Mark state={state} />
            </span>
          </span>
        ))}
      </button>
      <ul id={details} hidden={!open} className="absolute right-0 top-full z-30 mt-2 w-64 max-w-full rounded-xl border border-line bg-raised p-2 shadow-xl">
        {sources.map(({ service, state }) => (
          <li key={service} className="flex min-h-9 items-center justify-between gap-2 rounded-lg px-2 text-sm">
            {service === 'library' ? <span className="inline-flex items-center gap-1.5"><Icon name="library" size={14} />Медиатека</span>
              : <ServiceBadge service={service} />}
            <span className={cx('flex shrink-0 items-center gap-1 text-xs', stateColor[state])}>
              <Mark state={state} />{labels[state]}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
