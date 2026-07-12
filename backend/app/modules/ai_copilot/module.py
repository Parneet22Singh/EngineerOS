"""Module 5 — AI Copilot: plugin registration.

Unlike the scan modules, Copilot is an interactive Q&A capability rather than a
ScanRunner, so it registers no scan runner — it is driven through the CLI `ask`/`chat`
commands (and could expose a router later). It surfaces in module discovery so the
platform reports it as a first-class module.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from app.core.base_module import BaseModule, ModuleInfo

logger = logging.getLogger("engineeros.ai_copilot")


class AICopilotModule(BaseModule):
    @property
    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name="ai_copilot",
            title="AI Copilot",
            version="0.1.0",
            description="Grounded, conversational coding assistant over a local or cloud model.",
            capabilities=[
                "code-qa", "repo-grounded-answers", "retrieval-augmented",
                "provider-agnostic", "local-model", "cli-chat",
            ],
        )

    async def startup(self) -> None:
        provider = getattr(self.context, "ai", None)
        name = getattr(provider, "name", "none")
        logger.info("ai_copilot ready (provider=%s)", name)

    def router(self) -> APIRouter:
        return APIRouter()
