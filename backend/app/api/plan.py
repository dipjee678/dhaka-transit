"""Multimodal route-planning endpoints (/api/plan and /api/plan/compare)."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_service
from app.schemas.models import CompareResponseOut, FareEstimateOut, JourneyOut, LegOut, PlanResponseOut
from app.services.transit_service import TransitService
from transit.geo import in_bbox
from transit.gtfs.spec import format_gtfs_time, parse_gtfs_time
from transit.routing.dijkstra import plan_dijkstra
from transit.routing.journey import CostWeights, Journey
from transit.routing.planner import plan_journey
from transit.routing.raptor import plan_raptor

router = APIRouter(prefix="/api/plan", tags=["route-planner"])


def _resolve_endpoint(
    svc: TransitService,
    stop_id: str | None,
    lat: float | None,
    lon: float | None,
    label: str | None,
    role: str,
) -> tuple[float, float, str]:
    if stop_id:
        s = svc.feed.stops.get(stop_id)
        if s is None:
            raise HTTPException(status_code=404, detail=f"{role} stop '{stop_id}' not found")
        return s.stop_lat, s.stop_lon, label or s.stop_name
    if lat is not None and lon is not None:
        if not in_bbox(lat, lon):
            raise HTTPException(
                status_code=422,
                detail=f"{role} coordinates ({lat}, {lon}) are outside the Greater Dhaka bounding box",
            )
        return lat, lon, label or role.capitalize()
    raise HTTPException(
        status_code=422,
        detail=f"Provide either {role}_stop or both {role}_lat and {role}_lon",
    )


def _parse_time_param(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    v = value.strip()
    if v.count(":") == 1:
        v += ":00"
    try:
        return parse_gtfs_time(v)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid time '{value}': use HH:MM or HH:MM:SS") from exc


def _parse_date_param(value: str | None) -> dt.date:
    if not value:
        return dt.date.today()
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid date '{value}': use YYYY-MM-DD") from exc


def _parse_modes(modes: str | None) -> set[str] | None:
    if not modes:
        return None
    parsed = {m.strip().lower() for m in modes.split(",") if m.strip()}
    invalid = parsed - {"metro", "bus"}
    if invalid:
        raise HTTPException(status_code=422, detail=f"Unknown modes {sorted(invalid)}; allowed: metro, bus")
    return parsed or None


def _journey_out(j: Journey) -> JourneyOut:
    legs_out = [
        LegOut(
            mode=l.mode,
            from_name=l.from_name, from_lat=l.from_lat, from_lon=l.from_lon, from_stop_id=l.from_stop_id,
            to_name=l.to_name, to_lat=l.to_lat, to_lon=l.to_lon, to_stop_id=l.to_stop_id,
            duration_s=l.duration_s, wait_s=l.wait_s, distance_m=l.distance_m,
            departure_s=l.departure_s, arrival_s=l.arrival_s,
            departure_time=format_gtfs_time(l.departure_s) if l.departure_s is not None else None,
            arrival_time=format_gtfs_time(l.arrival_s) if l.arrival_s is not None else None,
            route_id=l.route_id, route_short_name=l.route_short_name, route_long_name=l.route_long_name,
            route_color=l.route_color, direction_id=l.direction_id, trip_id=l.trip_id, headsign=l.headsign,
            intermediate_stops=list(l.intermediate_stops),
            geometry=[[lat, lon] for lat, lon in l.geometry],
            provenance=l.provenance.value,
            is_estimated=l.is_estimated,
        )
        for l in j.legs
    ]
    return JourneyOut(
        algorithm=j.algorithm,
        total_duration_s=j.total_duration_s,
        total_walk_m=j.total_walk_m,
        total_walk_s=j.total_walk_s,
        total_wait_s=j.total_wait_s,
        total_in_vehicle_s=j.total_in_vehicle_s,
        transfers=j.transfers,
        generalized_cost=j.generalized_cost,
        departure_s=j.departure_s,
        arrival_s=j.arrival_s,
        departure_time=format_gtfs_time(j.departure_s) if j.departure_s is not None else None,
        arrival_time=format_gtfs_time(j.arrival_s) if j.arrival_s is not None else None,
        provenance=j.provenance.value,
        is_estimated=j.is_estimated,
        outside_service_hours=j.outside_service_hours,
        computation_ms=j.computation_ms,
        fare=FareEstimateOut(
            total_bdt=j.fare.total_bdt,
            currency=j.fare.currency,
            complete=j.fare.complete,
            leg_fares=list(j.fare.leg_fares),
            note=j.fare.note,
        ),
        legs=legs_out,
    )


@router.get("", response_model=PlanResponseOut, summary="Plan a multimodal journey (Metro + Bus + Walking)")
def plan_route(
    from_stop: str | None = Query(None, description="Origin GTFS stop_id or station_id (e.g. mrt6_mirpur_10)"),
    from_lat: float | None = Query(None, ge=-90.0, le=90.0),
    from_lon: float | None = Query(None, ge=-180.0, le=180.0),
    from_name: str | None = Query(None),
    to_stop: str | None = Query(None, description="Destination GTFS stop_id or station_id (e.g. mrt6_motijheel)"),
    to_lat: float | None = Query(None, ge=-90.0, le=90.0),
    to_lon: float | None = Query(None, ge=-180.0, le=180.0),
    to_name: str | None = Query(None),
    date: str | None = Query(None, description="Service date YYYY-MM-DD (defaults to today)"),
    time: str | None = Query(None, description="Optional departure time HH:MM or HH:MM:SS"),
    algorithm: Literal["combined", "raptor", "dijkstra"] = Query("combined"),
    modes: str | None = Query(None, description="Comma-separated allowed modes: metro,bus"),
    max_transfers: int = Query(4, ge=0, le=5),
    max_walk_m: float = Query(1200.0, ge=200.0, le=3000.0),
    svc: TransitService = Depends(get_service),
) -> PlanResponseOut:
    olat, olon, oname = _resolve_endpoint(svc, from_stop, from_lat, from_lon, from_name, "from")
    dlat, dlon, dname = _resolve_endpoint(svc, to_stop, to_lat, to_lon, to_name, "to")
    svc_date = _parse_date_param(date)
    dep_s = _parse_time_param(time)
    allowed = _parse_modes(modes)
    weights = CostWeights(max_access_walk_m=max_walk_m)
    tt = svc.timetable_for_date(svc_date)

    res = plan_journey(
        feed=svc.feed,
        graph=svc.frequency_graph,
        timetable=tt,
        boarding_index=svc.boarding_index,
        from_lat=olat, from_lon=olon, to_lat=dlat, to_lon=dlon,
        from_name=oname, to_name=dname,
        service_date=svc_date,
        departure_s=dep_s,
        algorithm=algorithm,
        allowed_modes=allowed,
        max_rounds=max_transfers + 1,
        weights=weights,
    )
    return PlanResponseOut(
        origin=res.origin,
        destination=res.destination,
        service_date=res.service_date,
        departure_time=format_gtfs_time(res.departure_s) if res.departure_s is not None else None,
        algorithm_requested=res.algorithm_requested,
        recommended=_journey_out(res.recommended) if res.recommended else None,
        alternatives=[_journey_out(j) for j in res.alternatives],
        comparison=res.comparison,
        warnings=list(res.warnings),
    )


@router.get("/compare", response_model=CompareResponseOut, summary="Compare RAPTOR and Dijkstra side-by-side")
def compare_algorithms(
    from_stop: str | None = Query(None),
    from_lat: float | None = Query(None, ge=-90.0, le=90.0),
    from_lon: float | None = Query(None, ge=-180.0, le=180.0),
    to_stop: str | None = Query(None),
    to_lat: float | None = Query(None, ge=-90.0, le=90.0),
    to_lon: float | None = Query(None, ge=-180.0, le=180.0),
    date: str | None = Query(None),
    time: str = Query("08:30", description="Departure time HH:MM or HH:MM:SS for RAPTOR"),
    modes: str | None = Query(None),
    max_transfers: int = Query(4, ge=0, le=5),
    svc: TransitService = Depends(get_service),
) -> CompareResponseOut:
    olat, olon, oname = _resolve_endpoint(svc, from_stop, from_lat, from_lon, None, "from")
    dlat, dlon, dname = _resolve_endpoint(svc, to_stop, to_lat, to_lon, None, "to")
    svc_date = _parse_date_param(date)
    dep_s = _parse_time_param(time)
    assert dep_s is not None
    allowed = _parse_modes(modes)
    tt = svc.timetable_for_date(svc_date)
    weights = CostWeights()

    raptor_js = (
        plan_raptor(
            timetable=tt, feed=svc.feed, boarding_index=svc.boarding_index,
            from_lat=olat, from_lon=olon, to_lat=dlat, to_lon=dlon,
            departure_s=dep_s, from_name=oname, to_name=dname,
            max_rounds=max_transfers + 1, weights=weights, allowed_modes=allowed,
        )
        if tt.patterns
        else []
    )
    dijk_j = plan_dijkstra(
        graph=svc.frequency_graph, feed=svc.feed, boarding_index=svc.boarding_index,
        from_lat=olat, from_lon=olon, to_lat=dlat, to_lon=dlon,
        from_name=oname, to_name=dname, weights=weights, allowed_modes=allowed,
        outside_service_hours=not any(j.algorithm == "raptor" for j in raptor_js),
    )

    best_r = min(raptor_js, key=lambda j: j.generalized_cost) if raptor_js else None
    same = best_r is not None and dijk_j is not None and best_r.signature(svc.feed) == dijk_j.signature(svc.feed)
    warnings = []
    if not any(r.mode == "bus" for r in svc.feed.routes.values()):
        warnings.append("Curated bus routes are not yet loaded; comparison runs on MRT-6 + walking.")
    if any(j.is_estimated for j in (*raptor_js, *([dijk_j] if dijk_j else []))):
        warnings.append("Travel times include estimated values pending official timetable verification.")

    return CompareResponseOut(
        origin={"name": oname, "lat": olat, "lon": olon},
        destination={"name": dname, "lat": dlat, "lon": dlon},
        service_date=svc_date.isoformat(),
        departure_time=format_gtfs_time(dep_s),
        raptor_journeys=[_journey_out(j) for j in raptor_js],
        dijkstra_journey=_journey_out(dijk_j) if dijk_j else None,
        comparison={
            "same_best_route": same,
            "raptor_ms": best_r.computation_ms if best_r else None,
            "dijkstra_ms": dijk_j.computation_ms if dijk_j else None,
            "raptor_duration_s": best_r.total_duration_s if best_r else None,
            "dijkstra_duration_s": dijk_j.total_duration_s if dijk_j else None,
            "raptor_pareto_options": len(raptor_js),
            "raptor_transfers": best_r.transfers if best_r else None,
            "dijkstra_transfers": dijk_j.transfers if dijk_j else None,
        },
        warnings=warnings,
    )
