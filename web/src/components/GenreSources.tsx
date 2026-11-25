import { Chip } from './Chip';
import { useState } from 'react';
import { GENRE_SOURCES, type GenreSource, type Tag, type TrackGenreSources } from '../lib/tags';
import { Icon } from './Icon';
import { ServiceIcon, serviceName } from './ServiceBadge';
import { Sheet, cx } from './ui';

const SOURCE_DETAILS: Record<GenreSource, string> = {
  file: 'Метаданные аудиофайла',
  spotify: 'Жанр трека',
  deezer: 'Жанр альбома',
  yandex: 'Жанр альбома',
  hitmoz: 'Жанр альбома',
  lastfm: 'Тег слушателей',
  manual: 'Добавлен вручную',
  unknown: 'Источник не сохранён',
};

function sourceName(source: GenreSource) {
  if (source === 'unknown') return 'Неизвестен';
  if (source === 'manual') return 'Вручную';
  return serviceName(source);
}

function GenreIcon({ source, size = 14 }: { source: GenreSource; size?: number }) {
  if (source === 'unknown' || source === 'manual') {
    return <Icon name={source === 'unknown' ? 'help' : 'tag'} size={size} className="shrink-0 text-muted" />;
  }
  return <ServiceIcon service={source} size={size} />;
}

export function GenreSourceFilter({ byGenre, selected, onChange }: {
  byGenre: ReadonlyMap<number, ReadonlySet<GenreSource>>;
  selected: GenreSource | '';
  onChange: (source: GenreSource | '') => void;
}) {
  const available = new Set([...byGenre.values()].flatMap((sources) => [...sources]));
  const options = GENRE_SOURCES.filter((source) => available.has(source) || source === selected);
  if (!options.length) return null;
  return (
    <div role="group" aria-label="Источник жанра" className="mb-2 flex flex-wrap gap-1.5">
      {(['', ...options] as const).map((source) => (
        <button key={source} type="button" aria-pressed={selected === source} onClick={() => onChange(source)}
          className={cx('inline-flex min-h-7 items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs',
            selected === source ? 'border-accent/50 bg-accent/15 text-fg' : 'border-line text-muted hover:text-fg')}>
          {source && <GenreIcon source={source} />}
          {source ? sourceName(source) : 'Все'}
        </button>
      ))}
    </div>
  );
}

const sourcesOf = (sources: TrackGenreSources, id: number) =>
  GENRE_SOURCES.filter((source) => (sources[id] ?? ['unknown']).includes(source));

function GenreDetails({ genres, sources }: { genres: Tag[]; sources: TrackGenreSources }) {
  return (
    <ul className="divide-y divide-line">
      {genres.map((genre) => (
        <li key={genre.id} className="grid gap-1.5 py-2.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,1.5fr)]">
          <span className="min-w-0 break-words text-sm font-medium">{genre.name}</span>
          <ul className="space-y-1 pl-2 sm:pl-0">
            {sourcesOf(sources, genre.id).map((source) => (
              <li key={source} className="flex flex-wrap items-center gap-x-2 text-xs text-muted">
                <span className="inline-flex items-center gap-1.5 text-fg"><GenreIcon source={source} />{sourceName(source)}</span>
                <span>{SOURCE_DETAILS[source]}</span>
              </li>
            ))}
          </ul>
        </li>
      ))}
    </ul>
  );
}

export function GenreCell({ genres, sources, title }: { genres: Tag[]; sources: TrackGenreSources; title: string }) {
  const [open, setOpen] = useState(false);
  const priority = (genre: Tag) => GENRE_SOURCES.indexOf(sourcesOf(sources, genre.id)[0]);
  const list = [...genres].sort((a, b) => priority(a) - priority(b));
  const first = list[0];
  if (!first) return <span />;
  const origins = sourcesOf(sources, first.id);
  return (
    <div data-row-actions className="min-w-0" onClick={(e) => e.stopPropagation()}>
      <button type="button" aria-haspopup="dialog" aria-label={`Источники жанров «${title}»`}
        title={`${first.name} · ${origins.map(sourceName).join(', ')}`} onClick={() => setOpen(true)}
        className="flex h-8 w-full min-w-0 items-center gap-1 rounded-md text-left text-xs text-muted hover:text-fg focus-visible:text-fg">
        {origins.slice(0, 2).map((source) => <GenreIcon key={source} source={source} size={12} />)}
        <span className="min-w-0 truncate">{first.name}</span>
        {list.length > 1 && <Chip size="xs">+{list.length - 1}</Chip>}
      </button>
      {open && <Sheet open onClose={() => setOpen(false)} title="Источники жанров">
        <p className="pb-1 text-sm text-muted">{title}</p>
        <GenreDetails genres={list} sources={sources} />
      </Sheet>}
    </div>
  );
}
