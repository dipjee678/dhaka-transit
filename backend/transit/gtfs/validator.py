"""GTFS feed validator with Dhaka-specific data-quality rules.

Complements (does not replace) the official MobilityData GTFS Validator:
- spec rules: required files/columns/values, primary keys, references,
  time/date formats, ordering of stop_times and shapes;
- data-quality rules from the project brief: missing coordinates, duplicate
  stops, invalid routes, inconsistent schedules, implausible speeds, stops far
  from their route geometry, and provenance coverage.

Usage:
    issues = validate_feed(read_feed(path))
    report = summarize(issues)
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass

from transit.geo import GridIndex, haversine_m, in_bbox
from transit.gtfs.io import Feed
from transit.gtfs.spec import (
    GTFS_FILES, PROJECT_REQUIRED_FILES, Presence, ROUTE_TYPE_BUS, ROUTE_TYPE_METRO,
    is_valid_color, is_valid_gtfs_date, parse_gtfs_time,
)

ERROR, WARNING, INFO = "error", "warning", "info"

# Thresholds (documented assumptions; tune as real data arrives)
DUPLICATE_SAME_NAME_M = 30.0      # same name, closer than this -> probable duplicate
DUPLICATE_ANY_NAME_M = 3.0        # any two stops this close -> probable duplicate
STOP_SHAPE_MAX_M = 150.0          # stop farther than this from its trip shape
MAX_SPEED_KMH = {ROUTE_TYPE_BUS: 60.0, ROUTE_TYPE_METRO: 110.0}
DEFAULT_MAX_SPEED_KMH = 120.0
PROVENANCE_ENTITIES = ("stops", "routes", "trips", "shapes", "frequencies", "fare_attributes")
VALID_CATEGORIES = {"official", "osm", "community", "estimated"}
VALID_STATUS = {"verified", "unverified", "disputed"}
VALID_ROUTE_TYPES = {0, 1, 2, 3, 4, 5, 6, 7, 11, 12}


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    file: str
    entity_id: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


class _Collector:
    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def add(self, severity: str, code: str, file: str, entity_id: str, message: str) -> None:
        self.issues.append(Issue(severity, code, file, str(entity_id), message))


def _int(v: str, default: int | None = None) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _float(v: str) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _coords(stop: dict) -> tuple[float, float] | None:
    lat, lon = _float(stop.get("stop_lat", "")), _float(stop.get("stop_lon", ""))
    return None if lat is None or lon is None else (lat, lon)


def _time(v: str) -> int | None:
    try:
        return parse_gtfs_time(v) if v else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Generic spec checks
# ---------------------------------------------------------------------------
def _check_structure(feed: Feed, c: _Collector) -> None:
    for f in PROJECT_REQUIRED_FILES:
        if f not in feed:
            sev = ERROR if GTFS_FILES[f].file_presence is Presence.REQUIRED else WARNING
            c.add(sev, "missing_file", f, "", f"{f} is missing")
    if "calendar.txt" not in feed and "calendar_dates.txt" not in feed:
        c.add(ERROR, "missing_calendar", "calendar.txt", "", "need calendar.txt or calendar_dates.txt")

    for fname, rows in feed.items():
        spec = GTFS_FILES.get(fname)
        if spec is None:
            continue
        cols = set(rows[0].keys()) if rows else set()
        for field in spec.required_fields:
            if rows and field not in cols:
                c.add(ERROR, "missing_required_column", fname, "", f"column '{field}' is missing")
        for i, row in enumerate(rows, start=2):  # line 1 is the header
            for field in spec.required_fields:
                if field in cols and row.get(field, "") == "":
                    c.add(ERROR, "missing_required_value", fname, f"line {i}", f"'{field}' is empty")
        if spec.primary_key:
            seen = Counter(tuple(r.get(k, "") for k in spec.primary_key) for r in rows)
            for key, n in seen.items():
                if n > 1:
                    c.add(ERROR, "duplicate_key", fname, "|".join(key), f"primary key appears {n} times")


# ---------------------------------------------------------------------------
# Table-specific checks
# ---------------------------------------------------------------------------
def _check_agency(feed: Feed, c: _Collector) -> set[str]:
    rows = feed.get("agency.txt", [])
    ids = {r.get("agency_id", "") for r in rows}
    if len(rows) > 1 and "" in ids:
        c.add(ERROR, "missing_agency_id", "agency.txt", "", "agency_id required when there are several agencies")
    tzs = {r.get("agency_timezone") for r in rows}
    if len(tzs) > 1:
        c.add(ERROR, "inconsistent_timezone", "agency.txt", "", f"agencies use different timezones: {tzs}")
    return ids


def _check_stops(feed: Feed, c: _Collector) -> dict[str, dict]:
    stops = {r["stop_id"]: r for r in feed.get("stops.txt", []) if r.get("stop_id")}
    idx: GridIndex[str] = GridIndex(cell_size_m=100)
    for sid, r in stops.items():
        lt = _int(r.get("location_type") or "0", -1)
        if lt not in (0, 1, 2, 3, 4):
            c.add(ERROR, "invalid_location_type", "stops.txt", sid, f"location_type={r.get('location_type')}")
            continue
        lat, lon = _float(r.get("stop_lat", "")), _float(r.get("stop_lon", ""))
        if lt <= 2:
            if not r.get("stop_name"):
                c.add(ERROR, "missing_stop_name", "stops.txt", sid, "stop_name required for location_type 0-2")
            if lat is None or lon is None:
                c.add(ERROR, "missing_coordinates", "stops.txt", sid, "stop_lat/stop_lon missing or not numeric")
                continue
        if lat is not None and lon is not None:
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                c.add(ERROR, "invalid_coordinates", "stops.txt", sid, f"({lat}, {lon}) out of range")
                continue
            if not in_bbox(lat, lon):
                c.add(ERROR, "outside_dhaka", "stops.txt", sid, f"({lat}, {lon}) outside the Dhaka bounding box")
            if lt == 0:
                idx.insert(sid, lat, lon)
        parent = r.get("parent_station", "")
        if parent:
            p = stops.get(parent)
            if p is None:
                c.add(ERROR, "unknown_parent_station", "stops.txt", sid, f"parent_station '{parent}' not found")
            elif _int(p.get("location_type") or "0") != 1:
                c.add(ERROR, "invalid_parent_station", "stops.txt", sid, f"parent '{parent}' is not a station")
        elif lt in (2, 3, 4):
            c.add(ERROR, "missing_parent_station", "stops.txt", sid, "entrances/nodes/boarding areas need a parent")

    # Duplicate detection (boarding stops only).
    reported: set[frozenset] = set()
    for sid, r in stops.items():
        if _int(r.get("location_type") or "0") != 0:
            continue
        lat, lon = _float(r.get("stop_lat", "")), _float(r.get("stop_lon", ""))
        if lat is None or lon is None:
            continue
        for n in idx.within(lat, lon, DUPLICATE_SAME_NAME_M):
            if n.key == sid:
                continue
            pair = frozenset((sid, n.key))
            if pair in reported:
                continue
            other = stops[n.key]
            same_name = r.get("stop_name", "").casefold() == other.get("stop_name", "").casefold()
            same_parent = r.get("parent_station") and r.get("parent_station") == other.get("parent_station")
            if (same_name and not same_parent) or n.distance_m <= DUPLICATE_ANY_NAME_M:
                reported.add(pair)
                c.add(WARNING, "duplicate_stop", "stops.txt", f"{sid}|{n.key}",
                      f"'{r.get('stop_name')}' and '{other.get('stop_name')}' are {n.distance_m:.1f} m apart")
    return stops


def _check_routes(feed: Feed, agency_ids: set[str], c: _Collector) -> dict[str, dict]:
    routes = {r["route_id"]: r for r in feed.get("routes.txt", []) if r.get("route_id")}
    multi_agency = len(agency_ids) > 1
    for rid, r in routes.items():
        if not r.get("route_short_name") and not r.get("route_long_name"):
            c.add(ERROR, "route_without_name", "routes.txt", rid, "need route_short_name or route_long_name")
        rt = _int(r.get("route_type", ""))
        if rt not in VALID_ROUTE_TYPES:
            c.add(ERROR, "invalid_route_type", "routes.txt", rid, f"route_type={r.get('route_type')}")
        aid = r.get("agency_id", "")
        if (multi_agency and not aid) or (aid and aid not in agency_ids):
            c.add(ERROR, "unknown_agency", "routes.txt", rid, f"agency_id '{aid}' not in agency.txt")
        for col in ("route_color", "route_text_color"):
            if r.get(col) and not is_valid_color(r[col]):
                c.add(ERROR, "invalid_color", "routes.txt", rid, f"{col}={r[col]}")
    return routes


def _service_ids(feed: Feed, c: _Collector) -> set[str]:
    ids: set[str] = set()
    for r in feed.get("calendar.txt", []):
        sid = r.get("service_id", "")
        ids.add(sid)
        s, e = r.get("start_date", ""), r.get("end_date", "")
        if not (is_valid_gtfs_date(s) and is_valid_gtfs_date(e)):
            c.add(ERROR, "invalid_date", "calendar.txt", sid, f"start_date={s} end_date={e}")
        elif s > e:
            c.add(ERROR, "start_after_end", "calendar.txt", sid, f"{s} > {e}")
        days = [r.get(d, "") for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")]
        if any(d not in ("0", "1") for d in days):
            c.add(ERROR, "invalid_day_flag", "calendar.txt", sid, "day columns must be 0 or 1")
    for r in feed.get("calendar_dates.txt", []):
        ids.add(r.get("service_id", ""))
        if not is_valid_gtfs_date(r.get("date", "")):
            c.add(ERROR, "invalid_date", "calendar_dates.txt", r.get("service_id", ""), f"date={r.get('date')}")
        if r.get("exception_type") not in ("1", "2"):
            c.add(ERROR, "invalid_exception_type", "calendar_dates.txt", r.get("service_id", ""),
                  f"exception_type={r.get('exception_type')}")
    return ids


def _load_shapes(feed: Feed, c: _Collector) -> dict[str, list[tuple[float, float]]]:
    pts: dict[str, list[tuple[int, float, float, float | None]]] = defaultdict(list)
    for r in feed.get("shapes.txt", []):
        seq, lat, lon = _int(r.get("shape_pt_sequence", "")), _float(r.get("shape_pt_lat", "")), _float(r.get("shape_pt_lon", ""))
        if seq is None or lat is None or lon is None:
            c.add(ERROR, "invalid_shape_point", "shapes.txt", r.get("shape_id", ""), "non-numeric lat/lon/sequence")
            continue
        pts[r["shape_id"]].append((seq, lat, lon, _float(r.get("shape_dist_traveled", ""))))
    shapes: dict[str, list[tuple[float, float]]] = {}
    for sid, plist in pts.items():
        plist.sort()
        if len(plist) < 2:
            c.add(ERROR, "shape_too_short", "shapes.txt", sid, "a shape needs at least 2 points")
        dists = [p[3] for p in plist if p[3] is not None]
        if any(b < a for a, b in zip(dists, dists[1:])):
            c.add(ERROR, "decreasing_shape_distance", "shapes.txt", sid, "shape_dist_traveled decreases")
        shapes[sid] = [(p[1], p[2]) for p in plist]
    return shapes


def _point_to_polyline_m(lat: float, lon: float, line: list[tuple[float, float]]) -> float:
    """Approximate distance (m) from a point to a polyline (local equirectangular)."""
    kx = 111_320.0 * math.cos(math.radians(lat))
    ky = 110_540.0
    best = float("inf")
    for (a_lat, a_lon), (b_lat, b_lon) in zip(line, line[1:]):
        ax, ay = (a_lon - lon) * kx, (a_lat - lat) * ky
        bx, by = (b_lon - lon) * kx, (b_lat - lat) * ky
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
        px, py = ax + t * dx, ay + t * dy
        best = min(best, math.hypot(px, py))
    if len(line) == 1:
        best = haversine_m(lat, lon, *line[0])
    return best


def _check_trips_and_times(feed: Feed, stops: dict, routes: dict, services: set[str],
                           shapes: dict, c: _Collector) -> None:
    trips = {r["trip_id"]: r for r in feed.get("trips.txt", []) if r.get("trip_id")}
    for tid, t in trips.items():
        if t.get("route_id") not in routes:
            c.add(ERROR, "unknown_route", "trips.txt", tid, f"route_id '{t.get('route_id')}' not found")
        if t.get("service_id") not in services:
            c.add(ERROR, "unknown_service", "trips.txt", tid, f"service_id '{t.get('service_id')}' not in calendar")
        if t.get("shape_id") and t["shape_id"] not in shapes:
            c.add(ERROR, "unknown_shape", "trips.txt", tid, f"shape_id '{t['shape_id']}' not in shapes.txt")
        if not t.get("shape_id"):
            c.add(WARNING, "trip_without_shape", "trips.txt", tid, "no route geometry (shape_id)")
        if t.get("direction_id") not in ("", "0", "1"):
            c.add(ERROR, "invalid_direction", "trips.txt", tid, f"direction_id={t.get('direction_id')}")

    by_trip: dict[str, list[dict]] = defaultdict(list)
    for r in feed.get("stop_times.txt", []):
        by_trip[r.get("trip_id", "")].append(r)
    used_stops: set[str] = set()

    for tid, rows in by_trip.items():
        if tid not in trips:
            c.add(ERROR, "unknown_trip", "stop_times.txt", tid, "trip_id not in trips.txt")
            continue
        rows.sort(key=lambda r: _int(r.get("stop_sequence", ""), -1))
        if len(rows) < 2:
            c.add(ERROR, "trip_too_few_stops", "stop_times.txt", tid, "a trip must serve at least 2 stops")
        route = routes.get(trips[tid].get("route_id", ""), {})
        vmax = MAX_SPEED_KMH.get(_int(route.get("route_type", "")), DEFAULT_MAX_SPEED_KMH)
        shape = shapes.get(trips[tid].get("shape_id", ""))
        prev_t: int | None = None
        prev_stop: dict | None = None
        for i, r in enumerate(rows):
            sid = r.get("stop_id", "")
            st = stops.get(sid)
            if st is None:
                c.add(ERROR, "unknown_stop", "stop_times.txt", f"{tid}|{r.get('stop_sequence')}", f"stop_id '{sid}' not found")
                continue
            used_stops.add(sid)
            if _int(st.get("location_type") or "0") != 0:
                c.add(ERROR, "trip_uses_station", "stop_times.txt", f"{tid}|{sid}", "stop_times must reference boarding stops (location_type 0)")
            arr, dep = _time(r.get("arrival_time", "")), _time(r.get("departure_time", ""))
            for col in ("arrival_time", "departure_time"):
                if r.get(col) and _time(r[col]) is None:
                    c.add(ERROR, "invalid_time", "stop_times.txt", f"{tid}|{r.get('stop_sequence')}", f"{col}={r[col]}")
            if (i == 0 or i == len(rows) - 1 or r.get("timepoint") == "1") and (arr is None or dep is None):
                c.add(ERROR, "missing_time", "stop_times.txt", f"{tid}|{r.get('stop_sequence')}",
                      "first/last stops and timepoints need arrival and departure times")
            if arr is not None and dep is not None and dep < arr:
                c.add(ERROR, "departure_before_arrival", "stop_times.txt", f"{tid}|{r.get('stop_sequence')}", "")
            cur = arr if arr is not None else dep
            if cur is not None and prev_t is not None:
                if cur < prev_t:
                    c.add(ERROR, "time_travel", "stop_times.txt", f"{tid}|{r.get('stop_sequence')}", "time decreases along the trip")
                elif prev_stop is not None and _coords(prev_stop) and _coords(st):
                    d = haversine_m(*_coords(prev_stop), *_coords(st))
                    dt = cur - prev_t
                    if dt > 0 and d / dt * 3.6 > vmax:
                        c.add(WARNING, "implausible_speed", "stop_times.txt", f"{tid}|{r.get('stop_sequence')}",
                              f"{d / dt * 3.6:.0f} km/h from previous stop (limit {vmax:.0f})")
            if dep is not None or arr is not None:
                prev_t = dep if dep is not None else arr
                prev_stop = st
            if shape and _coords(st):
                off = _point_to_polyline_m(*_coords(st), shape)
                if off > STOP_SHAPE_MAX_M:
                    c.add(WARNING, "stop_far_from_shape", "stop_times.txt", f"{tid}|{sid}",
                          f"stop is {off:.0f} m from the trip shape")

    for tid in trips:
        if tid not in by_trip:
            c.add(ERROR, "trip_without_stop_times", "trips.txt", tid, "trip has no stop_times")
    routes_with_trips = {t.get("route_id") for t in trips.values()}
    for rid in routes:
        if rid not in routes_with_trips:
            c.add(WARNING, "route_without_trips", "routes.txt", rid, "route has no trips")
    for sid, s in stops.items():
        if _int(s.get("location_type") or "0") == 0 and sid not in used_stops:
            c.add(INFO, "unused_stop", "stops.txt", sid, "stop is not served by any trip")

    for r in feed.get("frequencies.txt", []):
        tid = r.get("trip_id", "")
        if tid not in trips:
            c.add(ERROR, "unknown_trip", "frequencies.txt", tid, "trip_id not in trips.txt")
        s, e, h = _time(r.get("start_time", "")), _time(r.get("end_time", "")), _int(r.get("headway_secs", ""))
        if s is None or e is None or s >= e:
            c.add(ERROR, "invalid_frequency_window", "frequencies.txt", tid, f"{r.get('start_time')}-{r.get('end_time')}")
        if h is None or h <= 0:
            c.add(ERROR, "invalid_headway", "frequencies.txt", tid, f"headway_secs={r.get('headway_secs')}")


def _check_fares(feed: Feed, stops: dict, routes: dict, c: _Collector) -> None:
    fares = {r["fare_id"]: r for r in feed.get("fare_attributes.txt", []) if r.get("fare_id")}
    for fid, f in fares.items():
        p = _float(f.get("price", ""))
        if p is None or p < 0:
            c.add(ERROR, "invalid_price", "fare_attributes.txt", fid, f"price={f.get('price')}")
        if f.get("currency_type") and len(f["currency_type"]) != 3:
            c.add(ERROR, "invalid_currency", "fare_attributes.txt", fid, f"currency_type={f['currency_type']}")
    zones = {s.get("zone_id") for s in stops.values() if s.get("zone_id")}
    for r in feed.get("fare_rules.txt", []):
        fid = r.get("fare_id", "")
        if fid not in fares:
            c.add(ERROR, "unknown_fare", "fare_rules.txt", fid, "fare_id not in fare_attributes.txt")
        if r.get("route_id") and r["route_id"] not in routes:
            c.add(ERROR, "unknown_route", "fare_rules.txt", fid, f"route_id '{r['route_id']}' not found")
        for col in ("origin_id", "destination_id", "contains_id"):
            if r.get(col) and r[col] not in zones:
                c.add(ERROR, "unknown_zone", "fare_rules.txt", fid, f"{col} '{r[col]}' is not a stop zone_id")


def _check_provenance(feed: Feed, c: _Collector) -> None:
    prov = feed.get("provenance.txt")
    if prov is None:
        c.add(WARNING, "missing_provenance", "provenance.txt", "", "no provenance file; data origin is undocumented")
        return
    sources = {r.get("source_id") for r in feed.get("data_sources.txt", [])}
    covered: set[tuple[str, str]] = set()
    for r in prov:
        if r.get("category") not in VALID_CATEGORIES:
            c.add(ERROR, "invalid_provenance_category", "provenance.txt", r.get("entity_id", ""), f"category={r.get('category')}")
        if r.get("status") not in VALID_STATUS:
            c.add(ERROR, "invalid_provenance_status", "provenance.txt", r.get("entity_id", ""), f"status={r.get('status')}")
        if not r.get("source_id"):
            c.add(ERROR, "provenance_without_source", "provenance.txt", r.get("entity_id", ""), "source_id is empty")
        elif sources and r["source_id"] not in sources:
            c.add(ERROR, "unknown_source", "provenance.txt", r.get("entity_id", ""), f"source_id '{r['source_id']}' not in data_sources.txt")
        if not r.get("field"):
            covered.add((r.get("entity_table", ""), r.get("entity_id", "")))
    keys = {"stops": "stop_id", "routes": "route_id", "trips": "trip_id", "fare_attributes": "fare_id"}
    for table, key in keys.items():
        for row in feed.get(f"{table}.txt", []):
            if (table, row.get(key, "")) not in covered:
                c.add(ERROR, "missing_provenance", f"{table}.txt", row.get(key, ""), "record has no provenance entry")
    for sid in {r.get("shape_id") for r in feed.get("shapes.txt", [])}:
        if ("shapes", sid) not in covered:
            c.add(ERROR, "missing_provenance", "shapes.txt", sid, "shape has no provenance entry")
    for r in feed.get("frequencies.txt", []):
        if ("frequencies", f"{r.get('trip_id')}|{r.get('start_time')}") not in covered:
            c.add(ERROR, "missing_provenance", "frequencies.txt", f"{r.get('trip_id')}|{r.get('start_time')}",
                  "headway has no provenance entry")


# ---------------------------------------------------------------------------
def validate_feed(feed: Feed) -> list[Issue]:
    c = _Collector()
    _check_structure(feed, c)
    agency_ids = _check_agency(feed, c)
    stops = _check_stops(feed, c)
    routes = _check_routes(feed, agency_ids, c)
    services = _service_ids(feed, c)
    shapes = _load_shapes(feed, c)
    _check_trips_and_times(feed, stops, routes, services, shapes, c)
    _check_fares(feed, stops, routes, c)
    _check_provenance(feed, c)
    return c.issues


def summarize(issues: list[Issue]) -> dict:
    by_sev = Counter(i.severity for i in issues)
    by_code = Counter((i.severity, i.code) for i in issues)
    return {
        "errors": by_sev.get(ERROR, 0),
        "warnings": by_sev.get(WARNING, 0),
        "infos": by_sev.get(INFO, 0),
        "by_code": [{"severity": s, "code": k, "count": n} for (s, k), n in sorted(by_code.items())],
    }
