"""Persistence models shared across modules.

``Scan`` and ``Finding`` are generic enough to serve every analysis module: a scan is
one unit of work against a target, and a finding is one issue discovered. Module 1 and
Module 6 both write into these tables, which is what lets the shared intelligence layer
reason across modules later.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ScanStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class Severity(str, enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    info = "info"


# Sort weight so reports can order by severity deterministically.
SEVERITY_WEIGHT: dict[str, int] = {
    Severity.critical: 0,
    Severity.high: 1,
    Severity.medium: 2,
    Severity.low: 3,
    Severity.info: 4,
}


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    module: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(2048))
    status: Mapped[ScanStatus] = mapped_column(
        Enum(ScanStatus), default=ScanStatus.queued, index=True
    )
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str] = mapped_column(String(128), default="queued")
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    pages_scanned: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    findings: Mapped[list["Finding"]] = relationship(
        back_populates="scan", cascade="all, delete-orphan"
    )


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scan_id: Mapped[str] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)

    category: Mapped[str] = mapped_column(String(64), index=True)  # a11y, seo, links, ...
    severity: Mapped[Severity] = mapped_column(Enum(Severity), index=True)
    title: Mapped[str] = mapped_column(String(512))
    description: Mapped[str] = mapped_column(Text, default="")
    recommendation: Mapped[str] = mapped_column(Text, default="")
    page_url: Mapped[str] = mapped_column(String(2048), default="")
    element: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=3)  # 1 highest .. 5 lowest

    scan: Mapped["Scan"] = relationship(back_populates="findings")
