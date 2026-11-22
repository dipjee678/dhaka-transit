"""RAPTOR (Round-Based Public Transit Routing) engine.

Reference: Delling, Pajor, Werneck (2015), *Round-Based Public Transit Routing*,
Transportation Science 49(3): 591-604.

How the algorithm operates (see docs/PROJECT_PLAN.md §7.2):
- Round 0 : initialize earliest arrival ``tau[0][p]`` at each access stop ``p``
            within walking distance of the origin, and mark ``p``.
- Round k : represents journeys using at most ``k`` transit trips (``k - 1`` transfers).
  1. Collect ``Q[pattern_idx] = earliest marked stop position`` along each route
     pattern serving any stop marked in round ``k - 1``.
  2. Scan each pattern in ``Q`` forward from ``start_pos``, maintaining the
     earliest catchable trip ``current_trip``. At each stop ``p``, relax
     ``tau[k][p]`` if the trip improves both ``tau_star[p]`` (local pruning)
     and ``best_dest_arrival`` (target pruning). Then check if ``tau[k-1][p]``
     allows catching an earlier trip on the same route.
  3. Relax precomputed (transitively closed) footpaths from every stop improved
     in Step 2.
  4. Stop early when no stop was improved in round ``k``.
- Pareto set : keep round ``k``'s best arrival at destination only when it
  strictly beats every round ``< k``, producing optimal trade-offs between
  travel time and number of transfers.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from transit.geo import DETOUR_FACTOR, GridIndex, walk_distance_m, walk_time_s
from transit.graph.footpaths import Footpath
from transit.graph.timetable import RoutePattern, ScheduledTrip, Timetable
from transit.gtfs.model import TransitFeed
from transit.provenance import SourceCategory
from transit.routing.journey import (
    CostWeights, Journey, Leg, polyline_length_m, slice_route_geometry, summarize_journey,
)

INF = 10**12


@dataclass(frozen=True)
class _AccessPtr:
    walk_s: int
    distance_m: float


@dataclass(frozen=True)
class _TripPtr:
    pattern_idx: int
    trip: ScheduledTrip
    board_pos: int
    alight_pos: int
    board_time: int
    ready_time: int   # tau[k-1][board_stop] -> wait_s = board_time - ready_time


@dataclass(frozen=True)
class _FootpathPtr:
    from_stop_id: str
    footpath: Footpath


_BackPtr = _AccessPtr | _TripPtr | _FootpathPtr


def _earliest_trip(pattern: RoutePattern, pos: int, ready_s: int) -> ScheduledTrip | None:
    """Earliest trip in ``pattern`` whose departure at ``pos`` is >= ``ready_s``."""
    trips = pattern.trips
    if not trips:
        return None
    # Binary search assumes non-overtaking trips along a pattern (standard in GTFS).
    lo, hi = 0, len(trips)
    while lo < hi:
        mid = (lo + hi) // 2
        if trips[mid].departures[pos] < ready_s:
            lo = mid + 1
        else:
            hi = mid
    if lo < len(trips):
        return trips[lo]
    return None


def plan_raptor(
    timetable: Timetable,
    feed: TransitFeed,
    boarding_index: GridIndex[str],
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    departure_s: int,
    from_name: str = "Origin",
    to_name: str = "Destination",
    max_rounds: int = 5,
    weights: CostWeights = CostWeights(),
    allowed_modes: set[str] | None = None,
) -> list[Journey]:
    """Run RAPTOR from ``(from_lat, from_lon)`` to ``(to_lat, to_lon)`` at ``departure_s``.

    Returns the Pareto-optimal set of journeys (earliest arrival for each
    number of transfers, sorted by transfers ascending / arrival descending).
    """
    t0 = time.perf_counter()

    # Access & egress stops
    access_hits = boarding_index.within(from_lat, from_lon, weights.max_access_walk_m / DETOUR_FACTOR)
    if not access_hits:
        access_hits = boarding_index.nearest(from_lat, from_lon, k=4, max_radius_m=3000.0)

    egress_hits = boarding_index.within(to_lat, to_lon, weights.max_access_walk_m / DETOUR_FACTOR)
    if not egress_hits:
        egress_hits = boarding_index.nearest(to_lat, to_lon, k=4, max_radius_m=3000.0)

    tau: list[dict[str, int]] = [{} for _ in range(max_rounds + 1)]
    parent: list[dict[str, _BackPtr]] = [{} for _ in range(max_rounds + 1)]
    tau_star: dict[str, int] = {}
    marked: set[str] = set()

    # Round 0: access walks to nearby boarding stops
    for h in access_hits:
        s = feed.stops[h.key]
        wd = walk_distance_m(from_lat, from_lon, s.stop_lat, s.stop_lon)
        if wd <= weights.max_access_walk_m or len(tau[0]) < 2:
            ws = 0 if wd < 5.0 else walk_time_s(wd)
            arr = departure_s + ws
            tau[0][h.key] = arr
            tau_star[h.key] = arr
            parent[0][h.key] = _AccessPtr(walk_s=ws, distance_m=0.0 if wd < 5.0 else round(wd, 1))
            marked.add(h.key)

    egress: dict[str, tuple[int, float]] = {}
    for h in egress_hits:
        s = feed.stops[h.key]
        wd = walk_distance_m(s.stop_lat, s.stop_lon, to_lat, to_lon)
        if wd <= weights.max_access_walk_m or len(egress) < 2:
            egress[h.key] = (0, 0.0) if wd < 5.0 else (walk_time_s(wd), round(wd, 1))

    # Direct walk baseline for target pruning
    direct_wd = walk_distance_m(from_lat, from_lon, to_lat, to_lon)
    direct_walk_s = walk_time_s(direct_wd)
    best_dest_arrival = departure_s + direct_walk_s if direct_wd <= weights.max_direct_walk_m else INF

    # Track best egress stop per round k (strictly improving over earlier rounds)
    round_best_dest: dict[int, tuple[int, str]] = {}

    for k in range(1, max_rounds + 1):
        # Step 1: accumulate route patterns serving stops marked in round k - 1
        queue: dict[int, int] = {}
        for stop_id in marked:
            for pat_idx, pos in timetable.routes_by_stop.get(stop_id, ()):
                pat = timetable.patterns[pat_idx]
                if allowed_modes is not None:
                    route = feed.routes.get(pat.route_id)
                    if route is not None and route.mode not in allowed_modes:
                        continue
                if pat_idx not in queue or pos < queue[pat_idx]:
                    queue[pat_idx] = pos

        transit_marked: set[str] = set()

        # Step 2: scan each queued route pattern
        for pat_idx, start_pos in queue.items():
            pat = timetable.patterns[pat_idx]
            current_trip: ScheduledTrip | None = None
            board_pos = -1
            board_time = -1
            ready_at_board = -1

            for pos in range(start_pos, len(pat.stop_ids)):
                p = pat.stop_ids[pos]

                # Can the currently boarded trip improve stop p?
                if current_trip is not None:
                    arr = current_trip.arrivals[pos]
                    if arr < tau_star.get(p, INF) and arr < best_dest_arrival:
                        tau[k][p] = arr
                        tau_star[p] = arr
                        parent[k][p] = _TripPtr(
                            pattern_idx=pat_idx, trip=current_trip,
                            board_pos=board_pos, alight_pos=pos,
                            board_time=board_time, ready_time=ready_at_board,
                        )
                        transit_marked.add(p)
                        if p in egress:
                            dest_arr = arr + egress[p][0]
                            if dest_arr < best_dest_arrival:
                                best_dest_arrival = dest_arr
                                round_best_dest[k] = (dest_arr, p)

                # Can we board an earlier trip at p using round k - 1 arrival?
                prev_arr = tau[k - 1].get(p)
                if prev_arr is not None:
                    if current_trip is None or prev_arr <= current_trip.departures[pos]:
                        cand = _earliest_trip(pat, pos, prev_arr)
                        if cand is not None and (
                            current_trip is None or cand.departures[pos] < current_trip.departures[pos]
                        ):
                            current_trip = cand
                            board_pos = pos
                            board_time = cand.departures[pos]
                            ready_at_board = prev_arr

        # Step 3: relax footpaths from stops improved by transit in this round
        next_marked = set(transit_marked)
        for p in transit_marked:
            arr_p = tau[k][p]
            for fp in timetable.footpaths.get(p, ()):
                cand_arr = arr_p + fp.duration_s
                if cand_arr < tau_star.get(fp.to_stop_id, INF) and cand_arr < best_dest_arrival:
                    tau[k][fp.to_stop_id] = cand_arr
                    tau_star[fp.to_stop_id] = cand_arr
                    parent[k][fp.to_stop_id] = _FootpathPtr(from_stop_id=p, footpath=fp)
                    next_marked.add(fp.to_stop_id)
                    if fp.to_stop_id in egress:
                        dest_arr = cand_arr + egress[fp.to_stop_id][0]
                        if dest_arr < best_dest_arrival:
                            best_dest_arrival = dest_arr
                            round_best_dest[k] = (dest_arr, fp.to_stop_id)

        marked = next_marked
        if not marked:
            break

    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Reconstruct Pareto-optimal journeys (each round k in round_best_dest strictly improved arrival)
    journeys: list[Journey] = []
    for k in sorted(round_best_dest):
        dest_arr, egress_stop = round_best_dest[k]
        legs = _reconstruct_raptor_legs(
            k=k, egress_stop=egress_stop, dest_arr=dest_arr, egress=egress,
            parent=parent, timetable=timetable, feed=feed,
            from_lat=from_lat, from_lon=from_lon, from_name=from_name,
            to_lat=to_lat, to_lon=to_lon, to_name=to_name,
            departure_s=departure_s,
        )
        journeys.append(summarize_journey(
            algorithm="raptor",
            legs=legs,
            feed=feed,
            weights=weights,
            departure_s=departure_s,
            arrival_s=dest_arr,
            computation_ms=elapsed_ms,
        ))

    # If no transit journey beat walking and direct walk is within range, return walk-only option.
    if not journeys and direct_wd <= weights.max_direct_walk_m:
        walk_leg = Leg(
            mode="walk", from_name=from_name, from_lat=from_lat, from_lon=from_lon,
            to_name=to_name, to_lat=to_lat, to_lon=to_lon,
            duration_s=direct_walk_s, distance_m=round(direct_wd, 1),
            departure_s=departure_s, arrival_s=departure_s + direct_walk_s,
            geometry=((from_lat, from_lon), (to_lat, to_lon)),
            provenance=SourceCategory.ESTIMATED,
        )
        journeys.append(summarize_journey(
            algorithm="walk_only",
            legs=[walk_leg],
            feed=feed,
            weights=weights,
            departure_s=departure_s,
            arrival_s=departure_s + direct_walk_s,
            computation_ms=elapsed_ms,
        ))

    return journeys


def _reconstruct_raptor_legs(
    *,
    k: int,
    egress_stop: str,
    dest_arr: int,
    egress: dict[str, tuple[int, float]],
    parent: list[dict[str, _BackPtr]],
    timetable: Timetable,
    feed: TransitFeed,
    from_lat: float,
    from_lon: float,
    from_name: str,
    to_lat: float,
    to_lon: float,
    to_name: str,
    departure_s: int,
) -> list[Leg]:
    legs_rev: list[Leg] = []

    # Egress walk
    eg_walk_s, eg_dist_m = egress[egress_stop]
    if eg_dist_m >= 5.0:
        es = feed.stops[egress_stop]
        legs_rev.append(Leg(
            mode="walk", from_name=es.stop_name, from_lat=es.stop_lat, from_lon=es.stop_lon,
            from_stop_id=es.stop_id, to_name=to_name, to_lat=to_lat, to_lon=to_lon,
            duration_s=eg_walk_s, distance_m=eg_dist_m,
            departure_s=dest_arr - eg_walk_s, arrival_s=dest_arr,
            geometry=((es.stop_lat, es.stop_lon), (to_lat, to_lon)),
            provenance=SourceCategory.ESTIMATED,
        ))

    cur_round = k
    cur_stop = egress_stop

    while cur_round >= 0:
        ptr = parent[cur_round][cur_stop]
        if isinstance(ptr, _FootpathPtr):
            fp = ptr.footpath
            sf, st = feed.stops[fp.from_stop_id], feed.stops[fp.to_stop_id]
            arr_t = dest_arr - eg_walk_s if cur_round == k and cur_stop == egress_stop else None
            legs_rev.append(Leg(
                mode="transfer", from_name=sf.stop_name, from_lat=sf.stop_lat, from_lon=sf.stop_lon,
                from_stop_id=sf.stop_id, to_name=st.stop_name, to_lat=st.stop_lat, to_lon=st.stop_lon,
                to_stop_id=st.stop_id, duration_s=fp.duration_s, distance_m=fp.distance_m,
                departure_s=(arr_t - fp.duration_s) if arr_t is not None else None,
                arrival_s=arr_t,
                geometry=((sf.stop_lat, sf.stop_lon), (st.stop_lat, st.stop_lon)),
                provenance=SourceCategory.ESTIMATED,
            ))
            cur_stop = fp.from_stop_id
        elif isinstance(ptr, _TripPtr):
            pat = timetable.patterns[ptr.pattern_idx]
            tr = ptr.trip
            passed_ids = pat.stop_ids[ptr.board_pos : ptr.alight_pos + 1]
            passed_stops = [feed.stops[sid] for sid in passed_ids]
            board_s, alight_s = passed_stops[0], passed_stops[-1]
            route = feed.routes[tr.route_id]
            base_trip = feed.trips.get(tr.base_trip_id)
            shape_id = base_trip.shape_id if base_trip else ""
            headsign = base_trip.trip_headsign if base_trip else ""
            stop_coords = [(s.stop_lat, s.stop_lon) for s in passed_stops]
            geom = slice_route_geometry(feed, shape_id, board_s, alight_s, stop_coords)
            arr_time = tr.arrivals[ptr.alight_pos]
            wait_s = max(0, ptr.board_time - ptr.ready_time)

            inter = tuple(
                {
                    "stop_id": s.stop_id, "stop_name": s.stop_name,
                    "lat": s.stop_lat, "lon": s.stop_lon,
                    "arrival_s": tr.arrivals[ptr.board_pos + idx],
                }
                for idx, s in enumerate(passed_stops)
            )
            legs_rev.append(Leg(
                mode=route.mode,
                from_name=board_s.stop_name, from_lat=board_s.stop_lat, from_lon=board_s.stop_lon,
                from_stop_id=board_s.stop_id,
                to_name=alight_s.stop_name, to_lat=alight_s.stop_lat, to_lon=alight_s.stop_lon,
                to_stop_id=alight_s.stop_id,
                duration_s=max(0, arr_time - ptr.board_time),
                wait_s=wait_s,
                distance_m=round(polyline_length_m(geom), 1),
                departure_s=ptr.board_time,
                arrival_s=arr_time,
                route_id=tr.route_id,
                route_short_name=route.route_short_name,
                route_long_name=route.route_long_name,
                route_color=route.route_color,
                direction_id=tr.direction_id,
                trip_id=tr.trip_id,
                headsign=headsign,
                intermediate_stops=inter,
                geometry=geom,
                provenance=tr.provenance,
            ))
            cur_stop = pat.stop_ids[ptr.board_pos]
            cur_round -= 1
        elif isinstance(ptr, _AccessPtr):
            if ptr.distance_m >= 5.0:
                st = feed.stops[cur_stop]
                legs_rev.append(Leg(
                    mode="walk", from_name=from_name, from_lat=from_lat, from_lon=from_lon,
                    to_name=st.stop_name, to_lat=st.stop_lat, to_lon=st.stop_lon,
                    to_stop_id=st.stop_id, duration_s=ptr.walk_s, distance_m=ptr.distance_m,
                    departure_s=departure_s, arrival_s=departure_s + ptr.walk_s,
                    geometry=((from_lat, from_lon), (st.stop_lat, st.stop_lon)),
                    provenance=SourceCategory.ESTIMATED,
                ))
            break

    legs_rev.reverse()
    # Fill any missing timestamps on transfer legs cleanly from the preceding leg's arrival.
    fixed: list[Leg] = []
    for leg in legs_rev:
        if leg.mode == "transfer" and leg.departure_s is None and fixed and fixed[-1].arrival_s is not None:
            dep = fixed[-1].arrival_s
            fixed.append(Leg(
                **{**leg.__dict__, "departure_s": dep, "arrival_s": dep + leg.duration_s}
            ))
        else:
            fixed.append(leg)
    return fixed
