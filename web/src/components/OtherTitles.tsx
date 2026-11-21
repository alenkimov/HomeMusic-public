import { useState } from 'react';
import type { AlternateNameOut } from '../api/types';
import { Icon } from './Icon';
import { Button } from './ui';

const sources: Record<string, string> = { renamed: 'прежнее', file: 'из файла', manual: 'вручную', catalog: 'каталог' };

export function OtherTitles({ subject, names, readOnly, busy, maxLength, onChoose, onAdd, onRemove }: {
  subject: 'трек' | 'альбом';
  names?: AlternateNameOut[];
  readOnly?: boolean;
  busy?: boolean;
  maxLength: number;
  onChoose: (name: string) => void;
  onAdd: (name: string) => Promise<boolean>;
  onRemove: (id: number) => void;
}) {
  const [name, setName] = useState('');
  const [adding, setAdding] = useState(false);
  const add = async () => {
    if (busy || adding || !name.trim()) return;
    setAdding(true);
    try {
      if (await onAdd(name.trim())) setName('');
    } finally {
      setAdding(false);
    }
  };
  return (
    <div>
      <p className="pb-1.5 text-[13px] text-muted">Другие названия — по ним {subject} тоже находится</p>
      {!!names?.length && (
        <div className="flex flex-wrap gap-1.5 pb-2">
          {names.map((n) => {
            const id = n.id;
            return (
              <span key={`${id}:${n.name}`} className="inline-flex max-w-full items-center gap-1.5 rounded-full border border-line px-2.5 py-1 text-sm">
                <span className="truncate" title={n.name}>{n.name}</span>
                <span className="shrink-0 text-[11px] text-dim">{sources[n.source] ?? n.source}</span>
                {!readOnly && (
                  <>
                    <button type="button" disabled={busy || adding} className="shrink-0 rounded-full px-1.5 text-[11px] text-info hover:bg-fg/10 hover:underline"
                      aria-label={`Сделать основным: ${n.name}`} title="Сделать основным названием" onClick={() => onChoose(n.name)}>
                      сделать основным
                    </button>
                    {id != null && (
                      <button type="button" disabled={busy || adding} className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-dim hover:bg-fg/10 hover:text-fg"
                        aria-label={`Убрать название: ${n.name}`} title="Убрать" onClick={() => onRemove(id)}>
                        <Icon name="x" size={12} />
                      </button>
                    )}
                  </>
                )}
              </span>
            );
          })}
        </div>
      )}
      {!readOnly && (
        <div className="flex gap-2">
          <input className="input min-w-0 flex-1" placeholder="Ещё название" aria-label="Ещё название"
            value={name} onChange={(e) => setName(e.target.value)} maxLength={maxLength} autoComplete="off"
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); void add(); } }} />
          <Button small busy={adding} disabled={busy || !name.trim()} onClick={() => void add()}>Добавить</Button>
        </div>
      )}
    </div>
  );
}
