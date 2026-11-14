import { useForYouCounts } from '../lib/forYou';
import { tracksWord } from '../lib/format';
import { Link } from '../lib/router';
import { AUTO_PLAYLISTS } from '../lib/stats';
import { Icon, type IconName } from './Icon';
import { cx } from './ui';

const TINTS = ['bg-accent/15 text-accent', 'bg-[#2ba6bc]/15 text-[#5fcfe2]', 'bg-[#f2b705]/15 text-[#f5cd4d]', 'bg-[#fb923c]/15 text-[#fda766]'];

/** «Для вас»: the lists that make themselves — «Микс дня», «Открытия недели», the tops, «Давно не слушал» — as
 * tiles, each with its mark in its own colour and how many tracks it has. On «Главная» and in «Библиотека». */
export function ForYouTiles({ className }: { className?: string }) {
  const counts = useForYouCounts();
  const count = (n: number | undefined) => (n === undefined ? ' ' : tracksWord(n));
  return (
    <div className={cx('grid grid-cols-2 gap-2', className)}>
      <Tile to="/mix" icon="sparkle" tint="bg-[#a78bfa]/15 text-[#b9a3fb]" title="Микс дня" sub={count(counts.mix)} />
      <Tile to="/discoveries" icon="wave" tint="bg-[#4da3ff]/15 text-[#7dbbff]" title="Открытия недели" sub={count(counts.weekly)} />
      {AUTO_PLAYLISTS.map((a, i) => (
        <Tile key={a.kind} to={`/auto/${a.kind}`} icon={a.icon} tint={TINTS[i % TINTS.length]} title={a.title} sub={count(counts[a.kind])} />
      ))}
    </div>
  );
}

function Tile({ to, icon, tint, title, sub }: { to: string; icon: IconName; tint: string; title: string; sub: string }) {
  return (
    <Link to={to} className="flex flex-col gap-2.5 rounded-2xl bg-surface px-3.5 py-3 hover:bg-raised">
      <span className={`flex h-8 w-8 items-center justify-center rounded-full ${tint}`}>
        <Icon name={icon} size={18} />
      </span>
      <span className="min-w-0">
        <span className="block truncate text-[15px] font-semibold">{title}</span>
        <span className="block truncate text-[13px] text-muted">{sub}</span>
      </span>
    </Link>
  );
}
