import { imageUrl } from '../lib/img';
import type { PlayerLight as Look } from '../lib/playerLight';
import { useCoverColors } from '../lib/softCover';

const DRIFTS = ['hm-drift-a', 'hm-drift-b', 'hm-drift-c'] as const;
const place = (i: number) => ({
  left: ['-20%', '35%', '10%'][i], top: ['-15%', '5%', '40%'][i],
  animation: `${DRIFTS[i]} ${[34, 41, 47][i]}s ease-in-out infinite alternate`,
});

/** The full player's own light — the cover's colours in slow motion, three soft shapes each drifting its own way
 * (`lib/playerLight.ts` says which look). Fixed behind the page; `-z-10`, so its holder makes a stacking context. */
export function PlayerLight({ cover, look }: { cover: string | null | undefined; look: Look }) {
  const src = cover ? imageUrl(cover) : null;
  const colors = useCoverColors(look === 'light' ? src : null);
  if (!src) return null;
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      {look === 'blur' ? (
        <div className="absolute inset-0 saturate-150">
          {DRIFTS.map((name, i) => (
            <img key={name} src={src} alt="" className="absolute h-[85%] w-[85%] rounded-full object-cover opacity-45 blur-3xl motion-reduce:!animate-none" style={place(i)} />
          ))}
        </div>
      ) : (
        colors && DRIFTS.map((name, i) => {
          const [r, g, b] = colors[i];
          return (
            <div key={name} className="absolute h-[85%] w-[85%] motion-reduce:!animate-none"
              style={{ ...place(i), background: `radial-gradient(closest-side, rgb(${r} ${g} ${b} / 0.85), rgb(${r} ${g} ${b} / 0.6) 50%, rgb(${r} ${g} ${b} / 0) 100%)` }} />
          );
        })
      )}
      <div className="absolute inset-0 bg-bg/40" />
    </div>
  );
}
