"""In-memory result structures produced during a crawl.

These are plain dataclasses so the crawler/auditors have no dependency on the DB. The
engine converts :class:`RawFinding` into persisted ``Finding`` rows at the end.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RawFinding:
    category: str
    severity: str  # matches models.Severity values
    title: str
    description: str = ""
    recommendation: str = ""
    page_url: str = ""
    element: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    priority: int = 3


@dataclass(slots=True)
class Screenshot:
    viewport: str  # desktop | tablet | mobile
    path: str  # relative to artifacts dir
    width: int
    height: int
    label: str = ""  # e.g. "menu opened", "modal: Sign up"


@dataclass(slots=True)
class PageResult:
    url: str
    depth: int
    status_code: int | None = None
    final_url: str | None = None
    title: str = ""
    load_ms: int = 0
    screenshots: list[Screenshot] = field(default_factory=list)
    findings: list[RawFinding] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    console_errors: list[dict] = field(default_factory=list)
    failed_requests: list[dict] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
