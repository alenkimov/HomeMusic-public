"""The errors of outside services, one kind for all of them.

Each client spoke its own: `YandexError`, `DeezerError`, spotapi's `HTTPError` and its decoding's `TypeError`,
httpx's network errors, a timeout; the user read «Каталог недоступен: ArtistDetails.profile.biography: value does not
match…» and the code decided by itself what to try again. Now an error of a service is a `ServiceError` — the
service, what happened (`kind`), the words for the user — and `explain` makes one of whatever was raised:

- `unavailable` — no answer, a network error, 5xx, 451 (not served to this country: a proxy of Russia helps);
- `limited` — the service asks to wait (429; its own quota codes), `retry_after` when it says how long;
- `not_found` — no such record; `auth` — the sign-in is not accepted, or the thing is closed;
- `bad_answer` — an answer of a shape not understood; `refused` — any other refusal.

`unavailable` and `limited` are of the moment: worth trying again later (`retryable`).
"""

from __future__ import annotations

import asyncio
from typing import Literal

Kind = Literal["unavailable", "limited", "not_found", "auth", "bad_answer", "refused"]

NAMES = {
    "yandex": "Яндекс Музыка", "spotify": "Spotify", "deezer": "Deezer", "musicbrainz": "MusicBrainz",
    "lastfm": "Last.fm", "listenbrainz": "ListenBrainz", "vk": "ВКонтакте", "youtube": "YouTube",
    "soundcloud": "SoundCloud", "hitmoz": "hitmoz", "hitmos_fm": "hitmos.fm", "lyrics": "Источник текстов",
    "ai": "ИИ", "google": "Google", "telegram": "Telegram",
}
# a catalog's record names its service by its prefix
REF_SERVICES = {"dz": "deezer", "sp": "spotify", "ym": "yandex", "hm": "hitmoz", "mb": "musicbrainz"}

SAID = {
    "unavailable": "не отвечает — попробуйте позже",
    "limited": "просит подождать: слишком много запросов",
    "not_found": "не нашёл этого",
    "auth": "не принял вход — войдите заново",
    "bad_answer": "ответил в непривычном виде — его ответ не разобран",
    "refused": "отказал",
}


class ServiceError(Exception):
    def __init__(self, service: str | None, kind: Kind, message: str | None = None, *, status: int | None = None,
                 retry_after: float | None = None):
        self.service, self.kind, self.status, self.retry_after = service, kind, status, retry_after
        super().__init__(message or said(service, kind, status))

    @property
    def retryable(self) -> bool:
        return self.kind in ("unavailable", "limited")


def name_of(service: str | None) -> str:
    return NAMES.get(service or "", service or "Сервис")


def said(service: str | None, kind: Kind, status: int | None = None) -> str:
    if kind == "unavailable" and status == 451:
        return f"{name_of(service)} не отвечает на запросы из этой страны — нужен прокси («Прокси и запросы»)"
    return f"{name_of(service)} {SAID[kind]}"


def _status_kind(status: int) -> Kind:
    if status in (404, 410):
        return "not_found"
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "limited"
    if status == 451 or status >= 500:
        return "unavailable"
    return "refused"


def explain(e: BaseException, service: str | None = None) -> ServiceError:
    """`e`, whatever raised it, as an error of `service`."""
    import httpx

    if isinstance(e, ServiceError):
        if service and not e.service:
            e.service = service
        return e
    from app.catalog.models import CatalogNotFound

    if isinstance(e, CatalogNotFound):
        return ServiceError(service, "not_found")
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        after = e.response.headers.get("retry-after")
        return ServiceError(service, _status_kind(status), status=status,
                            retry_after=float(after) if after and after.isdigit() else None)
    if isinstance(e, (httpx.TransportError, asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return ServiceError(service, "unavailable")
    module = type(e).__module__ or ""
    if module.startswith(("spotapi", "wreq")):
        if isinstance(e, (TypeError, ValueError, KeyError)):
            return ServiceError(service or "spotify", "bad_answer")
        return ServiceError(service or "spotify", "unavailable")
    name = type(e).__name__
    if name in ("DeezerError", "MusicBrainzError"):
        text = str(e).lower()
        if "quota" in text or "busy" in text:
            return ServiceError(service, "limited")
        if "not found" in text or "800" in text:
            return ServiceError(service, "not_found")
        return ServiceError(service, "refused", f"{name_of(service)} отказал: {str(e)[:150]}")
    if isinstance(e, (TypeError, ValueError, KeyError)):
        return ServiceError(service, "bad_answer")
    return ServiceError(service, "refused", f"{name_of(service)} отказал: {str(e)[:150] or type(e).__name__}")


def service_of_ref(ref: str | None) -> str | None:
    return REF_SERVICES.get((ref or "").partition(":")[0])


def http_error(e: BaseException, ref_or_service: str | None, missing: str = "Не найдено"):
    """An error of a service as the answer of the Hub's API: 404 with `missing` when the record is not there, 503
    when it is of the moment (and when to ask again), else 502 — with words for the user."""
    from fastapi import HTTPException

    service = ref_or_service if ref_or_service in NAMES else service_of_ref(ref_or_service)
    err = explain(e, service)
    if err.kind == "not_found":
        return HTTPException(404, missing)
    headers = {"Retry-After": str(int(err.retry_after))} if err.retry_after else None
    return HTTPException(503 if err.retryable else 502, str(err), headers=headers)

