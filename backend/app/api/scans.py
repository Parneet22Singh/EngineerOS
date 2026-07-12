"""Module-agnostic scan API.

Handles scan creation, listing, retrieval, report downloads (JSON/CSV/HTML/PDF), and
live progress over WebSocket. Creation dispatches to the correct module's
:class:`ScanRunner` based on the request's ``module`` field.
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import reporting
from app.config import Settings
from app.core.scan_runner import ScanHub, ScanRegistry
from app.db.database import SessionLocal, get_session
from app.db.models import Scan, ScanStatus
from app.db.schemas import ScanCreate, ScanDetail, ScanOut

logger = logging.getLogger("engineeros.api.scans")

TERMINAL_STAGES = {"completed", "failed"}


def build_scan_router(settings: Settings, registry: ScanRegistry, hub: ScanHub) -> APIRouter:
    router = APIRouter(prefix="/scans", tags=["scans"])
    tasks: set[asyncio.Task] = set()

    def launch(scan_id: str, module: str, target: str, options: dict) -> None:
        runner = registry.get(module)
        if runner is None:
            raise HTTPException(status_code=400, detail=f"unknown module '{module}'")

        async def progress(stage: str, prog: float, detail: str = "") -> None:
            await hub.publish(
                scan_id,
                {"scan_id": scan_id, "stage": stage, "progress": round(prog, 3), "detail": detail},
            )

        task = asyncio.create_task(runner.run(scan_id, target, options, progress))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def load(session: AsyncSession, scan_id: str) -> Scan:
        result = await session.execute(
            select(Scan).options(selectinload(Scan.findings)).where(Scan.id == scan_id)
        )
        scan = result.scalar_one_or_none()
        if scan is None:
            raise HTTPException(status_code=404, detail="scan not found")
        return scan

    @router.post("", response_model=ScanOut, status_code=201)
    async def create_scan(body: ScanCreate, session: AsyncSession = Depends(get_session)) -> Scan:
        if registry.get(body.module) is None:
            raise HTTPException(
                status_code=400,
                detail=f"unknown module '{body.module}'. Available: {registry.names()}",
            )
        scan_id = uuid.uuid4().hex
        scan = Scan(
            id=scan_id,
            module=body.module,
            target=str(body.url),
            status=ScanStatus.queued,
            options=body.options.model_dump(),
        )
        session.add(scan)
        await session.commit()
        await session.refresh(scan)
        launch(scan_id, body.module, str(body.url), body.options.model_dump())
        return scan

    @router.get("", response_model=list[ScanOut])
    async def list_scans(session: AsyncSession = Depends(get_session)) -> list[Scan]:
        result = await session.execute(select(Scan).order_by(Scan.created_at.desc()).limit(100))
        return list(result.scalars())

    @router.get("/{scan_id}", response_model=ScanDetail)
    async def get_scan(scan_id: str, session: AsyncSession = Depends(get_session)) -> Scan:
        return await load(session, scan_id)

    @router.get("/{scan_id}/report.json")
    async def report_json(scan_id: str, session: AsyncSession = Depends(get_session)) -> dict:
        scan = await load(session, scan_id)
        return reporting.report_payload(scan, scan.findings)

    @router.get("/{scan_id}/report.csv")
    async def report_csv(scan_id: str, session: AsyncSession = Depends(get_session)) -> PlainTextResponse:
        scan = await load(session, scan_id)
        return PlainTextResponse(
            reporting.findings_to_csv(scan.findings),
            media_type="text/csv",
            headers={"content-disposition": f'attachment; filename="engineeros-{scan_id}.csv"'},
        )

    @router.get("/{scan_id}/report.html", response_class=HTMLResponse)
    async def report_html(scan_id: str, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
        scan = await load(session, scan_id)
        return HTMLResponse(reporting.render_html(scan, scan.findings, settings))

    @router.get("/{scan_id}/report.pdf")
    async def report_pdf(scan_id: str, session: AsyncSession = Depends(get_session)) -> Response:
        scan = await load(session, scan_id)
        pdf = await reporting.render_pdf(scan, scan.findings, settings)
        return Response(
            pdf,
            media_type="application/pdf",
            headers={"content-disposition": f'attachment; filename="engineeros-{scan_id}.pdf"'},
        )

    @router.websocket("/{scan_id}/stream")
    async def stream(websocket: WebSocket, scan_id: str) -> None:
        await websocket.accept()
        queue = hub.subscribe(scan_id)
        try:
            async with SessionLocal() as session:
                scan = await session.get(Scan, scan_id)
                if scan is None:
                    await websocket.send_json({"error": "scan not found"})
                    await websocket.close()
                    return
                await websocket.send_json(
                    {
                        "scan_id": scan_id,
                        "stage": scan.stage,
                        "progress": scan.progress,
                        "status": scan.status.value,
                        "detail": "",
                    }
                )
                if scan.status in (ScanStatus.completed, ScanStatus.failed):
                    await websocket.close()
                    return

            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    await websocket.send_json({"scan_id": scan_id, "stage": "heartbeat", "progress": None})
                    continue
                await websocket.send_json(message)
                if message.get("stage") in TERMINAL_STAGES:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            hub.unsubscribe(scan_id, queue)

    return router
