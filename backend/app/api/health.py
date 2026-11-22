"""Health and service diagnostics endpoint."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends

from app.api.deps import get_service
from app.schemas.models import HealthOut
from app.services.transit_service import TransitService

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthOut, summary="Service health and graph/timetable diagnostics")
def health(svc: TransitService = Depends(get_service)) -> HealthOut:
    today = dt.date.today()
    tt = svc.timetable_for_date(today)
    return HealthOut(
        status="ok",
        feed_version=svc.feed.feed_info.get("feed_version", "unknown"),
        stations=len(svc.feed.stations),
        boarding_stops=len(svc.feed.boarding_stops),
        routes=len(svc.feed.routes),
        osm_bus_stop_inventory=len(svc.osm_bus_stops),
        graph=svc.frequency_graph.stats(),
        timetable_sample={"date": today.isoformat(), **tt.stats()},
    )
