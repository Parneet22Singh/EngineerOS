"""Scan orchestrator for Module 1.

Ties the crawler, link checker, and Lighthouse runner together, persists findings,
maintains the ``Scan`` row's live progress/status, and emits progress events so the
WebSocket layer can stream them to the UI.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Awaitable, Callable
from urllib.parse import urlparse

from playwright.async_api import async_playwright
from sqlalchemy import select

from app.config import Settings
from app.core.browser import build_launch_kwargs
from app.core.event_bus import EventBus
from app.db.database import SessionLocal
from app.db.models import Finding, Scan, ScanStatus, Severity, SEVERITY_WEIGHT
from app.modules.website_intelligence.crawler import Crawler
from app.modules.website_intelligence.lighthouse import run_lighthouse
from app.modules.website_intelligence.linkcheck import check_links
from app.modules.website_intelligence.results import PageResult, RawFinding

logger = logging.getLogger("engineeros.engine")

ProgressCb = Callable[[str, float, str], Awaitable[None]]


class ScanEngine:
    def __init__(self, settings: Settings, bus: EventBus, progress_cb: ProgressCb | None = None) -> None:
        self._settings = settings
        self._bus = bus
        self._external_cb = progress_cb

    async def _update(self, scan_id: str, **fields) -> None:
        async with SessionLocal() as session:
            scan = await session.get(Scan, scan_id)
            if scan is None:
                return
            for key, value in fields.items():
                setattr(scan, key, value)
            await session.commit()

    def _make_progress(self, scan_id: str) -> ProgressCb:
        async def _cb(stage: str, progress: float, detail: str = "") -> None:
            await self._update(scan_id, stage=stage, progress=round(progress, 3))
            payload = {"scan_id": scan_id, "stage": stage, "progress": round(progress, 3), "detail": detail}
            await self._bus.emit("scan.progress", payload, source="website_intelligence")
            if self._external_cb:
                await self._external_cb(stage, progress, detail)

        return _cb

    async def run(self, scan_id: str, url: str, options: dict) -> None:
        progress = self._make_progress(scan_id)
        settings = self._settings
        max_pages = options.get("max_pages") or settings.crawl_max_pages
        max_depth = options.get("max_depth") if options.get("max_depth") is not None else settings.crawl_max_depth
        respect_robots = (
            options.get("respect_robots") if options.get("respect_robots") is not None else settings.respect_robots
        )
        run_lh = options.get("run_lighthouse")
        run_lh = settings.enable_lighthouse if run_lh is None else run_lh
        check_external = options.get("check_external_links", True)

        await self._update(
            scan_id,
            status=ScanStatus.running,
            started_at=datetime.now(timezone.utc),
            stage="starting",
            progress=0.02,
        )
        await self._bus.emit("scan.started", {"scan_id": scan_id, "target": url}, source="website_intelligence")

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(**build_launch_kwargs(settings, url, options))
                try:
                    crawler = Crawler(
                        browser,
                        artifacts_dir=settings.artifacts_dir,
                        scan_id=scan_id,
                        max_pages=max_pages,
                        max_depth=max_depth,
                        timeout_ms=settings.crawl_timeout_ms,
                        respect_robots=respect_robots,
                        progress_cb=progress,
                    )
                    pages = await crawler.crawl(url)
                finally:
                    await browser.close()

            all_findings: list[RawFinding] = []
            for page in pages:
                all_findings.extend(page.findings)

            # --- Link checking ---
            await progress("linkcheck", 0.62, "Checking links")
            all_links: set[str] = set()
            link_sources: dict[str, list[str]] = {}
            for page in pages:
                src = page.final_url or page.url
                for link in page.links:
                    all_links.add(link)
                    srcs = link_sources.setdefault(link, [])
                    if src not in srcs:
                        srcs.append(src)
            link_findings = await check_links(
                all_links,
                origin=url,
                check_external=check_external,
                concurrency=settings.crawl_concurrency,
                sources=link_sources,
            )
            all_findings.extend(link_findings)

            # --- Lighthouse ---
            scores: dict = {}
            if run_lh:
                await progress("lighthouse", 0.78, "Running Lighthouse")
                lh_findings, scores = await run_lighthouse(url, binary=settings.lighthouse_bin)
                all_findings.extend(lh_findings)
            else:
                scores = {"skipped": True, "reason": "disabled"}

            # --- Aggregate + persist ---
            await progress("report", 0.92, "Compiling report")
            summary = self._build_summary(url, pages, all_findings, scores)
            await self._persist(scan_id, all_findings, summary, pages_scanned=len(pages))

            await self._update(
                scan_id,
                status=ScanStatus.completed,
                stage="completed",
                progress=1.0,
                finished_at=datetime.now(timezone.utc),
            )
            await progress("completed", 1.0, f"{len(all_findings)} findings across {len(pages)} pages")
            await self._bus.emit(
                "scan.completed",
                {"scan_id": scan_id, "findings": len(all_findings), "pages": len(pages)},
                source="website_intelligence",
            )
        except Exception as exc:  # noqa: BLE001 — record failure, never crash the worker
            logger.exception("scan %s failed", scan_id)
            await self._update(
                scan_id,
                status=ScanStatus.failed,
                stage="failed",
                error=str(exc)[:1000],
                finished_at=datetime.now(timezone.utc),
            )
            await self._bus.emit("scan.failed", {"scan_id": scan_id, "error": str(exc)}, source="website_intelligence")

    def _build_summary(
        self, url: str, pages: list[PageResult], findings: list[RawFinding], scores: dict
    ) -> dict:
        by_severity = Counter(f.severity for f in findings)
        by_category = Counter(f.category for f in findings)
        page_summaries = [
            {
                "url": p.url,
                "final_url": p.final_url,
                "status_code": p.status_code,
                "title": p.title,
                "depth": p.depth,
                "load_ms": p.load_ms,
                "findings": len(p.findings),
                "error": p.error,
                "screenshots": [
                    {"viewport": s.viewport, "path": s.path, "width": s.width, "height": s.height}
                    for s in p.screenshots
                ],
            }
            for p in pages
        ]
        # A simple 0-100 health score: start at 100, subtract weighted penalties.
        penalties = (
            by_severity.get("critical", 0) * 15
            + by_severity.get("high", 0) * 6
            + by_severity.get("medium", 0) * 2
            + by_severity.get("low", 0) * 0.5
        )
        health = max(0, round(100 - penalties))
        return {
            "target": url,
            "origin": f"{urlparse(url).scheme}://{urlparse(url).netloc}",
            "pages_scanned": len(pages),
            "total_findings": len(findings),
            "by_severity": {k: by_severity.get(k, 0) for k in ("critical", "high", "medium", "low", "info")},
            "by_category": dict(by_category),
            "lighthouse": scores,
            "health_score": health,
            "pages": page_summaries,
        }

    async def _persist(
        self, scan_id: str, findings: list[RawFinding], summary: dict, pages_scanned: int
    ) -> None:
        async with SessionLocal() as session:
            # Clear any prior findings (idempotent re-runs).
            existing = await session.execute(select(Finding).where(Finding.scan_id == scan_id))
            for row in existing.scalars():
                await session.delete(row)

            for rf in sorted(findings, key=lambda f: (SEVERITY_WEIGHT.get(f.severity, 9), f.priority)):
                session.add(
                    Finding(
                        scan_id=scan_id,
                        category=rf.category,
                        severity=Severity(rf.severity),
                        title=rf.title,
                        description=rf.description,
                        recommendation=rf.recommendation,
                        page_url=rf.page_url,
                        element=rf.element,
                        evidence=rf.evidence,
                        priority=rf.priority,
                    )
                )
            scan = await session.get(Scan, scan_id)
            if scan is not None:
                scan.summary = summary
                scan.pages_scanned = pages_scanned
            await session.commit()
