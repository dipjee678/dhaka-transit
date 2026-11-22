"""Routes, stop sequences, frequencies, and GeoJSON geometry endpoints."""
from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_service
from app.schemas.models import RouteDetailOut, RouteDirectionOut, RouteFrequencyOut, RouteOut
from app.services.transit_service import TransitService
from transit.gtfs.spec import format_gtfs_time

router = APIRouter(prefix="/api/routes", tags=["routes"])
_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _route_out(rid: str, svc: TransitService) -> RouteOut:
    r = svc.feed.routes[rid]
    agency = svc.feed.agencies.get(r.agency_id)
    # Distinct stations / standalone stops served by this route
    stop_keys: set[str] = set()
    for t in svc.feed.trips_by_route.get(rid, ()):
        for st in svc.feed.stop_times_by_trip.get(t.trip_id, ()):
            s = svc.feed.stops.get(st.stop_id)
            if s:
                stop_keys.add(s.parent_station or s.stop_id)
    return RouteOut(
        route_id=r.route_id,
        agency_id=r.agency_id,
        agency_name=agency.agency_name if agency else "",
        route_short_name=r.route_short_name,
        route_long_name=r.route_long_name,
        route_long_name_bn=r.route_long_name_bn,
        route_type=r.route_type,
        mode=r.mode,
        route_color=r.route_color,
        route_text_color=r.route_text_color,
        stop_count=len(stop_keys),
        provenance=svc.prov_out(svc.feed.provenance_for("routes", rid)),
    )


@router.get("", response_model=list[RouteOut], summary="List all transit routes")
def list_routes(
    route_type: int | None = Query(None, description="1 = metro, 3 = bus"),
    svc: TransitService = Depends(get_service),
) -> list[RouteOut]:
    out = []
    for rid in sorted(svc.feed.routes):
        r = svc.feed.routes[rid]
        if route_type is not None and r.route_type != route_type:
            continue
        out.append(_route_out(rid, svc))
    return out


@router.get("/{route_id}", response_model=RouteDetailOut, summary="Get route directions, stops, frequencies, and geometry")
def get_route(route_id: str, svc: TransitService = Depends(get_service)) -> RouteDetailOut:
    if route_id not in svc.feed.routes:
        raise HTTPException(status_code=404, detail=f"Route '{route_id}' not found")
    by_dir: dict[int, list] = defaultdict(list)
    for t in svc.feed.trips_by_route.get(route_id, ()):
        by_dir[t.direction_id].append(t)

    directions: list[RouteDirectionOut] = []
    for did in sorted(by_dir):
        trips = by_dir[did]
        # Representative trip = the one serving the most stops in this direction
        rep = max(trips, key=lambda tr: len(svc.feed.stop_times_by_trip.get(tr.trip_id, ())))
        sts = svc.feed.stop_times_by_trip.get(rep.trip_id, [])
        stops_out = [svc.stop_out(svc.feed.stops[st.stop_id]) for st in sts if st.stop_id in svc.feed.stops]
        shape_pts = svc.feed.shapes.get(rep.shape_id, [])
        coords = [[p.lat, p.lon] for p in shape_pts]

        freqs: list[RouteFrequencyOut] = []
        for tr in sorted(trips, key=lambda x: x.trip_id):
            cal = svc.feed.calendars.get(tr.service_id)
            days = [d for d, flag in zip(_DAY_NAMES, cal.weekdays) if flag] if cal else []
            for f in svc.feed.frequencies_by_trip.get(tr.trip_id, ()):
                start_str = format_gtfs_time(f.start_s)
                freqs.append(RouteFrequencyOut(
                    service_id=tr.service_id,
                    days=days,
                    start_time=start_str,
                    end_time=format_gtfs_time(f.end_s),
                    headway_secs=f.headway_secs,
                    exact_times=f.exact_times,
                    provenance=svc.prov_out(svc.feed.provenance_for("frequencies", f"{tr.trip_id}|{start_str}")),
                ))
        directions.append(RouteDirectionOut(
            direction_id=did,
            headsign=rep.trip_headsign,
            shape_id=rep.shape_id,
            stops=stops_out,
            frequencies=freqs,
            shape_coordinates=coords,
        ))

    return RouteDetailOut(**_route_out(route_id, svc).model_dump(), directions=directions)


@router.get("/{route_id}/geojson", summary="GeoJSON FeatureCollection of route shapes and stops for Leaflet")
def get_route_geojson(route_id: str, svc: TransitService = Depends(get_service)) -> dict:
    detail = get_route(route_id, svc)
    features: list[dict] = []
    seen_stops: set[str] = set()
    for d in detail.directions:
        if d.shape_coordinates:
            features.append({
                "type": "Feature",
                "properties": {
                    "feature_type": "route_shape",
                    "route_id": detail.route_id,
                    "route_short_name": detail.route_short_name,
                    "mode": detail.mode,
                    "direction_id": d.direction_id,
                    "headsign": d.headsign,
                    "shape_id": d.shape_id,
                    "color": f"#{detail.route_color}" if detail.route_color else "#1d4ed8",
                },
                "geometry": {
                    "type": "LineString",
                    # RFC 7946 GeoJSON uses [lon, lat] order
                    "coordinates": [[lon, lat] for lat, lon in d.shape_coordinates],
                },
            })
        for s in d.stops:
            # Emit parent station once if present, else the boarding stop itself
            target_id = s.parent_station or s.stop_id
            if target_id in seen_stops:
                continue
            seen_stops.add(target_id)
            st = svc.feed.stops[target_id]
            features.append({
                "type": "Feature",
                "properties": {
                    "feature_type": "stop",
                    "stop_id": st.stop_id,
                    "stop_name": st.stop_name,
                    "stop_name_bn": st.stop_name_bn,
                    "location_type": st.location_type,
                    "route_id": detail.route_id,
                    "mode": detail.mode,
                },
                "geometry": {"type": "Point", "coordinates": [st.stop_lon, st.stop_lat]},
            })
    return {"type": "FeatureCollection", "features": features}
