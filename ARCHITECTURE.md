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
album its file is filed under, if any), `created_at`, `created_by`. Nothing about files.

**`track_files`** — a file of a track. `id`, `track_id`, `path` (unique, relative to `library/`), `uid` (written
into the file as `HOMEMUSIC_TRACK_ID`), `nd_song_id` (the song Navidrome made of it; set after a scan), `codec`,
`bitrate_kbps`, `source`, `source_url`, `source_upload_date`, `locked`, `warnings`, `acquired_at`, `added_by`.

**`track_links`** — what the track is called on music services (`spotify`, `yandex`, `ytm`): `track_id`,
`service`, `external_id`. Share links are made of them, and an import knows by them a song it has seen.
The catalog ref is not one of these: it is the track's own name (`tracks.ref`), the links are its names abroad.

**`playlists`** — `id`, `user_id`, `kind` (`liked` — «Любимое», one per user; `user` — made by hand or by an
import; `mix` — «Микс дня»), `name`, `cover`, `created_at`, `updated_at`, and what the mirror needs:
`nd_playlist_id`, `mirrored` (the song ids Navidrome was last given).

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

## Modules

- `app/library/tracks.py` — the registry: find or make a Track by a catalog ref or by names; its links; its files.
- `app/library/importer.py` — puts a file into the library and attaches it to a Track.
- `app/playlists.py` — playlists and their items; «Любимое»; the mirror job.
- `app/api/*` — speak `id`/`ref` only.
- `app/catalog/*`, `app/providers/*` — the outside world; unchanged in shape.

## Order of work

One release, built in passes that each leave the tests green:

1. `track_files` split off `tracks`; a track named by its catalog ref. *(done)*
2. Playlists and «Любимое» in the Hub; the mirror; the old favorites and pending lists removed.
3. Events, dislikes, statistics, taste, mixes and the wave on `track_id`.
4. Imports and downloads on top of it.
5. The API and the web on `id`/`ref`.
6. What is left of 1.x deleted; the migrations of 1.x replaced by one that makes the 2.0 schema.
