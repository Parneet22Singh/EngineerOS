"""Module 3 — Universal API Intelligence: plugin registration."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.core.base_module import BaseModule, ModuleContext, ModuleInfo
from app.core.scan_runner import ProgressCb
from app.modules.api_intelligence.engine import APIEngine

logger = logging.getLogger("engineeros.api_intelligence")


class APIScanRunner:
    module_name = "api_intelligence"

    def __init__(self, context: ModuleContext) -> None:
        self._context = context

    async def run(self, scan_id: str, target: str, options: dict, progress: ProgressCb) -> None:
        engine = APIEngine(self._context.settings, self._context.event_bus, progress)
        await engine.run(scan_id, target, options)


class APIIntelligenceModule(BaseModule):
    @property
    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name="api_intelligence",
            title="Universal API Intelligence",
            version="0.1.0",
            description="Discover APIs from a live site or a repo; generate OpenAPI + Postman.",
            capabilities=[
                "network-capture", "rest-discovery", "graphql-detection", "auth-detection",
                "route-extraction", "openapi-generation", "postman-generation",
                "broken-endpoint-detection", "insecure-endpoint-detection", "duplicate-routes",
                "html-report", "pdf-report", "json-report", "csv-export",
            ],
        )

    async def startup(self) -> None:
        self.context.scans.register(APIScanRunner(self.context))
        logger.info("registered api_intelligence scan runner")

    def router(self) -> APIRouter:
        return APIRouter()
