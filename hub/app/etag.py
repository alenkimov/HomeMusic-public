"""What a page reads of the Hub says which version it is (`ETag`); asked again with that version
(`If-None-Match`) and unchanged, the answer is a bare «304 Not Modified»: the list of thousands of tracks a transfer's
events make a page read again does not cross the network again when nothing in it changed. The browser asks so by
itself (`Cache-Control: no-cache`: always asked, never taken stale) and hands the page the body it kept.

Only the JSON answers to GETs of /api, read whole; the event stream, audio and files pass as they are."""

from __future__ import annotations

import hashlib

MAX_BYTES = 16 * 2**20  # an answer larger than this passes untouched


class ETagMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "GET" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        start: dict | None = None
        chunks: list[bytes] = []
        size = 0
        passing = False

        async def buffered(message):
            nonlocal start, size, passing
            if passing:
                return await send(message)
            if message["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in message.get("headers", [])}
                kind = headers.get(b"content-type", b"")
                if message["status"] != 200 or not kind.startswith(b"application/json") or b"etag" in headers \
                        or b"no-store" in headers.get(b"cache-control", b""):
                    passing = True
                    return await send(message)
                start = message
                return
            if message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))
                size += len(chunks[-1])
                if size > MAX_BYTES:
                    passing = True
                    await send(start)
                    await send({"type": "http.response.body", "body": b"".join(chunks), "more_body": message.get("more_body", False)})
                    return
                if message.get("more_body"):
                    return
                body = b"".join(chunks)
                tag = b'W/"' + hashlib.blake2b(body, digest_size=12).hexdigest().encode() + b'"'
                headers = [(k, v) for k, v in start.get("headers", []) if k.lower() not in (b"content-length", b"etag")]
                if not any(k.lower() == b"cache-control" for k, _ in headers):
                    headers.append((b"cache-control", b"no-cache, private"))
                headers.append((b"etag", tag))
                asked = dict((k.lower(), v) for k, v in scope.get("headers", [])).get(b"if-none-match", b"")
                if tag in [t.strip() for t in asked.split(b",")]:
                    await send({"type": "http.response.start", "status": 304, "headers": headers})
                    return await send({"type": "http.response.body", "body": b""})
                headers.append((b"content-length", str(len(body)).encode()))
                await send({**start, "headers": headers})
                return await send({"type": "http.response.body", "body": body})
            await send(message)

        await self.app(scope, receive, buffered)
