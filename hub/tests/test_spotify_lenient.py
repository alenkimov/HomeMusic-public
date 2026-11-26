"""An answer of Spotify with one field of a shape spotapi does not expect is read without it."""

from __future__ import annotations

import pytest
from spotapi.models.catalogue.artist_details import ArtistDetails

from app.catalog.spotify import lenient


def raw(biography):
    return {"id": "x", "uri": "spotify:artist:x", "stats": {}, "visuals": {}, "discography": {},
            "profile": {"name": "в поиске дна печали", "biography": biography, "verified": False}}


def test_a_biography_of_another_shape_is_left_out():
    row = lenient(ArtistDetails, raw({"text": None, "type": 5}))
    assert row.profile.name == "в поиске дна печали" and row.profile.biography is None


def test_the_rest_stays_and_the_answer_is_not_changed():
    answer = raw({"text": "о группе", "type": "AUTOBIOGRAPHY"})
    row = lenient(ArtistDetails, answer)
    assert row.profile.biography.text == "о группе"
    bad = raw({"text": None})
    lenient(ArtistDetails, bad)
    assert bad["profile"]["biography"] == {"text": None}  # a copy was read


def test_a_required_field_missing_is_still_an_error():
    answer = raw(None)
    del answer["profile"]["name"]
    with pytest.raises((TypeError, ValueError)):
        lenient(ArtistDetails, answer)
