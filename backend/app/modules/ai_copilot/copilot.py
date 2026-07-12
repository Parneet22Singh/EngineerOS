"""Copilot engine: retrieval-augmented coding Q&A over a local model.

Grounds answers in real repository code when a repo is supplied, and cites the files
it drew from. Provider-agnostic — talks through the shared AI layer, so it works with
a local llama.cpp server or a cloud key, whatever is configured.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from app.ai.provider import AIProvider
from app.modules.ai_copilot.retriever import gather_context

logger = logging.getLogger("engineeros.copilot")

REPO_SYSTEM = (
    "You are EngineerOS Copilot, a precise senior software engineer. Answer the "
    "developer's question using the provided repository context. Ground every claim in "
    "that context and cite files with backticks like `path/to/file.py`. If the context "
    "does not contain the answer, say so plainly instead of guessing. Be concise and concrete."
)
GENERAL_SYSTEM = (
    "You are EngineerOS Copilot, a precise senior software engineer. Answer the "
    "developer's coding question directly and concisely, with correct, runnable code "
    "when relevant."
)


@dataclass(slots=True)
class CopilotAnswer:
    text: str
    sources: list[str] = field(default_factory=list)
    grounded: bool = False
    error: str = ""


class Copilot:
    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    async def ask(
        self,
        question: str,
        *,
        repo: Path | None = None,
        max_files: int = 6,
        char_budget: int = 8000,
        max_tokens: int = 512,
    ) -> CopilotAnswer:
        if not self._provider.available:
            return CopilotAnswer(
                text="", error="No AI provider is available. Set AI_PROVIDER and start the model server.")

        context, sources = ("", [])
        if repo is not None:
            context, sources = gather_context(
                repo, question, max_files=max_files, char_budget=char_budget)

        if context:
            system = REPO_SYSTEM
            prompt = f"Repository context:\n\n{context}\n\n---\n\nQuestion: {question}"
        else:
            system = GENERAL_SYSTEM
            prompt = question

        try:
            text = await self._provider.complete(prompt, system=system, max_tokens=max_tokens)
        except Exception as exc:  # noqa: BLE001
            logger.exception("copilot completion failed")
            return CopilotAnswer(text="", sources=sources, error=str(exc))

        if not text.strip():
            return CopilotAnswer(
                text="", sources=sources,
                error="The model returned no output (is the AI server running and healthy?).")

        return CopilotAnswer(text=text.strip(), sources=sources, grounded=bool(context))
