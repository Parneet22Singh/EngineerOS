"""Discovers, instantiates, and manages the lifecycle of modules.

Modules live under ``app.modules.<name>`` and expose a top-level ``MODULE`` class
(a :class:`BaseModule` subclass). The manager imports each package, instantiates the
class with a shared :class:`ModuleContext`, and tracks it. Adding a module is a matter
of dropping a new package in — the core never changes.
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType

from fastapi import FastAPI

from app.core.base_module import BaseModule, ModuleContext, ModuleInfo

logger = logging.getLogger("engineeros.plugins")


class PluginManager:
    def __init__(self, context: ModuleContext) -> None:
        self._context = context
        self._modules: dict[str, BaseModule] = {}

    @property
    def modules(self) -> dict[str, BaseModule]:
        return dict(self._modules)

    def discover(self) -> None:
        """Import every package under ``app.modules`` and register its ``MODULE``."""
        import app.modules as modules_pkg

        for mod_info in pkgutil.iter_modules(modules_pkg.__path__):
            if not mod_info.ispkg:
                continue
            self._load_package(f"app.modules.{mod_info.name}")

    def _load_package(self, dotted_path: str) -> None:
        try:
            package: ModuleType = importlib.import_module(dotted_path)
        except Exception:  # noqa: BLE001 — one bad module must not sink the platform
            logger.exception("failed to import module package %s", dotted_path)
            return

        module_cls = getattr(package, "MODULE", None)
        if module_cls is None or not issubclass(module_cls, BaseModule):
            logger.warning("%s has no BaseModule 'MODULE' export; skipping", dotted_path)
            return

        try:
            instance: BaseModule = module_cls(self._context)
        except Exception:  # noqa: BLE001
            logger.exception("failed to instantiate module %s", dotted_path)
            return

        self._modules[instance.info.name] = instance
        logger.info("loaded module: %s (%s)", instance.info.title, instance.info.name)

    def mount_all(self, app: FastAPI) -> None:
        for module in self._modules.values():
            app.include_router(module.router(), prefix="/api")

    def infos(self) -> list[ModuleInfo]:
        return [m.info for m in self._modules.values()]

    async def startup_all(self) -> None:
        for module in self._modules.values():
            await module.startup()

    async def shutdown_all(self) -> None:
        for module in self._modules.values():
            try:
                await module.shutdown()
            except Exception:  # noqa: BLE001
                logger.exception("error during shutdown of %s", module.info.name)
