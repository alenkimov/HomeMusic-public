import { useEffect, useState } from 'react';
import { Button, Empty, PageHeader, Spinner } from '../components/ui';
import { enc, navigate } from '../lib/router';
import { spotifyURL } from '../lib/spotify';
import { clearShare, readShare } from '../lib/shareStore';
import { isAudioFile, queueFilesForUpload } from './Upload';

type Phase = { kind: 'working'; text: string } | { kind: 'empty' } | { kind: 'error'; text: string };

// Guards against double processing (StrictMode effects, re-renders).
let running: Promise<void> | null = null;

/**
 * Android "Share" → HomeMusic. The service worker stored the shared payload in IndexedDB and redirected here:
 * a Spotify link opens as one, audio goes to the upload form, other text or a link becomes a search.
 */
export function SharePage() {
  const [phase, setPhase] = useState<Phase>({ kind: 'working', text: 'Открываю то, чем вы поделились…' });

  useEffect(() => {
    if (running) return;
    running = (async () => {
      let payload;
      try {
        payload = await readShare();
      } catch {
        payload = null;
      }
      if (!payload) {
        setPhase({ kind: 'empty' });
        return;
      }
      await clearShare().catch(() => undefined);

      const images = payload.files.filter((f) => f.type.startsWith('image/'));
      const audio = payload.files.filter((f) => !f.type.startsWith('image/') && isAudioFile(f));
      const text = [payload.title, payload.text, payload.url].map((s) => (s ?? '').trim()).filter(Boolean).join(' ');
      const spotify = spotifyURL(payload.url || '') || spotifyURL(text);
      if (!images.length && !audio.length && spotify) {
        navigate(`/open?url=${enc(spotify)}`, { replace: true });
        return;
      }

      if (audio.length) {
        queueFilesForUpload(audio);
        navigate('/upload', { replace: true });
        return;
      }
      if (text && !images.length) {
        navigate(`/search?q=${enc(text)}`, { replace: true });
        return;
      }
      setPhase({ kind: 'error', text: images.length ? 'Скриншоты не поддерживаются. Поделитесь аудиофайлом, ссылкой или названием трека.' : 'Этот тип файлов не поддерживается. Поделитесь аудиофайлом, ссылкой или названием трека.' });
    })().finally(() => {
      running = null;
    });
  }, []);

  return (
    <div>
      <PageHeader title="Поделиться" back="/search" />
      {phase.kind === 'working' ? (
        <div className="flex flex-col items-center gap-4 px-8 py-16 text-center">
          <Spinner size={32} className="text-accent" />
          <p className="text-muted">{phase.text}</p>
        </div>
      ) : phase.kind === 'empty' ? (
        <Empty icon="search" title="Нечего обрабатывать" text="Поделитесь аудиофайлом, ссылкой или названием трека из другого приложения и выберите HomeMusic.">
          <Button small onClick={() => navigate('/search', { replace: true })}>
            К поиску
          </Button>
        </Empty>
      ) : (
        <Empty icon="alert" title="Не получилось" text={phase.text}>
          <Button small onClick={() => navigate('/search', { replace: true })}>
            К поиску
          </Button>
        </Empty>
      )}
    </div>
  );
}
