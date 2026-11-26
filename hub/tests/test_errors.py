"""The errors of outside services, told apart and said in words."""

from __future__ import annotations

import httpx
import pytest

from app.catalog.models import CatalogNotFound
from app.catalog.ym import YandexError
from app.errors import ServiceError, explain, http_error


def status(code: int, **headers) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://api.deezer.com/x")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req, headers=headers))


@pytest.mark.parametrize(("error", "kind"), [
    (status(404), "not_found"), (status(401), "auth"), (status(429), "limited"), (status(503), "unavailable"),
    (status(451), "unavailable"), (status(418), "refused"), (httpx.ConnectTimeout("t"), "unavailable"),
    (TimeoutError(), "unavailable"), (CatalogNotFound("dz:1"), "not_found"), (KeyError("x"), "bad_answer"),
])
def test_what_happened_is_told_apart(error, kind):
    assert explain(error, "deezer").kind == kind


def test_the_words_say_who_and_what():
    assert str(explain(status(429), "deezer")) == "Deezer просит подождать: слишком много запросов"
    assert "нужен прокси" in str(explain(status(451), "yandex"))
    assert str(YandexError("auth", "x")) == "x" and YandexError("auth", "x").service == "yandex"


def test_a_spotapi_answer_of_another_shape_is_a_bad_answer():
    class Odd(TypeError):
        __module__ = "spotapi._decoding"

    err = explain(Odd("ArtistDetails.profile.biography: value does not match"))
    assert (err.service, err.kind) == ("spotify", "bad_answer")
    assert "biography" not in str(err)  # the library's words stay in the log


def test_the_api_answers_with_what_the_user_can_act_on():
    assert http_error(CatalogNotFound("x"), "dz:1", "Альбом не найден").status_code == 404
    busy = http_error(status(429, **{"retry-after": "30"}), "sp:1")
    assert busy.status_code == 503 and busy.headers == {"Retry-After": "30"} and busy.detail.startswith("Spotify")
    assert http_error(status(401), "ym").status_code == 502


def test_only_what_is_of_the_moment_is_tried_again():
    assert ServiceError("x", "limited").retryable and ServiceError("x", "unavailable").retryable
    assert not ServiceError("x", "auth").retryable and not ServiceError("x", "bad_answer").retryable
