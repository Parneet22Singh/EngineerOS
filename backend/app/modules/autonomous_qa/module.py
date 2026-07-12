"""Module 6 — Autonomous QA Agent.

Registers a :class:`ScanRunner` for ``module="autonomous_qa"`` with the core so the
shared ``/api/scans`` API can dispatch autonomous exploration scans to it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.core.base_module import BaseModule, ModuleContext, ModuleInfo
from app.core.scan_runner import ProgressCb
from app.modules.autonomous_qa.engine import QAEngine

logger = logging.getLogger("engineeros.autonomous_qa")


class QAScanRunner:
    module_name = "autonomous_qa"

    def __init__(self, context: ModuleContext) -> None:
        self._context = context

    async def run(self, scan_id: str, target: str, options: dict, progress: ProgressCb) -> None:
        engine = QAEngine(self._context.settings, self._context.event_bus, progress)
        await engine.run(scan_id, target, options)


class AutonomousQAModule(BaseModule):
    @property
    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name="autonomous_qa",
            title="Autonomous QA Agent",
            version="0.1.0",
            description="Given only a URL, autonomously explores and tests a site, then writes a QA report.",
            capabilities=[
                "autonomous-exploration", "click-buttons", "open-menus", "fill-forms",
                "submit-forms", "detect-modals", "dialog-handling", "runtime-error-detection",
                "accessibility", "screenshots", "lighthouse",
                "html-report", "pdf-report", "json-report", "csv-export",
            ],
        )

    async def startup(self) -> None:
        self.context.scans.register(QAScanRunner(self.context))
        logger.info("registered autonomous_qa scan runner")

    def router(self) -> APIRouter:
        return APIRouter()
