"""The wide picture over an artist's page: read out of what YouTube Music answers, and only from a page that is the
artist's — the very name, and a song of theirs on it."""

from __future__ import annotations

from app.catalog import banners


def row(title: str, browse: str | None = None) -> dict:
    r = {"flexColumns": [{"musicResponsiveListItemFlexColumnRenderer": {"text": {"runs": [{"text": title}]}}}]}
    if browse:
        r["navigationEndpoint"] = {"browseEndpoint": {"browseId": browse}}
    return {"musicResponsiveListItemRenderer": r}


def test_the_artists_of_a_search_and_what_a_page_says():
    search = {"contents": {"x": [row("TAK", "UC1"), row("TAK Music", "UC2"), row("A song", None), row("Album", "MPRE9")]}}
    assert banners.artists_found(search) == [("TAK", "UC1"), ("TAK Music", "UC2")]

    page = {
        "header": {"musicImmersiveHeaderRenderer": {
            "title": {"runs": [{"text": "TAK"}]},
            "thumbnail": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [
                {"url": "u540", "width": 540, "height": 225}, {"url": "u1920", "width": 1920, "height": 800},
                {"url": "u2880", "width": 2880, "height": 1200}]}}}}},
        "contents": [row("PPPP (feat. Hatsune Miku)"), row("mochimochi")],
    }
    name, pics, songs = banners.page_of(page)
    assert name == "TAK" and len(pics) == 3 and len(songs) == 2
    # one name is the other with more said; a word of two letters proves nothing
    assert banners.theirs({"psi"}, {"プシ psi"}) and banners.theirs({"my level egotrip"}, {"egotrip"})
    assert not banners.theirs({"psi"}, {"mochimochi"}) and not banners.theirs({"x"}, {"x"}) and not banners.theirs({"go on"}, {"on"})
    assert banners._wide(pics)["url"] == "u1920"  # the widest a page needs, not the largest there is
    # a square picture is the photo again: not a banner
    assert banners._wide([{"url": "sq", "width": 1000, "height": 1000}]) is None
    assert banners.page_of({}) == ("", [], set())


async def test_a_namesakes_page_is_not_taken(monkeypatch):
    from app.catalog.models import CatalogArtist

    artist = CatalogArtist(ref="dz:7", source="dz", id="7", name="TAK")
    top = ["mochimochi"]
    wide = {"thumbnail": {"musicThumbnailRenderer": {"thumbnail": {"thumbnails": [{"url": "https://img.test/w", "width": 1440, "height": 600}]}}}}
    pages = {
        "UC1": {"header": {"h": {"title": {"runs": [{"text": "TAK"}]}, **wide}}, "contents": [row("Smoke and Beers")]},  # a namesake
        "UC2": {"header": {"h": {"title": {"runs": [{"text": "TAK"}]}, **wide}}, "contents": [row("Mochimochi")]},  # theirs
    }
    asked: list[str] = []

    class Answer:
        def __init__(self, body=None, content=b""):
            self._body, self.content = body, content

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, headers=None, json=None):
            if "search" in url:
                return Answer({"contents": [row("Tak", "UC1"), row("TAK", "UC2"), row("TAKE", "UC3")]})
            asked.append(json["browseId"])
            return Answer(pages[json["browseId"]])

        async def get(self, url):
            asked.append(url)
            return Answer(content=b"jpeg")

    monkeypatch.setattr(banners, "make_client", lambda timeout=20: Client())
    monkeypatch.setattr(banners, "_jpeg", lambda data: b"kept:" + data)
    assert await banners.find(artist, top) == b"kept:jpeg"
    assert asked == ["UC1", "UC2", "https://img.test/w"]  # the first of that name was a namesake's: passed by
    # none of the pages is theirs
    asked.clear()
    other = ["Nowhere"]
    assert await banners.find(artist, other) is None and asked == ["UC1", "UC2"]
