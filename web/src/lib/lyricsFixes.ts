import type { TidyProposalOut } from '../api/types';

/** A line «Наведение порядка» offers to put right, and how. */
export interface Fix {
  p: TidyProposalOut;
  /** the line as it would be; null: it goes */
  to: string | null;
}

/** What `proposals` (of «lyrics»: lines to drop, of «uncensor»: words to put back) do to the lines of `text`, by the
 * line's place — while the line still reads as the proposal saw it (a text changed since is not offered for). */
export function fixesOf(text: readonly string[], proposals: readonly TidyProposalOut[]): Map<number, Fix> {
  const out = new Map<number, Fix>();
  for (const p of proposals) {
    const at = p.kind === 'lyrics' ? p.show.drop_at : p.kind === 'uncensor' ? p.show.fix_at : undefined;
    at?.forEach((i, k) => {
      const was = p.kind === 'lyrics' ? p.show.lines?.[k] : p.show.pairs?.[k]?.was;
      if (was !== undefined && text[i] === was) out.set(i, { p, to: p.kind === 'lyrics' ? null : (p.show.pairs?.[k]?.to ?? null) });
    });
  }
  return out;
}
