"""Catalog entities shared by all catalog sources (Deezer, Yandex Music, hitmoz, MusicBrainz).

A `ref` is "<source>:<id>", e.g. "dz:4505880" (Deezer), "ym:50685843" (Yandex Music) or "hm:12345" (hitmoz). The kind (artist/album/track)
is implied by the API that returns or accepts it.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

# "nd": a song only the library has (Navidrome id), no catalog record: mixes (`app.mixes`) list such songs too
# "file": an exact provider file used as the first track of a wave, including songs absent from catalogs.
Source = Literal["dz", "ym", "sp", "hm", "mb", "nd", "file"]
CATALOGS = ("dz", "ym", "sp", "hm", "mb")  # the sources that are catalogs: a record of one names a song for good
CATALOG_SERVICES = {"dz": "deezer", "ym": "yandex", "sp": "spotify", "hm": "hitmoz", "mb": "musicbrainz"}
RecordType = Literal["album", "single", "ep", "compile"]


class CatalogNotFound(Exception):
    """The requested catalog entity does not exist."""


def make_ref(source: str, id_: str | int) -> str:
    return f"{source}:{id_}"


def split_ref(ref: str) -> tuple[str, str]:
    source, _, id_ = ref.partition(":")
    if not id_:
        raise ValueError(f"bad catalog ref: {ref!r}")
    return source, id_


class CatalogArtist(BaseModel):
    ref: str
    source: Source
    id: str
    name: str
    hub_artist_id: int | None = None
    picture_url: str | None = None
    picture_small_url: str | None = None  # for lists (Deezer picture_medium, 250 px)
    nb_albums: int | None = None
    nb_fans: int | None = None
    monthly_listeners: int | None = None
    banner_url: str | None = None


class CatalogTrack(BaseModel):
    ref: str
    source: Source
    id: str
    title: str  # full title as in the catalog, e.g. "Группа крови (Remastered 2019)"
    alternate_titles: list[str] = []
    artist: str  # main artist name
    artists: list[str] = []  # all credited artists (main first)
    artist_refs: list[str | None] = []  # their catalog refs, in the order of `artists` (None: unknown)
    credits_complete: bool = False  # the catalog supplied the track's full credits
    artist_ref: str | None = None
    album_ref: str | None = None
    album_title: str | None = None
    album_artist: str | None = None
    duration_s: int | None = None
    track_no: int | None = None
    disc_no: int | None = None
    isrc: str | None = None
    explicit: bool | None = None
    release_date: date | None = None
    cover_url: str | None = None  # largest available album cover
    cover_small_url: str | None = None  # for lists (Deezer cover_medium, 250 px): ~10× fewer bytes than `cover_url`
    preview_url: str | None = None
    rank: int | None = None  # Deezer popularity (mixes score with it)
    spotify_id: str | None = None
    spotify_plays: int | None = None


class CatalogAlbum(BaseModel):
    ref: str
    source: Source
    id: str
    title: str
    alternate_titles: list[str] = []
    artist: str
    artist_ref: str | None = None
    # everyone the release is credited to, the main one first, and their refs — said only by the release's own
    # record (`get_album`), not by a list of releases
    artists: list[str] = []
    artist_refs: list[str | None] = []
    record_type: RecordType = "album"
    release_date: date | None = None
    year: int | None = None
    cover_url: str | None = None  # largest (Deezer cover_xl, 1000px)
    cover_small_url: str | None = None
    nb_tracks: int | None = None
    explicit: bool | None = None
    upc: str | None = None
    genre: str | None = None
    tracks: list[CatalogTrack] | None = None  # filled by get_album


class ArtistPage(BaseModel):
    artist: CatalogArtist
    top_tracks: list[CatalogTrack] = []
    albums: list[CatalogAlbum] = []  # all releases (albums, singles, EPs, compilations), newest first


class CatalogPlaylist(BaseModel):
    id: str
    title: str
    owner: str | None = None
    cover_url: str | None = None
    total_tracks: int | None = None


class SearchResults(BaseModel):
    artists: list[CatalogArtist] = []
    albums: list[CatalogAlbum] = []
    tracks: list[CatalogTrack] = []
    playlists: list[CatalogPlaylist] = []
