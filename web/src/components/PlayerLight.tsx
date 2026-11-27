import { imageUrl } from '../lib/img';

const DRIFTS = ['hm-drift-a', 'hm-drift-b', 'hm-drift-c'] as const;
const place = (i: number) => ({
  left: ['-20%', '35%', '10%'][i], top: ['-15%', '5%', '40%'][i],
  animation: `${DRIFTS[i]} ${[34, 41, 47][i]}s ease-in-out infinite alternate`,
});

/** The full player's own light — the cover in slow motion: three copies of it under a strong blur, each drifting
 * its own way. (A lighter look of radial gradients of the cover's colours was tried beside it: measured on Android,
 * an iPhone and a computer it cost no less to notice, and looked worse.) Fixed behind the page; `-z-10`, so its
 * holder makes a stacking context. */
export function PlayerLight({ cover }: { cover: string | null | undefined }) {
  const src = cover ? imageUrl(cover) : null;
  if (!src) return null;
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <div className="absolute inset-0 saturate-150">
        {DRIFTS.map((name, i) => (
          <img key={name} src={src} alt="" className="absolute h-[85%] w-[85%] rounded-full object-cover opacity-45 blur-3xl motion-reduce:!animate-none" style={place(i)} />
        ))}
      </div>
      <div className="absolute inset-0 bg-bg/40" />
    </div>
  );
}
