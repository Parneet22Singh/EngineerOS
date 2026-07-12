"""Playwright-based crawler.

Given a start URL it: reads robots.txt (optionally respecting it), seeds from
sitemap.xml, then breadth-first crawls same-origin pages up to the configured page/depth
limits. For each page it captures the HTTP status, load time, console errors, failed
network requests, desktop/tablet/mobile screenshots, and a full DOM/a11y/SEO audit.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Awaitable, Callable
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import Browser, Error as PlaywrightError

from app.modules.website_intelligence.page_audit import AUDIT_JS, findings_from_audit
from app.modules.website_intelligence.results import PageResult, RawFinding, Screenshot

logger = logging.getLogger("engineeros.crawler")

ProgressCb = Callable[[str, float, str], Awaitable[None]]

VIEWPORTS = {
    "desktop": (1366, 900),
    "tablet": (768, 1024),
    "mobile": (390, 844),
}


def _normalize(url: str) -> str:
    """Drop the fragment and trailing slash noise so we don't crawl duplicates."""
    url, _ = urldefrag(url)
    return url.rstrip("/") or url


def _reg_host(netloc: str) -> str:
    """Fold a leading www. so apex and www are treated as the same site."""
    netloc = netloc.lower()
    return netloc[4:] if netloc.startswith("www.") else netloc


def _same_origin(a: str, b: str) -> bool:
    """Same site, treating http/https and www/apex as equivalent.

    A bare entry URL (e.g. ``example.com``) very often redirects to ``www.example.com``,
    and all internal links then point at the www host. Comparing on the exact netloc
    would reject every internal link and strand the crawl on a single page, so we
    compare on the registrable host and ignore the scheme.
    """
    pa, pb = urlparse(a), urlparse(b)
    return _reg_host(pa.netloc) == _reg_host(pb.netloc)


# Extensions that are not HTML pages — auditing them as web pages produces nonsense
# (e.g. an XML sitemap has no <title>/<h1>). Keep them out of the crawl queue.
NON_HTML_EXT = (
    ".xml", ".pdf", ".zip", ".gz", ".tar", ".rar", ".7z", ".jpg", ".jpeg", ".png",
    ".gif", ".svg", ".webp", ".avif", ".ico", ".css", ".js", ".mjs", ".json", ".txt",
    ".csv", ".xlsx", ".xls", ".doc", ".docx", ".ppt", ".pptx", ".mp4", ".webm", ".mov",
    ".mp3", ".wav", ".woff", ".woff2", ".ttf", ".eot", ".rss", ".atom",
)


def _looks_html(url: str) -> bool:
    return not urlparse(url).path.lower().endswith(NON_HTML_EXT)


class Crawler:
    def __init__(
        self,
        browser: Browser,
        *,
        artifacts_dir,
        scan_id: str,
        max_pages: int,
        max_depth: int,
        timeout_ms: int,
        respect_robots: bool,
        progress_cb: ProgressCb | None = None,
    ) -> None:
        self._browser = browser
        self._artifacts_dir = artifacts_dir
        self._scan_id = scan_id
        self._max_pages = max_pages
        self._max_depth = max_depth
        self._timeout_ms = timeout_ms
        self._respect_robots = respect_robots
        self._progress_cb = progress_cb
        self._robots: RobotFileParser | None = None

    async def _progress(self, stage: str, progress: float, detail: str = "") -> None:
        if self._progress_cb:
            await self._progress_cb(stage, progress, detail)

    async def _load_robots(self, start_url: str) -> None:
        parsed = urlparse(start_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = RobotFileParser()
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(robots_url)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                    self._robots = rp
        except Exception as exc:  # noqa: BLE001
            logger.info("robots.txt unavailable for %s: %r", robots_url, exc)

    def _allowed(self, url: str) -> bool:
        if not self._respect_robots or self._robots is None:
            return True
        try:
            return self._robots.can_fetch("*", url)
        except Exception:  # noqa: BLE001
            return True

    async def _sitemap_urls(self, start_url: str) -> list[str]:
        parsed = urlparse(start_url)
        candidates = [f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"]
        if self._robots and getattr(self._robots, "site_maps", None):
            try:
                candidates = list(self._robots.site_maps()) + candidates
            except Exception:  # noqa: BLE001
                pass
        found: list[str] = []
        try:
            async with httpx.AsyncClient(
                timeout=15, follow_redirects=True,
                headers={"user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")},
            ) as client:
                for sm in candidates[:3]:
                    found.extend(await self._fetch_sitemap(client, sm, start_url, depth=0))
                    if found:
                        break
        except Exception as exc:  # noqa: BLE001
            logger.info("sitemap fetch failed: %r", exc)
        # De-dup, keep only same-site HTML pages (never sub-sitemaps or asset files).
        seen: set[str] = set()
        out: list[str] = []
        for u in found:
            n = _normalize(u)
            if n not in seen and _same_origin(n, start_url) and _looks_html(n):
                seen.add(n)
                out.append(n)
        return out

    async def _fetch_sitemap(self, client, sm_url: str, start_url: str, depth: int) -> list[str]:
        """Return page URLs from a sitemap, recursing one level into sitemap indexes."""
        try:
            resp = await client.get(sm_url)
        except Exception:  # noqa: BLE001
            return []
        if resp.status_code != 200:
            return []
        ctype = resp.headers.get("content-type", "")
        if "xml" not in ctype and not sm_url.lower().endswith(".xml"):
            return []
        try:
            soup = BeautifulSoup(resp.text, "xml")
        except Exception:  # noqa: BLE001
            return []

        # A <sitemapindex> lists child sitemaps (not pages) — recurse once into them.
        index_entries = soup.find_all("sitemap")
        if index_entries and depth < 1:
            out: list[str] = []
            for entry in index_entries[:8]:
                loc = entry.find("loc")
                if loc and loc.text:
                    out.extend(await self._fetch_sitemap(client, loc.text.strip(), start_url, depth + 1))
                if len(out) >= 200:
                    break
            return out

        # A <urlset> lists actual pages under <url><loc>.
        pages = [(u.find("loc").text or "").strip()
                 for u in soup.find_all("url") if u.find("loc")]
        if not pages:  # fall back to bare <loc> if structure is unusual
            pages = [(loc.text or "").strip() for loc in soup.find_all("loc")]
        return pages

    async def crawl(self, start_url: str) -> list[PageResult]:
        start_url = _normalize(start_url)
        await self._progress("crawl:init", 0.05, "Reading robots.txt and sitemap")
        await self._load_robots(start_url)
        seeds = await self._sitemap_urls(start_url)

        queue: deque[tuple[str, int]] = deque()
        seen: set[str] = set()
        for u in [start_url, *seeds]:
            if u not in seen:
                seen.add(u)
                queue.append((u, 0))

        results: list[PageResult] = []
        context = await self._browser.new_context(
            viewport={"width": VIEWPORTS["desktop"][0], "height": VIEWPORTS["desktop"][1]},
            ignore_https_errors=True,
            user_agent="EngineerOS-WebsiteIntelligence/0.1 (+https://engineeros.dev)",
        )
        try:
            while queue and len(results) < self._max_pages:
                url, depth = queue.popleft()
                if not self._allowed(url):
                    results.append(
                        PageResult(
                            url=url,
                            depth=depth,
                            error="Blocked by robots.txt",
                            findings=[
                                RawFinding(
                                    category="crawl",
                                    severity="info",
                                    title="Skipped (robots.txt)",
                                    page_url=url,
                                    description="This URL is disallowed by robots.txt and was not fetched.",
                                    priority=5,
                                )
                            ],
                        )
                    )
                    continue

                frac = len(results) / max(self._max_pages, 1)
                await self._progress("crawl:page", 0.1 + 0.5 * frac, url)
                page_result = await self._scan_page(context, url, depth)
                results.append(page_result)

                if depth < self._max_depth and not page_result.error:
                    for link in page_result.links:
                        n = _normalize(link)
                        if n not in seen and _same_origin(n, start_url) and _looks_html(n):
                            seen.add(n)
                            queue.append((n, depth + 1))
        finally:
            await context.close()

        return results

    async def _scan_page(self, context, url: str, depth: int) -> PageResult:
        result = PageResult(url=url, depth=depth)
        page = await context.new_page()

        console_errors: list[dict] = []
        failed_requests: list[dict] = []

        def on_console(msg) -> None:
            if msg.type in ("error", "warning"):
                console_errors.append({"type": msg.type, "text": msg.text[:500]})

        def on_requestfailed(request) -> None:
            failed_requests.append(
                {
                    "url": request.url,
                    "method": request.method,
                    "resource_type": request.resource_type,
                    "failure": (request.failure or "")[:200] if isinstance(request.failure, str) else "",
                }
            )

        page.on("console", on_console)
        page.on("requestfailed", on_requestfailed)

        # Track HTTP error responses for sub-resources (404s, 5xx assets).
        bad_responses: list[dict] = []

        def on_response(response) -> None:
            if response.status >= 400:
                bad_responses.append(
                    {"url": response.url, "status": response.status, "type": response.request.resource_type}
                )

        page.on("response", on_response)

        try:
            start = time.monotonic()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except PlaywrightError:
                pass  # networkidle can time out on chatty pages; not fatal
            result.load_ms = int((time.monotonic() - start) * 1000)
            result.status_code = response.status if response else None
            result.final_url = page.url

            audit = await page.evaluate(AUDIT_JS)
            result.title = (audit.get("meta") or {}).get("title", "")
            result.meta = audit.get("meta") or {}
            result.links = audit.get("links") or []
            result.images = audit.get("allImages") or []
            result.findings.extend(findings_from_audit(url, audit))

            # Screenshots at three viewports.
            for name, (w, h) in VIEWPORTS.items():
                await page.set_viewport_size({"width": w, "height": h})
                await asyncio.sleep(0.2)  # let layout settle
                rel = f"{self._scan_id}/screenshots/{name}_{len(result.screenshots)}_{abs(hash(url)) % 10_000}.png"
                dest = self._artifacts_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    await page.screenshot(path=str(dest), full_page=True, timeout=15000)
                    result.screenshots.append(Screenshot(viewport=name, path=rel, width=w, height=h))
                except PlaywrightError as exc:
                    logger.info("screenshot failed (%s) for %s: %r", name, url, exc)

            # HTTP status finding.
            if result.status_code and result.status_code >= 400:
                result.findings.append(
                    RawFinding(
                        category="http",
                        severity="critical" if result.status_code >= 500 else "high",
                        title=f"Page returned HTTP {result.status_code}",
                        page_url=url,
                        description=f"The page responded with status {result.status_code}.",
                        recommendation="Fix the server error or broken route.",
                        evidence={"status": result.status_code},
                        priority=1,
                    )
                )

            # Console errors.
            errors_only = [c for c in console_errors if c["type"] == "error"]
            if errors_only:
                result.findings.append(
                    RawFinding(
                        category="console",
                        severity="medium",
                        title=f"{len(errors_only)} JavaScript console error(s)",
                        page_url=url,
                        description="Console errors indicate broken scripts or failed runtime operations.",
                        recommendation="Open DevTools and resolve the logged errors.",
                        evidence={"errors": errors_only[:50]},
                        priority=3,
                    )
                )

            # Failed / broken sub-resources.
            broken_assets = failed_requests + bad_responses
            if broken_assets:
                result.findings.append(
                    RawFinding(
                        category="assets",
                        severity="high",
                        title=f"{len(broken_assets)} broken/failed resource request(s)",
                        page_url=url,
                        description="Assets (images, scripts, CSS, fonts) failed to load or returned an error status.",
                        recommendation="Fix or remove the broken asset references.",
                        evidence={"resources": broken_assets[:80]},
                        priority=2,
                    )
                )

            result.console_errors = console_errors
            result.failed_requests = broken_assets

        except PlaywrightError as exc:
            result.error = str(exc)[:400]
            result.findings.append(
                RawFinding(
                    category="crawl",
                    severity="high",
                    title="Page failed to load",
                    page_url=url,
                    description=f"Navigation failed: {result.error}",
                    recommendation="Verify the URL is reachable and responds within the timeout.",
                    priority=2,
                )
            )
        finally:
            await page.close()

        return result
