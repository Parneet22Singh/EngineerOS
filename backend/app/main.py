"""EngineerOS core engine — FastAPI application entrypoint.

Boots the shared services (settings, event bus, AI layer), discovers and mounts every
module through the plugin manager, initializes the database, and serves scan artifacts.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.ai.provider import build_provider
from app.api.scans import build_scan_router
from app.config import get_settings
from app.core.base_module import ModuleContext
from app.core.event_bus import EventBus
from app.core.plugin_manager import PluginManager
from app.core.scan_runner import ScanHub, ScanRegistry
from app.db.database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("engineeros")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    await init_db()

    bus = EventBus()
    ai = build_provider(settings)
    context = ModuleContext(
        settings=settings,
        event_bus=bus,
        ai=ai,
        scans=app.state.registry,
        hub=app.state.hub,
    )

    manager = PluginManager(context)
    manager.discover()
    manager.mount_all(app)
    await manager.startup_all()  # modules register their scan runners here

    app.state.settings = settings
    app.state.bus = bus
    app.state.plugins = manager
    logger.info(
        "EngineerOS %s ready — %d module(s), AI provider: %s",
        __version__, len(manager.modules), ai.name,
    )
    try:
        yield
    finally:
        await manager.shutdown_all()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="EngineerOS",
        version=__version__,
        description="The AI operating system for software engineers.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/artifacts", StaticFiles(directory=str(settings.artifacts_dir)), name="artifacts")

    # Shared scan infrastructure — created here so the router can close over it; the
    # registry is populated when modules start up (during lifespan).
    app.state.registry = ScanRegistry()
    app.state.hub = ScanHub()
    app.include_router(build_scan_router(settings, app.state.registry, app.state.hub), prefix="/api")

    @app.get("/", include_in_schema=False)
    async def root() -> RedirectResponse:
        # The API lives under /api/*; send bare-URL visitors to the interactive docs.
        return RedirectResponse(url="/docs")

    @app.get("/api/health", tags=["core"])
    async def health() -> dict:
        manager: PluginManager = app.state.plugins
        return {
            "status": "ok",
            "version": __version__,
            "modules": [m.name for m in manager.infos()],
            "scan_modules": app.state.registry.names(),
        }

    @app.get("/api/modules", tags=["core"])
    async def modules() -> list[dict]:
        manager: PluginManager = app.state.plugins
        scannable = set(app.state.registry.names())
        return [
            {
                "name": i.name,
                "title": i.title,
                "version": i.version,
                "description": i.description,
                "capabilities": i.capabilities,
                "scannable": i.name in scannable,
            }
            for i in manager.infos()
        ]

    return app


app = create_app()
