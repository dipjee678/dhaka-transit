"""Typed in-memory representation of a GTFS feed with provenance metadata.

Framework-independent (stdlib only). Used by the graph/timetable builders,
the routing engines (Phase 4), and the FastAPI service layer.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from transit.gtfs.io import Feed, read_feed
from transit.gtfs.spec import parse_gtfs_time
from transit.provenance import SourceCategory, VerificationStatus

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _opt_int(val: str, default: int | None = None) -> int | None:
    return int(val) if val not in ("", None) else default


def _opt_float(val: str) -> float | None:
    return float(val) if val not in ("", None) else None


@dataclass(frozen=True)
class Agency:
    agency_id: str
    agency_name: str
    agency_url: str
    agency_timezone: str
    agency_lang: str = ""
    agency_phone: str = ""


@dataclass(frozen=True)
class Stop:
    stop_id: str
    stop_name: str
    stop_lat: float
    stop_lon: float
    location_type: int = 0
    parent_station: str = ""
    zone_id: str = ""
    stop_code: str = ""
    stop_desc: str = ""
    stop_name_bn: str = ""
    platform_code: str = ""


@dataclass(frozen=True)
class Route:
    route_id: str
    agency_id: str
    route_short_name: str
    route_long_name: str
    route_type: int
    route_desc: str = ""
    route_color: str = ""
    route_text_color: str = ""
    route_long_name_bn: str = ""

    @property
    def mode(self) -> str:
        return {1: "metro", 3: "bus"}.get(self.route_type, f"type_{self.route_type}")


@dataclass(frozen=True)
class ServiceCalendar:
    service_id: str
    weekdays: tuple[bool, bool, bool, bool, bool, bool, bool]  # Mon..Sun (date.weekday() order)
    start_date: str  # YYYYMMDD
    end_date: str    # YYYYMMDD


@dataclass(frozen=True)
class CalendarException:
    service_id: str
    date: str            # YYYYMMDD
    exception_type: int  # 1 = added, 2 = removed


@dataclass(frozen=True)
class Trip:
    trip_id: str
    route_id: str
    service_id: str
    direction_id: int = 0
    trip_headsign: str = ""
    shape_id: str = ""


@dataclass(frozen=True)
class StopTime:
    trip_id: str
    stop_id: str
    stop_sequence: int
    arrival_s: int
    departure_s: int
    timepoint: int = 0
    shape_dist_traveled: float | None = None


@dataclass(frozen=True)
class Frequency:
    trip_id: str
    start_s: int
    end_s: int
    headway_secs: int
    exact_times: int = 0


@dataclass(frozen=True)
class ShapePoint:
    shape_id: str
    seq: int
    lat: float
    lon: float
    dist_traveled: float | None = None


@dataclass(frozen=True)
class FareAttribute:
    fare_id: str
    price: float
    currency_type: str = "BDT"
    payment_method: int = 1
    transfers: int | None = 0
    agency_id: str = ""


@dataclass(frozen=True)
class FareRule:
    fare_id: str
    route_id: str = ""
    origin_id: str = ""
    destination_id: str = ""
    contains_id: str = ""


@dataclass(frozen=True)
class DataSource:
    source_id: str
    name: str
    url: str = ""
    license: str = ""
    category: str = "estimated"
    retrieved_at: str = ""
    notes: str = ""


@dataclass(frozen=True)
class Provenance:
    entity_table: str
    entity_id: str
    field: str
    source_id: str
    category: SourceCategory
    status: VerificationStatus
    verified_at: str = ""
    notes: str = ""


@dataclass
class TransitFeed:
    """Typed GTFS feed with indexed lookups and calendar resolution."""

    agencies: dict[str, Agency]
    stops: dict[str, Stop]
    routes: dict[str, Route]
    trips: dict[str, Trip]
    stop_times_by_trip: dict[str, list[StopTime]]
    frequencies_by_trip: dict[str, list[Frequency]]
    calendars: dict[str, ServiceCalendar]
    calendar_dates: list[CalendarException]
    shapes: dict[str, list[ShapePoint]]
    fare_attributes: dict[str, FareAttribute]
    fare_rules: list[FareRule]
    feed_info: dict[str, str]
    data_sources: dict[str, DataSource]
    provenance: dict[tuple[str, str, str], Provenance]
    # Derived indexes
    platforms_by_station: dict[str, list[str]] = field(default_factory=dict)
    trips_by_route: dict[str, list[Trip]] = field(default_factory=dict)
    routes_by_stop: dict[str, set[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        platforms: dict[str, list[str]] = defaultdict(list)
        for s in self.stops.values():
            if s.parent_station:
                platforms[s.parent_station].append(s.stop_id)
        for lst in platforms.values():
            lst.sort()
        self.platforms_by_station = dict(platforms)

        tbr: dict[str, list[Trip]] = defaultdict(list)
        rbs: dict[str, set[str]] = defaultdict(set)
        for t in self.trips.values():
            tbr[t.route_id].append(t)
            for st in self.stop_times_by_trip.get(t.trip_id, ()):
                rbs[st.stop_id].add(t.route_id)
                stop = self.stops.get(st.stop_id)
                if stop and stop.parent_station:
                    rbs[stop.parent_station].add(t.route_id)
        self.trips_by_route = dict(tbr)
        self.routes_by_stop = dict(rbs)

    @property
    def boarding_stops(self) -> dict[str, Stop]:
        return {sid: s for sid, s in self.stops.items() if s.location_type == 0}

    @property
    def stations(self) -> dict[str, Stop]:
        return {sid: s for sid, s in self.stops.items() if s.location_type == 1}

    def provenance_for(self, table: str, entity_id: str, fld: str = "") -> Provenance | None:
        return self.provenance.get((table, entity_id, fld))

    def active_service_ids(self, service_date: dt.date) -> set[str]:
        """Exact GTFS service resolution for ``service_date``."""
        ymd = service_date.strftime("%Y%m%d")
        wd = service_date.weekday()  # 0=Mon .. 6=Sun
        active: set[str] = set()
        for sid, cal in self.calendars.items():
            if cal.start_date <= ymd <= cal.end_date and cal.weekdays[wd]:
                active.add(sid)
        for exc in self.calendar_dates:
            if exc.date != ymd:
                continue
            if exc.exception_type == 1:
                active.add(exc.service_id)
            elif exc.exception_type == 2:
                active.discard(exc.service_id)
        return active

    @classmethod
    def from_path(cls, path: Path | str) -> "TransitFeed":
        return cls.from_feed_dict(read_feed(Path(path)))

    @classmethod
    def from_feed_dict(cls, raw: Feed) -> "TransitFeed":
        # Bangla translations lookup: (table_name, field_name, record_id) -> translation
        bn: dict[tuple[str, str, str], str] = {}
        for r in raw.get("translations.txt", []):
            if r.get("language") == "bn" and r.get("record_id"):
                bn[(r.get("table_name", ""), r.get("field_name", ""), r["record_id"])] = r.get("translation", "")

        agencies = {
            r["agency_id"]: Agency(
                agency_id=r["agency_id"], agency_name=r["agency_name"], agency_url=r["agency_url"],
                agency_timezone=r["agency_timezone"], agency_lang=r.get("agency_lang", ""),
                agency_phone=r.get("agency_phone", ""),
            )
            for r in raw.get("agency.txt", []) if r.get("agency_id")
        }

        stops: dict[str, Stop] = {}
        for r in raw.get("stops.txt", []):
            sid = r.get("stop_id", "")
            if not sid or not r.get("stop_lat") or not r.get("stop_lon"):
                continue
            stops[sid] = Stop(
                stop_id=sid, stop_name=r.get("stop_name", ""),
                stop_lat=float(r["stop_lat"]), stop_lon=float(r["stop_lon"]),
                location_type=_opt_int(r.get("location_type", ""), 0) or 0,
                parent_station=r.get("parent_station", ""), zone_id=r.get("zone_id", ""),
                stop_code=r.get("stop_code", ""), stop_desc=r.get("stop_desc", ""),
                stop_name_bn=bn.get(("stops", "stop_name", sid), ""),
                platform_code=r.get("platform_code", ""),
            )

        routes: dict[str, Route] = {}
        for r in raw.get("routes.txt", []):
            rid = r.get("route_id", "")
            if not rid:
                continue
            routes[rid] = Route(
                route_id=rid, agency_id=r.get("agency_id", ""),
                route_short_name=r.get("route_short_name", ""),
                route_long_name=r.get("route_long_name", ""),
                route_type=int(r["route_type"]),
                route_desc=r.get("route_desc", ""),
                route_color=r.get("route_color", ""),
                route_text_color=r.get("route_text_color", ""),
                route_long_name_bn=bn.get(("routes", "route_long_name", rid), ""),
            )

        calendars = {
            r["service_id"]: ServiceCalendar(
                service_id=r["service_id"],
                weekdays=tuple(r.get(d) == "1" for d in _WEEKDAYS),  # type: ignore[arg-type]
                start_date=r["start_date"], end_date=r["end_date"],
            )
            for r in raw.get("calendar.txt", []) if r.get("service_id")
        }

        calendar_dates = [
            CalendarException(
                service_id=r["service_id"], date=r["date"], exception_type=int(r["exception_type"]),
            )
            for r in raw.get("calendar_dates.txt", []) if r.get("service_id")
        ]

        trips = {
            r["trip_id"]: Trip(
                trip_id=r["trip_id"], route_id=r["route_id"], service_id=r["service_id"],
                direction_id=_opt_int(r.get("direction_id", ""), 0) or 0,
                trip_headsign=r.get("trip_headsign", ""), shape_id=r.get("shape_id", ""),
            )
            for r in raw.get("trips.txt", []) if r.get("trip_id")
        }

        st_by_trip: dict[str, list[StopTime]] = defaultdict(list)
        for r in raw.get("stop_times.txt", []):
            arr = parse_gtfs_time(r["arrival_time"]) if r.get("arrival_time") else parse_gtfs_time(r["departure_time"])
            dep = parse_gtfs_time(r["departure_time"]) if r.get("departure_time") else arr
            st_by_trip[r["trip_id"]].append(StopTime(
                trip_id=r["trip_id"], stop_id=r["stop_id"], stop_sequence=int(r["stop_sequence"]),
                arrival_s=arr, departure_s=dep, timepoint=_opt_int(r.get("timepoint", ""), 0) or 0,
                shape_dist_traveled=_opt_float(r.get("shape_dist_traveled", "")),
            ))
        for lst in st_by_trip.values():
            lst.sort(key=lambda x: x.stop_sequence)

        freq_by_trip: dict[str, list[Frequency]] = defaultdict(list)
        for r in raw.get("frequencies.txt", []):
            freq_by_trip[r["trip_id"]].append(Frequency(
                trip_id=r["trip_id"], start_s=parse_gtfs_time(r["start_time"]),
                end_s=parse_gtfs_time(r["end_time"]), headway_secs=int(r["headway_secs"]),
                exact_times=_opt_int(r.get("exact_times", ""), 0) or 0,
            ))
        for lst in freq_by_trip.values():
            lst.sort(key=lambda x: x.start_s)

        shapes: dict[str, list[ShapePoint]] = defaultdict(list)
        for r in raw.get("shapes.txt", []):
            shapes[r["shape_id"]].append(ShapePoint(
                shape_id=r["shape_id"], seq=int(r["shape_pt_sequence"]),
                lat=float(r["shape_pt_lat"]), lon=float(r["shape_pt_lon"]),
                dist_traveled=_opt_float(r.get("shape_dist_traveled", "")),
            ))
        for lst in shapes.values():
            lst.sort(key=lambda x: x.seq)

        fare_attributes = {
            r["fare_id"]: FareAttribute(
                fare_id=r["fare_id"], price=float(r["price"]),
                currency_type=r.get("currency_type") or "BDT",
                payment_method=_opt_int(r.get("payment_method", ""), 1) or 1,
                transfers=_opt_int(r.get("transfers", "")),
                agency_id=r.get("agency_id", ""),
            )
            for r in raw.get("fare_attributes.txt", []) if r.get("fare_id")
        }
        fare_rules = [
            FareRule(
                fare_id=r["fare_id"], route_id=r.get("route_id", ""),
                origin_id=r.get("origin_id", ""), destination_id=r.get("destination_id", ""),
                contains_id=r.get("contains_id", ""),
            )
            for r in raw.get("fare_rules.txt", []) if r.get("fare_id")
        ]

        feed_info = dict(raw["feed_info.txt"][0]) if raw.get("feed_info.txt") else {}
        data_sources = {
            r["source_id"]: DataSource(
                source_id=r["source_id"], name=r.get("name", ""), url=r.get("url", ""),
                license=r.get("license", ""), category=r.get("category", "estimated"),
                retrieved_at=r.get("retrieved_at", ""), notes=r.get("notes", ""),
            )
            for r in raw.get("data_sources.txt", []) if r.get("source_id")
        }
        provenance: dict[tuple[str, str, str], Provenance] = {}
        for r in raw.get("provenance.txt", []):
            key = (r.get("entity_table", ""), r.get("entity_id", ""), r.get("field", ""))
            provenance[key] = Provenance(
                entity_table=key[0], entity_id=key[1], field=key[2],
                source_id=r.get("source_id", ""),
                category=SourceCategory(r["category"]),
                status=VerificationStatus(r.get("status") or "unverified"),
                verified_at=r.get("verified_at", ""), notes=r.get("notes", ""),
            )

        return cls(
            agencies=agencies, stops=stops, routes=routes, trips=trips,
            stop_times_by_trip=dict(st_by_trip), frequencies_by_trip=dict(freq_by_trip),
            calendars=calendars, calendar_dates=calendar_dates, shapes=dict(shapes),
            fare_attributes=fare_attributes, fare_rules=fare_rules, feed_info=feed_info,
            data_sources=data_sources, provenance=provenance,
        )
