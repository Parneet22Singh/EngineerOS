"""Knowledge Graph scan orchestrator.

Builds the structural graph deterministically, then uses the shared AI layer to add a
one-sentence role summary to the most-connected components. AI work is strictly
budget-capped (top-N nodes, short outputs) because a local model is slow; if no AI
provider is configured the graph is still produced, just without summaries.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import stat
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.ai.provider import build_provider
from app.config import Settings
from app.core.event_bus import EventBus
from app.core.scan_runner import ProgressCb
from app.db.database import SessionLocal
from app.db.models import Scan, ScanStatus
from app.modules.knowledge_graph.builder import build_graph
from app.modules.repo_intelligence.engine import _clone_error_hint

logger = logging.getLogger("engineeros.kg.engine")

GIT_URL_PREFIXES = ("http://", "https://", "git@", "ssh://")

SUMMARY_SYSTEM = (
    "You are a senior engineer mapping a codebase. Given one source file, reply with a "
    "SINGLE concise sentence describing its role. No preamble, no lists, no code."
)


def _rmtree_force(path: Path) -> None:
    def onexc(func, p, _exc) -> None:
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    try:
        shutil.rmtree(path, onexc=onexc)
    except OSError:
        logger.warning("could not remove temp dir %s", path)


def _snippet(path: Path, max_chars: int = 1600) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # Prefer signal-bearing lines (defs/classes/exports) plus the file head.
    lines = text.splitlines()
    head = lines[:40]
    signals = [ln for ln in lines
               if ln.lstrip().startswith(("def ", "class ", "export ", "function ",
                                          "public ", "func ", "const ", "type "))][:25]
    combined = "\n".join(head + (["", "# key declarations:"] if signals else []) + signals)
    return combined[:max_chars]


class KnowledgeGraphEngine:
    def __init__(self, settings: Settings, bus: EventBus, progress_cb: ProgressCb) -> None:
        self._settings = settings
        self._bus = bus
        self._progress_cb = progress_cb
        self._ai = build_provider(settings)

    async def _update(self, scan_id: str, **fields) -> None:
        async with SessionLocal() as session:
            scan = await session.get(Scan, scan_id)
            if scan is None:
                return
            for key, value in fields.items():
                setattr(scan, key, value)
            await session.commit()

    async def _progress(self, scan_id: str, stage: str, prog: float, detail: str = "") -> None:
        await self._update(scan_id, stage=stage, progress=round(prog, 3))
        await self._progress_cb(stage, prog, detail)

    async def run(self, scan_id: str, target: str, options: dict) -> None:
        max_files = options.get("max_files") or 5000
        max_nodes = options.get("max_nodes") or 12
        await self._update(scan_id, status=ScanStatus.running,
                           started_at=datetime.now(timezone.utc), stage="starting", progress=0.02)
        await self._bus.emit("scan.started", {"scan_id": scan_id, "target": target}, source="knowledge_graph")

        cleanup: Path | None = None
        try:
            root, cleanup = await self._resolve_repo(scan_id, target)

            await self._progress(scan_id, "kg:build", 0.15, "Building component graph")
            kg = await asyncio.to_thread(build_graph, root, max_files)

            # AI summaries for the top-N most-connected components.
            summarized = 0
            if self._ai.available and kg.nodes:
                top = kg.nodes[:max_nodes]
                for i, node in enumerate(top):
                    await self._progress(
                        scan_id, "kg:summarize", 0.25 + 0.65 * (i / max(len(top), 1)),
                        f"Summarizing {node.id}")
                    snippet = await asyncio.to_thread(_snippet, root / node.id)
                    if not snippet.strip():
                        continue
                    prompt = f"File: {node.id}\n\n```\n{snippet}\n```"
                    try:
                        text = await self._ai.complete(prompt, system=SUMMARY_SYSTEM, max_tokens=80)
                    except Exception:  # noqa: BLE001
                        text = ""
                    node.summary = " ".join(text.split()).strip()
                    if node.summary:
                        summarized += 1

            await self._progress(scan_id, "kg:artifact", 0.92, "Writing graph.json")
            artifact_rel = self._write_artifact(scan_id, kg)
            summary = self._build_summary(target, kg, artifact_rel, summarized)
            await self._persist(scan_id, summary, len(kg.nodes))

            await self._update(scan_id, status=ScanStatus.completed, stage="completed", progress=1.0,
                               finished_at=datetime.now(timezone.utc))
            await self._progress_cb("completed", 1.0,
                                    f"{len(kg.nodes)} components, {summarized} summarized")
            await self._bus.emit("scan.completed",
                                 {"scan_id": scan_id, "nodes": len(kg.nodes)}, source="knowledge_graph")
        except Exception as exc:  # noqa: BLE001
            logger.exception("knowledge_graph scan %s failed", scan_id)
            await self._update(scan_id, status=ScanStatus.failed, stage="failed",
                               error=str(exc)[:1000], finished_at=datetime.now(timezone.utc))
            await self._bus.emit("scan.failed", {"scan_id": scan_id, "error": str(exc)},
                                 source="knowledge_graph")
        finally:
            if cleanup is not None:
                _rmtree_force(cleanup)

    async def _resolve_repo(self, scan_id: str, target: str) -> tuple[Path, Path | None]:
        local = Path(target)
        if local.is_dir():
            return local.resolve(), None
        if target.startswith(GIT_URL_PREFIXES):
            dest = self._settings.artifacts_dir / ".repos" / uuid.uuid4().hex[:12]
            dest.parent.mkdir(parents=True, exist_ok=True)
            await self._progress(scan_id, "kg:clone", 0.05, f"Cloning {target}")
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", "--single-branch", target, str(dest),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode != 0:
                raise RuntimeError(_clone_error_hint(target, stderr.decode(errors="replace")))
            return dest, dest
        raise RuntimeError(f"target is neither a directory nor a git URL: {target}")

    def _write_artifact(self, scan_id: str, kg) -> str:
        base = self._settings.artifacts_dir / scan_id / "knowledge_graph"
        base.mkdir(parents=True, exist_ok=True)
        doc = {
            "nodes": [{"id": n.id, "language": n.language, "lines": n.lines,
                       "in_degree": n.in_degree, "out_degree": n.out_degree,
                       "summary": n.summary} for n in kg.nodes],
            "edges": [{"source": s, "target": t} for s, t in kg.edges],
            "cycles": kg.cycles,
        }
        (base / "graph.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
        return f"{scan_id}/knowledge_graph/graph.json"

    def _build_summary(self, target, kg, artifact_rel, summarized) -> dict:
        components = [{
            "id": n.id, "language": n.language, "lines": n.lines,
            "in_degree": n.in_degree, "out_degree": n.out_degree, "summary": n.summary,
        } for n in kg.nodes[:40]]
        return {
            "target": target,
            "total_findings": 0,
            "by_severity": {k: 0 for k in ("critical", "high", "medium", "low", "info")},
            "by_category": {},
            "health_score": None,
            "knowledge_graph": {
                "node_count": len(kg.nodes),
                "edge_count": len(kg.edges),
                "cycle_count": len(kg.cycles),
                "cycles": kg.cycles[:10],
                "languages": kg.languages,
                "frameworks": kg.frameworks,
                "entry_points": kg.entry_points,
                "components": components,
                "summarized": summarized,
                "ai_provider": self._ai.name if self._ai.available else "none",
                "artifact": artifact_rel,
            },
        }

    async def _persist(self, scan_id, summary, node_count) -> None:
        async with SessionLocal() as session:
            scan = await session.get(Scan, scan_id)
            if scan is not None:
                scan.summary = summary
                scan.pages_scanned = node_count
            await session.commit()
