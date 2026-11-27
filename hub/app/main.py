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
    import app.backup.restore
    import app.backup.service
    import app.imports.core
    import app.jobs.artists
    import app.jobs.downloads
    import app.mixes.core
    import app.mixes.discover
    import app.playlists_mirror
    import app.tidy.scan
    import app.checks.probes  # noqa: F401  (its job)


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
    from app import relay

    relay.start()  # the Hub as two processes (`hub_role`): what passes between them
    from app import outbound

    await outbound.start(sweeping=s.hub_role != "api")  # the proxies of outside requests, and their journal
    from app import checks

    checks.start()  # what the work says of each outside service (`app.checks`), written down every minute
    worker = s.hub_role != "api"  # the jobs and the scheduled loops; the api only answers

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

    if worker:
        async with session_scope() as names_s:  # every artist the library names is one the Hub knows
            await artist_names.seed(names_s)
    _import_handlers()
    pool = WorkerPool(s.cfg.jobs.workers)
    tasks: list[asyncio.Task] = []
    if worker and not s.disable_background:
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
        tasks.append(_loop_task(recovery_loop(), "import-recovery"))
        tasks.append(_loop_task(scheduler_loop(), "scheduler"))
        tasks.append(_loop_task(scrobbles_loop(), "scrobbles"))
        from app.library.cards import keep_fresh

        tasks.append(_loop_task(keep_fresh(), "track-cards"))
        tasks.append(_loop_task(schedule_loop(), "mixes"))
        from app.releases import loop as releases_loop

        tasks.append(_loop_task(releases_loop(), "releases"))
        from app.playlists_mirror import loop as playlists_loop

        tasks.append(_loop_task(playlists_loop(), "playlists"))
        from app.mixes.discover import loop as discoveries_loop

        tasks.append(_loop_task(discoveries_loop(), "discoveries"))
        tasks.append(_loop_task(for_you_loop(), "for_you"))
        from app.listenbrainz import loop as listenbrainz_loop

        tasks.append(_loop_task(listenbrainz_loop(), "listenbrainz"))
        if s.telegram_bot_token.strip():
            tasks.append(_loop_task(telegram_chats.poll(), "telegram"))
        tasks.append(_loop_task(_sync_tags(), "tags"))
        from app.genres import loop as genres_loop

        tasks.append(_loop_task(genres_loop(), "genres"))
        from app.artist_pages import loop as artist_pages_loop

        tasks.append(_loop_task(artist_pages_loop(), "artist-pages"))
        from app.library.tracks import fold_known

        async def fold_stubs() -> None:  # once: tracks without a file whose song the library has as another track
            if n := await fold_known():
                log.info("folded %s tracks without a file into the library's tracks of their songs", n)

        tasks.append(asyncio.create_task(fold_stubs(), name="fold-stubs"))
        from app.library.acoustic import loop as doubles_loop

        tasks.append(_loop_task(doubles_loop(), "doubles"))
        from app.updates import loop as updates_loop

        tasks.append(_loop_task(updates_loop(), "updates"))
        from app.checks.probes import loop as checks_loop

        tasks.append(_loop_task(checks_loop(), "service-checks"))
    from app.version import build, version

    log.info("hub %s (%s) started (%s)", version(), build()["commit"] or "?", s.hub_role)
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
        await checks.stop()
        await outbound.stop()
        await relay.stop()
        await db.dispose()
        await watchdog.close()


def _loop_task(coro, name: str) -> asyncio.Task:
    """A scheduled loop, its outside requests journaled as made for it (`app.outbound`)."""
    import contextvars

    from app.outbound import set_purpose

    ctx = contextvars.copy_context()
    ctx.run(set_purpose, f"loop {name}")
    return asyncio.create_task(coro, name=name, context=ctx)


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
    from app.api import (
        outbound as outbound_api,
    )
    from app.api import (
        version as version_api,
    )
    from app.api import checks as checks_api

    # no page that lists the Hub's commands: it is reached from the internet, and nobody but a guesser reads it
    from app.version import version as hub_version

    app = FastAPI(title="HomeMusic Hub", version=hub_version(), lifespan=lifespan, docs_url=None, openapi_url=None,
                  redoc_url=None)
    for r in (auth.router, search.router, catalog.router, downloads.router, listen.router, library.router, medialib.router, file_picker.router,
              uploads.router, playlists.router, plays.router, playback_queue.router, playback_devices.router, mixes.router, wave.router, smart_shuffle.router, imports.router, events.router, system.router, admin.router,
              artists.router, insights.router, telegram.router, lyrics.router, links.router, tags.router, stats.router, releases.router, quality.router, discoveries.router, listenbrainz.router, for_you.router, subsonic.router, integrations.router, tidy.router, backup.router, outbound_api.router, version_api.router, checks_api.router):
        app.include_router(r)
    app.include_router(integrations.callback_router)

    from app.etag import ETagMiddleware

    app.add_middleware(ETagMiddleware)  # a list read again unchanged does not cross the network again
    from app.outbound import PurposeMiddleware

    app.add_middleware(PurposeMiddleware)  # what a page asked the Hub for is what its outside requests were for
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
