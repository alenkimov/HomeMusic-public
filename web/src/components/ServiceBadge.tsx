import type { ReactNode } from 'react';
import { providerLabel } from '../lib/format';
import { BrandIcon, hasBrand } from './BrandIcon';
import { Icon } from './Icon';

const SERVICES: Record<string, { name: string; aliases?: string[]; initials?: string }> = {
  hitmos: { name: 'hitmos.fm', aliases: ['hitmoz', 'hitmos.fm', 'hitmos_fm', 'хитмоз'] },
  youtube: { name: 'YouTube', aliases: ['ytdlp', 'yt-dlp', 'yt_reupload', 'ютуб'] },
  ytm: { name: 'YouTube Music', aliases: ['ytmusic', 'ytm_radio'] },
  soundcloud: { name: 'SoundCloud', aliases: ['саундклауд'] },
  soulseek: { name: 'Soulseek', aliases: ['slskd'] },
  spotify: { name: 'Spotify' },
  deezer: { name: 'Deezer', aliases: ['deezer_radio'] },
  yandex: { name: 'Яндекс Музыка', aliases: ['Яндекс Музыки', 'Яндекс Музыке', 'Яндекс Музыку', 'Яндекс', 'Яндекса', 'Яндексе', 'yandex_wave'] },
  vk: { name: 'ВКонтакте', aliases: ['ВК', 'VK Music'] },
  lastfm: { name: 'Last.fm', aliases: ['lastfm_similar'] },
  listenbrainz: { name: 'ListenBrainz' },
  navidrome: { name: 'Navidrome' },
  telegram: { name: 'Telegram', aliases: ['Телеграм'] },
  google: { name: 'Google' },
  googletranslate: { name: 'Google Translate' },
  lrclib: { name: 'LRCLIB', initials: 'LRC' },
  lrcred: { name: 'lrc.red', initials: 'LRC' },
  lyricsplus: { name: 'LyricsPlus', initials: 'L+' },
  netease: { name: 'NetEase' },
  ovh: { name: 'lyrics.ovh', initials: 'OVH' },
  mymemory: { name: 'MyMemory', initials: 'MM' },
  llm: { name: 'ИИ Хаба', initials: 'ИИ' },
  musicbrainz: { name: 'MusicBrainz' },
  exportify: { name: 'Exportify', initials: 'EX' },
  homemusic: { name: 'HomeMusic' },
  catalog: { name: 'Каталог' },
  file: { name: 'Теги файла' },
  upload: { name: 'Загрузка' },
};

const serviceIds = new Map<string, string>();
const mentions = new Map<string, string>();
for (const [id, { name, aliases = [] }] of Object.entries(SERVICES)) {
  for (const spelling of [id, name, ...aliases]) serviceIds.set(spelling.toLowerCase(), id);
  // These labels describe local features as well as link destinations.
  if (['catalog', 'file', 'upload', 'homemusic'].includes(id)) continue;
  for (const spelling of [name, ...aliases]) mentions.set(spelling.toLowerCase(), id);
}
const spellings = [...mentions.keys()].sort((a, b) => b.length - a.length).map((name) => name.replace(/[.*+?^$(){}|[\]\\]/g, '\\$&'));
const servicePattern = new RegExp(
  /(?<![\p{L}\p{N}_./@-])/u.source + '(' + spellings.join('|') + ')' + /(?![\p{L}\p{N}_/@-]|\.[\p{L}\p{N}])/u.source,
  'giu',
);

const idOf = (service: string) => serviceIds.get(service.toLowerCase()) ?? service.toLowerCase();

export function serviceName(service: string) {
  if (service === 'hitmoz' || service === 'yt_reupload') return providerLabel(service);
  return SERVICES[idOf(service)]?.name ?? providerLabel(service);
}

export function ServiceIcon({ service, size = 14, className }: { service: string; size?: number; className?: string }) {
  const id = idOf(service);
  const box = ['inline-block shrink-0 align-[-0.15em]', className].filter(Boolean).join(' ');
  if (hasBrand(id)) return <BrandIcon service={id} size={size} className={box} />;
  const initials = SERVICES[id]?.initials;
  if (initials) {
    return (
      <span aria-hidden="true" className={box + ' rounded-sm bg-fg/10 text-center font-bold text-muted'}
        style={{ width: size, height: size, lineHeight: size + 'px', fontSize: size * (initials.length > 2 ? 0.36 : 0.48) }}>
        {initials}
      </span>
    );
  }
  return <Icon name={id === 'upload' ? 'upload' : id === 'file' ? 'lyrics' : id === 'homemusic' ? 'disc' : 'link'} size={size} className={box} />;
}

export function ServiceBadge({ service, label, iconOnly = false, size, className }: {
  service?: string | null; label?: string | null; iconOnly?: boolean; size?: number; className?: string;
}) {
  if (!service) return label ?? null;
  const name = label ?? serviceName(service);
  return (
    <span className={['inline-flex max-w-full items-baseline gap-1.5 align-baseline', className].filter(Boolean).join(' ')} title={name}>
      <span className="shrink-0 leading-none"><ServiceIcon service={service} size={size} /></span>
      <span className={iconOnly ? 'sr-only' : 'min-w-0 truncate'}>{name}</span>
    </span>
  );
}

/** Service names in fixed UI copy and in diagnostic strings already stored by the Hub. */
export function ServiceText({ text }: { text: string }) {
  const parts = text.split(servicePattern);
  const content: ReactNode[] = [parts[0]];
  for (let i = 1; i < parts.length; i += 2) {
    const part = parts[i];
    const spelling = part.toLowerCase();
    const id = mentions.get(spelling)!;
    const name = serviceName(spelling);
    const isProviderId = /^[a-z]+[_-][a-z]+$/i.test(part) || /^(ytdlp|ytmusic|slskd)$/.test(spelling);
    const label = isProviderId || name.toLowerCase() === spelling ? name : part;
    content.push(<ServiceBadge key={i} service={id} label={label} />);
    // Older errors can include both the provider prefix and the downloader's prefix.
    while (i + 3 < parts.length && /^\s*:\s*$/.test(parts[i + 1]) && /^\s*:/.test(parts[i + 3])) {
      const next = parts[i + 2].toLowerCase();
      if (name !== serviceName(next) && !(next === 'yt-dlp' && (id === 'youtube' || id === 'soundcloud'))) break;
      i += 2;
    }
    content.push(parts[i + 1]);
  }
  return content;
}

export function AudioServiceList() {
  return (
    <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
      {['ytdlp', 'soundcloud', 'hitmoz', 'hitmos_fm'].map((service) => <ServiceBadge key={service} service={service} />)}
    </span>
  );
}
