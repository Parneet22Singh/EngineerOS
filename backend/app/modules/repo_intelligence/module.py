"""Module 2 — Universal Repository Intelligence: plugin registration."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.core.base_module import BaseModule, ModuleContext, ModuleInfo
from app.core.scan_runner import ProgressCb
from app.modules.repo_intelligence.engine import RepoEngine

logger = logging.getLogger("engineeros.repo_intelligence")


class RepoScanRunner:
    module_name = "repo_intelligence"

    def __init__(self, context: ModuleContext) -> None:
        self._context = context

    async def run(self, scan_id: str, target: str, options: dict, progress: ProgressCb) -> None:
        engine = RepoEngine(self._context.settings, self._context.event_bus, progress)
        await engine.run(scan_id, target, options)


class RepoIntelligenceModule(BaseModule):
    @property
    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name="repo_intelligence",
            title="Universal Repository Intelligence",
            version="0.1.0",
            description="Analyze a repository: stack, structure, dependency graph, smells, secrets.",
            capabilities=[
                "language-inventory", "framework-detection", "dependency-manifests",
                "entry-points", "structure-map", "import-graph", "circular-dependencies",
                "dead-code-candidates", "large-files", "long-functions",
                "secret-detection", "todo-debt",
                "html-report", "pdf-report", "json-report", "csv-export",
            ],
        )

    async def startup(self) -> None:
        self.context.scans.register(RepoScanRunner(self.context))
        logger.info("registered repo_intelligence scan runner")

    def router(self) -> APIRouter:
        return APIRouter()
