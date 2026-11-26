# HomeMusic 2.0 — how the Hub is put together

This is the model the code is being moved to. Where the code still differs, the code is behind, not the document.
Backward compatibility is not a goal: the database and the library may be wiped at the cut-over.

## Why

In 1.x a *track* was a *file*: a row of `tracks` appeared when a file landed in the library. Everything that was
not downloaded lived as something else, each with its own way to name the same song — a row of an import with a
catalog ref, a favorite with «aliases», a «add to the playlist when it arrives», a play event with a ref *or* a
Navidrome id. Tags and lyrics hang on `tracks.id`, so a song without a file could have neither. Playlists lived in
Navidrome, which knows only files, so the songs without one were appended after the rest and lost their places.

## Three layers

| Layer | What it is | Who owns it |
|---|---|---|
| **Catalog** | The outside world: Deezer, YouTube Music, the providers of files. Read-only; the Hub keeps a cache. | nobody here |
| **Track** | A song the Hub knows: names, length, ISRC. Independent of any file. | the Hub |
| **File** | A file on disk that plays a track: path, codec, where it came from, its id in Navidrome. | the Hub |

A catalog track becomes a **Track** the moment it matters to someone: it is put into a playlist (so also liked),
tagged, downloaded, or listened to for a counted listen (30 s and longer). A search result nobody touched stays a
catalog object and has no row.

A Track has zero, one or several Files. «Downloaded» is not a separate world, it is `track has a file`. The file
that plays is the newest one.

Navidrome is a *player of files*: it scans the library folder, streams, and serves the Subsonic API to third-party
apps. It owns nothing the Hub cares about. The Hub mirrors to it what such apps should see (below).

## Tables

**`tracks`** — the song. `id`, `ref` (what the catalog calls it, `dz:3135556`; one track per ref, none if the
catalog does not know the song), `artist`, `title`, `album`, `album_artist`, `track_no`, `disc_no`, `year`,
`genre`, `duration_s`, `isrc`, `explicit`, `fingerprint` (normalised «artist|title»), `album_id` (the library
album its file is filed under, if any), `catalog` (what the catalog said of it when the track was made — its
cover, its album and artists there: a track without a file is drawn from this), `created_at`, `created_by`.
Nothing about files.

**`track_files`** — a file of a track. `id`, `track_id`, `path` (unique, relative to `library/`), `uid` (written
into the file as `HOMEMUSIC_TRACK_ID`), `nd_song_id` (the song Navidrome made of it; set after a scan), `codec`,
`bitrate_kbps`, `source`, `source_url`, `source_upload_date`, `locked`, `warnings`, `acquired_at`, `added_by`.

**`track_links`** — what the track is called on music services (`spotify`, `yandex`, `ytm`): `track_id`,
`service`, `external_id`. Share links are made of them, and an import knows by them a song it has seen.
The catalog ref is not one of these: it is the track's own name (`tracks.ref`), the links are its names abroad.

**`playlists`** — `id`, `user_id`, `kind` (`liked` — «Любимое», one per user; `user` — made by hand or by an
import; `mix` — «Микс дня», which the mix builder writes in Navidrome and the Hub's row only follows), `name`,
`created_at`, `updated_at`, and what the mirror needs: `nd_playlist_id`, `mirrored` (the song ids Navidrome was
last given), `nd_cover` (the picture Navidrome makes of the mirror's albums).

**`playlist_items`** — `playlist_id`, `track_id`, `position`, `added_at`. The order and the dates are the Hub's.
A track without a file stands in its own place like any other.

**Tags, lyrics, translations** — as before, by `track_id`; now any track can have them.

**Listening** — `play_events.track_id`. An event names a Track; the raw catalog ref it came with is kept only as
`origin` for analytics. Dislikes name a `track_id` or an artist.

**Imports** — an import item, once matched, names a `track_id` and its tracks go into playlists at once. There is
no «pending» list: whether a track has a file yet is seen on the track.

**Downloads** — a download item names the `track_id` it is getting a file for; done, it adds a `track_files` row.

## What a track is called in the API

The web addresses a track by **`id`** (the Hub's) when the Hub knows it, else by its catalog **`ref`**. Every
action that takes a track (put into a playlist, tag, play, download) accepts either; given a ref it makes the
Track first. Navidrome ids never leave the server.

Artist pages use `artists.id`. Catalog profile references are kept in `artist_refs` and can still open the same
page. Each release opened in the Hub has a `catalog_releases.id` independent of the current source or file
availability. `catalog_release_refs` links confirmed catalog records to that release. A release may point to the
library's album, whose UUID stays in the audio tags used by Navidrome. In ordinary search, unopened results
keep their external references until opened.

## «Любимое»

A playlist of `kind = liked`. Liking is adding to it, the ♥ of a track is «is in it». A like and an unlike are
also written to `play_events` — the taste profile reads them as signals — but the *state* is the playlist.
Albums and artists are not tracks: «в коллекцию» and following an artist stay typed favorites of their own.

## The mirror to Navidrome

For third-party Subsonic apps. One job per playlist: the tracks of the playlist that have a file, in the Hub's
order, written to the Navidrome playlist named in `nd_playlist_id`; for «Любимое» — as stars.

Coming back: before it writes, the job reads what Navidrome has now and compares it with `mirrored`. A song that
appeared there was added in another app — it is added to the Hub's playlist; one that disappeared is removed. Then
the Hub's state is written. The Hub wins a conflict; an edit made elsewhere is never lost silently, it is merged.

## Gone with 1.x

`pending_playlist_adds`; the «pending» lists of playlists; `favorite_subjects` for tracks and the alias index;
«после переноса» as a deferred order; `playlist_dates`; `find_existing` answering two questions at once (it
becomes `tracks.find` — «does the Hub know this song» — and `has a file`); `nd_song_id` and `ref` side by side in
events, dislikes and the items of mixes.

## Processes

In `docker-compose.yml` the Hub is two containers of one image (`HUB_ROLE`): **hub** («api») answers the apps —
the API, the event streams, the audio; **hub-worker** («worker») does the background work — the job queue
(downloads, a transfer's thousands of them, ffmpeg and fpcalc, yt-dlp), mixes and the scheduled loops. However busy
the worker is, the answers do not wait for it. What passes between them goes through PostgreSQL's LISTEN/NOTIFY
(`app/relay.py`): the events the worker publishes, reaching the apps through the api; a job the api queues, waking
the worker; somebody listening, which makes the worker's background jobs give way; a pick the api must forget. The
working files are on the Linux disk of the hub-data volume (`STAGING_ROOT`), the library on MUSIC_ROOT. Development
and tests run one process doing both (`HUB_ROLE=all`).

Of the downloads waiting at one priority, the next is of whoever has the fewest running now: one person's transfer
does not hold every hand.

## Artist pages

An artist's page is put together of what the catalogs say — some seconds of their answers. It is kept, the same for
everyone (`app/artist_pages.py`, `catalog_cache` key `artistpage:<artist>`); what is of a listener (♥, the library's
copies) and the Hub's own choices (photo, wide picture) are put on it when shown, from the database only. An opening
shows the kept page at once and puts an old one (6 h) together again behind it — the apps get an `artist` event. The
worker keeps the pages warm, one artist after another: first what is asked (the web player asks for the artists of
what plays now and next; a first or old page), then the library's artists whose page is missing or older than 3 days,
one every 15 s (30 s while somebody listens, longer each time a catalog fails). Each put-together is in the log with
its steps and the slowest catalogs (`artist 12 (Кино): 2.4 s — records 1.6 s, …; slowest: Spotify 1.5 s, …`).

## Outside requests

Every request of the Hub to an outside service goes through `app/outbound.py`: through the proxy of the account it is
made with (`integration_connections.proxy_id`), else of its service (`service_proxies`), else direct — and into the
journal (`outbound_requests`, 14 days): the service, the path with the names of its query (never the values), what
it was for (the job, the API route, the loop), the account or key, the proxy, the status or the error, the time.
httpx clients (`app.net.make_client`) by their transport, `requests` (the Yandex Music client, yt-dlp) by a hook,
the Spotify session and Telegram by their proxy setting. The admin page «Прокси и запросы» sets the proxies and
reads the journal: by service, proxy and purpose, how many requests failed or hit a limit (429), how long they took.

## Where a file comes from

A track to download or to listen to is looked for in the sources one after another (`Services.provider_order`):
Yandex Music first — the official file, FLAC or MP3 320, through a Yandex account with Plus connected in
«Интеграции» (`app/providers/yandex.py`; a track of Yandex taken by its id, any other by its names; without such an
account it finds nothing) — then the Russian order (hitmoz, hitmos.fm, SoundCloud, re-uploads, YouTube) or the
default one (hitmoz, YouTube). A track is Russian by its names in Cyrillic, its Cyrillic original on Yandex, a CIS
barcode or ISRC, or its artist (`app/matching/russian.py`: two titles in Cyrillic among what the Hub knows of them, or
an ISRC of Russia). An explicit track of Russia (not one of Ukraine, Belarus, Kazakhstan) takes first the copies known
to be uncensored — uploaded before the 2026 law, or re-uploads said «без цензуры»; Yandex's and YouTube Music's own
copies never count as such, their audio is replaced in place — and a possibly censored copy only when there is none.

## Modules

- `app/library/tracks.py` — the registry: find or make a Track by a catalog ref or by names; its links; its files.
- `app/library/importer.py` — puts a file into the library and attaches it to a Track.
- `app/playlists.py` — playlists and their items; «Любимое»; the mirror job.
- `app/api/*` — speak `id`/`ref` only.
- `app/catalog/*`, `app/providers/*` — the outside world; unchanged in shape.

## Order of work

One release, built in passes that each leave the tests green:

1. `track_files` split off `tracks`; a track named by its catalog ref. *(done)*
2. Playlists in the Hub, the mirror, tags on any track; «Любимое» shown as a playlist made of the journal of
   likes. *(done; the journal still decides, and `favorite_subjects` is still there)*
3. Events on `track_id`. *(done: an event names its track for good — `plays.attach_tracks` — and the statistics
   and the journal of likes go by it. Not done, on purpose: taste, mixes, the wave and their analytics keep their
   own keys — they reason about catalog tracks, most of which the Hub never keeps, so those are not Tracks and
   have no `track_id` to go by; the same holds for a dislike of a catalog track)*
4. Imports and downloads on top of it. *(done: a download knows its track before there is a file and puts it into
   its playlists at once; a ♥ knows nothing of downloads — the song is starred in Navidrome when a scan finds its
   file)*
5. The API and the web on `id`/`ref`. *(done: a page names a track by the Hub's id — to tags, playlists, ♥, the
   listening history and «Не нравится» — and by its catalog ref only where the Hub does not know the track. What
   stays Navidrome's is the file: a library file is streamed by the Navidrome song of that file, and a library
   song with no catalog record is still listed under the ref "nd:<song>", which the recommendations keep as its
   key. Albums and artists of the library are Navidrome's objects and are named by its ids)*
6. Hub persistence is PostgreSQL. Revision `30a000000001` creates the schema on a fresh database. Navidrome, the
   embedder and the music files keep their own storage.

After the passes: `favorite_subjects` keeps only albums and artists (a catalog ref and a Navidrome id seen to be
one). What a track is called — its id, its ref, the song of each of its files — is read from the library
(`favorites.subjects_of`); the journal of likes still decides, and «Любимое» is the list it comes to.
