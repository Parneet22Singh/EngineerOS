"""API Intelligence scan orchestrator.

Picks web-capture or repo-extraction based on the target (URL vs directory/git URL),
generates OpenAPI + Postman artifacts, and raises findings for undocumented, insecure,
or broken endpoints.
"""
from __future__ import annotations

import asyncio
import json
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
from app.core.browser import build_launch_kwargs
from app.core.event_bus import EventBus
from app.core.scan_runner import ProgressCb
from app.db.database import SessionLocal
from app.db.models import Finding, Scan, ScanStatus, Severity, SEVERITY_WEIGHT
from app.modules.api_intelligence import specgen
from app.modules.api_intelligence.discovery_repo import extract_repo_routes
from app.modules.api_intelligence.discovery_web import WebAPIDiscoverer
from app.modules.repo_intelligence.engine import _clone_error_hint
from app.modules.website_intelligence.results import RawFinding

logger = logging.getLogger("engineeros.api.engine")

GIT_URL_PREFIXES = ("http://", "https://", "git@", "ssh://")


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


class APIEngine:
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

    async def run(self, scan_id: str, target: str, options: dict) -> None:
        await self._update(scan_id, status=ScanStatus.running,
                           started_at=datetime.now(timezone.utc), stage="starting", progress=0.02)
        await self._bus.emit("scan.started", {"scan_id": scan_id, "target": target}, source="api_intelligence")

        is_url = target.startswith(("http://", "https://")) and not Path(target).exists()
        looks_repo = Path(target).is_dir() or target.startswith(("git@", "ssh://"))
        # http(s) that is a git host with a repo path -> treat as repo when mode=repo
        mode = options.get("mode")
        if mode == "repo" or (looks_repo and mode != "web"):
            source = "repo"
        elif is_url:
            source = "web"
        else:
            source = "repo"

        cleanup: Path | None = None
        try:
            if source == "web":
                endpoints, summary_extra, findings = await self._discover_web(scan_id, target, options)
            else:
                root, cleanup = await self._resolve_repo(scan_id, target)
                endpoints, summary_extra, findings = await self._discover_repo(scan_id, root, target)

            await self._progress(scan_id, "specgen", 0.85, "Generating OpenAPI + Postman")
            base_url = summary_extra.get("base_url", "")
            title = summary_extra.get("title", "Discovered API")
            openapi = specgen.build_openapi(title, base_url, endpoints)
            postman = specgen.build_postman(title, base_url, endpoints)

            artifacts_rel = self._write_artifacts(scan_id, openapi, postman)
            summary = self._build_summary(target, source, endpoints, summary_extra, findings, artifacts_rel)
            await self._persist(scan_id, findings, summary, len(endpoints))

            await self._update(scan_id, status=ScanStatus.completed, stage="completed", progress=1.0,
                               finished_at=datetime.now(timezone.utc))
            await self._progress_cb("completed", 1.0,
                                    f"{len(endpoints)} endpoint(s), {len(findings)} finding(s)")
            await self._bus.emit("scan.completed",
                                 {"scan_id": scan_id, "endpoints": len(endpoints)},
                                 source="api_intelligence")
        except Exception as exc:  # noqa: BLE001
            logger.exception("api scan %s failed", scan_id)
            await self._update(scan_id, status=ScanStatus.failed, stage="failed",
                               error=str(exc)[:1000], finished_at=datetime.now(timezone.utc))
            await self._bus.emit("scan.failed", {"scan_id": scan_id, "error": str(exc)},
                                 source="api_intelligence")
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
            await self._progress(scan_id, "api:clone", 0.05, f"Cloning {target}")
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

    async def _discover_web(self, scan_id, target, options):
        from urllib.parse import urlparse
        await self._progress(scan_id, "api:capture", 0.2, "Capturing network traffic")
        disc = WebAPIDiscoverer(target, timeout_ms=self._settings.crawl_timeout_ms,
                                max_pages=options.get("max_pages") or 4,
                                launch_kwargs=build_launch_kwargs(self._settings, target, options))
        eps = await disc.discover()
        parsed = urlparse(target)
        base_url = f"{parsed.scheme}://{parsed.netloc}"

        endpoints = [{
            "method": e.method, "path": e.path, "url": e.url, "params": [],
            "auth": e.auth, "status": e.status, "kind": e.kind,
            "summary": f"{e.method} {e.path}" + (f" [{e.kind}]" if e.kind != "REST" else ""),
        } for e in eps]

        findings = self._web_findings(eps)
        graphql = sorted({op for e in eps for op in e.graphql_ops})
        summary_extra = {
            "base_url": base_url, "title": f"{parsed.netloc} API",
            "endpoint_rows": [{
                "method": e.method, "path": e.path, "host": e.host, "kind": e.kind,
                "status": e.status, "auth": e.auth or "none",
                "same_origin": e.same_origin, "count": e.count,
            } for e in eps],
            "graphql_operations": graphql,
            "third_party": sorted({e.host for e in eps if not e.same_origin}),
        }
        return endpoints, summary_extra, findings

    async def _discover_repo(self, scan_id, root: Path, target):
        await self._progress(scan_id, "api:extract", 0.4, "Extracting routes from source")
        routes = await asyncio.to_thread(extract_repo_routes, root, 6000)
        endpoints = [{
            "method": r.method, "path": r.path, "url": r.path, "params": r.params,
            "auth": "Bearer" if r.auth_hint else "", "status": 200, "kind": "REST",
            "summary": f"{r.method} {r.path} ({r.framework})",
        } for r in routes]

        findings = self._repo_findings(routes)
        frameworks = sorted({r.framework for r in routes})
        summary_extra = {
            "base_url": "", "title": f"{root.name} API",
            "endpoint_rows": [{
                "method": r.method, "path": r.path, "host": "", "kind": "REST",
                "status": None, "auth": "auth" if r.auth_hint else "none",
                "same_origin": True, "count": 1, "framework": r.framework, "source": r.source,
            } for r in routes],
            "frameworks": frameworks,
            "graphql_operations": [],
            "third_party": [],
        }
        return endpoints, summary_extra, findings

    def _web_findings(self, eps) -> list[RawFinding]:
        findings: list[RawFinding] = []
        for e in eps:
            if not e.same_origin:
                continue
            if e.status and e.status >= 500:
                findings.append(RawFinding(
                    category="reliability", severity="high",
                    title=f"API returned {e.status}: {e.method} {e.path}",
                    page_url=e.url, description="A first-party API call returned a server error.",
                    recommendation="Investigate the failing endpoint.", element=f"{e.method} {e.path}", priority=2))
            elif e.status and e.status in (401, 403):
                continue  # auth-gated, expected
            elif e.status and e.status >= 400:
                findings.append(RawFinding(
                    category="reliability", severity="medium",
                    title=f"API returned {e.status}: {e.method} {e.path}",
                    page_url=e.url, description="A first-party API call returned a client error during normal use.",
                    recommendation="Verify the request contract; this fired during page load/navigation.",
                    element=f"{e.method} {e.path}", priority=3))
            if e.path.startswith("http://") or e.url.startswith("http://"):
                findings.append(RawFinding(
                    category="security", severity="high",
                    title=f"API called over plaintext HTTP: {e.path}",
                    page_url=e.url, description="An API request was made over unencrypted HTTP.",
                    recommendation="Serve and call all APIs over HTTPS.", element=e.url, priority=2))
            if e.method in ("POST", "PUT", "PATCH", "DELETE") and not e.auth:
                findings.append(RawFinding(
                    category="security", severity="low",
                    title=f"State-changing call without observed auth: {e.method} {e.path}",
                    page_url=e.url,
                    description="A mutating request carried no Authorization header or cookie in the captured traffic.",
                    recommendation="Confirm the endpoint enforces authentication and CSRF protection server-side.",
                    element=f"{e.method} {e.path}", priority=4))
        return findings

    def _repo_findings(self, routes) -> list[RawFinding]:
        findings: list[RawFinding] = []
        seen_paths: dict[tuple, list[str]] = {}
        for r in routes:
            seen_paths.setdefault((r.method, r.path), []).append(r.source)
            if r.method in ("POST", "PUT", "PATCH", "DELETE") and not r.auth_hint:
                findings.append(RawFinding(
                    category="security", severity="low",
                    title=f"State-changing route without visible auth: {r.method} {r.path}",
                    page_url=r.source,
                    description=f"No auth decorator/middleware detected near this {r.framework} route.",
                    recommendation="Confirm authentication/authorization is enforced for this endpoint.",
                    element=r.source, priority=4))
        for (method, path), sources in seen_paths.items():
            if len(sources) > 1:
                findings.append(RawFinding(
                    category="api-design", severity="low",
                    title=f"Duplicate route definition: {method} {path}",
                    page_url=sources[0],
                    description=f"Defined in {len(sources)} places: {', '.join(sources[:4])}.",
                    recommendation="Consolidate duplicate route handlers to avoid shadowing.",
                    element=path, priority=4))
        return findings

    def _write_artifacts(self, scan_id: str, openapi: dict, postman: dict) -> dict:
        base = self._settings.artifacts_dir / scan_id / "api"
        base.mkdir(parents=True, exist_ok=True)
        (base / "openapi.json").write_text(json.dumps(openapi, indent=2), encoding="utf-8")
        (base / "postman_collection.json").write_text(json.dumps(postman, indent=2), encoding="utf-8")
        return {
            "openapi": f"{scan_id}/api/openapi.json",
            "postman": f"{scan_id}/api/postman_collection.json",
        }

    def _build_summary(self, target, source, endpoints, extra, findings, artifacts) -> dict:
        by_severity = Counter(f.severity for f in findings)
        by_category = Counter(f.category for f in findings)
        penalties = (by_severity.get("critical", 0) * 15 + by_severity.get("high", 0) * 6
                     + by_severity.get("medium", 0) * 2 + by_severity.get("low", 0) * 0.5)
        health = max(0, round(100 - penalties))
        methods = Counter(e["method"] for e in endpoints)
        return {
            "target": target,
            "total_findings": len(findings),
            "by_severity": {k: by_severity.get(k, 0) for k in ("critical", "high", "medium", "low", "info")},
            "by_category": dict(by_category),
            "health_score": health,
            "lighthouse": {"skipped": True, "reason": "not applicable"},
            "api": {
                "source": source,
                "endpoint_count": len(endpoints),
                "by_method": dict(methods),
                "endpoints": extra["endpoint_rows"][:200],
                "frameworks": extra.get("frameworks", []),
                "graphql_operations": extra.get("graphql_operations", []),
                "third_party": extra.get("third_party", []),
                "base_url": extra.get("base_url", ""),
                "artifacts": artifacts,
            },
        }

    async def _persist(self, scan_id, findings, summary, endpoint_count) -> None:
        async with SessionLocal() as session:
            existing = await session.execute(select(Finding).where(Finding.scan_id == scan_id))
            for row in existing.scalars():
                await session.delete(row)
            for rf in sorted(findings, key=lambda f: (SEVERITY_WEIGHT.get(f.severity, 9), f.priority)):
                session.add(Finding(
                    scan_id=scan_id, category=rf.category, severity=Severity(rf.severity),
                    title=rf.title, description=rf.description, recommendation=rf.recommendation,
                    page_url=rf.page_url, element=rf.element, evidence=rf.evidence, priority=rf.priority))
            scan = await session.get(Scan, scan_id)
            if scan is not None:
                scan.summary = summary
                scan.pages_scanned = endpoint_count
            await session.commit()
