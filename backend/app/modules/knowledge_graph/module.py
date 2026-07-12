"""Module 4 — Knowledge Graph: plugin registration."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.core.base_module import BaseModule, ModuleContext, ModuleInfo
from app.core.scan_runner import ProgressCb
from app.modules.knowledge_graph.engine import KnowledgeGraphEngine

logger = logging.getLogger("engineeros.knowledge_graph")


class KnowledgeGraphScanRunner:
    module_name = "knowledge_graph"

    def __init__(self, context: ModuleContext) -> None:
        self._context = context

    async def run(self, scan_id: str, target: str, options: dict, progress: ProgressCb) -> None:
        engine = KnowledgeGraphEngine(self._context.settings, self._context.event_bus, progress)
        await engine.run(scan_id, target, options)


class KnowledgeGraphModule(BaseModule):
    @property
    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name="knowledge_graph",
            title="Knowledge Graph",
            version="0.1.0",
            description="Semantic map of a repo's components and their relationships, with AI summaries.",
            capabilities=[
                "component-graph", "import-relationships", "connectivity-metrics",
                "ai-component-summaries", "cycle-detection", "graph-json-export",
                "html-report", "provider-agnostic", "local-model",
            ],
        )

    async def startup(self) -> None:
        self.context.scans.register(KnowledgeGraphScanRunner(self.context))
        logger.info("registered knowledge_graph scan runner")

    def router(self) -> APIRouter:
        return APIRouter()
