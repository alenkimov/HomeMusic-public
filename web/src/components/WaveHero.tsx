import { toggle, useListen } from '../lib/listen';
import { canWave, startWave, useWave } from '../lib/wave';
import { Icon } from './Icon';
import { Spinner } from './ui';
import { WaveGlow } from './WaveGlow';

/** «Моя волна» as the hero of «Главная», on a phone and on a computer alike: no card, a living soft blob glows right
 * on the page background (a canvas wider than the column, its edges fade out); the title and the state line stand
 * over the round play button, which is the middle of the glow. The blob's colors come from the current track's cover;
 * it is bigger while the wave plays and shrinks (slowly) on pause. Only a computer has it move with the loudness of
 * the sound (`lib/waveAudio.ts`): a phone's player is not to be touched for it. */
export function WaveHero() {
  const { title, starting } = useWave();
  const wave = useListen((s) => !!s.track && s.context.startsWith('wave:'));
  const phase = useListen((s) => s.phase);
  const cover = useListen((s) => s.track?.cover_small_url ?? s.track?.cover_url ?? null);
  if (!canWave()) return null;
  const busy = starting === '';
  const on = wave && title === 'Моя волна';
  const playing = on && phase === 'playing';
  // wanted to play (loading the next track counts): the button then offers a pause
  const running = on && (playing || phase === 'loading' || phase === 'resolving');
  // The mask fades the canvas out toward its edges, whatever the blob does there.
  const fade = 'radial-gradient(closest-side, #000 72%, transparent 100%)';
  const caption = busy ? 'busy' : running ? 'playing' : on ? 'paused' : 'idle';
  const captions: Array<[string, string]> = [
    ['idle', 'Бесконечный поток под ваш вкус'],
    ['busy', 'Подбираем первые треки…'],
    ['playing', 'Играет — подстраивается под каждый пропуск и ♥'],
    ['paused', 'На паузе — продолжим с того же места'],
  ];
  const label = busy ? 'Моя волна: запускаем' : running ? 'Моя волна: пауза' : on ? 'Моя волна: продолжить' : 'Моя волна: слушать';

  return (
    <div className="relative mt-1 h-[300px] lg:h-[360px]">
      <WaveGlow
        playing={playing}
        cover={cover}
        center={[0.5, 0.5]}
        zoom={0.9}
        className="pointer-events-none absolute inset-y-0 left-1/2 w-[min(1100px,calc(100vw-32px))] -translate-x-1/2"
        style={{ maskImage: fade, WebkitMaskImage: fade }}
      />
      {/* the button is the very middle of the glow; the name and the caption stand over it */}
      <div className="absolute inset-x-0 bottom-[calc(50%+36px)] flex flex-col items-center text-center text-white" style={{ textShadow: '0 1px 14px rgba(0,0,0,.6)' }}>
        <h2 className="text-2xl font-bold">Моя волна</h2>
        {/* A fixed-size slot: the captions cross-fade in place, so neither the title nor the button ever moves. */}
        <div className="relative mt-1 h-10 w-80 max-w-full text-sm leading-snug text-white/80">
          {captions.map(([key, text]) => (
            <p key={key} className={`absolute inset-x-0 top-0 transition-opacity duration-500 ${key === caption ? 'opacity-100' : 'opacity-0'}`} aria-hidden={key !== caption}>
              {text}
            </p>
          ))}
        </div>
      </div>
      <button
          type="button"
          onClick={() => (on ? toggle() : void startWave(null, 'home_card'))}
          disabled={starting !== null}
          aria-label={label}
          className="absolute left-1/2 top-1/2 inline-flex h-14 w-14 -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-fg text-bg shadow-lg transition hover:scale-105 active:scale-95 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-white disabled:opacity-90"
        >
          {busy ? <Spinner size={24} /> : running ? <Icon name="pause" size={24} stroke={3} /> : <Icon name="play" size={24} stroke={2.4} className="translate-x-px" />}
        </button>
    </div>
  );
}
