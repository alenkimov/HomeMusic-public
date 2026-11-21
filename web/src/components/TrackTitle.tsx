import type { ReactNode } from 'react';
import { useVersionLabel, type VersionIdentity } from '../lib/versionLabels';

export function VersionBadge({ label }: { label?: string | null }) {
  return label ? (
    <span className="max-w-[50%] shrink-0 truncate rounded-full bg-fg/10 px-2 py-px align-middle text-[11px] font-medium text-muted" title={label}>
      {label}
    </span>
  ) : null;
}

export function TrackTitle({ title, track, label }: { title: ReactNode; track?: VersionIdentity | null; label?: string | null }) {
  const saved = useVersionLabel(track);
  const text = label === undefined ? saved : label;
  if (!text) return <>{title}</>;
  return (
    <span className="inline-flex min-w-0 max-w-full items-center gap-1.5 align-bottom">
      <span className="min-w-0 truncate">{title}</span>
      <VersionBadge label={text} />
    </span>
  );
}
