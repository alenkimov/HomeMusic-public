"""HomeMusic Hub: FastAPI app (API + PWA static files) with in-process job workers."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db
from app.config import get_settings
from app.logging_setup import setup_logging

log = logging.getLogger("app")


def _import_handlers() -> None:
    # registering job handlers is a side effect of importing these modules
    import app.imports.core
    import app.jobs.artists
    import app.jobs.downloads
    import app.mixes.core
    import app.mixes.discover
    import app.playlists_mirror
    import app.tidy.scan
    import app.backup.service
    import app.backup.restore


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level)
    for d in (s.library_dir, s.staging_dir, s.trash_dir, s.backups_dir):
        d.mkdir(parents=True, exist_ok=True)
    from app import staging

    await asyncio.to_thread(staging.move_old)  # the working folder moved off the music folder: what was there comes
    await asyncio.to_thread(db.run_migrations)
    db.init_engine()
    from app.watchdog import watchdog

    watchdog.start()  # a loop held up is said in the log, with where

    from app import listen, telegram_chats
    from app.for_you import loop as for_you_loop
    from app.housekeeping import scheduler_loop
    from app.jobs.queue import WorkerPool, recover_stale
    from app.mixes import smart, wave
    from app.mixes.core import schedule_loop
    from app.plays import scrobbles_loop
    from app.services import svc

    svc.start()
    from app.catalog import namesakes

    await namesakes.load()  # what the catalog's client leaves out of an artist's releases
    from app.db import session_scope
    from app.library import artists as artist_names

    async with session_scope() as names_s:  # every artist the library names is one the Hub knows
        await artist_names.seed(names_s)
    _import_handlers()
    pool = WorkerPool(s.cfg.jobs.workers)
    tasks: list[asyncio.Task] = []
    if not s.disable_background:
        n = await recover_stale()
        if n:
            log.info("re-queued %s interrupted jobs", n)
        from app.jobs.downloads import recover_interrupted

        if n := await recover_interrupted():
            log.info("re-queued %s interrupted downloads", n)
        from app.imports.core import recovery_loop, resume_orphaned

        if n := await resume_orphaned():
            log.info("resumed %s interrupted transfers", n)
        pool.start()
        tasks.append(asyncio.create_task(recovery_loop(), name="import-recovery"))
        tasks.append(asyncio.create_task(scheduler_loop(), name="scheduler"))
        tasks.append(asyncio.create_task(scrobbles_loop(), name="scrobbles"))
        from app.library.cards import keep_fresh

        tasks.append(asyncio.create_task(keep_fresh(), name="track-cards"))
        tasks.append(asyncio.create_task(schedule_loop(), name="mixes"))
        from app.releases import loop as releases_loop

        tasks.append(asyncio.create_task(releases_loop(), name="releases"))
        from app.playlists_mirror import loop as playlists_loop

        tasks.append(asyncio.create_task(playlists_loop(), name="playlists"))
        from app.mixes.discover import loop as discoveries_loop

        tasks.append(asyncio.create_task(discoveries_loop(), name="discoveries"))
        tasks.append(asyncio.create_task(for_you_loop(), name="for_you"))
        from app.listenbrainz import loop as listenbrainz_loop

        tasks.append(asyncio.create_task(listenbrainz_loop(), name="listenbrainz"))
        if s.telegram_bot_token.strip():
            tasks.append(asyncio.create_task(telegram_chats.poll(), name="telegram"))
        tasks.append(asyncio.create_task(_sync_tags(), name="tags"))
        from app.genres import loop as genres_loop

        tasks.append(asyncio.create_task(genres_loop(), name="genres"))
    log.info("hub started")
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await pool.stop()
        await listen.reset()
        await wave.reset()
        await smart.reset()
        await svc.stop()
        await db.dispose()
        await watchdog.close()


async def _sync_tags() -> None:
    """The tags the Hub tells by itself, for the tracks that came while it was down (and all of them the first time)."""
    from app import tags as track_tags

    try:
        async with db.session_scope() as s:
            await track_tags.sync(s)
    except Exception:
        log.exception("tags: the sync at start failed")


class HubMark:
    """Every answer of the Hub says it is the Hub's (`X-HomeMusic-Hub`). The page tells by it a 502/503/504 of the
    Hub's own (a catalog or Navidrome behind it did not answer) from one the gateway on the VPS gives for a Hub it
    cannot reach — and only the latter means the Hub is out of reach (`web/src/lib/reach.ts`). A plain ASGI
    wrapper: the event stream and the audio pass through it as they are."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def marked(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"x-homemusic-hub", b"1")]
            await send(message)

        await self.app(scope, receive, marked)


def create_app() -> FastAPI:
    from app.api import (
        admin,
        artists,
        auth,
        backup,
        catalog,
        discoveries,
        downloads,
        events,
        file_picker,
        for_you,
        imports,
        insights,
        integrations,
        library,
        links,
        listen,
        listenbrainz,
        lyrics,
        medialib,
        mixes,
        playback_devices,
        playback_queue,
        playlists,
        plays,
        quality,
        releases,
        search,
        smart_shuffle,
        stats,
        subsonic,
        system,
        tags,
        telegram,
        tidy,
        uploads,
        wave,
    )

    # no page that lists the Hub's commands: it is reached from the internet, and nobody but a guesser reads it
    app = FastAPI(title="HomeMusic Hub", version="0.1.0", lifespan=lifespan, docs_url=None, openapi_url=None,
                  redoc_url=None)
    for r in (auth.router, search.router, catalog.router, downloads.router, listen.router, library.router, medialib.router, file_picker.router,
              uploads.router, playlists.router, plays.router, playback_queue.router, playback_devices.router, mixes.router, wave.router, smart_shuffle.router, imports.router, events.router, system.router, admin.router,
              artists.router, insights.router, telegram.router, lyrics.router, links.router, tags.router, stats.router, releases.router, quality.router, discoveries.router, listenbrainz.router, for_you.router, subsonic.router, integrations.router, tidy.router, backup.router):
        app.include_router(r)
    app.include_router(integrations.callback_router)

    app.add_middleware(HubMark)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Внутренняя ошибка сервера"}, status_code=500)

    static = get_settings().static_dir
    if (static / "index.html").is_file():
        if (static / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"detail": "Не найдено"}, status_code=404)
            f = (static / path).resolve()
            if path and f.is_file() and static.resolve() in f.parents:
                headers = {"Cache-Control": "no-cache"} if f.name in ("sw.js", "manifest.webmanifest") else None
                return FileResponse(f, headers=headers)
            return FileResponse(static / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_app()
