# --------------------------------------------------
# Compliance Dashboard API
# Day 49B — Enterprise Reporting Layer
# (DB-backed + Multi-tenant enforced)
# --------------------------------------------------

from __future__ import annotations

from fastapi import APIRouter, Query, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from typing import Optional, List, Dict, Any
import io
import csv

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from compliance.audit_metrics import AuditMetricsAggregator
from compliance.models import AuditEvent
from database import get_db  # ✅ use the shared session dependency

router = APIRouter(prefix="/compliance", tags=["Compliance Dashboard"])


# =========================================================
# Helpers
# =========================================================
def _require_tenant(request: Request) -> str:
    """
    Tenant is set by api_server middleware into request.state.client_id.
    We do NOT accept client_id from query params for security.
    """
    cid = (getattr(request.state, "client_id", "") or "").strip()
    if not cid:
        raise HTTPException(status_code=401, detail="Missing tenant context (X-Client-Id header).")
    return cid


def _tenant_stmt(client_id: str):
    """
    Base SELECT for AuditEvent that ALWAYS enforces tenant filter.
    Any new query should start from here.
    """
    return select(AuditEvent).where(AuditEvent.client_id == client_id)


async def _get_events_for_tenant(session: AsyncSession, client_id: str) -> List[Dict[str, Any]]:
    """
    Reads audit events from Postgres for ONE tenant only.
    Returns list of plain dicts compatible with AuditMetricsAggregator.
    """
    stmt = _tenant_stmt(client_id).order_by(AuditEvent.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()

    events: List[Dict[str, Any]] = []
    for r in rows:
        events.append(
            {
                "id": r.id,
                "client_id": r.client_id,
                "event_type": r.event_type,
                "payload": r.payload,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )
    return events


# =========================================================
# 1️⃣ Summary Endpoint
# =========================================================
@router.get("/summary")
async def get_summary(request: Request, db: AsyncSession = Depends(get_db)):
    client_id = _require_tenant(request)
    events = await _get_events_for_tenant(db, client_id)

    return {
        "client_id": client_id,
        "total_events": len(events),
        "events": events,
    }


# =========================================================
# 2️⃣ KPI Endpoint (Health Score)
# =========================================================
@router.get("/kpis")
async def compliance_kpis(
    request: Request,
    db: AsyncSession = Depends(get_db),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
):
    client_id = _require_tenant(request)

    events = await _get_events_for_tenant(db, client_id)
    aggregator = AuditMetricsAggregator(events)

    kpis = aggregator.generate_kpis(
        start_time=start_time,
        end_time=end_time,
        client_id=client_id,
    )

    return JSONResponse(content=kpis)


# =========================================================
# 3️⃣ JSON Export
# =========================================================
@router.get("/export/json")
async def export_json(
    request: Request,
    db: AsyncSession = Depends(get_db),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
):
    client_id = _require_tenant(request)

    events = await _get_events_for_tenant(db, client_id)
    aggregator = AuditMetricsAggregator(events)

    summary = aggregator.generate_summary(
        start_time=start_time,
        end_time=end_time,
        client_id=client_id,
    )

    return JSONResponse(
        content=summary,
        headers={"Content-Disposition": "attachment; filename=compliance_report.json"},
    )


# =========================================================
# 4️⃣ CSV Export
# =========================================================
@router.get("/export/csv")
async def export_csv(
    request: Request,
    db: AsyncSession = Depends(get_db),
    start_time: Optional[str] = Query(None),
    end_time: Optional[str] = Query(None),
):
    client_id = _require_tenant(request)

    events = await _get_events_for_tenant(db, client_id)
    aggregator = AuditMetricsAggregator(events)

    summary = aggregator.generate_summary(
        start_time=start_time,
        end_time=end_time,
        client_id=client_id,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Metric", "Value"])

    for key, value in summary.items():
        writer.writerow([key, value])

    output.seek(0)

    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=compliance_report.csv"},
    )
