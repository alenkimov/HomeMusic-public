"""A track's name and cover put right by a listener: for everyone, written down, with the way back."""

from __future__ import annotations

import io

from tests.test_plays import env as env  # the fixture


def picture() -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (40, 40), (200, 30, 30)).save(out, "PNG")
    return out.getvalue()


async def test_a_catalog_song_is_renamed_for_everyone_and_goes_back(env, monkeypatch):
    from sqlalchemy import select

    from app.catalog.models import CatalogTrack
    from app.db import session_scope
    from app.library.owned import annotate_tracks
    from app.mixes import resolve
    from app.models import Edit, Track

    c = env["client"]
    cat = CatalogTrack(ref="dz:55", source="dz", id="55", title="Группа крови (Remastered 2019)", artist="Кино",
                       artists=["Кино"], duration_s=245, cover_url="https://img.test/a.jpg")

    async def catalog_track(ref):
        return cat if ref == "dz:55" else None

    monkeypatch.setattr(resolve, "catalog_track", catalog_track)

    # the window opens on a song the Hub has not met: it becomes a track
    info = (await c.get("/api/library/track-edit", params={"ref": "dz:55"})).json()
    tid = info["track_id"]
    assert (info["title"], info["catalog_title"], info["renamed"], info["last_by"], info["has_file"]) == (
        "Группа крови (Remastered 2019)", "Группа крови (Remastered 2019)", False, None, False)
    assert (await c.get("/api/library/track-edit", params={"ref": "dz:404"})).status_code == 404

    r = await c.patch(f"/api/library/tracks/{tid}", json={"title": " Группа крови ", "artist": "Кино"})
    assert r.status_code == 200 and r.json()["title"] == "Группа крови"
    assert (await c.patch(f"/api/library/tracks/{tid}", json={"title": "  "})).status_code == 422  # a name there must be
    info = (await c.get("/api/library/track-edit", params={"track_id": tid})).json()
    assert (info["title"], info["catalog_title"], info["renamed"]) == ("Группа крови", "Группа крови (Remastered 2019)", True)
    assert info["last_by"] and info["last_at"]

    async with session_scope() as s:
        # a list the catalog answers shows the Hub's name of it
        out = (await annotate_tracks(s, [cat], 1))[0]
        assert (out.title, out.library_track_id) == ("Группа крови", tid)
        edits = list((await s.execute(select(Edit).order_by(Edit.id))).scalars())
        assert [(e.target_type, e.target_id, e.changes_json) for e in edits] == [
            ("track", tid, {"title": ["Группа крови (Remastered 2019)", "Группа крови"]})]
        assert (await s.get(Track, tid)).fingerprint  # found by its new name

    # the same name again changes nothing and writes nothing down
    await c.patch(f"/api/library/tracks/{tid}", json={"title": "Группа крови"})
    async with session_scope() as s:
        assert len(list((await s.execute(select(Edit))).scalars())) == 1

    # its own cover, and back to none
    r = await c.put(f"/api/library/tracks/{tid}/cover", files={"file": ("c.png", picture(), "image/png")})
    assert r.status_code == 200 and r.json()["own_cover"] and r.json()["cover_url"].startswith(f"/api/library/tracks/{tid}/cover?v=")
    got = await c.get(r.json()["cover_url"])
    assert got.status_code == 200 and got.headers["content-type"] == "image/jpeg"
    async with session_scope() as s:
        assert (await annotate_tracks(s, [cat], 1))[0].cover_url == r.json()["cover_url"]
    r = await c.delete(f"/api/library/tracks/{tid}/cover")
    assert not r.json()["own_cover"] and r.json()["cover_url"] == "https://img.test/a.jpg"
    assert (await c.get(f"/api/library/tracks/{tid}/cover")).status_code == 404
