"""A library artist is the catalog's by the Hub's records of their tracks, whatever the spelling of the name."""

from __future__ import annotations

from types import SimpleNamespace


def track(artist: str, artist_ref: str | None):
    return SimpleNamespace(artist=artist, catalog={"artist_ref": artist_ref} if artist_ref else None)


def test_the_catalogs_artist_of_a_library_artist():
    from app.api.medialib import _catalog_ref_of

    theirs = [track("Михаил Круг", "dz:3128271"), track("Михаил Круг", "dz:3128271"),
              track("Другой, Михаил Круг", "dz:999"),  # a guest there: that record's artist is not him
              track("Михаил Круг", None)]
    assert _catalog_ref_of(theirs, "михаил круг ") == "dz:3128271"
    assert _catalog_ref_of([track("Другой, Михаил Круг", "dz:999")], "Михаил Круг") is None
    assert _catalog_ref_of([track("Своя группа", None)], "Своя группа") is None
