"""Clone a website to disk as a browsable local mirror.

Uses a real Chromium instance (via Playwright) to render each page — including
JavaScript-driven content — then captures the exact bytes of every asset the
browser actually received (CSS, JS, images, fonts) via response interception, and
rewrites the saved HTML so it opens correctly offline. Assets are captured
straight from the already-successful page load, so nothing gets re-requested (and
re-blocked) afterward.

Same-origin pages are crawled breadth-first up to --max-pages/--max-depth, mirroring
the BFS approach used by EngineerOS's Website Intelligence crawler. Cross-origin
assets (CDN fonts, jsDelivr scripts, etc.) referenced by a page ARE downloaded too,
since a mirror that can't reach the internet needs them locally to render correctly.

Only clone sites you own or are otherwise authorized to mirror.

Usage:
    python clone_site.py https://example.com --out D:\\EngineerOS\\clones\\example
    python clone_site.py https://example.com --max-pages 30 --max-depth 3 --headed
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import socket
import sys
from collections import deque
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.async_api import Error as PlaywrightError, async_playwright


def _ipv4_pin_args(url: str) -> list[str]:
    """Pin Chromium to an IPv4 address for this host.

    Some machines advertise/route IPv6 for a site (AAAA records exist) but have no
    working IPv6 egress; Chromium then hangs on the IPv6 attempt and reports the whole
    navigation as unresolvable, even though plain IPv4 works fine. Resolving the host
    ourselves and pinning Chromium to that address sidesteps it. No-ops if lookup fails.
    """
    host = urlparse(url).hostname
    if not host:
        return []
    try:
        ipv4 = socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
    except OSError:
        return []
    return [f"--host-resolver-rules=MAP {host} {ipv4}"]

ASSET_TYPES = {"stylesheet", "script", "image", "font", "media"}
CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", re.I)


def _normalize(url: str) -> str:
    url, _ = urldefrag(url)
    return url.rstrip("/") or url


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


def _local_path_for(url: str, root: Path) -> Path:
    """Map a URL to a stable on-disk path under root, preserving directory structure."""
    parsed = urlparse(url)
    domain_dir = root / "_assets" / parsed.netloc.replace(":", "_")
    path = parsed.path.lstrip("/")
    if not path:
        path = "index"
    # Query strings can distinguish otherwise-identical asset paths (e.g. cache-busted
    # bundles); fold a short hash of the query into the filename so nothing collides.
    if parsed.query:
        digest = hashlib.sha1(parsed.query.encode("utf-8")).hexdigest()[:8]
        p = Path(path)
        path = str(p.with_name(f"{p.stem}.{digest}{p.suffix}"))
    dest = domain_dir / path
    if dest.suffix == "" and not path.endswith("/"):
        dest = dest.with_suffix(dest.suffix or ".bin")
    return dest


def _page_local_path(url: str, root: Path) -> Path:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if not path:
        return root / "index.html"
    if "." in Path(path).name:
        return root / path
    return root / path / "index.html"


class SiteCloner:
    def __init__(self, out_dir: Path, *, max_pages: int, max_depth: int, timeout_ms: int,
                 headed: bool, channel: str | None = None) -> None:
        self._root = out_dir
        self._max_pages = max_pages
        self._max_depth = max_depth
        self._timeout_ms = timeout_ms
        self._headed = headed
        self._channel = channel
        self._asset_map: dict[str, str] = {}   # absolute URL -> path relative to page
        self._downloaded: set[str] = set()
        self._pages_written = 0
        self._assets_written = 0

    async def run(self, start_url: str) -> None:
        start_url = _normalize(start_url)
        self._root.mkdir(parents=True, exist_ok=True)

        queue: deque[tuple[str, int]] = deque([(start_url, 0)])
        seen = {start_url}

        async with async_playwright() as pw:
            launch_kw = dict(
                headless=not self._headed,
                args=["--no-sandbox", "--disable-gpu", *_ipv4_pin_args(start_url)])
            if self._channel:
                launch_kw["channel"] = self._channel
            browser = await pw.chromium.launch(**launch_kw)
            context = await browser.new_context(ignore_https_errors=True)

            try:
                while queue and self._pages_written < self._max_pages:
                    url, depth = queue.popleft()
                    print(f"  [{self._pages_written + 1}/{self._max_pages}] {url}")
                    links = await self._clone_page(context, url)
                    self._pages_written += 1
                    if depth < self._max_depth:
                        for link in links:
                            n = _normalize(link)
                            if n not in seen and _same_origin(n, start_url):
                                seen.add(n)
                                queue.append((n, depth + 1))
            finally:
                await context.close()
                await browser.close()

        print(f"\n  done: {self._pages_written} page(s), {self._assets_written} asset(s)")
        print(f"  saved to: {self._root}")

    async def _clone_page(self, context, url: str) -> list[str]:
        page = await context.new_page()
        pending_assets: list = []

        def on_response(response) -> None:
            try:
                req = response.request
                if req.resource_type in ASSET_TYPES and response.url not in self._downloaded:
                    self._downloaded.add(response.url)
                    pending_assets.append(response)
            except Exception:  # noqa: BLE001
                pass

        page.on("response", on_response)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self._timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except PlaywrightError:
                pass

            for resp in pending_assets:
                await self._save_asset(resp)

            html = await page.content()
            links = await page.eval_on_selector_all(
                "a[href]", "els => els.map(e => e.getAttribute('href')).filter(Boolean)")
            absolute_links = [urljoin(url, h) for h in links
                              if not h.startswith(("#", "mailto:", "tel:", "javascript:"))]

            self._write_page(url, html)
            return absolute_links
        except PlaywrightError as exc:
            print(f"      ! failed to load: {exc}")
            return []
        finally:
            await page.close()

    async def _save_asset(self, response) -> None:
        try:
            body = await response.body()
        except Exception:  # noqa: BLE001
            return
        dest = _local_path_for(response.url, self._root)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
        except OSError:
            return
        self._asset_map[response.url] = dest.relative_to(self._root).as_posix()
        self._assets_written += 1
        # Rewrite url(...) references inside downloaded CSS (fonts, background images)
        # to point at whatever we've already saved locally for those URLs.
        if dest.suffix.lower() == ".css":
            try:
                text = body.decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                return
            rewritten = CSS_URL_RE.sub(
                lambda m: f"url({self._resolve_ref(response.url, m.group(2))})", text)
            dest.write_text(rewritten, encoding="utf-8")

    def _resolve_ref(self, base_url: str, ref: str) -> str:
        if ref.startswith("data:"):
            return ref
        absolute = urljoin(base_url, ref)
        local = self._asset_map.get(absolute)
        return local if local else ref

    def _write_page(self, url: str, html: str) -> None:
        soup = BeautifulSoup(html, "html.parser")
        dest = _page_local_path(url, self._root)
        dest.parent.mkdir(parents=True, exist_ok=True)

        for tag, attr in (("link", "href"), ("script", "src"), ("img", "src"),
                          ("source", "src"), ("video", "src"), ("audio", "src")):
            for el in soup.find_all(tag):
                val = el.get(attr)
                if not val:
                    continue
                absolute = urljoin(url, val)
                local = self._asset_map.get(absolute)
                if local:
                    depth = len(dest.relative_to(self._root).parts) - 1
                    prefix = "../" * depth
                    el[attr] = prefix + local

        dest.write_text(str(soup), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url", help="start URL to clone")
    ap.add_argument("--out", default=None, help="output directory (default: D:\\EngineerOS\\clones\\<domain>)")
    ap.add_argument("--max-pages", type=int, default=20)
    ap.add_argument("--max-depth", type=int, default=2)
    ap.add_argument("--timeout-ms", type=int, default=30000)
    ap.add_argument("--headed", action="store_true",
                     help="show a real browser window instead of headless — WAF-protected sites "
                          "(Akamai/Cloudflare) that block headless bots let a headed session through")
    ap.add_argument("--channel", default=None,
                     help="browser channel: 'chrome' or 'msedge' to drive your installed browser "
                          "instead of the bundled Chromium (most browser-like; pair with --headed)")
    args = ap.parse_args()

    domain = urlparse(args.url).netloc.replace(":", "_") or "site"
    out = Path(args.out) if args.out else Path("D:/EngineerOS/clones") / domain

    print(f"\n  cloning {args.url}")
    print(f"  -> {out}\n")

    cloner = SiteCloner(out, max_pages=args.max_pages, max_depth=args.max_depth,
                        timeout_ms=args.timeout_ms, headed=args.headed)
    asyncio.run(cloner.run(args.url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
