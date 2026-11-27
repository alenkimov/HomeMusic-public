"""Process-wide singletons: download providers and catalog clients (created in the app lifespan)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.config import get_settings
from app.providers.base import Provider, TrackQuery

if TYPE_CHECKING:
    from app.catalog.deezer import DeezerClient
    from app.catalog.hitmoz_catalog import HitmozCatalog
    from app.catalog.models import ArtistPage, CatalogAlbum, CatalogTrack
    from app.catalog.musicbrainz import MusicBrainzClient
    from app.catalog.spotify import SpotifyCatalog
    from app.catalog.yandex_catalog import YandexCatalog

log = logging.getLogger(__name__)


class Services:
    def __init__(self) -> None:
        self.providers: dict[str, Provider] = {}
        self.deezer: DeezerClient | None = None
        self.hitmoz_catalog: HitmozCatalog | None = None
        self.yandex: YandexCatalog | None = None
        self.musicbrainz: MusicBrainzClient | None = None
        self.spotify: SpotifyCatalog | None = None

    def start(self) -> None:
        from app.catalog.deezer import DeezerClient
        from app.catalog.hitmoz_catalog import HitmozCatalog
        from app.catalog.musicbrainz import MusicBrainzClient
        from app.catalog.spotify import SpotifyCatalog
        from app.providers.hitmos_fm import HitmosFmProvider
        from app.providers.hitmoz import HitmozProvider
        from app.providers.reuploads import SoundcloudProvider, YtReuploadProvider
        from app.providers.ytdlp import YoutubeAccess, YtdlpProvider

        cfg = get_settings().cfg
        hitmoz = HitmozProvider(cfg.hitmoz)
        youtube = YoutubeAccess(cfg.ytdlp)
        self.providers = {
            "hitmoz": hitmoz,
            "hitmos_fm": HitmosFmProvider(cfg.hitmos_fm),
            "ytdlp": YtdlpProvider(cfg.ytdlp, access=youtube),
            "yt_reupload": YtReuploadProvider(cfg.ytdlp, access=youtube),
            "soundcloud": SoundcloudProvider(cfg.ytdlp),
        }
        if get_settings().yandex_catalog:
            from app.providers.yandex import YandexProvider

            self.providers["yandex"] = YandexProvider()  # finds nothing without an account with Plus
        from app.checks import watch

        # each search, download and opening of a file is a check of its source (`app.checks`)
        self.providers = {name: watch(p) for name, p in self.providers.items()}
        self.deezer = DeezerClient(cfg.catalog) if get_settings().deezer_catalog else None
        self.hitmoz_catalog = HitmozCatalog(hitmoz)
        from app.catalog.yandex_catalog import YandexCatalog

        self.yandex = YandexCatalog() if get_settings().yandex_catalog else None
        self.musicbrainz = MusicBrainzClient(cfg.catalog)
        self.spotify = SpotifyCatalog()
        self.spotify.warm()

    async def stop(self) -> None:
        for p in self.providers.values():
            try:
                await p.aclose()
            except Exception:
                log.exception("closing provider %s", p.name)
        for c in (self.deezer, self.musicbrainz, self.spotify):
            if c is not None:
                try:
                    await c.aclose()
                except Exception:
                    log.exception("closing catalog client")
        self.spotify = None

    # --- a record of a catalog, read from the catalog it is of (`CatalogNotFound`: no such record, or catalog) ---

    async def artist_page(self, ref: str) -> ArtistPage:
        from app.catalog.models import CatalogNotFound
        from app.catalog.models import split_ref

        src, id_ = split_ref(ref)
        if src == "dz" and self.deezer is not None:
            return await self.deezer.get_artist_page(id_)
        if src == "ym" and self.yandex is not None:
            return await self.yandex.get_artist(id_)
        if src == "hm" and self.hitmoz_catalog is not None:
            return await self.hitmoz_catalog.get_artist(id_)
        if src == "sp" and self.spotify is not None:
            return await self.spotify.artist_page(id_)
        raise CatalogNotFound(ref)

    async def album(self, ref: str, *, credited: bool = False) -> CatalogAlbum:
        """`credited`: with everyone credited on each track (Deezer says so only track by track)."""
        from app.catalog.models import CatalogNotFound
        from app.catalog.models import split_ref

        src, id_ = split_ref(ref)
        if src == "dz" and self.deezer is not None:
            return await (self.deezer.get_album_credited(id_) if credited else self.deezer.get_album(id_))
        if src == "ym" and self.yandex is not None:
            return await self.yandex.get_album(id_)
        if src == "hm" and self.hitmoz_catalog is not None:
            return await self.hitmoz_catalog.get_album(id_)
        if src == "sp" and self.spotify is not None:
            return await self.spotify.album(id_)
        raise CatalogNotFound(ref)

    async def track(self, ref: str) -> CatalogTrack:
        from app.catalog.models import CatalogNotFound
        from app.catalog.models import split_ref

        src, id_ = split_ref(ref)
        if src == "dz" and self.deezer is not None:
            return await self.deezer.get_track(id_)
        if src == "ym" and self.yandex is not None:
            return await self.yandex.get_track(id_)
        if src == "sp" and self.spotify is not None:
            return await self.spotify.track(id_)
        if src == "hm" and self.hitmoz_catalog is not None:
            if track := await self.hitmoz_catalog.get_track(id_):
                return track
        raise CatalogNotFound(ref)

    def provider_order(self, q: TrackQuery, *, for_download: bool = False) -> list[Provider]:
        from app.matching.normalize import is_russian_query

        sources = get_settings().cfg.sources
        names = sources.ru if is_russian_query(q) else sources.default
        if sources.yandex_first and "yandex" not in names:
            names = ["yandex", *names]
        if for_download:
            names = [*names, *sources.download_fallback]
        return [self.providers[n] for n in dict.fromkeys(names) if n in self.providers]

    def reupload_providers(self) -> list[Provider]:
        """Sources of re-uploads by small channels: only for copies of explicit tracks without censorship."""
        return [self.providers[n] for n in get_settings().cfg.sources.reuploads if n in self.providers]


svc = Services()
