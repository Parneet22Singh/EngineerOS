"""Shared scan infrastructure used by every analysis module.

The core owns the scan store, the REST/WebSocket API, and the reporting engine. Each
module that performs scans registers a :class:`ScanRunner` for its module name; the core
dispatches ``POST /api/scans`` to the right runner based on the request's ``module``
field. This is what lets Module 1 (website intelligence) and Module 6 (autonomous QA)
share one API, one scan history, and one reporting pipeline.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Awaitable, Callable, Protocol

# stage, progress (0..1), human-readable detail
ProgressCb = Callable[[str, float, str], Awaitable[None]]


class ScanRunner(Protocol):
    """A module's scan executor."""

    module_name: str

    async def run(self, scan_id: str, target: str, options: dict, progress: ProgressCb) -> None:
        """Execute the scan, persisting findings and updating the Scan row as it goes."""
        ...


class ScanRegistry:
    """Maps a module name to the runner that executes its scans."""

    def __init__(self) -> None:
        self._runners: dict[str, ScanRunner] = {}

    def register(self, runner: ScanRunner) -> None:
        self._runners[runner.module_name] = runner

    def get(self, module_name: str) -> ScanRunner | None:
        return self._runners.get(module_name)

    def names(self) -> list[str]:
        return list(self._runners)


class ScanHub:
    """In-memory progress fan-out for WebSocket clients.

    Each subscriber gets its own queue; publishing a progress frame copies it to every
    subscriber of that scan. Process-local — a multi-worker deployment would back this
    with Redis pub/sub (same shape, swappable).
    """

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, scan_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subs[scan_id].add(q)
        return q

    def unsubscribe(self, scan_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(scan_id)
        if subs:
            subs.discard(q)
            if not subs:
                self._subs.pop(scan_id, None)

    async def publish(self, scan_id: str, message: dict) -> None:
        for q in list(self._subs.get(scan_id, ())):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass  # slow consumer; drop intermediate frames
