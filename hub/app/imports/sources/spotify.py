"""Read Spotify collections into the same snapshot used by other transfers."""

from __future__ import annotations

import logging

from app.catalog import spotify_session
from app.catalog.models import CatalogTrack
from app.catalog.spotify import SpotifyLocalTrack, expand, is_short, resource
from app.imports.sources.base import SnapCollection, SnapItem, Snapshot, SourceError
from app.services import svc

log = logging.getLogger(__name__)


def link_target(value: str) -> str:
    if len(value) <= 2048 and (((found := resource(value)) and found[0] == "playlist") or is_short(value)):
        return value
    raise SourceError("Вставьте ссылку на плейлист Spotify")


async def fetch_link(value: str, *, fresh: bool = False) -> Snapshot:
    if svc.spotify is None:
        raise SourceError("Каталог Spotify недоступен")
    try:
        target = await expand(value)
        if not target or target[0] != "playlist":
            raise SourceError("Ссылка не ведёт на публичный плейлист Spotify")
        playlist, tracks, _ = await svc.spotify.playlist(target[1], fresh=fresh)
    except SourceError:
        raise
    except Exception as error:
        log.info("Spotify import: %r", error)
        raise SourceError("Не удалось прочитать плейлист Spotify. Проверьте, что он открыт по ссылке.") from error
    return Snapshot(playlist.owner, [SnapCollection("playlist", playlist.id, playlist.title, _items(tracks),
                                                    playlist.cover_url)])


def _items(tracks: list[CatalogTrack | SpotifyLocalTrack]) -> list[SnapItem]:
    unique = {}
    for track in tracks:
        unique.setdefault(track.id, track)
    items = []
    for track in unique.values():
        if isinstance(track, SpotifyLocalTrack):
            raw = {"artist": track.artist, "title": track.title, "album": track.album, "duration_s": track.duration_s}
        else:
            raw = {"artist": track.artist, "artists": track.artists, "title": track.title, "album": track.album_title,
                   "duration_s": track.duration_s, "cover_url": track.cover_url, "catalog": track.model_dump(mode="json")}
        items.append(SnapItem(track.id, raw))
    return items


async def fetch_account(secret: str, url: str | None) -> Snapshot:
    """Read a playlist or liked tracks with the owner's session and a private snapshot."""
    try:
        target = await expand(url) if url else None
        if url and (not target or target[0] != "playlist"):
            raise SourceError("Ссылка не ведёт на плейлист Spotify")
        result = await spotify_session.collection(secret, target[1] if target else None)
        dates = {}
        tracks = []
        for entry in result["tracks"]:
            data = entry["track"]
            track = SpotifyLocalTrack.model_validate(data) if "uri" in data else CatalogTrack.model_validate(data)
            tracks.append(track)
            dates[track.id] = entry["added_at"]
        items = _items(tracks)
        for item in items:
            if added_at := dates[item.external_id]:
                item.raw["liked_at"] = added_at
        cover = next((t.cover_url for t in tracks if isinstance(t, CatalogTrack) and t.cover_url), None) if target else None
        collection = SnapCollection("playlist" if target else "likes", target[1] if target else "likes",
                                    result["title"], items, cover)
    except SourceError:
        raise
    except spotify_session.SessionError as error:
        if error.expired:
            raise SourceError("Вход в Spotify устарел. Подключите аккаунт заново.") from None
        raise SourceError("Не удалось прочитать коллекцию с этим аккаунтом Spotify") from None
    except Exception as error:
        log.info("Spotify personal import failed (%s)", type(error).__name__)
        raise SourceError("Не удалось прочитать коллекцию с этим аккаунтом Spotify") from None
    if not tracks:
        raise SourceError("В коллекции нет доступных треков")
    return Snapshot(result["subject"], [collection], account_subject=result["subject"], plan=result["plan"])
