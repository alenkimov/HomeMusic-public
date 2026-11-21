"""A catalog profile is evidence only for the artist it actually credits."""

from __future__ import annotations

from types import SimpleNamespace


def track(artist: str, catalog_artist: str | None, artist_ref: str | None):
    return SimpleNamespace(artist=artist, catalog={"artist": catalog_artist, "artist_ref": artist_ref})


def test_the_catalogs_artist_of_a_library_artist():
    from app.api.medialib import _catalog_refs_of

    theirs = [track("Михаил Круг", "Михаил Круг", "dz:3128271"),
              track("Михаил Круг", "Посторонний", "dz:999"),
              track("Другой, Михаил Круг", "Михаил Круг", "dz:123"),
              track("Михаил Круг", None, None)]
    assert _catalog_refs_of(theirs, "михаил круг ") == {"dz:3128271"}
