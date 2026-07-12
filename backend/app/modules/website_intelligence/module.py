"""Module 1 — Universal Website Intelligence.

Registers a :class:`ScanRunner` with the core so the shared ``/api/scans`` API can
dispatch ``module="website_intelligence"`` scans to its crawl/audit engine.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.core.base_module import BaseModule, ModuleContext, ModuleInfo
from app.core.scan_runner import ProgressCb
from app.modules.website_intelligence.engine import ScanEngine

logger = logging.getLogger("engineeros.website_intelligence")


class WebsiteScanRunner:
    module_name = "website_intelligence"

    def __init__(self, context: ModuleContext) -> None:
        self._context = context

    async def run(self, scan_id: str, target: str, options: dict, progress: ProgressCb) -> None:
        engine = ScanEngine(self._context.settings, self._context.event_bus, progress_cb=progress)
        await engine.run(scan_id, target, options)


class WebsiteIntelligenceModule(BaseModule):
    @property
    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name="website_intelligence",
            title="Universal Website Intelligence",
            version="0.1.0",
            description="Crawl, audit, and QA any public website; generate enterprise reports.",
            capabilities=[
                "crawl", "screenshots", "accessibility", "seo", "responsive",
                "broken-links", "console-errors", "network-failures", "lighthouse",
                "html-report", "pdf-report", "json-report", "csv-export",
            ],
        )

    async def startup(self) -> None:
        self.context.scans.register(WebsiteScanRunner(self.context))
        logger.info("registered website_intelligence scan runner")

    def router(self) -> APIRouter:
        # This module contributes no extra routes; scans go through the core API.
        return APIRouter()
