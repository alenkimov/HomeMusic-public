import type { ReactNode } from 'react';
import { useTidyMarks, type MarkSubject } from '../lib/tidyMarks';
import { useVersionLabel, type VersionIdentity } from '../lib/versionLabels';
import { TidyMarkButton } from './TidyMark';

export function VersionBadge({ label }: { label?: string | null }) {
  return label ? (
    <span className="max-w-[50%] shrink-0 truncate rounded-full bg-fg/10 px-2 py-px align-middle text-[11px] font-medium text-muted" title={label}>
      {label}
    </span>
  ) : null;
}

/** A track's name, its version's mark after it, and the mark of «Наведение порядка» when it offers something for
 * the name (`mark`: the library's track; the name shortens, the marks stay). */
export function TrackTitle({ title, track, label, mark }: { title: ReactNode; track?: VersionIdentity | null; label?: string | null; mark?: number | null }) {
  const saved = useVersionLabel(track);
  const text = label === undefined ? saved : label;
  const marks = useTidyMarks(markOf(mark));
  if (!text && !marks.length) return <>{title}</>;
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1.5 align-bottom">
      <span className="min-w-0 truncate">{title}</span>
      <VersionBadge label={text} />
      {marks.length > 0 && <TidyMarkButton proposals={marks} />}
    </span>
  );
}

const markOf = (id: number | null | undefined): MarkSubject | null => (id && id > 0 ? { track: id } : null);
