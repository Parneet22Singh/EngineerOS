"""Pydantic schemas for the public API surface."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl

from app.db.models import ScanStatus, Severity


class ScanOptions(BaseModel):
    # Module 1 (website intelligence)
    max_pages: int | None = Field(default=None, ge=1, le=500)
    max_depth: int | None = Field(default=None, ge=0, le=10)
    respect_robots: bool | None = None
    check_external_links: bool = True
    # Module 6 (autonomous QA)
    max_actions: int | None = Field(default=None, ge=1, le=100)
    # Shared
    run_lighthouse: bool | None = None


class ScanCreate(BaseModel):
    url: HttpUrl
    module: str = "website_intelligence"
    options: ScanOptions = Field(default_factory=ScanOptions)


class FindingOut(BaseModel):
    id: int
    category: str
    severity: Severity
    title: str
    description: str
    recommendation: str
    page_url: str
    element: str | None
    evidence: dict
    priority: int

    class Config:
        from_attributes = True


class ScanOut(BaseModel):
    id: str
    module: str
    target: str
    status: ScanStatus
    progress: float
    stage: str
    pages_scanned: int
    summary: dict
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    class Config:
        from_attributes = True


class ScanDetail(ScanOut):
    findings: list[FindingOut] = []
