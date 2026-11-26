import { useEffect, useRef, useState, type KeyboardEvent, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import type { TidyProposalOut } from '../api/types';
import { navigate } from '../lib/router';
import { tidyPath, useTidyMarks, type MarkSubject } from '../lib/tidyMarks';
import { Icon } from './Icon';
import { Badge, cx } from './ui';

/** What each kind offers, in a few words: the head of a mark's card. */
export const KIND_TITLE: Record<TidyProposalOut['kind'], string> = {
  title: 'Лишнее в названии',
  cyrillic: 'Название кириллицей',
  version: 'Версия песни',
  double: 'Запись есть дважды',
  artist: 'Один исполнитель',
  lyrics: 'Лишние строки текста',
  uncensor: 'Слова под цензурой',
};

/** Who says so: a rule, the AI, the AI unsure (in yellow — a person decides). */
export function ByMark({ p }: { p: TidyProposalOut }) {
  if (p.by === 'rule') return <Badge tone="muted">Правило</Badge>;
  return p.sure ? <Badge tone="accent">ИИ уверен</Badge> : <Badge tone="warn">ИИ сомневается</Badge>;
}

const SIZES = { sm: [20, 12], md: [24, 14], lg: [32, 18] } as const;

/** The mark of «Наведение порядка» beside what it offers to put right: a press leads to the proposal in its section,
 * a mouse over it shows what would be. It may stand inside a row that is itself a link or a button, so it is not an
 * `<a>` of its own: it takes the press away from the row. */
export function TidyMarkButton({ proposals, size = 'sm', className }: { proposals: TidyProposalOut[]; size?: keyof typeof SIZES; className?: string }) {
  const [box, setBox] = useState<DOMRect | null>(null);
  const ref = useRef<HTMLSpanElement>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const first = proposals[0];
  if (!first) return null;
  const [d, icon] = SIZES[size];
  const go = (e: MouseEvent | KeyboardEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setBox(null);
    navigate(tidyPath(first));
  };
  const label = `Наведение порядка: ${KIND_TITLE[first.kind].toLowerCase()}${first.to ? ` — «${first.to}»` : ''}`;
  return (
    <>
      <span
        ref={ref}
        role="link"
        tabIndex={0}
        aria-label={label}
        data-row-actions
        onClick={go}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && go(e)}
        onPointerDown={(e) => e.stopPropagation()}
        onPointerEnter={(e) => {
          if (e.pointerType !== 'mouse') return; // a finger has no «over»: its press leads on at once
          window.clearTimeout(timer.current);
          timer.current = window.setTimeout(() => ref.current && setBox(ref.current.getBoundingClientRect()), 250);
        }}
        onPointerLeave={() => {
          window.clearTimeout(timer.current);
          setBox(null);
        }}
        className={cx('relative inline-flex shrink-0 cursor-pointer items-center justify-center rounded-full bg-ai/15 align-middle text-ai transition hover:bg-ai/25',
          // the finger's target is larger than the mark
          'before:absolute before:-inset-2.5', className)}
        style={{ width: d, height: d }}
      >
        <Icon name="sparkle" size={icon} filled stroke={0} />
      </span>
      {box && createPortal(<MarkCard proposals={proposals} at={box} />, document.body)}
    </>
  );
}

/** The mark of the subject's name, when something waits for an answer about it. */
export function TidyMark({ on, size, className }: { on: MarkSubject | null | undefined; size?: keyof typeof SIZES; className?: string }) {
  const marks = useTidyMarks(on);
  return marks.length ? <TidyMarkButton proposals={marks} size={size} className={className} /> : null;
}

/** What a mark offers, under it (over it near the bottom of the window): «было → станет», who says so and why. */
function MarkCard({ proposals, at }: { proposals: TidyProposalOut[]; at: DOMRect }) {
  const W = 320;
  const left = Math.max(8, Math.min(window.innerWidth - W - 8, at.left + at.width / 2 - 28));
  const below = at.bottom + 220 < window.innerHeight;
  return (
    <div
      role="tooltip"
      className="pointer-events-none fixed z-[90] flex flex-col gap-3 rounded-xl bg-raised p-4 text-left shadow-2xl ring-1 ring-line"
      style={{ width: W, left, ...(below ? { top: at.bottom + 8 } : { bottom: window.innerHeight - at.top + 8 }) }}
    >
      {proposals.slice(0, 3).map((p) => (
        <div key={p.id} className="flex flex-col gap-1">
          <span className="flex items-center gap-2">
            <span className="text-[13px] font-semibold">{KIND_TITLE[p.kind]}</span>
            <span className="ml-auto"><ByMark p={p} /></span>
          </span>
          {p.was && <span className="line-clamp-2 break-words text-[13px] text-muted line-through decoration-danger/70">{p.was}</span>}
          {p.to && <span className="line-clamp-2 break-words text-[15px] font-semibold"><span className="font-normal text-dim">→ </span>{p.to}</span>}
          {p.why && <span className={cx('line-clamp-2 text-[12px]', p.by === 'ai' && !p.sure ? 'text-warn' : 'text-dim')}>{p.why}</span>}
        </div>
      ))}
      {proposals.length > 3 && <span className="text-[12px] text-dim">И ещё {proposals.length - 3}</span>}
      <span className="border-t border-line pt-2.5 text-[12px] text-ai">Нажмите, чтобы открыть в «Наведении порядка»</span>
    </div>
  );
}
