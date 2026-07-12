"""Autonomous QA scan orchestrator.

Runs the interaction explorer, optionally Lighthouse, aggregates findings into a
severity-categorized enterprise report, and persists everything into the shared
Scan/Finding tables so the report pipeline and future intelligence layer can use it.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from playwright.async_api import async_playwright
from sqlalchemy import select

from app.config import Settings
from app.core.browser import build_launch_kwargs
from app.core.event_bus import EventBus
from app.core.scan_runner import ProgressCb
from app.db.database import SessionLocal
from app.db.models import Finding, Scan, ScanStatus, Severity, SEVERITY_WEIGHT
from app.modules.autonomous_qa.explorer import Explorer, Flow
from app.modules.website_intelligence.lighthouse import run_lighthouse
from app.modules.website_intelligence.results import PageResult, RawFinding

logger = logging.getLogger("engineeros.qa.engine")


class QAEngine:
    def __init__(self, settings: Settings, bus: EventBus, progress_cb: ProgressCb) -> None:
        self._settings = settings
        self._bus = bus
        self._progress = progress_cb

    async def _update(self, scan_id: str, **fields) -> None:
        async with SessionLocal() as session:
            scan = await session.get(Scan, scan_id)
            if scan is None:
                return
            for key, value in fields.items():
                setattr(scan, key, value)
            await session.commit()

    async def _progress_and_store(self, scan_id: str, stage: str, progress: float, detail: str = "") -> None:
        await self._update(scan_id, stage=stage, progress=round(progress, 3))
        await self._progress(stage, progress, detail)

    async def run(self, scan_id: str, target: str, options: dict) -> None:
        settings = self._settings
        max_actions = options.get("max_actions") or 18
        run_lh = options.get("run_lighthouse")
        run_lh = settings.enable_lighthouse if run_lh is None else run_lh

        async def progress(stage: str, prog: float, detail: str = "") -> None:
            await self._progress_and_store(scan_id, stage, prog, detail)

        await self._update(
            scan_id, status=ScanStatus.running, started_at=datetime.now(timezone.utc),
            stage="starting", progress=0.02,
        )
        await self._bus.emit("scan.started", {"scan_id": scan_id, "target": target}, source="autonomous_qa")

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(**build_launch_kwargs(settings, target, options))
                try:
                    explorer = Explorer(
                        browser,
                        artifacts_dir=settings.artifacts_dir,
                        scan_id=scan_id,
                        entry_url=target,
                        timeout_ms=settings.crawl_timeout_ms,
                        max_actions=max_actions,
                        progress_cb=progress,
                    )
                    page, flows, stats = await explorer.explore()
                finally:
                    await browser.close()

            findings: list[RawFinding] = list(page.findings)

            scores: dict = {"skipped": True, "reason": "disabled"}
            if run_lh:
                await progress("lighthouse", 0.82, "Running Lighthouse")
                lh_findings, scores = await run_lighthouse(target, binary=settings.lighthouse_bin)
                findings.extend(lh_findings)

            await progress("report", 0.92, "Compiling QA report")
            summary = self._build_summary(target, page, flows, stats, findings, scores)
            await self._persist(scan_id, findings, summary)

            await self._update(
                scan_id, status=ScanStatus.completed, stage="completed", progress=1.0,
                finished_at=datetime.now(timezone.utc), pages_scanned=1,
            )
            await progress("completed", 1.0,
                           f"{len(findings)} findings · {stats['actions_performed']} interactions")
            await self._bus.emit(
                "scan.completed",
                {"scan_id": scan_id, "findings": len(findings), "actions": stats["actions_performed"]},
                source="autonomous_qa",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("QA scan %s failed", scan_id)
            await self._update(
                scan_id, status=ScanStatus.failed, stage="failed",
                error=str(exc)[:1000], finished_at=datetime.now(timezone.utc),
            )
            await self._bus.emit("scan.failed", {"scan_id": scan_id, "error": str(exc)}, source="autonomous_qa")

    def _build_summary(
        self, target: str, page: PageResult, flows: list[Flow], stats: dict,
        findings: list[RawFinding], scores: dict,
    ) -> dict:
        by_severity = Counter(f.severity for f in findings)
        by_category = Counter(f.category for f in findings)
        penalties = (
            by_severity.get("critical", 0) * 15 + by_severity.get("high", 0) * 6
            + by_severity.get("medium", 0) * 2 + by_severity.get("low", 0) * 0.5
        )
        health = max(0, round(100 - penalties))
        page_summary = {
            "url": page.url,
            "final_url": page.final_url,
            "status_code": page.status_code,
            "title": page.title,
            "depth": 0,
            "load_ms": page.load_ms,
            "findings": len(page.findings),
            "error": page.error,
            "screenshots": [
                {"viewport": s.viewport, "path": s.path, "width": s.width, "height": s.height, "label": s.label}
                for s in page.screenshots
            ],
        }
        return {
            "target": target,
            "pages_scanned": 1,
            "total_findings": len(findings),
            "actions_performed": stats["actions_performed"],
            "element_counts": stats["counts"],
            "by_severity": {k: by_severity.get(k, 0) for k in ("critical", "high", "medium", "low", "info")},
            "by_category": dict(by_category),
            "lighthouse": scores,
            "health_score": health,
            "pages": [page_summary],
            "flows": [{"action": f.action, "label": f.label, "result": f.result, "issue": f.issue} for f in flows],
        }

    async def _persist(self, scan_id: str, findings: list[RawFinding], summary: dict) -> None:
        async with SessionLocal() as session:
            existing = await session.execute(select(Finding).where(Finding.scan_id == scan_id))
            for row in existing.scalars():
                await session.delete(row)
            for rf in sorted(findings, key=lambda f: (SEVERITY_WEIGHT.get(f.severity, 9), f.priority)):
                session.add(
                    Finding(
                        scan_id=scan_id, category=rf.category, severity=Severity(rf.severity),
                        title=rf.title, description=rf.description, recommendation=rf.recommendation,
                        page_url=rf.page_url, element=rf.element, evidence=rf.evidence, priority=rf.priority,
                    )
                )
            scan = await session.get(Scan, scan_id)
            if scan is not None:
                scan.summary = summary
                scan.pages_scanned = 1
            await session.commit()
