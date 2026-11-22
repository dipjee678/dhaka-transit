"""Stops, nearby spatial search, and OSM bus-stop inventory endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_service
from app.schemas.models import NearbyStopOut, OsmBusStopOut, StopDetailOut, StopOut
from app.services.transit_service import TransitService
from transit.geo import DETOUR_FACTOR, walk_time_s

router = APIRouter(prefix="/api/stops", tags=["stops"])


@router.get("", response_model=list[StopOut], summary="List or search GTFS stops and stations")
def list_stops(
    q: str | None = Query(None, description="Case-insensitive search across English and Bangla stop names"),
    location_type: int | None = Query(None, ge=0, le=4, description="0 = boarding stop/platform, 1 = station"),
    route_type: int | None = Query(None, description="Filter by serving mode: 1 = metro, 3 = bus"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    svc: TransitService = Depends(get_service),
) -> list[StopOut]:
    needle = q.strip().casefold() if q else None
    results = []
    for s in sorted(svc.feed.stops.values(), key=lambda x: (x.location_type != 1, x.stop_name, x.stop_id)):
        if location_type is not None and s.location_type != location_type:
            continue
        if route_type is not None:
            rids = svc.feed.routes_by_stop.get(s.stop_id, set())
            if not any(svc.feed.routes[rid].route_type == route_type for rid in rids if rid in svc.feed.routes):
                continue
        if needle and needle not in s.stop_name.casefold() and needle not in s.stop_name_bn.casefold():
            continue
        results.append(svc.stop_out(s))
    return results[offset : offset + limit]


@router.get("/nearby", response_model=list[NearbyStopOut], summary="Find nearest transit stops/stations to coordinates")
def nearby_stops(
    lat: float = Query(..., ge=-90.0, le=90.0, description="Latitude (WGS84)"),
    lon: float = Query(..., ge=-180.0, le=180.0, description="Longitude (WGS84)"),
    radius_m: float = Query(800.0, gt=0.0, le=5000.0, description="Straight-line search radius in metres"),
    limit: int = Query(10, ge=1, le=50),
    include_platforms: bool = Query(False, description="Return individual boarding platforms instead of grouping by station"),
    svc: TransitService = Depends(get_service),
) -> list[NearbyStopOut]:
    idx = svc.boarding_index if include_platforms else svc.place_index
    hits = idx.within(lat, lon, radius_m)[:limit]
    out: list[NearbyStopOut] = []
    for h in hits:
        s = svc.feed.stops[h.key]
        base = svc.stop_out(s)
        wdist = round(h.distance_m * DETOUR_FACTOR, 1)
        out.append(NearbyStopOut(
            **base.model_dump(),
            distance_m=round(h.distance_m, 1),
            walk_distance_m=wdist,
            walk_time_s=walk_time_s(wdist),
        ))
    return out


@router.get(
    "/osm-inventory",
    response_model=list[OsmBusStopOut],
    summary="Mapped Dhaka OSM bus-stop inventory (unlinked to bus routes yet)",
)
def osm_bus_inventory(
    q: str | None = Query(None, description="Search OSM stop name (English or Bangla)"),
    kind: str | None = Query(None, description="bus_stop | platform | stop_position | bus_station"),
    named_only: bool = Query(False),
    limit: int = Query(1200, ge=1, le=2000),
    svc: TransitService = Depends(get_service),
) -> list[OsmBusStopOut]:
    needle = q.strip().casefold() if q else None
    out = []
    for row in svc.osm_bus_stops:
        if named_only and not row["name"]:
            continue
        if kind and row["kind"] != kind:
            continue
        if needle and needle not in row["name"].casefold() and needle not in row["name_bn"].casefold():
            continue
        out.append(OsmBusStopOut(**row))
        if len(out) >= limit:
            break
    return out


@router.get("/{stop_id}", response_model=StopDetailOut, summary="Get stop or station details with provenance")
def get_stop(stop_id: str, svc: TransitService = Depends(get_service)) -> StopDetailOut:
    s = svc.feed.stops.get(stop_id)
    if s is None:
        raise HTTPException(status_code=404, detail=f"Stop '{stop_id}' not found")
    platforms = [svc.stop_out(svc.feed.stops[pid]) for pid in svc.feed.platforms_by_station.get(stop_id, ())]
    parent = svc.stop_out(svc.feed.stops[s.parent_station]) if s.parent_station in svc.feed.stops else None
    return StopDetailOut(**svc.stop_out(s).model_dump(), platforms=platforms, parent=parent)
