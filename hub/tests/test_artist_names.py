"""An artist's one name and other spellings, projects of one person, and the other names of a track."""

from __future__ import annotations

from tests.test_flow import download_one, drain_jobs
from tests.test_flow import env as env  # the fixture
from tests.test_flow import src_mp3 as src_mp3  # its file


async def _library_track():
    from sqlalchemy import select

    from app.db import session_scope
    from app.models import Album, Track, TrackFile

    async with session_scope() as s:
        t = (await s.execute(select(Track).join(TrackFile, TrackFile.track_id == Track.id))).scalars().first()
        f = (await s.execute(select(TrackFile).where(TrackFile.track_id == t.id))).scalars().first()
        a = await s.get(Album, t.album_id) if t.album_id else None
        return t, f, a


async def test_a_spelling_made_the_name_rewrites_the_library_and_its_files(env):
    from app.library import tagger
    from app.library.importer import library_abs

    c = env["client"]
    await download_one(c, {"artist": "Кино", "title": "Группа крови"})
    card = (await c.get("/api/artists/of", params={"name": "кино"})).json()  # the letters' case is no other artist
    assert card["name"] == "Кино" and [n["main"] for n in card["names"]] == [True]

    r = await c.post(f"/api/artists/{card['id']}/names", json={"name": "Kino"})
    assert r.status_code == 200 and [n["name"] for n in r.json()["names"]] == ["Кино", "Kino"], r.text
    # the search finds the track by the spelling, and says so
    (own,) = (await c.get("/api/search", params={"q": "kino gruppa"})).json()["library"]
    assert own["title"] == "Группа крови"
    (own,) = (await c.get("/api/search", params={"q": "Kino"})).json()["library"]
    assert own["found_by"] == [] or own["found_by"] == ["Kino"]  # «кино» reads «kino» by itself

    kino = next(n for n in r.json()["names"] if n["name"] == "Kino")
    r = await c.post(f"/api/artists/{card['id']}/main", json={"name_id": kino["id"]})
    assert r.status_code == 200 and r.json()["name"] == "Kino" and r.json()["rewriting"], r.text
    await drain_jobs()
    t, f, a = await _library_track()
    assert (t.artist, t.album_artist, a.album_artist) == ("Kino", "Kino", "Kino")
    tags = tagger.read_tags(library_abs(f.path))
    assert tags["artist"] == "Kino" and tags["album_artist"] == "Kino", tags
    hist = (await c.get(f"/api/library/tracks/{t.id}/edits")).json()
    assert any(h["changes"].get("artist") == ["Кино", "Kino"] for h in hist), hist

    # what comes under the other spelling from now on is filed under the name
    from app.db import session_scope
    from app.library import artists

    async with session_scope() as s:
        assert (await artists.credits(s, "кино, Ноль"))[0] == "Kino, Ноль"


async def test_detaching_a_profile_keeps_two_namesakes_separate(env, monkeypatch):
    import asyncio

    from sqlalchemy import select

    from app.api import artists as api_artists
    from app.api import catalog
    from app.catalog.models import ArtistPage, CatalogAlbum, CatalogArtist
    from app.db import session_scope
    from app.models import Artist, Edit

    profiles = {
        "dz:50": CatalogArtist(ref="dz:50", source="dz", id="50", name="TAK", nb_fans=5103),
        "dz:51": CatalogArtist(ref="dz:51", source="dz", id="51", name="TAK", nb_fans=17),
    }

    async def profile_card(ref):
        return profiles.get(ref)

    async def get_artist_page(ref):
        who = profiles[ref]
        return ArtistPage(artist=who, albums=[
            CatalogAlbum(ref=f"dz:al{who.id}", source="dz", id=f"al{who.id}",
                         title=f"Album {who.id}", artist=who.name, artist_ref=ref),
        ])

    monkeypatch.setattr(api_artists, "profile_card", profile_card)
    monkeypatch.setattr(catalog, "get_artist_page", get_artist_page)
    c = env["client"]
    main = (await c.get("/api/artists/of", params={"name": "TAK", "ref": "dz:50"})).json()
    namesake = (await c.get("/api/artists/of", params={"name": "TAK", "ref": "dz:51"})).json()
    assert main["id"] != namesake["id"]
    api = f"/api/artists/{main['id']}"
    merged = await c.post(f"{api}/merge", json={"other_id": namesake["id"]})
    assert merged.status_code == 200
    rows = (await c.get(f"{api}/profiles")).json()
    assert [(p["ref"], p["service"], p["followers"]) for p in rows] == [("dz:50", "deezer", 5103), ("dz:51", "deezer", 17)]
    assert rows[1]["url"] == "https://www.deezer.com/artist/51"

    detached = await asyncio.gather(*(c.delete(f"{api}/profiles/dz%3A51") for _ in range(2)))
    assert sorted(r.status_code for r in detached) in ([200, 404], [200, 409])
    assert next(r.json() for r in detached if r.status_code == 200)["profiles"] == ["dz:50"]
    for ref, title in [("dz:50", "Album 50"), ("dz:51", "Album 51")]:
        d = (await c.get(f"/api/catalog/artist/{ref}")).json()
        assert d["profiles"] == [ref] and [a["title"] for a in d["albums"]] == [title]
        card = (await c.get("/api/artists/of", params={"ref": ref})).json()
        assert card["suggestions"] == []
    bare_name = (await c.get("/api/artists/of", params={"name": "TAK"})).json()
    assert bare_name["id"] == main["id"]
    async with session_scope() as s:
        assert len((await s.scalars(select(Artist).where(Artist.merged_into_id.is_(None)))).all()) == 2
        edits = (await s.scalars(select(Edit).where(Edit.target_type == "artist", Edit.target_id == main["id"]))).all()
        assert [e.changes_json["profile"] for e in edits if "profile" in e.changes_json] == [["dz:51", None]]


async def test_discography_stream_reports_active_catalogs_and_pending_spotify(env, monkeypatch):
    import asyncio
    import json

    import httpx
    from fastapi import HTTPException

    from app.api import catalog
    from app.catalog.models import ArtistPage, CatalogAlbum, CatalogArtist
    from app.db import session_scope
    from app.library import artists
    from app.services import svc

    def page(source):
        a = CatalogArtist(ref=f"{source}:50", source=source, id="50", name="TAK")
        release = CatalogAlbum(ref=f"{source}:600", source=source, id="600", title=f"{source} album", artist=a.name)
        return ArtistPage(artist=a, albums=[release])

    pages = {source: page(source) for source in ("dz", "sp")}
    async with session_scope() as s:
        who = await artists.who(s, "TAK", "dz:50")
        await artists.tie_profile(s, who.id, "sp:50")
        await artists.tie_profile(s, who.id, "ym:50")

    async def get_artist_page(ref):
        source = ref.partition(":")[0]
        if source == "ym":
            raise HTTPException(502, "catalog unavailable")
        return pages[source]

    albums_started = asyncio.Event()
    deezer_requests = 0
    background_pending = True

    async def get_album(ref):
        source = ref.partition(":")[0]
        if source == "dz":
            await albums_started.wait()
        return pages[source].albums[0]

    async def spotify_discography(id_):
        return pages["sp"].albums, background_pending, background_pending

    monkeypatch.setattr(catalog, "get_artist_page", get_artist_page)
    monkeypatch.setattr(catalog, "get_album", get_album)
    monkeypatch.setattr(svc.spotify, "discography", spotify_discography)
    monkeypatch.setattr(svc, "yandex", object())

    async def capture(scope, receive, send):
        async def observe(message):
            nonlocal deezer_requests
            await send(message)
            if message["type"] != "http.response.body" or not message.get("body"):
                return
            event = json.loads(message["body"])
            if event == {"type": "source", "data": {"service": "deezer", "state": "searching"}}:
                deezer_requests += 1
                if deezer_requests == 2:  # the album request starts after the artist response
                    albums_started.set()

        await env["app"](scope, receive, observe)

    transport = httpx.ASGITransport(app=capture)
    async with httpx.AsyncClient(transport=transport, base_url="http://hub.test", cookies=env["client"].cookies) as c:
        path = "/api/catalog/artist/dz:50/discography"
        headers = {"Accept": "application/x-ndjson"}
        for pending in (True, False):
            background_pending = pending
            r = await asyncio.wait_for(c.get(path, headers=headers), 5)
            assert r.status_code == 200 and r.headers["Content-Type"] == "application/x-ndjson"
            events = [json.loads(line) for line in r.text.splitlines()]
            states = {e["data"]["service"]: e["data"]["state"] for e in events if e["type"] == "source"}
            assert states == {"deezer": "ready", "yandex": "error", "spotify": "searching" if pending else "ready"}
            assert events[-1]["type"] == "done" and events[-1]["data"]["spotify_pending"] is pending
            assert {a["ref"] for a in events[-1]["data"]["albums"]} == {"dz:600", "sp:600"}


async def test_two_artists_are_joined_and_one_may_be_set_apart(env):
    c = env["client"]
    a = (await c.get("/api/artists/of", params={"name": "ЛСП"})).json()
    b = (await c.get("/api/artists/of", params={"name": "LSP", "ref": "dz:artist:7"})).json()
    assert a["id"] != b["id"]
    # the two read alike in Latin letters: each is offered to the other
    a = (await c.get("/api/artists/of", params={"name": "ЛСП"})).json()
    assert [x["name"] for x in a["suggestions"]] == ["LSP"]
    # a spelling that is somebody's is not taken silently
    r = await c.post(f"/api/artists/{a['id']}/names", json={"name": "lsp"})
    assert r.status_code == 409 and r.json()["detail"]["artist_id"] == b["id"], r.text

    r = await c.post(f"/api/artists/{a['id']}/merge", json={"other_id": b["id"]})
    assert r.status_code == 200 and [n["name"] for n in r.json()["names"]] == ["ЛСП", "LSP"] and not r.json()["suggestions"], r.text
    await drain_jobs()
    # the catalog's page of «LSP» is the artist's now, under the one name
    again = (await c.get("/api/artists/of", params={"name": "LSP", "ref": "dz:artist:7"})).json()
    assert (again["id"], again["name"]) == (a["id"], "ЛСП")
    assert [x["name"] for x in (await c.get("/api/artists/find", params={"q": "lsp"})).json()] == ["ЛСП"]
    # a catalog's own card of the artist is shown under the name here
    from app.catalog.models import CatalogArtist
    from app.db import session_scope
    from app.library import artists

    async with session_scope() as s:
        got = await artists.shown(s, [CatalogArtist(ref="dz:artist:7", source="dz", id="7", name="LSP"),
                                      CatalogArtist(ref="dz:artist:8", source="dz", id="8", name="Lsp"),
                                      CatalogArtist(ref="dz:artist:9", source="dz", id="9", name="Кино")])
        # …and another record under their spelling is not theirs by the name alone: a namesake, perhaps
        assert [a.name for a in got] == ["ЛСП", "Lsp", "Кино"]

    lsp = next(n for n in again["names"] if n["name"] == "LSP")
    r = await c.delete(f"/api/artists/{a['id']}/names/{lsp['id']}")
    assert r.status_code == 200 and [n["name"] for n in r.json()["names"]] == ["ЛСП"], r.text
    assert r.json()["suggestions"] == []  # set apart by hand: not offered again
    main = r.json()["names"][0]
    assert (await c.delete(f"/api/artists/{a['id']}/names/{main['id']}")).status_code == 422


async def test_a_project_is_linked_not_joined(env):
    c = env["client"]
    a = (await c.get("/api/artists/of", params={"name": "GONE.Fludd"})).json()
    r = await c.post(f"/api/artists/{a['id']}/projects", json={"name": "M00NCHILD"})
    assert r.status_code == 200 and [p["name"] for p in r.json()["projects"]] == ["M00NCHILD"], r.text
    other = (await c.get("/api/artists/of", params={"name": "m00nchild"})).json()
    assert other["id"] != a["id"] and [p["name"] for p in other["projects"]] == ["GONE.Fludd"]
    assert (await c.post(f"/api/artists/{a['id']}/projects", json={"other_id": a["id"]})).status_code == 422
    # the projects of one person are one group: a third, named on one page, is on the pages of all
    r = await c.post(f"/api/artists/{other['id']}/projects", json={"name": "Третий"})
    third = next(p for p in r.json()["projects"] if p["name"] == "Третий")
    assert sorted(p["name"] for p in r.json()["projects"]) == ["GONE.Fludd", "Третий"]
    assert sorted(p["name"] for p in (await c.get("/api/artists/of", params={"name": "GONE.Fludd"})).json()["projects"]) == ["M00NCHILD", "Третий"]
    assert sorted(p["name"] for p in (await c.get("/api/artists/of", params={"name": "Третий"})).json()["projects"]) == ["GONE.Fludd", "M00NCHILD"]
    # the one in the middle leaves: the other two stay each other's
    r = await c.delete(f"/api/artists/{a['id']}/projects/{other['id']}")
    assert r.status_code == 200 and [p["name"] for p in r.json()["projects"]] == ["Третий"], r.text
    assert (await c.get("/api/artists/of", params={"name": "M00NCHILD"})).json()["projects"] == []
    r = await c.delete(f"/api/artists/{a['id']}/projects/{third['id']}")
    assert r.status_code == 200 and r.json()["projects"] == []


async def test_a_track_is_found_by_its_other_names(env):
    c = env["client"]
    await download_one(c, {"artist": "Кино", "title": "Группа крови"})
    t, _, _ = await _library_track()
    assert (await c.get("/api/search", params={"q": "марафоны"})).json()["library"] == []
    r = await c.post(f"/api/library/tracks/{t.id}/names", json={"name": "Марафоны"})
    assert r.status_code == 200 and [(n["name"], n["source"]) for n in r.json()["names"]] == [("Марафоны", "manual")], r.text
    (own,) = (await c.get("/api/search", params={"q": "marafony кино"})).json()["library"]
    assert own["title"] == "Группа крови" and own["found_by"] == ["Марафоны"]
    (own,) = (await c.get("/api/search", params={"q": "gruppa krovi"})).json()["library"]  # Latin for Cyrillic
    assert own["found_by"] == []

    # renamed: the name it had is one it is found by, and its new name is not among the others
    r = await c.patch(f"/api/library/tracks/{t.id}", json={"title": "Марафоны"})
    assert r.status_code == 200, r.text
    info = (await c.get("/api/library/track-edit", params={"track_id": t.id})).json()
    assert info["title"] == "Марафоны" and ("Группа крови", "renamed") in [(n["name"], n["source"]) for n in info["names"]]
    assert "Марафоны" not in [n["name"] for n in info["names"]]
    (own,) = (await c.get("/api/search", params={"q": "группа крови"})).json()["library"]
    assert own["title"] == "Марафоны"  # (the album is still called so: the row itself shows the words)
    kept = next(n for n in info["names"] if n["id"])
    r = await c.delete(f"/api/library/tracks/{t.id}/names/{kept['id']}")
    assert r.status_code == 200 and kept["id"] not in [n["id"] for n in r.json()["names"]]


async def test_a_catalog_keeps_several_records_of_one_artist_and_a_namesake_is_somebody_else(env, monkeypatch):
    """The first record of the catalog is the artist's by itself; another under their spelling is a namesake until
    a listener joins the two — then the page is drawn of both records, and the namesake's music stays theirs."""
    from datetime import date

    from app.api import catalog
    from app.catalog.models import ArtistPage, CatalogAlbum, CatalogArtist, CatalogTrack
    from app.db import session_scope
    from app.library import artists
    from app.library.owned import annotate_tracks

    def page(id_, name, fans, albums, top=()):
        a = CatalogArtist(ref=f"dz:{id_}", source="dz", id=str(id_), name=name, nb_fans=fans)
        return ArtistPage(artist=a, albums=[CatalogAlbum(ref=f"dz:al{n}", source="dz", id=f"al{n}", title=title, artist=name, artist_ref=a.ref,
                                                         record_type="album", release_date=date(year, 1, 1)) for n, title, year in albums],
                          top_tracks=[CatalogTrack(ref=f"dz:t{n}", source="dz", id=f"t{n}", title=title, artist=name, artists=[name],
                                                   artist_refs=[a.ref], artist_ref=a.ref, rank=rank) for n, title, rank in top])

    pages = {"dz:1": page(1, "ЛСП", 1000, [(1, "Искра", 2026), (2, "Общий", 2020)], [(1, "Новое", 5)]),
             "dz:2": page(2, "LSP", 10, [(3, "Magic City", 2015), (4, "Obschiy", 2020)], [(2, "Staroe", 9)]),
             "dz:3": page(3, "LSP", 500, [(5, "Boots and Suits", 2026)], [(3, "Boots", 7)])}

    async def get_artist_page(ref):
        return pages[ref]

    monkeypatch.setattr(catalog, "get_artist_page", get_artist_page)
    c = env["client"]
    one = (await c.get("/api/catalog/artist/dz:1")).json()
    two = (await c.get("/api/catalog/artist/dz:2")).json()  # «LSP»: nobody's spelling yet — an artist of its own
    assert (one["artist"]["name"], one["profiles"], two["artist"]["name"], two["profiles"]) == ("ЛСП", ["dz:1"], "LSP", ["dz:2"])
    a = (await c.get("/api/artists/of", params={"ref": "dz:1"})).json()
    b = (await c.get("/api/artists/of", params={"ref": "dz:2"})).json()
    assert [x["name"] for x in a["suggestions"]] == ["LSP"] and a["suggestions"][0]["ref"] == "dz:2"
    r = await c.post(f"/api/artists/{a['id']}/merge", json={"other_id": b["id"]})
    assert r.status_code == 200 and r.json()["profiles"] == ["dz:1", "dz:2"], r.text

    # one page, whichever record it is opened by: the name here, the releases of both, the followers of both
    for ref in ("dz:1", "dz:2"):
        d = (await c.get(f"/api/catalog/artist/{ref}")).json()
        assert (d["artist"]["name"], d["artist"]["ref"], d["artist"]["nb_fans"], d["profiles"]) == ("ЛСП", "dz:1", 1010, ["dz:1", "dz:2"])
        # each record as the catalog itself shows it: its own name, followers and releases
        assert [(x["service"], x["ref"], x["name"], x["followers"], x["releases"], x["url"]) for x in d["sources"]] == [
            ("deezer", "dz:1", "ЛСП", 1000, 2, "https://www.deezer.com/artist/1"), ("deezer", "dz:2", "LSP", 10, 2, "https://www.deezer.com/artist/2")]
        assert {x["title"] for x in d["albums"]} == {"Искра", "Общий", "Magic City"}  # «Obschiy» of 2020 is «Общий»
        assert [t["title"] for t in d["top_tracks"]] == ["Staroe"] and d["top_tracks"][0]["artist"] == "ЛСП"

    # a third record under a spelling of theirs: not theirs by the name alone
    three = (await c.get("/api/catalog/artist/dz:3")).json()
    assert (three["artist"]["name"], three["profiles"]) == ("LSP", ["dz:3"])
    band = (await c.get("/api/artists/of", params={"ref": "dz:3"})).json()
    assert band["id"] != a["id"] and band["held_by"] == "ЛСП" and band["names"][0] == {"id": None, "name": "LSP", "source": "catalog", "main": True}
    assert [x["ref"] for x in (await c.get("/api/artists/of", params={"ref": "dz:1"})).json()["suggestions"]] == ["dz:3"]
    async with session_scope() as s:
        told = await annotate_tracks(s, [pages["dz:2"].top_tracks[0], pages["dz:3"].top_tracks[0]])
        assert [t.artist for t in told] == ["ЛСП", "LSP"]  # the artist's own record, and the namesake's
        cards = await artists.shown(s, [pages[r].artist for r in ("dz:2", "dz:1", "dz:3")])
        assert [(x.ref, x.name) for x in cards] == [("dz:1", "ЛСП"), ("dz:3", "LSP")]  # one card an artist
        # music that says only the name is the artist's whose spelling it is; the catalog's record says otherwise
        assert (await artists.credits(s, "LSP"))[0] == "ЛСП"
        assert (await artists.credits(s, "LSP", {"lsp": "dz:3"}))[0] == "LSP"
        await s.rollback()
    r = await c.post(f"/api/artists/{a['id']}/apart", json={"other_id": band["id"]})
    assert r.status_code == 200 and r.json()["suggestions"] == []

    # «Не включать» is of the whole artist: put on one record, it is of the other too — and not of the namesake
    r = await c.post("/api/dislikes", json={"artist_ref": "dz:2", "artist": "LSP"})
    assert r.status_code == 200 and set(r.json()["artist_refs"]) == {"dz:1", "dz:2", f"artist:{a['id']}"}, r.text
    assert sorted(r.json()["artist_names"]) == ["LSP", "ЛСП"]
    assert (await c.post("/api/dislikes", json={"artist_ref": "dz:1", "artist": "ЛСП"})).json()["id"] == r.json()["id"]  # one mark
    from sqlalchemy import select

    from app.mixes.taste import build_profile
    from app.models import User

    async with session_scope() as s:
        user = (await s.execute(select(User).limit(1))).scalars().first()
        prof = await build_profile(s, user)
        hid = lambda name, ref: prof.disliked(ref=None, nd_song_id=None, artist=name, artist_ref=ref, artists=[name], artist_refs=[ref])  # noqa: E731
        assert hid("ЛСП", "dz:1") and hid("LSP", "dz:2") and hid("lsp", None)
        assert not hid("LSP", "dz:3")  # the namesake's own record says it is somebody else
    assert (await c.delete("/api/dislikes", params={"artist_ref": "dz:1"})).json() == {"removed": 1}
    assert (await c.get("/api/dislikes")).json() == []


async def test_how_an_artist_looks_is_chosen_for_everyone(env, monkeypatch):
    """The round photo and the wide picture of an artist are chosen: among what is found, none, or one's own."""
    import io

    from PIL import Image

    from app.api import catalog
    from app.catalog import banners
    from app.catalog.models import ArtistPage, CatalogArtist

    def picture(size):
        out = io.BytesIO()
        Image.new("RGB", size, (20, 40, 200)).save(out, "PNG")
        return out.getvalue()

    page = ArtistPage(artist=CatalogArtist(ref="dz:1", source="dz", id="1", name="ЛСП", nb_fans=5,
                                           picture_url="https://img.test/big.jpg", picture_small_url="https://img.test/small.jpg"))

    async def get_artist_page(ref):
        return page.model_copy(deep=True)

    async def find(artist, titles):  # the wide picture is found by a name: one spelling has it, another has not
        return picture((1200, 500)) if artist.name == "ЛСП" else None

    async def url_of(artist, titles, wait_s=3.0):
        return "/found-by-itself"

    monkeypatch.setattr(catalog, "get_artist_page", get_artist_page)
    monkeypatch.setattr(banners, "find", find)
    monkeypatch.setattr(banners, "url_of", url_of)
    c = env["client"]

    async def shown():
        d = (await c.get("/api/catalog/artist/dz:1")).json()
        return d["artist"]["picture_url"], d["banner_url"]

    assert await shown() == ("https://img.test/big.jpg", "/found-by-itself")
    a = (await c.get("/api/artists/of", params={"ref": "dz:1"})).json()
    await c.post(f"/api/artists/{a['id']}/names", json={"name": "LSP"})
    looks = (await c.get(f"/api/artists/{a['id']}/looks")).json()
    assert (looks["photo"], looks["banner"]) == (None, None)
    assert [b["choice"] for b in looks["banners"]] == ["name:ЛСП", "name:LSP"]
    found = looks["banners"][0]["url"]
    assert (await c.get(found)).headers["content-type"] == "image/jpeg" and (await c.get(looks["banners"][1]["url"])).status_code == 404

    r = await c.put(f"/api/artists/{a['id']}/banner", json={"choice": "none"})
    assert r.status_code == 200 and r.json()["banner"] == "none" and (await shown())[1] is None
    assert (await c.put(f"/api/artists/{a['id']}/banner", json={"choice": "name:LSP"})).status_code == 422  # nothing by it
    assert (await c.put(f"/api/artists/{a['id']}/banner", json={"choice": "name:Чужой"})).status_code == 422  # not a spelling of theirs
    r = await c.put(f"/api/artists/{a['id']}/banner", json={"choice": "name:ЛСП"})
    assert r.status_code == 200, r.text
    url = (await shown())[1]
    assert url.startswith(f"/api/artists/{a['id']}/banner?v=") and (await c.get(url)).headers["content-type"] == "image/jpeg"
    r = await c.post(f"/api/artists/{a['id']}/banner", files={"file": ("b.png", picture((3000, 900)), "image/png")})
    assert r.status_code == 200 and r.json()["banner"] == "own" and "own" in [b["choice"] for b in r.json()["banners"]]
    assert Image.open(io.BytesIO((await c.get((await shown())[1])).content)).width == 1920  # kept no wider than a page

    r = await c.post(f"/api/artists/{a['id']}/photo", files={"file": ("p.png", picture((900, 900)), "image/png")})
    assert r.status_code == 200 and r.json()["photo"] == "own", r.text
    photo = (await shown())[0]
    assert photo.startswith(f"/api/artists/{a['id']}/photo?v=") and Image.open(io.BytesIO((await c.get(photo)).content)).size == (640, 640)
    assert (await c.post(f"/api/artists/{a['id']}/photo", files={"file": ("p.txt", b"not a picture", "text/plain")})).status_code == 422
    assert (await c.put(f"/api/artists/{a['id']}/photo", json={"choice": "profile:dz:999"})).status_code == 422
    # back to how it was by itself
    await c.put(f"/api/artists/{a['id']}/photo", json={"choice": None})
    await c.put(f"/api/artists/{a['id']}/banner", json={"choice": None})
    assert await shown() == ("https://img.test/big.jpg", "/found-by-itself")



async def test_yandexs_record_adds_releases_to_an_artists_page_and_not_its_most_listened(env, monkeypatch):
    """Yandex's list of an artist's most listened counts other people's tracks with them as a guest: it does not
    replace the leading catalog's word. Its releases join the page — one the page has already, once."""
    from datetime import date

    from app.api import catalog
    from app.catalog.models import ArtistPage, CatalogAlbum, CatalogArtist, CatalogTrack
    from app.services import svc

    def page(src, id_, fans, albums, top):
        a = CatalogArtist(ref=f"{src}:{id_}", source=src, id=str(id_), name="Автостопом", nb_fans=fans)
        return ArtistPage(
            artist=a,
            albums=[CatalogAlbum(ref=f"{src}:al{n}", source=src, id=f"al{n}", title=title, artist=a.name, artist_ref=a.ref,
                                 record_type=kind, release_date=date(2026, 1, 1)) for n, title, kind in albums],
            top_tracks=[CatalogTrack(ref=f"{src}:t{n}", source=src, id=f"t{n}", title=title, artist=who, artists=[who], rank=rank)
                        for n, title, who, rank in top])

    pages = {"dz:1": page("dz", 1, 2000, [(1, "ОКЕАНАРИУМ", "single"), (2, "Движение", "album")], [(1, "Давай поправимся", "Автостопом", 9)]),
             "ym:7": page("ym", 7, 200000, [(3, "ОКЕАНАРИУМ (prod. by Evil Xan)", "single"), (4, "Только в Яндексе", "album")],
                          [(2, "Утиль", "m0roks0n", None), (3, "Проблема", "Heart Sorrower", None)])}

    class Yandex:
        async def find_artist(self, name):
            return pages["ym:7"].artist

        async def get_artist(self, id_):
            return pages[f"ym:{id_}"]

        async def listeners(self, id_):
            return 4321

    async def get_artist_page(ref):
        return pages[ref]

    monkeypatch.setattr(catalog, "get_artist_page", get_artist_page)
    monkeypatch.setattr(svc, "yandex", Yandex())
    got = (await env["client"].get("/api/catalog/artist/dz:1")).json()
    assert got["profiles"] == ["dz:1", "ym:7"]  # tied: the same name, a release in common; Deezer's record leads
    assert [t["title"] for t in got["top_tracks"]] == ["Давай поправимся"]
    found_in = {a["title"]: a["found_in"] for a in [*got["albums"], *got["singles"]]}
    assert found_in == {"Движение": ["deezer"], "ОКЕАНАРИУМ": ["deezer", "yandex"], "Только в Яндексе": ["yandex"]}
    yandex = next(x for x in got["sources"] if x["service"] == "yandex")
    assert (yandex["listeners"], yandex["url"]) == (4321, "https://music.yandex.ru/artist/7")

    from app.db import session_scope
    from app.library import artists

    pages["sp:8"] = page("sp", 8, 0, [], [])
    async with session_scope() as s:
        who = await artists.by_ref(s, "dz:1")
        await artists.tie_profile(s, who.id, "sp:8")
        await s.commit()

    async def spotify_discography(id_):
        assert id_ == "8"
        return page("sp", 8, 0, [(9, "Движение", "album")], []).albums, False, False

    async def get_album(ref):
        return next(a for p in pages.values() for a in p.albums if a.ref == ref)

    monkeypatch.setattr(svc.spotify, "discography", spotify_discography)
    monkeypatch.setattr(catalog, "get_album", get_album)
    result = await env["client"].get("/api/catalog/artist/dz:1/discography")
    assert result.status_code == 200
    found_in["Движение"] = ["deezer", "spotify"]
    assert {a["title"]: a["found_in"] for a in result.json()["albums"]} == found_in
