"""NDJSON responses that report source activity while a request is running."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

EmitProgress = Callable[[str, object], None]
SourceState = Literal["searching", "ready", "empty", "error"]
log = logging.getLogger(__name__)


def progress_stream(work: Callable[[EmitProgress], Awaitable[BaseModel]], error: str) -> StreamingResponse:
    async def events():
        queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()

        def emit(kind: str, data: object) -> None:
            queue.put_nowait((kind, data))

        async def run() -> None:
            try:
                result = await work(emit)
            except HTTPException as exc:
                emit("error", exc.detail if isinstance(exc.detail, str) else error)
            except Exception:
                log.exception("progress stream failed")
                emit("error", error)
            else:
                emit("done", result)

        task = asyncio.create_task(run())
        try:
            while True:
                kind, data = await queue.get()
                if isinstance(data, BaseModel):
                    data = data.model_dump(mode="json")
                yield json.dumps({"type": kind, "data": data}, ensure_ascii=False) + "\n"
                if kind in ("done", "error"):
                    break
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(events(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no"})


class SourceProgress:
    """Combine concurrent work and a pending background job into one status per service."""

    def __init__(self, emit: EmitProgress):
        self.emit = emit
        self.active: dict[str, int] = {}
        self.states: dict[str, SourceState] = {}
        self.outcomes: dict[str, SourceState] = {}
        self.pending: set[str] = set()

    def _state(self, service: str, state: SourceState) -> None:
        if self.states.get(service) != state:
            self.states[service] = state
            self.emit("source", {"service": service, "state": state})

    async def follow[T](self, service: str, work: Awaitable[T], outcome: Callable[[T], SourceState]) -> T:
        self.active[service] = self.active.get(service, 0) + 1
        self._state(service, "searching")
        result_state: SourceState = "error"
        try:
            result = await work
            result_state = outcome(result)
            return result
        except HTTPException as exc:
            if exc.status_code == 404:
                result_state = "empty"
            raise
        finally:
            if result_state == "searching":
                self.pending.add(service)
            else:
                priority = {"empty": 0, "ready": 1, "error": 2}
                previous = self.outcomes.get(service, "empty")
                if priority[result_state] > priority[previous]:
                    self.outcomes[service] = result_state
            self.active[service] -= 1
            if not self.active[service] and service not in self.pending:
                self._state(service, self.outcomes.get(service, "empty"))
