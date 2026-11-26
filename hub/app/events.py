"""Pub/sub for private events and shared library notifications. The apps' event streams are the api process's: an
event the worker publishes goes there through the relay (`app.relay`)."""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue[str]]] = defaultdict(set)

    def subscribe(self, user_id: int) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self._subs[user_id].add(q)
        return q

    def unsubscribe(self, user_id: int, q: asyncio.Queue[str]) -> None:
        self._subs[user_id].discard(q)
        if not self._subs[user_id]:
            self._subs.pop(user_id, None)

    def publish(self, user_id: int, event: str, data: dict[str, Any]) -> None:
        from app import relay

        if relay.role() == "worker":
            return relay.send("event", user=user_id, event=event, data=data)
        self.deliver(user_id, event, data)

    def deliver(self, user_id: int, event: str, data: dict[str, Any]) -> None:
        msg = json.dumps({"event": event, "data": data}, ensure_ascii=False, default=str)
        for q in list(self._subs.get(user_id, ())):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # slow client: drop, it will refetch state on reconnect

    def broadcast(self, event: str, data: dict[str, Any]) -> None:
        from app import relay

        if relay.role() == "worker":
            return relay.send("event", user=None, event=event, data=data)
        for user_id in list(self._subs):
            self.deliver(user_id, event, data)


bus = EventBus()


def _relayed(msg: dict[str, Any]) -> None:
    if msg.get("user") is None:
        for user_id in list(bus._subs):
            bus.deliver(user_id, msg["event"], msg.get("data") or {})
    else:
        bus.deliver(int(msg["user"]), msg["event"], msg.get("data") or {})


def _register() -> None:
    from app import relay

    relay.on("event")(_relayed)


_register()
