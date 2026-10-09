"""Dashboard and hall occupancy endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.common import HallCode
from app.schemas.dashboard import DashboardSummary, HallOccupancy
from app.services import dashboard_service

router = APIRouter(tags=["Dashboard"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get(
    "/api/dashboard/summary",
    response_model=DashboardSummary,
    summary="Dashboard counters",
    description="Headline figures for the live dashboard. All values are read from "
    "PostgreSQL and are legitimately zero while the database is empty.",
)
def dashboard_summary(session: DbSession) -> DashboardSummary:
    return dashboard_service.get_summary(session)


@router.get(
    "/api/halls/{hall_id}/occupancy",
    response_model=HallOccupancy,
    summary="Hall occupancy",
    description="Seat occupancy for one hall. A hall with no seats reports 0.0%.",
)
def hall_occupancy(hall_id: HallCode, session: DbSession) -> HallOccupancy:
    return dashboard_service.get_hall_occupancy(session, hall_id)
