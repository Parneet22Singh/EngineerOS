"""Shared reporting engine: JSON / CSV / HTML / PDF.

Used by every scan module. The HTML report is self-contained (screenshots embedded as
data URIs) so it can be downloaded and shared; the PDF is produced by rendering that
same HTML in headless Chromium. ``module_title`` lets each module brand its report
(e.g. "Website Intelligence" vs "Autonomous QA").
"""
from __future__ import annotations

import base64
import csv
import io
import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import async_playwright

from app.config import Settings
from app.db.models import Finding, Scan, SEVERITY_WEIGHT

logger = logging.getLogger("engineeros.reporting")

_TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES)),
    autoescape=select_autoescape(["html", "xml"]),
)

# How many screenshots to embed in the HTML/PDF (keeps file size sane).
_MAX_EMBEDDED_SHOTS = 16

SEVERITY_COLORS = {
    "critical": "#ef4444",
    "high": "#f97316",
    "medium": "#eab308",
    "low": "#38bdf8",
    "info": "#94a3b8",
}

MODULE_TITLES = {
    "website_intelligence": "Website Intelligence",
    "autonomous_qa": "Autonomous QA",
    "repo_intelligence": "Repository Intelligence",
    "api_intelligence": "API Intelligence",
    "knowledge_graph": "Knowledge Graph",
}


def module_title(module: str) -> str:
    return MODULE_TITLES.get(module, module.replace("_", " ").title())


def report_payload(scan: Scan, findings: list[Finding]) -> dict:
    """Canonical JSON structure returned by /report.json."""
    return {
        "scan": {
            "id": scan.id,
            "module": scan.module,
            "target": scan.target,
            "status": scan.status.value,
            "pages_scanned": scan.pages_scanned,
            "created_at": scan.created_at.isoformat() if scan.created_at else None,
            "finished_at": scan.finished_at.isoformat() if scan.finished_at else None,
        },
        "summary": scan.summary or {},
        "findings": [
            {
                "id": f.id,
                "category": f.category,
                "severity": f.severity.value,
                "title": f.title,
                "description": f.description,
                "recommendation": f.recommendation,
                "page_url": f.page_url,
                "element": f.element,
                "evidence": f.evidence,
                "priority": f.priority,
            }
            for f in findings
        ],
    }


def findings_to_csv(findings: list[Finding]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["severity", "priority", "category", "title", "page_url", "element", "description", "recommendation"]
    )
    for f in _sorted(findings):
        writer.writerow(
            [
                f.severity.value,
                f.priority,
                f.category,
                f.title,
                f.page_url,
                (f.element or "").replace("\n", " "),
                f.description.replace("\n", " "),
                f.recommendation.replace("\n", " "),
            ]
        )
    return buf.getvalue()


def _sorted(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda f: (SEVERITY_WEIGHT.get(f.severity.value, 9), f.priority))


def _embed_screenshots(scan: Scan, settings: Settings) -> list[dict]:
    summary = scan.summary or {}
    embedded: list[dict] = []
    for page in summary.get("pages", []):
        for shot in page.get("screenshots", []):
            if len(embedded) >= _MAX_EMBEDDED_SHOTS:
                return embedded
            abs_path = settings.artifacts_dir / shot["path"]
            try:
                data = abs_path.read_bytes()
            except OSError:
                continue
            b64 = base64.b64encode(data).decode("ascii")
            embedded.append(
                {
                    "page_url": page.get("url", ""),
                    "viewport": shot.get("viewport", ""),
                    "label": shot.get("label", ""),
                    "data_uri": f"data:image/png;base64,{b64}",
                }
            )
    return embedded


def render_html(scan: Scan, findings: list[Finding], settings: Settings) -> str:
    template = _env.get_template("report.html.j2")
    grouped: dict[str, list[Finding]] = {}
    for f in _sorted(findings):
        grouped.setdefault(f.severity.value, []).append(f)
    return template.render(
        scan=scan,
        module_title=module_title(scan.module),
        summary=scan.summary or {},
        grouped=grouped,
        total=len(findings),
        screenshots=_embed_screenshots(scan, settings),
        severity_colors=SEVERITY_COLORS,
        severity_order=["critical", "high", "medium", "low", "info"],
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )


async def render_pdf(scan: Scan, findings: list[Finding], settings: Settings) -> bytes:
    html = render_html(scan, findings, settings)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-gpu"])
        try:
            page = await browser.new_page()
            await page.set_content(html, wait_until="networkidle")
            return await page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "16mm", "bottom": "16mm", "left": "12mm", "right": "12mm"},
            )
        finally:
            await browser.close()
