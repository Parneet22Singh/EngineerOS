"""Broken-link and redirect-loop checker.

Collects every link discovered during the crawl and probes each one concurrently
(HEAD, falling back to GET). Reports HTTP failures, unreachable hosts, and redirect
loops. External links are checked too unless disabled per-scan.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

import httpx

from app.modules.website_intelligence.results import RawFinding

logger = logging.getLogger("engineeros.linkcheck")


# Statuses that mean "the resource exists but access is gated" (auth, rate limit, or
# — very commonly — a WAF/bot filter blocking our non-browser probe). These are NOT
# broken links; flagging them as broken produces false positives on protected sites.
RESTRICTED_STATUSES = {401, 403, 429}


async def _check_one(client: httpx.AsyncClient, url: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        try:
            resp = await client.head(url, follow_redirects=True)
            # Some servers reject HEAD — retry with GET.
            if resp.status_code in (403, 405, 501):
                resp = await client.get(url, follow_redirects=True)
            status = resp.status_code
            redirects = len(resp.history)
            restricted = status in RESTRICTED_STATUSES
            return {
                "url": url, "status": status, "redirects": redirects,
                "ok": status < 400, "restricted": restricted,
            }
        except httpx.TooManyRedirects:
            return {"url": url, "status": None, "redirects": -1, "ok": False, "reason": "redirect loop"}
        except httpx.HTTPError as exc:
            return {"url": url, "status": None, "redirects": 0, "ok": False, "reason": type(exc).__name__}


async def check_links(
    links: set[str],
    *,
    origin: str,
    check_external: bool,
    concurrency: int,
    sources: dict[str, list[str]] | None = None,
) -> list[RawFinding]:
    origin_netloc = urlparse(origin).netloc
    sources = sources or {}
    targets = sorted(
        u for u in links
        if check_external or urlparse(u).netloc == origin_netloc
    )
    if not targets:
        return []

    sem = asyncio.Semaphore(max(1, concurrency))
    # A realistic browser UA avoids trivial UA-based blocks; WAFs may still gate us,
    # which is exactly why 401/403/429 are treated as "restricted" rather than broken.
    async with httpx.AsyncClient(
        timeout=15,
        headers={"user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")},
        max_redirects=10,
    ) as client:
        results = await asyncio.gather(*(_check_one(client, u, sem) for u in targets))

    # Attach the page(s) each link was found on, so the report can say *where*.
    for r in results:
        found_on = sources.get(r["url"], [])
        if found_on:
            r["found_on"] = found_on[:8]

    broken = [r for r in results if not r["ok"] and not r.get("restricted")
              and r.get("reason") != "redirect loop"]
    restricted = [r for r in results if r.get("restricted")]
    loops = [r for r in results if r.get("reason") == "redirect loop"]
    long_chains = [r for r in results if isinstance(r.get("redirects"), int) and r["redirects"] >= 4]

    findings: list[RawFinding] = []
    if broken:
        findings.append(
            RawFinding(
                category="links",
                severity="high",
                title=f"{len(broken)} broken link(s)",
                description="Links that return a 404/410/5xx or fail to resolve.",
                recommendation="Update or remove the broken links.",
                evidence={"links": broken[:250]},
                priority=2,
            )
        )
    if restricted:
        findings.append(
            RawFinding(
                category="links",
                severity="info",
                title=f"{len(restricted)} link(s) returned access-restricted (401/403/429)",
                description="These links responded but gated access — often auth-required pages "
                            "or, commonly, a WAF/bot filter blocking the automated checker rather "
                            "than a broken link. Verify manually in a browser if unsure.",
                recommendation="Usually safe to ignore; confirm any that should be publicly reachable.",
                evidence={"links": restricted[:150]},
                priority=5,
            )
        )
    if loops:
        findings.append(
            RawFinding(
                category="links",
                severity="high",
                title=f"{len(loops)} redirect loop(s)",
                description="These URLs redirect in a cycle and never resolve.",
                recommendation="Fix the redirect configuration to terminate at a final URL.",
                evidence={"links": loops[:25]},
                priority=2,
            )
        )
    if long_chains:
        findings.append(
            RawFinding(
                category="links",
                severity="low",
                title=f"{len(long_chains)} link(s) with long redirect chains",
                description="Chains of 4+ redirects slow navigation and waste crawl budget.",
                recommendation="Point links directly at their final destination.",
                evidence={"links": long_chains[:25]},
                priority=4,
            )
        )
    return findings
