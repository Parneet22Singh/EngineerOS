"""Async publish/subscribe event bus.

Modules communicate through events rather than direct calls, so a module can react
to another module's work without a compile-time dependency. The bus is intentionally
tiny: subscribe with a coroutine, publish an :class:`Event`, and every matching
handler is awaited concurrently.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("engineeros.eventbus")

Handler = Callable[["Event"], Awaitable[None]]


@dataclass(slots=True)
class Event:
    """A single message on the bus.

    ``topic`` is a dotted string (e.g. ``"scan.progress"``). ``payload`` carries
    arbitrary JSON-serializable data. ``source`` names the emitting module.
    """

    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    source: str = "core"


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)

    def subscribe(self, topic: str, handler: Handler) -> Callable[[], None]:
        """Register ``handler`` for ``topic``. Returns an unsubscribe callable.

        Use ``"*"`` to receive every event.
        """
        self._handlers[topic].append(handler)

        def _unsubscribe() -> None:
            try:
                self._handlers[topic].remove(handler)
            except ValueError:
                pass

        return _unsubscribe

    async def publish(self, event: Event) -> None:
        handlers = [*self._handlers.get(event.topic, ()), *self._handlers.get("*", ())]
        if not handlers:
            return
        results = await asyncio.gather(
            *(h(event) for h in handlers), return_exceptions=True
        )
        for result in results:
            if isinstance(result, Exception):
                logger.warning("event handler failed for %s: %r", event.topic, result)

    async def emit(self, topic: str, payload: dict[str, Any], source: str = "core") -> None:
        await self.publish(Event(topic=topic, payload=payload, source=source))
