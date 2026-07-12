"""Lighthouse runner.

Shells out to the Lighthouse CLI (Node) to score Performance / Accessibility / SEO /
Best Practices for the entry URL. Lighthouse is optional: if the binary isn't on PATH
the runner returns no findings and records that it was skipped, so the platform works
without a Node toolchain installed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil

from app.modules.website_intelligence.results import RawFinding

logger = logging.getLogger("engineeros.lighthouse")

CATEGORY_SEVERITY = [
    (0.5, "high"),   # score < 0.5 -> high
    (0.9, "medium"),  # 0.5 <= score < 0.9 -> medium
]


def _severity_for(score: float) -> str:
    for threshold, sev in CATEGORY_SEVERITY:
        if score < threshold:
            return sev
    return "low"


def is_available(binary: str) -> bool:
    return shutil.which(binary) is not None


async def _terminate(proc) -> None:
    """Best-effort kill + reap of a subprocess so its pipe transports close."""
    if proc is None or proc.returncode is not None:
        return
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except (asyncio.TimeoutError, ProcessLookupError):
        pass


async def run_lighthouse(url: str, *, binary: str) -> tuple[list[RawFinding], dict]:
    """Return (findings, scores). ``scores`` is {} when Lighthouse is unavailable."""
    if not is_available(binary):
        logger.info("lighthouse binary '%s' not found; skipping", binary)
        return [], {"skipped": True, "reason": "lighthouse CLI not installed"}

    cmd = [
        binary,
        url,
        "--quiet",
        "--output=json",
        "--output-path=stdout",
        "--only-categories=performance,accessibility,seo,best-practices",
        '--chrome-flags=--headless=new --no-sandbox --disable-gpu',
        "--max-wait-for-load=45000",
    ]
    proc: asyncio.subprocess.Process | None = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
    except (FileNotFoundError, asyncio.TimeoutError) as exc:
        logger.warning("lighthouse run failed: %r", exc)
        # Kill and reap the child so its stdio transports close cleanly; otherwise
        # Python later GCs the unclosed pipes and emits noisy ResourceWarning traces.
        await _terminate(proc)
        return [], {"skipped": True, "reason": type(exc).__name__}

    if proc.returncode != 0 and not stdout:
        logger.warning("lighthouse exited %s: %s", proc.returncode, stderr.decode(errors="ignore")[:300])
        return [], {"skipped": True, "reason": f"exit {proc.returncode}"}

    try:
        report = json.loads(stdout.decode(errors="ignore"))
    except json.JSONDecodeError:
        return [], {"skipped": True, "reason": "could not parse lighthouse output"}

    categories = report.get("categories", {})
    scores: dict[str, int] = {}
    findings: list[RawFinding] = []
    for key, cat in categories.items():
        score = cat.get("score")
        if score is None:
            continue
        pct = round(score * 100)
        scores[key] = pct
        if score < 0.9:
            findings.append(
                RawFinding(
                    category="lighthouse",
                    severity=_severity_for(score),
                    title=f"Lighthouse {cat.get('title', key)} score: {pct}/100",
                    page_url=url,
                    description=f"Lighthouse scored {cat.get('title', key)} at {pct}/100 for the entry page.",
                    recommendation="Review the failing Lighthouse audits in this category.",
                    evidence={"category": key, "score": pct},
                    priority=2 if score < 0.5 else 3,
                )
            )
    return findings, scores
