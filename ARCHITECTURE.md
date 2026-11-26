# How the Hub is put together

A map for whoever changes the code: the ideas the code is built on, the ways the main flows go, and why they go so.
Not a list of tables or modules — those are in the code (`hub/app/models.py`, the docstrings of each module). A
change to something described here changes this file in the same commit.

## The parts

- **Hub** (`hub/`, FastAPI + PostgreSQL) — everything the user does: the library, playlists, search, listening,
  downloads, transfers, mixes, the admin's pages. It also answers as a Subsonic server (`/rest`).
- **Web app** (`web/`, React PWA) — built into the Hub's image and served by it.
- **Navidrome** — only a player of files: it scans the library folder and streams. It owns nothing the Hub cares
  about; the Hub mirrors to it what third-party Subsonic apps should see (playlists, ♥ as stars).
- **Caddy** (`gateway/direct/`) — HTTPS on the home's public address, and the audio itself (below).
- **Embedder** — the sound embeddings mixes are built of.

## A track is not a file

| | What it is |
|---|---|
| **Catalog** | The outside world — Spotify, Deezer, Yandex Music, the sources of files. Read only, kept in `catalog_cache`. |
| **Track** | A song the Hub knows: names, length, ISRC, its catalog ref. Exists without any file. |
| **File** | A file on disk that plays a track (`track_files`). A track has none, one or several; the newest plays. |

A catalog track becomes a Track the moment it matters to someone: put into a playlist (a ♥ too: «Любимое» is a
playlist), tagged, downloaded, listened to for a counted listen. A search result nobody touched stays a catalog
object. So a playlist, a tag, a ♥, lyrics hold songs with no file yet; «downloaded» is only «the track has a file».

The web names a track by the Hub's `id` where the Hub knows it, else by its catalog `ref` (`dz:…`, `sp:…`, `ym:…`);
every action takes either and makes the Track first. Navidrome's ids stay on the server. An artist is the Hub's
`artists.id` with several records in the catalogs (`artist_refs`); a release opened is a `catalog_releases.id`.

The mirror to Navidrome reads back before it writes: a song added or removed in another app is merged into the
Hub's playlist, then the Hub's order is written. The Hub wins a conflict, but nothing done elsewhere is lost.

## Two processes

`docker-compose.yml` runs one image twice (`HUB_ROLE`): **hub** («api») answers — the API, the event stream, the
audio; **hub-worker** («worker») does the background — the job queue (downloads, ffmpeg, fpcalc, yt-dlp), mixes,
the scheduled loops, the warming of artist pages. Busy as the worker is, an answer never waits for it. Between them
— PostgreSQL's LISTEN/NOTIFY (`app/relay.py`): events published by the worker reach the apps through the api; a job
queued by the api wakes the worker; «somebody is listening» makes the worker's background give way; the proxies
changed. Development and tests run one process doing both (`HUB_ROLE=all`): the relay is then not used at all.

Why: a transfer of thousands of tracks once took the Hub from its listeners — a track did not start, the apps lost
the connection. Also against that: the job queue gives a user's own requests priority over background jobs, the
next job is of whoever has the fewest running, background subprocesses run `nice`d (`probe.yielding`), and only
two background slots work while somebody listens (`app/activity.py`). A loop held up for a second or more is logged
with its stack (`app/watchdog.py`): the first thing to look at when the Hub «freezes».

## Listening

`GET /api/listen?ref=` picks what plays (`app/listen.py`): the library's file; else a file of the sources, found as
a download would find it and streamed through the Hub while it is fetched; the pick is remembered, and the file is
kept on the server 30 days / 30 GB (`stream.json` next to it, so the other process and a restart find it). The
audio is sent by **Caddy**, not by Python: the Hub checks who asks and answers with the file's place only
(`X-Accel-Redirect`, `accel_path`); Caddy reads it from the folders mounted under `/srv/_files` with the ranges a
seek asks for. A browser that does not play a file's format gets a copy (`light.py`): Safari gets Opus put into MP4
as it is (iOS 17+), else AAC — Safari is never trusted with Ogg.

On the device (`web/src/lib/offline.ts`, `heard.ts`, `public/sw.js`): lists kept for offline (`hm-audio-v1`, kept
up to date by themselves, as subscriptions) and what was heard lately (`hm-heard-v1`, LRU within a size the user
sets). The service worker answers the stream address from them; a copy of another file than the track's now
(`file_uid`), or in a format this browser does not play, is fetched anew.

## Where a file comes from

The sources are asked one after another (`Services.provider_order`): **Yandex Music** first — the official file,
FLAC or MP3 320, through a connected Yandex account with Plus (`app/providers/yandex.py`; a track of Yandex by its
id, any other by its names; without such an account it finds nothing) — then the Russian order (hitmoz, hitmos.fm,
SoundCloud, re-uploads, YouTube) or the default one (hitmoz, YouTube). In every source the files are ranked by the
names and the length (`app/matching/select.py`); a file is checked after the download (its length, silences in the
middle — a censor's cut).

A track is Russian by Cyrillic in its names, its Cyrillic original on Yandex (Deezer gives Russian names
transliterated), a CIS barcode or ISRC, or its artist (`app/matching/russian.py`: two titles in Cyrillic among what
the Hub knows of them — a count, not a share: Russian artists named in Latin have from 7 % of such titles). An
explicit track of Russia — not of Ukraine, Belarus, Kazakhstan: the 2026 law is Russia's — takes first the copies
known to be uncensored: uploaded before the law, or re-uploads said «без цензуры». Yandex's and YouTube Music's own
copies never count as such: their audio is replaced in place, with the same id and date. A possibly censored copy
is taken only when there is none, and says so.

## Artist pages

Put together of what the catalogs say — seconds of their answers — so kept, the same for everyone
(`app/artist_pages.py`, `catalog_cache` key `artistpage:<artist>`); what is of a listener and the Hub's own choices
(♥, the library's copies, the photo chosen) are put on it when shown, from the database only. An opening shows the
kept page at once; one older than 6 h is put together again behind it and the apps take it quietly (`artist`
event). A first page does not wait for the search of the artist's records in the other catalogs: the warming does
that right after. The worker warms pages one artist after another, slowly — the artists of what plays now and next
first (the web player asks), then the library's — far below the catalogs' limits, longer when a catalog fails.

## Outside requests

Every request to an outside service goes through `app/outbound.py`: through the proxy of the account it is made
with, else of its service, else direct (set on the admin's page «Прокси и запросы»), and into the journal
(`outbound_requests`, 14 days): service, path with the names of the query (never the values — tokens travel there),
what it was for (the job, the API route, the loop), account or key, proxy, status or error, time. The ways out:
httpx (`app.net.make_client` — every new client goes through it, or through `RoutingTransport`; Yandex Music is the
Hub's own client on it, `app/catalog/ym.py`), `requests` (yt-dlp) and `wreq` (spotapi, the Spotify catalog — given
its proxy when its session opens) by hooks, the Spotify session (another process) and Telegram by their proxy setting.
«The requests that follow are an account's» is set when its secret is read (`integrations.credential`).

## The app and the Hub talking

Events go over one SSE stream (`/api/events`, `app/events.py`); a page reads a list again on them, at most once per
burst (`useCoalesced`). A JSON GET carries an ETag, and a list asked again unchanged is a bare 304. A page shows
what it showed last time at once and the fresh answer after (`useAsync` with a key, kept on the device too). The web
app reloads itself when the Hub serves a newer build (`lib/update.ts`).

## Versions

`VERSION` (CalVer: `2026.11.2` — the second release of November 2026) and `CHANGELOG.md` at the root; the Hub reads
both (`app/version.py`) and the app shows them («Меню» → «Что нового», the admin's «О сервере»). What a listener or
the admin would notice goes under «Не выпущено» in words for them; a push is a release: the section gets the next
number and a date, `VERSION` follows, a tag `v<version>`.

## Easy to get wrong

- `main._import_handlers` imports modules only for their side effects (job handlers); `ruff --fix` of F401 strips
  them — fix imports with `--select I001` or by hand.
- A new outside HTTP client made with `httpx.AsyncClient(...)` directly bypasses the proxies and the journal.
- A long computation or a blocking call on the event loop holds up every request: `asyncio.to_thread`, or the
  worker.
- `web/tsconfig.tsbuildinfo` changes with every type check: not committed.
- The server updates itself from `origin/master` (`scripts/update-server.ps1`): a push deploys.
