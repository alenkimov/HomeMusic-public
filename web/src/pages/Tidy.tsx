import { useEffect, useRef, useState, type ReactNode } from 'react';
import { api, type TidyDecision } from '../api/client';
import type { TidyOverviewOut, TidyProposalOut, TidyProposalSection, TidySectionId, TrackOut } from '../api/types';
import { TrackRow } from '../components/catalog';
import { LyricsExcerpt } from '../components/LyricsExcerpt';
import { Icon } from '../components/Icon';
import { ByMark } from '../components/TidyMark';
import { Button, Cover, Empty, ErrorState, IconButton, PageHeader, SkeletonRows, Segmented, cx } from '../components/ui';
import { useHubEvent } from '../lib/events';
import { fmtWhen, plural } from '../lib/format';
import { useAsync } from '../lib/hooks';
import { Link, albumPath, artistPath, goBack, navigate, useRoute } from '../lib/router';
import { dropMark } from '../lib/tidyMarks';
import { attempt, toast } from '../lib/ui';
import { AlbumMergeSection } from './AlbumMerge';

const SECTIONS: TidySectionId[] = ['names', 'versions', 'artists', 'albums', 'lyrics'];
const isSection = (x: string | null): x is TidySectionId => !!x && (SECTIONS as string[]).includes(x);

/** What a section looks for, in a line under its tabs. */
const ABOUT: Record<TidySectionId, string> = {
  names: 'Лишнее в названиях — «(Official Video)», «[Lyrics]», имя исполнителя — и русские названия, записанные латиницей.',
  versions: 'Другие записи той же песни (Sped Up, Live, Remix…) — связать с оригиналом как версии. Одна запись в двух файлах — оставить лучший.',
  artists: 'Один исполнитель под разными написаниями — объединить. Прежние написания останутся: по ним тоже найдётся.',
  albums: '',
  lyrics: 'Строки текста, которые не поются (ссылки, призывы подписаться, реклама), и слова, скрытые цензурой: «б***ь», «[цензура]».',
};

/** The AI's month and the last look-through, in a line. */
function statusLine(o: TidyOverviewOut): string {
  const checked = o.running ? 'Проверяю…' : o.checked_at ? `Проверено ${fmtWhen(o.checked_at)}` : 'Ещё не проверялось';
  const money = (x: number) => `$${x.toFixed(x < 10 ? 2 : 0)}`;
  const ai = o.ai.state === 'off'
    ? 'ИИ не подключён — работают только правила'
    : o.ai.state === 'limit'
      ? `ИИ: лимит месяца исчерпан (${money(o.ai.spent_usd)} из ${money(o.ai.limit_usd)})`
      : `ИИ за месяц: ${money(o.ai.spent_usd)} из ${money(o.ai.limit_usd)}`;
  return `${checked} · ${ai}`;
}

/** «Наведение порядка» (from «Меню»): what the Hub found to put right in the library, by sections — names, versions and
 * doubles, artists, albums in pieces, lyrics. Nothing changes until it is accepted; what was decided is in «Журнал» (the
 * header's button), where it can be taken back. The admins start a look-through by hand; it also runs every night. */
export function TidyPage() {
  const route = useRoute();
  const asked = route.query.get('tab');
  // come from a mark on a page (`lib/tidyMarks`): that proposal, lit, and the way back
  const focus = Number(route.query.get('p')) || null;
  const overview = useAsync((s) => api.tidy(s), [], true, 'tidy');
  const o = overview.data;
  // the tab asked for, else the first with something in it
  const section: TidySectionId = isSection(asked) ? asked : (o?.sections.find((x) => x.count > 0)?.id ?? 'names');
  const pick = (id: TidySectionId) => navigate(`/tidy?tab=${id}${focus ? `&p=${focus}` : ''}`, { replace: true });
  const [scanning, setScanning] = useState(false);

  useHubEvent('tidy', () => overview.reload(true));
  // while a look-through goes, its end is asked for now and then too (an event may be missed)
  useEffect(() => {
    if (!o?.running) return;
    const t = window.setInterval(() => overview.reload(true), 5000);
    return () => window.clearInterval(t);
  }, [o?.running, overview]);

  const scan = async () => {
    setScanning(true);
    await attempt(() => api.tidyScan(), 'Проверка началась — это займёт пару минут');
    setScanning(false);
    overview.reload(true);
  };

  return (
    <div className="pb-10">
      <PageHeader
        title="Наведение порядка"
        back="/more"
        right={
          // a phone has the room for the title only: the buttons are their icons there
          <>
            {o?.can_scan && (
              <>
                <Button small icon="refresh" busy={scanning || o.running} disabled={o.running} onClick={scan} className="mr-1 whitespace-nowrap max-sm:hidden">
                  {o.running ? 'Проверяю' : 'Проверить сейчас'}
                </Button>
                <IconButton icon="refresh" label="Проверить сейчас" busy={scanning || o.running} disabled={o.running} onClick={scan} className="sm:hidden" />
              </>
            )}
            <IconButton icon="history" label="Журнал" title="Журнал: принятое и отклонённое" onClick={() => navigate('/tidy/journal')} className="mr-1" />
          </>
        }
      />
      {!o ? (
        overview.error ? <ErrorState text={overview.error} onRetry={() => overview.reload()} /> : <SkeletonRows n={6} />
      ) : (
        <>
          <p className="px-4 text-[13px] text-muted lg:px-6">{statusLine(o)}</p>
          <div className="px-4 pt-4 lg:px-6">
            <Segmented<TidySectionId>
              label="Что навести"
              value={section}
              onChange={pick}
              options={o.sections.map((x) => ({ value: x.id, label: x.title, count: x.count || null }))}
            />
          </div>
          {focus && (
            <button type="button" onClick={() => goBack('/tidy')} className="mx-4 mt-3 flex min-h-9 items-center gap-1 text-[13px] font-semibold text-ai hover:underline lg:mx-6">
              <Icon name="chevron" size={16} className="rotate-180" />
              Вернуться
            </button>
          )}
          {section === 'albums' ? (
            <AlbumMergeSection onChange={() => overview.reload(true)} />
          ) : (
            <Proposals
              key={section}
              section={section}
              sure={o.sections.find((x) => x.id === section)?.sure ?? 0}
              focus={focus}
              checked={o.checked_at}
              onChange={() => overview.reload(true)}
            />
          )}
        </>
      )}
    </div>
  );
}

/** A section's proposals. A decided one stays where it was, dimmed, with «Отменить», until the list is asked anew
 * (another tab, a look-through done). */
/** The tracks the proposals are about, as every list shows them: their cards, asked in one request. */
/** The other track a proposal is about: a double's copy that stays, a version's original — its row is on the card
 * too. */
const pairOf = (p: TidyProposalOut) => (p.kind === 'double' ? p.show.keep_id : p.kind === 'version' ? p.show.of_id : null) ?? null;

function useTrackCards(rows: readonly TidyProposalOut[] | null | undefined): Map<number, TrackOut> {
  const ids = [...new Set((rows ?? []).flatMap((p) => [
    ...(p.subject_type === 'track' ? [p.subject_id] : []),
    ...(pairOf(p) ? [pairOf(p) as number] : []), // a double's copy that stays, a version's original
  ]))].sort((a, b) => a - b);
  const key = ids.join(',');
  const res = useAsync(() => (ids.length ? api.trackCards(ids) : Promise.resolve([])), [key], true, `tidy:cards:${key}`);
  return new Map((res.data ?? []).filter((t) => t.library_track_id != null).map((t) => [t.library_track_id as number, t]));
}

function Proposals({ section, sure, focus, checked, onChange }: { section: TidyProposalSection; sure: number; focus: number | null; checked: string | null; onChange: () => void }) {
  const res = useAsync((s) => api.tidyProposals(section, s), [section]);
  const [rows, setRows] = useState<TidyProposalOut[] | null>(null);
  const [busy, setBusy] = useState<number | 'sure' | null>(null);
  useEffect(() => { if (res.data) setRows(res.data); }, [res.data]);
  useHubEvent('tidy', (d) => { if (d.kind === 'checked') res.reload(true); });

  // the proposal a mark led to is brought into view once the list is there
  const lit = useRef<HTMLLIElement>(null);
  const seen = useRef<number | null>(null);
  useEffect(() => {
    if (!focus || seen.current === focus || !lit.current) return;
    seen.current = focus;
    lit.current.scrollIntoView({ block: 'center', behavior: 'smooth' });
  });

  const decide = async (p: TidyProposalOut, what: TidyDecision) => {
    setBusy(p.id);
    try {
      const next = await api.tidyDecide(p.id, what);
      if (what === 'accept' || what === 'reject') dropMark(p.id);
      setRows((xs) => (xs ?? []).map((x) => (x.id === p.id ? next : x)));
    } catch (e) {
      // what changed since is gone from the list: the Hub said why
      toast(e instanceof Error ? e.message : 'Не получилось', 'error');
      if ((e as { status?: number }).status === 409) setRows((xs) => (xs ?? []).filter((x) => x.id !== p.id));
    }
    setBusy(null);
    onChange();
  };

  const acceptSure = async () => {
    setBusy('sure');
    const r = await attempt(() => api.tidyAcceptSure(section));
    setBusy(null);
    if (r) {
      toast(r.failed ? `Принято: ${r.accepted}. Не получилось: ${r.failed} — уже изменились.` : `Принято: ${r.accepted}`, 'success');
      res.reload(true);
      onChange();
    }
  };

  const shown = rows ?? res.data;
  const tracks = useTrackCards(shown);
  const waiting = (shown ?? []).filter((p) => p.state === 'open' && p.sure).length;
  return (
    <div className="max-w-4xl">
      <div className="flex flex-wrap items-center gap-3 px-4 pt-3 lg:px-6">
        <p className="min-w-0 flex-1 text-[13px] leading-snug text-muted max-sm:basis-full">{ABOUT[section]}</p>
        {sure > 0 && waiting > 0 && (
          <Button small variant="primary" icon="check" busy={busy === 'sure'} onClick={acceptSure} className="whitespace-nowrap max-sm:w-full">
            Принять уверенные · {waiting}
          </Button>
        )}
      </div>
      {!shown ? (
        res.error ? <ErrorState text={res.error} onRetry={() => res.reload()} /> : <SkeletonRows n={5} />
      ) : shown.length === 0 ? (
        <Empty icon="check" title="Здесь всё в порядке" text={checked ? `Хаб проверил библиотеку ${fmtWhen(checked)} и ничего не нашёл.` : 'Хаб проверяет библиотеку каждую ночь.'} />
      ) : (
        <ul className="mt-3 space-y-2 px-2 lg:px-4">
          {shown.map((p) => (
            <li key={p.id} ref={p.id === focus ? lit : undefined}>
              <ProposalCard p={p} lit={p.id === focus} busy={busy === p.id} onDecide={(what) => decide(p, what)}
                track={p.subject_type === 'track' ? tracks.get(p.subject_id) : undefined} pair={pairOf(p) ? tracks.get(pairOf(p) as number) : undefined} />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Where the subject leads: a track and an album — to their album, an artist — to their page. */
function subjectLink(p: TidyProposalOut): string | null {
  if (p.subject_type === 'artist') return artistPath({ ref: '', hub_artist_id: p.show.artist_id ?? p.subject_id });
  return p.show.album_id ? albumPath({ ref: `lib:${p.show.album_id}` }) : null;
}

const WHAT: Record<TidyProposalOut['subject_type'], string> = { track: 'Трек', album: 'Альбом', artist: 'Исполнитель' };

/** The subject as the proposal names it: its picture, what it is and whose; then «было → станет» in the kind's words. */
export function ProposalCard({ p, busy, onDecide, journal, lit, track, pair }: {
  p: TidyProposalOut; busy: boolean; onDecide: (what: TidyDecision) => void; journal?: boolean;
  /** the one a mark on a page led to */
  lit?: boolean;
  /** the track it is about, as every list shows a track (`useTrackCards`): its row heads the card */
  track?: TrackOut;
  /** the other track (`pairOf`): a double's copy that stays — under the one that goes — or a version's original —
   * above the version; each row plays, to be compared */
  pair?: TrackOut;
}) {
  const to = subjectLink(p);
  const head = [WHAT[p.subject_type], p.show.line].filter(Boolean).join(' · ');
  const decided = p.state !== 'open';
  const picture = p.subject_type === 'artist'
    ? <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-raised text-lg font-bold uppercase text-muted">{(p.show.name || p.was).slice(0, 1)}</span>
    : <Cover src={p.show.cover} size={48} />;

  let body: ReactNode;
  if (p.kind === 'lyrics') {
    body = (
      <>
        <span className="block truncate text-[15px] font-semibold">{p.show.title_now}</span>
        <span className="mt-1 block space-y-0.5">
          {(p.show.lines ?? []).map((line, i) => (
            <span key={i} className="block truncate text-[13px] text-muted line-through decoration-danger/70">{line}</span>
          ))}
        </span>
        <span className="mt-1 block text-[13px] font-semibold">{p.to}</span>
      </>
    );
  } else if (p.kind === 'uncensor') {
    body = (
      <>
        <span className="block truncate text-[15px] font-semibold">{p.show.title_now}</span>
        <span className="mt-1 block space-y-1">
          {(p.show.pairs ?? []).map((x, i) => (
            <span key={i} className="block">
              <span className="block break-words text-[13px] text-muted line-through decoration-danger/70">{x.was}</span>
              <span className="block break-words text-[13px] font-semibold"><span className="text-dim">→ </span>{x.to}</span>
            </span>
          ))}
        </span>
      </>
    );
  } else if (p.kind === 'double') {
    body = (
      <>
        <span className="block truncate text-[15px] font-semibold">{p.show.title_now}</span>
        <span className="mt-0.5 line-clamp-2 block break-words text-[13px] text-muted line-through decoration-danger/70">Удалить копию: {p.was}</span>
        <span className="line-clamp-2 block break-words text-[13px] font-semibold">Оставить: {p.to}</span>
      </>
    );
  } else {
    body = (
      <>
        <span className="line-clamp-2 block break-words text-[14px] text-muted line-through decoration-danger/70">{p.was}</span>
        <span className="line-clamp-2 block break-words text-[15px] font-semibold"><span className="text-dim">→ </span>{p.to}</span>
        {p.kind === 'artist' && p.show.into_line && <span className="block text-[12px] text-dim">{p.show.line} → {p.show.into_line}</span>}
      </>
    );
  }

  // what changes, without the track's name: under the track's own row
  const change: ReactNode = p.kind === 'lyrics' ? (
    <>
      {(p.show.lines ?? []).map((line, i) => <s key={i} className="block truncate text-[13px] text-muted decoration-danger/70">{line}</s>)}
      <span className="mt-0.5 block text-[13px] font-semibold">{p.to}</span>
    </>
  ) : p.kind === 'uncensor' ? (
    (p.show.pairs ?? []).map((x, i) => (
      <span key={i} className="block">
        <s className="block break-words text-[13px] text-muted decoration-danger/70">{x.was}</s>
        <span className="block break-words text-[13px] font-semibold"><span className="text-dim">→ </span>{x.to}</span>
      </span>
    ))
  ) : p.kind === 'double' ? (
    <>
      <span className="line-clamp-2 block break-words text-[13px] text-muted line-through decoration-danger/70">Удалить копию: {p.was}</span>
      <span className="line-clamp-2 block break-words text-[13px] font-semibold">Оставить: {p.to}</span>
    </>
  ) : (
    <>
      <span className="line-clamp-2 block break-words text-[14px] text-muted line-through decoration-danger/70">{p.was}</span>
      <span className="line-clamp-2 block break-words text-[15px] font-semibold"><span className="text-dim">→ </span>{p.to}</span>
    </>
  );

  const stateWord = p.state === 'accepted' ? 'Принято' : p.state === 'rejected' ? 'Отклонено' : p.state === 'reverted' ? 'Откачено' : journal ? 'Снова на рассмотрении' : '';
  const actions = journal ? (
    p.state !== 'open' && (
      <span className="flex flex-wrap gap-2 max-sm:w-full">
        {p.state === 'accepted' && (
          <Button small variant="outline" disabled={busy} onClick={() => onDecide('revert')} className="whitespace-nowrap max-sm:flex-1">Откатить</Button>
        )}
        <Button small variant="outline" busy={busy} onClick={() => onDecide('review')} className="whitespace-nowrap max-sm:flex-1"
          title={p.state === 'accepted' ? 'Откатить и снова показать в «Наведении порядка»' : 'Снова показать в «Наведении порядка»'}>
          Вернуть на рассмотрение
        </Button>
      </span>
    )
  ) : decided ? (
    <span className="flex items-center gap-2 text-[13px] text-muted">
      {stateWord}
      <button type="button" disabled={busy} onClick={() => onDecide(p.state === 'accepted' ? 'revert' : 'reopen')} className="min-h-9 px-2 underline hover:text-fg disabled:opacity-50">
        Отменить
      </button>
    </span>
  ) : (
    <span className="flex gap-2 max-sm:w-full">
      <Button small variant="outline" disabled={busy} onClick={() => onDecide('reject')} className="max-sm:flex-1">Отклонить</Button>
      <Button small variant="primary" busy={busy} onClick={() => onDecide('accept')} className="max-sm:flex-1">Принять</Button>
    </span>
  );

  if (p.kind === 'artist' && p.show.into_id) {
    // two artists that are one: the two photos overlapping, their names and tracks, an arrow, the one they become
    const count = (line?: string | null) => Number(/\d+/.exec(line ?? '')?.[0] ?? 0);
    const total = count(p.show.line) + count(p.show.into_line);
    const ring = 'rounded-full ring-[3px] ring-surface';
    const other = artistPath({ ref: '', hub_artist_id: p.show.artist_id ?? p.subject_id });
    const into = artistPath({ ref: '', hub_artist_id: p.show.into_id });
    return (
      <div className={cx('rounded-xl bg-raised/60 p-4 transition sm:p-5', lit && 'bg-ai/10 ring-2 ring-ai', decided && !journal && 'opacity-60')}>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:gap-6">
          <div className="flex min-w-0 flex-1 items-center gap-4">
            <span className="relative h-14 w-[92px] shrink-0">
              <Cover src={p.show.picture} size={56} round icon="user" className={cx('absolute left-0 top-0', ring)} />
              <Cover src={p.show.into_picture} size={56} round icon="user" className={cx('absolute left-9 top-0', ring)} />
            </span>
            <span className="flex min-w-0 flex-col gap-1">
              <Link to={other} className="flex min-w-0 items-baseline gap-2 text-[15px] font-semibold hover:underline">
                <span className="truncate">{p.show.name || p.was}</span>
                <span className="shrink-0 text-[12px] font-medium text-muted">{count(p.show.line)}</span>
              </Link>
              <Link to={into} className="flex min-w-0 items-baseline gap-2 text-[15px] font-semibold hover:underline">
                <span className="truncate">{p.show.into_name || p.to}</span>
                <span className="shrink-0 text-[12px] font-medium text-muted">{count(p.show.into_line)}</span>
              </Link>
            </span>
          </div>
          <Icon name="chevron" size={28} className="shrink-0 self-center text-ai max-sm:rotate-90" />
          <Link to={into} className="flex min-w-0 flex-1 items-center gap-4">
            <Cover src={p.show.into_picture} size={64} round icon="user" className="shrink-0 rounded-full ring-2 ring-ai ring-offset-[3px] ring-offset-surface" />
            <span className="flex min-w-0 flex-col gap-1">
              <span className="truncate text-lg font-bold">{p.show.into_name || p.to}</span>
              {total > 0 && <span className="text-[13px] font-semibold text-ai">{total} {plural(total, ['трек', 'трека', 'треков'])}</span>}
            </span>
          </Link>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <span className="flex min-w-0 flex-1 basis-40 flex-col gap-1">
            <span title={p.why || undefined} className="w-fit"><ByMark p={p} /></span>
            {journal && (
              <span className="text-[12px] text-dim">
                {stateWord}{p.decided_by ? ` · ${p.decided_by}` : ''}{p.decided_at ? ` · ${fmtWhen(p.decided_at)}` : ''}
              </span>
            )}
          </span>
          {actions && <div className="shrink-0 max-sm:w-full">{actions}</div>}
        </div>
      </div>
    );
  }

  if (track && pair) {
    // both tracks as rows: a double's copy that goes and the one that stays, a version's original and the version
    const label = (text: string, more: string | undefined, tone: string) => (
      <p className={cx('px-3 pt-2 text-[12px] font-semibold sm:px-4', tone)}>{text}{more ? <span className="font-normal text-muted"> · {more}</span> : null}</p>
    );
    const version = p.kind === 'version';
    return (
      <div className={cx('rounded-xl bg-raised/60 pb-2 pt-1 transition', lit && 'bg-ai/10 ring-2 ring-ai', decided && !journal && 'opacity-60')}>
        {version ? (
          <>
            {label('Оригинал', undefined, 'text-muted')}
            <TrackRow track={pair} showAlbum />
            {label(p.state === 'accepted' ? 'Его версия' : 'Похоже на его версию', p.show.label ? `«${p.show.label}»` : undefined, 'text-ai')}
            <TrackRow track={track} showAlbum />
          </>
        ) : (
          <>
            {label(p.state === 'accepted' ? 'Удалена копия' : 'Удалить копию', p.show.file, 'text-danger')}
            <TrackRow track={track} showAlbum />
            {label('Оставить', p.show.keep_file, 'text-accent')}
            <TrackRow track={pair} showAlbum />
          </>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-3 px-3 sm:px-4">
          <div className="min-w-0 flex-1 basis-60">
            <span className="flex min-w-0 items-center gap-2">
              <ByMark p={p} />
              {p.why && <span className={cx('truncate text-[12px]', p.by === 'ai' && !p.sure ? 'text-warn' : 'text-dim')}>{p.why}</span>}
            </span>
            {journal && (
              <span className="mt-1 block text-[12px] text-dim">
                {stateWord}{p.decided_by ? ` · ${p.decided_by}` : ''}{p.decided_at ? ` · ${fmtWhen(p.decided_at)}` : ''}
              </span>
            )}
          </div>
          {actions && <div className="shrink-0 max-sm:w-full">{actions}</div>}
        </div>
      </div>
    );
  }

  if (track) {
    // the track's own row, as in a playlist or an album; under it what changes
    const text = p.kind === 'lyrics' || p.kind === 'uncensor';
    return (
      <div className={cx('rounded-xl bg-raised/60 pb-2 pt-1 transition', lit && 'bg-ai/10 ring-2 ring-ai', decided && !journal && 'opacity-60')}>
        <TrackRow track={track} showAlbum />
        <div className="mt-2 px-2 sm:px-3">
          {text && !journal ? (
            <LyricsExcerpt p={p} track={track} actions={actions} />
          ) : (
            <div className="flex flex-wrap items-center gap-3 rounded-xl bg-raised px-3 py-2.5 sm:px-4">
              <div className="min-w-0 flex-1 basis-60">
                {change}
                <span className="mt-1 flex min-w-0 items-center gap-2">
                  <ByMark p={p} />
                  {p.why && <span className={cx('truncate text-[12px]', p.by === 'ai' && !p.sure ? 'text-warn' : 'text-dim')}>{p.why}</span>}
                </span>
                {journal && (
                  <span className="mt-1 block text-[12px] text-dim">
                    {stateWord}{p.decided_by ? ` · ${p.decided_by}` : ''}{p.decided_at ? ` · ${fmtWhen(p.decided_at)}` : ''}
                  </span>
                )}
              </div>
              {actions && <div className="shrink-0 max-sm:w-full">{actions}</div>}
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={cx('flex items-start gap-3 rounded-xl bg-raised/60 p-3 transition max-sm:flex-wrap sm:items-center', lit && 'bg-ai/10 ring-2 ring-ai', decided && !journal && 'opacity-60')}>
      {to ? <Link to={to} className="shrink-0">{picture}</Link> : picture}
      <div className="min-w-0 flex-1">
        <span className="block truncate text-[12px] text-dim">{head}</span>
        {body}
        <span className="mt-1 flex min-w-0 items-center gap-2">
          <ByMark p={p} />
          {p.why && <span className={cx('truncate text-[12px]', p.by === 'ai' && !p.sure ? 'text-warn' : 'text-dim')}>{p.why}</span>}
        </span>
        {journal && (
          <span className="mt-1 block text-[12px] text-dim">
            {stateWord}{p.decided_by ? ` · ${p.decided_by}` : ''}{p.decided_at ? ` · ${fmtWhen(p.decided_at)}` : ''}
          </span>
        )}
      </div>
      {actions && <div className="shrink-0 max-sm:w-full max-sm:pl-[60px]">{actions}</div>}
    </div>
  );
}

const PAGE = 50;

/** «Журнал» of «Наведение порядка»: everything decided, the latest first — an accepted one with «Откатить», and each
 * with «Вернуть на рассмотрение» (the proposal back in its section's list, an accepted one undone first). */
export function TidyJournalPage() {
  const first = useAsync((s) => api.tidyJournal(undefined, s), []);
  const [rows, setRows] = useState<TidyProposalOut[] | null>(null);
  const [more, setMore] = useState(true);
  const [busy, setBusy] = useState<number | 'more' | null>(null);
  const tracks = useTrackCards(rows);
  useEffect(() => {
    if (first.data) {
      setRows(first.data);
      setMore(first.data.length >= PAGE);
    }
  }, [first.data]);

  const decide = async (p: TidyProposalOut, what: TidyDecision) => {
    setBusy(p.id);
    const next = await attempt(() => api.tidyDecide(p.id, what), what === 'review' ? 'Снова в «Наведении порядка»' : 'Откачено');
    setBusy(null);
    if (next) setRows((xs) => (xs ?? []).map((x) => (x.id === p.id ? next : x)));
  };

  const loadMore = async () => {
    const last = rows?.[rows.length - 1];
    if (!last) return;
    setBusy('more');
    const next = await attempt(() => api.tidyJournal(last.id));
    setBusy(null);
    if (next) {
      setRows((xs) => [...(xs ?? []), ...next]);
      setMore(next.length >= PAGE);
    }
  };

  return (
    <div className="pb-10">
      <PageHeader title="Журнал" back="/tidy" sub="Наведение порядка" />
      {!rows ? (
        first.error ? <ErrorState text={first.error} onRetry={() => first.reload()} /> : <SkeletonRows n={6} />
      ) : rows.length === 0 ? (
        <Empty icon="history" title="Пока ничего не решено" text="Здесь появится всё, что примут или отклонят в «Наведении порядка»." />
      ) : (
        <div className="max-w-4xl">
          <p className="px-4 text-[13px] text-muted lg:px-6">{rows.length} {plural(rows.length, ['изменение', 'изменения', 'изменений'])}{more ? ' и раньше' : ''}</p>
          <ul className="mt-3 space-y-2 px-2 lg:px-4">
            {rows.map((p) => (
              <li key={p.id}><ProposalCard p={p} journal busy={busy === p.id} onDecide={(what) => void decide(p, what)}
                track={p.subject_type === 'track' ? tracks.get(p.subject_id) : undefined} pair={pairOf(p) ? tracks.get(pairOf(p) as number) : undefined} /></li>
            ))}
          </ul>
          {more && (
            <div className="px-4 pt-4 lg:px-6">
              <Button small busy={busy === 'more'} onClick={loadMore}>Показать ещё</Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
