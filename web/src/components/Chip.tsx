import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { Link } from '../lib/router';
import { Icon, type IconName } from './Icon';
import { cx } from './ui';

/** A pill with a name in it: a tag, a genre, a chosen playlist, a «+N». One look everywhere, four sizes —
 * `xs` a column of a track list, `sm` the filter over a list, `md` the choosing of tags and what an upload will
 * get, `lg` a saved selection one opens. `tone`: plain, `on` (chosen), `some` (on some of the tracks only),
 * `no` (left out of a filter). `onRemove` adds a ✕ of its own; `onClick` makes the whole pill a button, `to` a
 * link. */
export type ChipTone = 'plain' | 'on' | 'some' | 'no';

const SIZE = {
  xs: 'gap-1 px-2 py-0.5 text-[11px]',
  sm: 'h-7 gap-1 pl-2.5 pr-1.5 text-xs',
  md: 'min-h-8 gap-1.5 px-3 py-1 text-sm',
  lg: 'h-11 gap-2 pl-3 pr-4 text-[15px] font-medium',
};
const TONE: Record<ChipTone, string> = {
  plain: 'bg-fg/10 text-muted',
  on: 'bg-accent text-on-accent',
  some: 'bg-warn/20 text-warn',
  no: 'bg-danger/20 text-danger line-through',
};

export function Chip({ children, size = 'md', tone = 'plain', icon, iconClass, iconFilled, after, onRemove, removeLabel, onClick, to, button, title, className }: {
  children: ReactNode;
  size?: keyof typeof SIZE;
  tone?: ChipTone;
  icon?: IconName;
  /** the icon's own colour (a selection's filter mark is the accent's) */
  iconClass?: string;
  iconFilled?: boolean;
  /** after the name: a count, the ✕ of a filter's pill */
  after?: ReactNode;
  onRemove?: () => void;
  removeLabel?: string;
  onClick?: () => void;
  to?: string;
  button?: ButtonHTMLAttributes<HTMLButtonElement>;
  title?: string;
  className?: string;
}) {
  // the large one stands on the page's cards, not on a tint
  const look = size === 'lg' ? 'bg-surface text-fg hover:bg-raised' : cx(TONE[tone], (onClick || to) && tone === 'plain' && 'hover:text-fg');
  const cls = cx('flex min-w-0 shrink-0 items-center whitespace-nowrap rounded-full transition', SIZE[size], look, onRemove && '!pr-1', className);
  const body = (
    <>
      {icon && <Icon name={icon} size={size === 'lg' ? 18 : size === 'xs' ? 11 : 13} className={cx('shrink-0', iconClass)} filled={iconFilled} />}
      <span className="min-w-0 truncate">{children}</span>
      {after}
      {onRemove && (
        <button type="button" aria-label={removeLabel} title={removeLabel} onClick={onRemove}
          className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-muted hover:bg-fg/10 hover:text-fg">
          <Icon name="x" size={13} stroke={2.5} />
        </button>
      )}
    </>
  );
  if (to) return <Link to={to} title={title} className={cls}>{body}</Link>;
  if (onClick) return <button type="button" {...button} title={title} onClick={onClick} className={cls}>{body}</button>;
  return <span title={title} className={cls}>{body}</span>;
}
