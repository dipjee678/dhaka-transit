"""Timetable data structure for the RAPTOR public-transit routing algorithm.

Key RAPTOR definitions (Delling, Pajor, Werneck 2015):
- A **RoutePattern** is a maximal set of trips that serve the exact same ordered
  sequence of stops (and share the same GTFS ``route_id`` and ``direction_id``).
- Frequency-based trips (``frequencies.txt``) are expanded into concrete trips
  departing at ``start_s, start_s + headway_secs, ... < end_s``.
- Within each pattern, trips are sorted by departure time at stop 0 so RAPTOR
  can scan backwards/forwards for the earliest catchable trip in O(|trips|).
- ``routes_by_stop[stop_id]`` gives ``(pattern_idx, stop_pos)`` for fast lookup
  of every route pattern serving a marked stop.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from transit.graph.footpaths import Footpath, footpaths_by_stop
from transit.gtfs.model import TransitFeed
from transit.provenance import SourceCategory


@dataclass(frozen=True)
class ScheduledTrip:
    trip_id: str          # concrete id (e.g. 'mrt6_sat_thu_d0@06:40:00' for frequency expansions)
    base_trip_id: str     # GTFS trip_id
    route_id: str
    direction_id: int
    arrivals: tuple[int, ...]     # seconds since midnight, aligned with pattern.stop_ids
    departures: tuple[int, ...]
    provenance: SourceCategory


@dataclass(frozen=True)
class RoutePattern:
    pattern_idx: int
    route_id: str
    direction_id: int
    stop_ids: tuple[str, ...]
    trips: tuple[ScheduledTrip, ...]  # sorted ascending by departures[0]

    def stop_index(self, stop_id: str) -> int:
        return self.stop_ids.index(stop_id)


@dataclass
class Timetable:
    patterns: tuple[RoutePattern, ...]
    routes_by_stop: dict[str, list[tuple[int, int]]]  # stop_id -> [(pattern_idx, pos_in_pattern)]
    footpaths: dict[str, list[Footpath]]
    stop_ids: frozenset[str]

    def stats(self) -> dict:
        return {
            "patterns": len(self.patterns),
            "trips": sum(len(p.trips) for p in self.patterns),
            "served_stops": len(self.routes_by_stop),
            "footpaths": sum(len(v) for v in self.footpaths.values()),
        }


def build_timetable(
    feed: TransitFeed,
    footpaths: list[Footpath],
    service_ids: set[str] | None = None,
) -> Timetable:
    """Build a RAPTOR :class:`Timetable` for the active ``service_ids``."""
    grouped: dict[tuple[str, int, tuple[str, ...]], list[ScheduledTrip]] = defaultdict(list)

    for trip in feed.trips.values():
        if service_ids is not None and trip.service_id not in service_ids:
            continue
        sts = feed.stop_times_by_trip.get(trip.trip_id, [])
        if len(sts) < 2:
            continue

        stop_seq = tuple(st.stop_id for st in sts)
        base_dep = sts[0].departure_s
        rel_arr = tuple(st.arrival_s - base_dep for st in sts)
        rel_dep = tuple(st.departure_s - base_dep for st in sts)

        prov_rec = feed.provenance_for("stop_times", trip.trip_id) or feed.provenance_for("trips", trip.trip_id)
        prov = prov_rec.category if prov_rec else SourceCategory.ESTIMATED
        key = (trip.route_id, trip.direction_id, stop_seq)

        freqs = feed.frequencies_by_trip.get(trip.trip_id, [])
        if freqs:
            for f in freqs:
                t = f.start_s
                while t < f.end_s:
                    grouped[key].append(ScheduledTrip(
                        trip_id=f"{trip.trip_id}@{t}",
                        base_trip_id=trip.trip_id,
                        route_id=trip.route_id,
                        direction_id=trip.direction_id,
                        arrivals=tuple(t + d for d in rel_arr),
                        departures=tuple(t + d for d in rel_dep),
                        provenance=prov,
                    ))
                    t += f.headway_secs
        else:
            grouped[key].append(ScheduledTrip(
                trip_id=trip.trip_id,
                base_trip_id=trip.trip_id,
                route_id=trip.route_id,
                direction_id=trip.direction_id,
                arrivals=tuple(st.arrival_s for st in sts),
                departures=tuple(st.departure_s for st in sts),
                provenance=prov,
            ))

    patterns: list[RoutePattern] = []
    rbs: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for idx, ((rid, did, stop_seq), trips_list) in enumerate(sorted(grouped.items())):
        trips_list.sort(key=lambda tr: tr.departures[0])
        pat = RoutePattern(
            pattern_idx=idx, route_id=rid, direction_id=did,
            stop_ids=stop_seq, trips=tuple(trips_list),
        )
        patterns.append(pat)
        for pos, sid in enumerate(stop_seq):
            rbs[sid].append((idx, pos))

    return Timetable(
        patterns=tuple(patterns),
        routes_by_stop=dict(rbs),
        footpaths=footpaths_by_stop(footpaths),
        stop_ids=frozenset(feed.boarding_stops.keys()),
    )
