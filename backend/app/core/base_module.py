"""The common interface every EngineerOS module implements.

A module is a self-contained capability (website intelligence, repo intelligence,
API intelligence, ...). The plugin manager discovers modules, hands each one a
:class:`ModuleContext` (settings + event bus + shared services), and mounts its
router under the main API.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import APIRouter

from app.config import Settings

if TYPE_CHECKING:
    from app.ai.provider import AIProvider
    from app.core.event_bus import EventBus
    from app.core.scan_runner import ScanHub, ScanRegistry


@dataclass(slots=True)
class ModuleContext:
    """Everything a module needs from the core."""

    settings: Settings
    event_bus: "EventBus"
    ai: "AIProvider"
    scans: "ScanRegistry"
    hub: "ScanHub"


@dataclass(slots=True)
class ModuleInfo:
    """Static metadata a module advertises to the platform."""

    name: str  # machine name, e.g. "website_intelligence"
    title: str  # human title, e.g. "Universal Website Intelligence"
    version: str
    description: str
    capabilities: list[str]


class BaseModule(abc.ABC):
    """Base class for all pluggable modules."""

    def __init__(self, context: ModuleContext) -> None:
        self.context = context

    @property
    def settings(self) -> Settings:
        return self.context.settings

    @property
    def bus(self) -> "EventBus":
        return self.context.event_bus

    @property
    @abc.abstractmethod
    def info(self) -> ModuleInfo:
        """Return static metadata describing this module."""

    @abc.abstractmethod
    def router(self) -> APIRouter:
        """Return the FastAPI router to mount under ``/api``."""

    async def startup(self) -> None:  # pragma: no cover - optional hook
        """Optional async initialization (open pools, warm caches, subscribe)."""

    async def shutdown(self) -> None:  # pragma: no cover - optional hook
        """Optional async teardown."""
