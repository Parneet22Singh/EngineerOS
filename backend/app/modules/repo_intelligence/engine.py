"""Repository Intelligence scan orchestrator.

Resolves the target (local path, or git URL shallow-cloned into the artifacts area),
runs inventory + import-graph + smell analysis, converts results into shared Findings,
and persists a summary the reporting pipeline can render.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.config import Settings
from app.core.event_bus import EventBus
from app.core.scan_runner import ProgressCb
from app.db.database import SessionLocal
from app.db.models import Finding, Scan, ScanStatus, Severity, SEVERITY_WEIGHT
from app.modules.repo_intelligence import graph as graphmod
from app.modules.repo_intelligence import smells
from app.modules.repo_intelligence.inventory import build_inventory
from app.modules.website_intelligence.results import RawFinding

logger = logging.getLogger("engineeros.repo.engine")

GIT_URL_PREFIXES = ("http://", "https://", "git@", "ssh://")


def _clone_error_hint(target: str, stderr: str) -> str:
    """Turn a raw git-clone failure into a concise, actionable message."""
    low = stderr.lower()
    if "repository not found" in low or "not found" in low or "authentication failed" in low \
            or "could not read username" in low or "permission denied" in low:
        return (
            f"Cannot access '{target}'. This usually means the repo is PRIVATE and git has "
            "no credentials here. Fastest fix: clone it locally with your normal git access, "
            "then scan the folder — e.g.  eos scan D:\\path\\to\\your-repo -m repo. "
            "(Or configure Git Credential Manager / gh auth login so git can clone it.)"
        )
    return f"git clone failed: {stderr[:300]}"


def _rmtree_force(path: Path) -> None:
    """rmtree that also removes read-only files (git object packs on Windows)."""

    def onexc(func, p, _exc) -> None:
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass

    try:
        shutil.rmtree(path, onexc=onexc)
    except OSError:
        logger.warning("could not fully remove clone dir %s", path)


class RepoEngine:
    def __init__(self, settings: Settings, bus: EventBus, progress_cb: ProgressCb) -> None:
        self._settings = settings
        self._bus = bus
        self._progress_cb = progress_cb

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

    async def _resolve_target(self, scan_id: str, target: str) -> tuple[Path, Path | None]:
        """Return (repo_root, tempdir_to_cleanup)."""
        local = Path(target)
        if local.exists() and local.is_dir():
            return local.resolve(), None
        if target.startswith(GIT_URL_PREFIXES):
            dest = self._settings.artifacts_dir / ".repos" / uuid.uuid4().hex[:12]
            dest.parent.mkdir(parents=True, exist_ok=True)
            await self._progress(scan_id, "repo:clone", 0.05, f"Cloning {target}")
            # GIT_TERMINAL_PROMPT=0 so a private repo fails fast instead of hanging on a
            # username/password prompt in this non-interactive context.
            env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
            proc = await asyncio.create_subprocess_exec(
                "git", "clone", "--depth", "1", "--single-branch", target, str(dest),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode != 0:
                raise RuntimeError(_clone_error_hint(target, stderr.decode(errors="replace")))
            return dest, dest
        raise RuntimeError(f"target is neither an existing directory nor a git URL: {target}")

    async def run(self, scan_id: str, target: str, options: dict) -> None:
        max_files = options.get("max_files") or 5000
        await self._update(scan_id, status=ScanStatus.running,
                           started_at=datetime.now(timezone.utc), stage="starting", progress=0.02)
        await self._bus.emit("scan.started", {"scan_id": scan_id, "target": target}, source="repo_intelligence")

        cleanup: Path | None = None
        try:
            root, cleanup = await self._resolve_target(scan_id, target)

            await self._progress(scan_id, "repo:inventory", 0.2, "Scanning files and manifests")
            inv = await asyncio.to_thread(build_inventory, root, max_files)

            await self._progress(scan_id, "repo:graph", 0.45, "Building import graph")
            rels = [f.relative_to(root).as_posix() for f in inv.files]
            igraph = await asyncio.to_thread(graphmod.build_import_graph, root, inv.files)
            cycles = graphmod.find_cycles(igraph)
            dead = graphmod.dead_file_candidates(igraph, rels)

            await self._progress(scan_id, "repo:smells", 0.7, "Detecting smells and secrets")
            findings: list[RawFinding] = []
            findings += await asyncio.to_thread(smells.scan_secrets, root, inv.files)
            findings += smells.scan_env_files(root)
            findings += smells.scan_sizes(root, inv.line_counts)
            findings += await asyncio.to_thread(smells.scan_python_functions, root, inv.files)
            todo_findings, todo_total = await asyncio.to_thread(smells.scan_todos, root, inv.files)
            findings += todo_findings

            for cycle in cycles:
                findings.append(RawFinding(
                    category="architecture",
                    severity="medium",
                    title=f"Circular dependency ({len(cycle) - 1} modules)",
                    page_url=cycle[0],
                    description=" → ".join(cycle),
                    recommendation="Break the cycle by extracting the shared piece into its own module or inverting one dependency.",
                    evidence={"cycle": cycle},
                    priority=3,
                ))
            if dead:
                findings.append(RawFinding(
                    category="dead-code",
                    severity="low",
                    title=f"{len(dead)} file(s) appear unreferenced",
                    page_url=dead[0],
                    description="No repo-internal import points at these files (entry points, tests, and scripts excluded). Heuristic — verify before deleting.",
                    recommendation="Confirm each is unused (dynamic imports, plugins, docs) and remove it.",
                    evidence={"files": dead[:25]},
                    priority=4,
                ))

            await self._progress(scan_id, "report", 0.9, "Compiling repository report")
            summary = self._build_summary(target, root, inv, igraph, cycles, dead, todo_total, findings)
            await self._persist(scan_id, findings, summary, inv.total_files)

            await self._update(scan_id, status=ScanStatus.completed, stage="completed", progress=1.0,
                               finished_at=datetime.now(timezone.utc))
            await self._progress_cb("completed", 1.0,
                                    f"{len(findings)} findings across {inv.total_files} files")
            await self._bus.emit("scan.completed",
                                 {"scan_id": scan_id, "findings": len(findings), "files": inv.total_files},
                                 source="repo_intelligence")
        except Exception as exc:  # noqa: BLE001
            logger.exception("repo scan %s failed", scan_id)
            await self._update(scan_id, status=ScanStatus.failed, stage="failed",
                               error=str(exc)[:1000], finished_at=datetime.now(timezone.utc))
            await self._bus.emit("scan.failed", {"scan_id": scan_id, "error": str(exc)},
                                 source="repo_intelligence")
        finally:
            if cleanup is not None:
                _rmtree_force(cleanup)

    def _build_summary(self, target, root, inv, igraph, cycles, dead, todo_total, findings) -> dict:
        by_severity = Counter(f.severity for f in findings)
        by_category = Counter(f.category for f in findings)
        penalties = (
            by_severity.get("critical", 0) * 15 + by_severity.get("high", 0) * 6
            + by_severity.get("medium", 0) * 2 + by_severity.get("low", 0) * 0.5
        )
        health = max(0, round(100 - penalties))
        edge_count = sum(len(v) for v in igraph.values())
        dep_counts = {manifest: len(names) for manifest, names in inv.dependencies.items()}
        return {
            "target": target,
            "total_findings": len(findings),
            "by_severity": {k: by_severity.get(k, 0) for k in ("critical", "high", "medium", "low", "info")},
            "by_category": dict(by_category),
            "health_score": health,
            "lighthouse": {"skipped": True, "reason": "not applicable"},
            "repo": {
                "root_name": root.name,
                "total_files": inv.total_files,
                "total_lines": inv.total_lines,
                "size_mb": round(inv.total_bytes / 1_000_000, 1),
                "languages": inv.languages[:10],
                "frameworks": inv.frameworks,
                "dependency_counts": dep_counts,
                "top_dependencies": {m: names[:15] for m, names in inv.dependencies.items()},
                "entry_points": inv.entry_points,
                "structure": inv.structure,
                "graph": {"nodes": len(inv.files), "edges": edge_count,
                          "cycles": len(cycles), "dead_candidates": len(dead)},
                "todo_markers": todo_total,
            },
        }

    async def _persist(self, scan_id: str, findings: list[RawFinding], summary: dict, files: int) -> None:
        async with SessionLocal() as session:
            existing = await session.execute(select(Finding).where(Finding.scan_id == scan_id))
            for row in existing.scalars():
                await session.delete(row)
            for rf in sorted(findings, key=lambda f: (SEVERITY_WEIGHT.get(f.severity, 9), f.priority)):
                session.add(Finding(
                    scan_id=scan_id, category=rf.category, severity=Severity(rf.severity),
                    title=rf.title, description=rf.description, recommendation=rf.recommendation,
                    page_url=rf.page_url, element=rf.element, evidence=rf.evidence, priority=rf.priority,
                ))
            scan = await session.get(Scan, scan_id)
            if scan is not None:
                scan.summary = summary
                scan.pages_scanned = files
            await session.commit()
