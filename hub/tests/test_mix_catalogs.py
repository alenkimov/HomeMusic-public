import pytest
from sqlalchemy import select

from app import listen
from app.catalog.models import CatalogTrack
from app.db import session_scope
from app.mixes import build, core
from app.models import DownloadItem, Track, User
from tests.test_mixes import add_track, env, mix_rows  # noqa: F401


@pytest.mark.parametrize("weekly", [False, True], ids=["daily", "weekly"])
async def test_mix_downloads_spotify_and_yandex_recommendations(env, monkeypatch, weekly):
    monkeypatch.setattr(listen, "warm_light", lambda refs: None)
    library_id = await add_track(701, deezer=False)
    async with session_scope() as s:
        track = await s.get(Track, library_id)
        track.ref = "ym:701"

    def candidate(source: str, ident: str) -> build.Cand:
        artist = f"Исполнитель {ident}"
        return build.Cand(CatalogTrack(ref=f"{source}:{ident}", source=source, id=ident,
                                       title=f"Трек {ident}", artist=artist, artists=[artist],
                                       artist_refs=[f"{source}:artist{ident}"],
                                       album_ref=f"{source}:album{ident}", album_title=f"Альбом {ident}",
                                       duration_s=200), gens={"lastfm_similar": 1})

    picked = [candidate("ym", "701"), candidate("sp", "702"), candidate("ym", "703")]
    await build.link_library({c.track.ref: c for c in picked})
    assert picked[0].library_track_id == library_id
    if weekly:
        mix_id = await core.save_weekly(env["admin"], picked, issue_id=1, description="")
    else:
        async with session_scope() as s:
            mix = await core.save(s, await s.get(User, env["admin"]), build.Built(picked, [], "Микс", "", {}))
            mix_id = mix.id
        await core.sync(mix_id)

    rows = await mix_rows(mix_id)
    assert [row.ref for row in rows] == [c.track.ref for c in picked]
    assert all(row.state == "active" for row in rows)
    assert rows[0].download_item_id is None
    async with session_scope() as s:
        downloads = (await s.scalars(select(DownloadItem).order_by(DownloadItem.id))).all()
        assert [d.id for d in downloads] == [row.download_item_id for row in rows[1:]]
        for download, cand in zip(downloads, picked[1:], strict=True):
            assert core.quiet(download.query_json)
            meta = download.query_json["meta"]
            assert meta["track_ref"] == cand.track.ref
            assert meta["album_ref"] == cand.track.album_ref
            assert meta["artist_refs"] == cand.track.artist_refs
