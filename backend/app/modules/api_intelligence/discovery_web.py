"""Live-website API discovery via Playwright network capture.

Loads the target, records every XHR/fetch request (ignoring static assets and
third-party analytics), lightly navigates same-origin links to surface more calls,
and normalizes what it saw into structured endpoint records with observed methods,
status codes, auth style, and a sample response content-type.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from playwright.async_api import Error as PlaywrightError, async_playwright

logger = logging.getLogger("engineeros.api.web")

# Extensions / paths that are page assets, not API calls.
ASSET_RE = re.compile(
    r"\.(js|mjs|css|png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|eot|map|mp4|webm|mp3|"
    r"pdf|wasm|txt|xml)(\?|$)", re.I,
)
ANALYTICS_HOSTS = re.compile(
    r"(google-analytics|googletagmanager|doubleclick|facebook|hotjar|segment|"
    r"sentry|mixpanel|amplitude|intercom|clarity\.ms|cloudflareinsights)", re.I,
)
XHR_TYPES = {"xhr", "fetch"}


@dataclass(slots=True)
class Endpoint:
    method: str
    url: str            # full URL (first seen)
    path: str           # path component
    host: str
    kind: str = "REST"  # REST | GraphQL
    status: int | None = None
    request_ct: str = ""
    response_ct: str = ""
    auth: str = ""      # "", "Bearer", "Cookie", "Basic", "API-Key"
    same_origin: bool = True
    count: int = 1
    graphql_ops: list[str] = field(default_factory=list)


def _auth_style(headers: dict) -> str:
    h = {k.lower(): v for k, v in headers.items()}
    auth = h.get("authorization", "")
    if auth.lower().startswith("bearer"):
        return "Bearer"
    if auth.lower().startswith("basic"):
        return "Basic"
    if auth:
        return "Authorization"
    if "cookie" in h:
        return "Cookie"
    for key in h:
        if "api-key" in key or "apikey" in key or key == "x-auth-token":
            return "API-Key"
    return ""


class WebAPIDiscoverer:
    def __init__(self, entry_url: str, *, timeout_ms: int, max_pages: int = 4,
                 launch_kwargs: dict | None = None) -> None:
        self._entry = entry_url
        self._timeout = timeout_ms
        self._max_pages = max_pages
        self._origin = urlparse(entry_url).netloc
        self._seen: dict[tuple[str, str], Endpoint] = {}
        self._launch_kwargs = launch_kwargs or {"headless": True, "args": ["--no-sandbox", "--disable-gpu"]}

    def _record(self, request, response_status: int | None, response_ct: str) -> None:
        url = request.url
        if ASSET_RE.search(url) or ANALYTICS_HOSTS.search(url):
            return
        if request.resource_type not in XHR_TYPES:
            return
        parsed = urlparse(url)
        method = request.method.upper()
        key = (method, parsed.path or "/")
        headers = request.headers

        existing = self._seen.get(key)
        if existing:
            existing.count += 1
            if existing.status is None and response_status is not None:
                existing.status = response_status
            return

        ep = Endpoint(
            method=method,
            url=url,
            path=parsed.path or "/",
            host=parsed.netloc,
            status=response_status,
            request_ct=headers.get("content-type", ""),
            response_ct=response_ct,
            auth=_auth_style(headers),
            same_origin=(parsed.netloc == self._origin),
        )
        # GraphQL detection: path hint, or a POST carrying a "query" body.
        if "graphql" in parsed.path.lower():
            ep.kind = "GraphQL"
            post = request.post_data or ""
            for m in re.finditer(r'"operationName"\s*:\s*"([^"]+)"', post):
                if m.group(1) not in ep.graphql_ops:
                    ep.graphql_ops.append(m.group(1))
        self._seen[key] = ep

    async def discover(self) -> list[Endpoint]:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**self._launch_kwargs)
            context = await browser.new_context(ignore_https_errors=True)
            page = await context.new_page()

            pending: dict[str, object] = {}

            def on_request(req) -> None:
                pending[req.url + req.method] = req

            async def on_response(resp) -> None:
                try:
                    req = resp.request
                    self._record(req, resp.status, resp.headers.get("content-type", ""))
                except PlaywrightError:
                    pass

            page.on("request", on_request)
            page.on("response", lambda r: asyncio.create_task(on_response(r)))

            try:
                await page.goto(self._entry, wait_until="domcontentloaded", timeout=self._timeout)
                try:
                    await page.wait_for_load_state("networkidle", timeout=6000)
                except PlaywrightError:
                    pass

                # Collect a few same-origin links and visit them to surface more API calls.
                links = await self._same_origin_links(page)
                for link in links[: self._max_pages - 1]:
                    try:
                        await page.goto(link, wait_until="domcontentloaded", timeout=self._timeout)
                        try:
                            await page.wait_for_load_state("networkidle", timeout=4000)
                        except PlaywrightError:
                            pass
                    except PlaywrightError:
                        continue
            except PlaywrightError as exc:
                logger.info("web API discovery navigation issue: %r", exc)
            finally:
                await context.close()
                await browser.close()

        return sorted(self._seen.values(), key=lambda e: (not e.same_origin, e.path, e.method))

    async def _same_origin_links(self, page) -> list[str]:
        try:
            hrefs = await page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.getAttribute('href')).filter(Boolean)"
            )
        except PlaywrightError:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for href in hrefs:
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            absolute = urljoin(self._entry, href)
            p = urlparse(absolute)
            if p.netloc == self._origin and absolute not in seen:
                seen.add(absolute)
                out.append(absolute)
        return out
