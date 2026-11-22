"""Phase 4 tests: Dijkstra, RAPTOR, A*, fare lookup, and multimodal planner.

Includes:
- Dijkstra verified against exhaustive brute-force shortest path.
- RAPTOR verified against an independent time-expanded Dijkstra oracle.
- Multimodal Pareto test: 0-transfer slow bus vs 1-transfer bus + walk + metro.
- A* verified against Dijkstra on a 2D street grid (exactness + fewer expansions).
- Real Dhaka MRT-6 routing tests (Mirpur 10 -> Motijheel, Sat vs Fri, late night).
"""
from __future__ import annotations

import datetime as dt
import heapq
from collections import defaultdict
from pathlib import Path

import pytest

from transit.geo import GridIndex, haversine_m
from transit.graph.footpaths import build_footpaths
from transit.graph.frequency_graph import build_frequency_graph
from transit.graph.timetable import Timetable, build_timetable
from transit.gtfs.model import TransitFeed
from transit.routing.astar import WalkGraph, astar_walk, build_walk_graph_from_osm
from transit.routing.dijkstra import DEST, ORIGIN, dijkstra_shortest_path, plan_dijkstra
from transit.routing.journey import CostWeights
from transit.routing.planner import plan_journey
from transit.routing.raptor import plan_raptor

ROOT = Path(__file__).resolve().parents[2]
DHAKA_FEED = ROOT / "data" / "gtfs" / "dhaka"


# ---------------------------------------------------------------------------
# Synthetic multimodal fixture (2 bus routes + 1 express metro + walk transfer + fares)
# Layout (all inside Dhaka bbox, clearly prefixed TEST_):
#   Slow direct bus (BUS_SLOW): B_START -> B_MID -> B_END  (40 min total, every 15 min)
#   Feeder bus (BUS_FEED):      B_START -> B_XFER          (5 min, every 10 min)
#   Walk transfer (~150 m):     B_XFER  <-> M_XFER         (~156 s)
#   Express metro (METRO_EXP):  M_XFER  -> M_END           (8 min, every 10 min)
#   M_END is 100 m from B_END (both near destination).
# Expected Pareto set from B_START to M_END at 08:00:
#   - Round 1 (0 transfers): BUS_SLOW (40 min + egress walk)
#   - Round 2 (1 transfer):  BUS_FEED + walk transfer + METRO_EXP (~20 min, strictly earlier!)
# ---------------------------------------------------------------------------
def _multimodal_feed() -> TransitFeed:
    raw = {
        "agency.txt": [
            {"agency_id": "TEST_M", "agency_name": "Test Metro", "agency_url": "https://example.org", "agency_timezone": "Asia/Dhaka"},
            {"agency_id": "TEST_B", "agency_name": "Test Bus", "agency_url": "https://example.org", "agency_timezone": "Asia/Dhaka"},
        ],
        "stops.txt": [
            {"stop_id": "B_START", "stop_name": "Start Bus", "stop_lat": "23.810000", "stop_lon": "90.370000", "location_type": "0", "zone_id": "ZB1"},
            {"stop_id": "B_MID",   "stop_name": "Mid Bus",   "stop_lat": "23.780000", "stop_lon": "90.385000", "location_type": "0", "zone_id": "ZB2"},
            {"stop_id": "B_XFER",  "stop_name": "Xfer Bus",  "stop_lat": "23.800000", "stop_lon": "90.375000", "location_type": "0", "zone_id": "ZB3"},
            {"stop_id": "M_XFER",  "stop_name": "Xfer Metro","stop_lat": "23.801200", "stop_lon": "90.375000", "location_type": "0", "zone_id": "ZM1"},
            {"stop_id": "M_END",   "stop_name": "End Metro", "stop_lat": "23.730000", "stop_lon": "90.410000", "location_type": "0", "zone_id": "ZM2"},
            {"stop_id": "B_END",   "stop_name": "End Bus",   "stop_lat": "23.730800", "stop_lon": "90.410000", "location_type": "0", "zone_id": "ZB4"},
        ],
        "routes.txt": [
            {"route_id": "BUS_SLOW", "agency_id": "TEST_B", "route_short_name": "B-Slow", "route_long_name": "Direct Slow Bus", "route_type": "3", "route_color": "D97706"},
            {"route_id": "BUS_FEED", "agency_id": "TEST_B", "route_short_name": "B-Feed", "route_long_name": "Feeder Bus", "route_type": "3", "route_color": "2563EB"},
            {"route_id": "METRO_EXP", "agency_id": "TEST_M", "route_short_name": "M-Exp", "route_long_name": "Express Metro", "route_type": "1", "route_color": "006E34"},
        ],
        "calendar.txt": [
            {"service_id": "DAILY", "monday": "1", "tuesday": "1", "wednesday": "1", "thursday": "1",
             "friday": "1", "saturday": "1", "sunday": "1", "start_date": "20260101", "end_date": "20261231"},
        ],
        "calendar_dates.txt": [],
        "trips.txt": [
            {"route_id": "BUS_SLOW",  "service_id": "DAILY", "trip_id": "T_SLOW", "direction_id": "0", "trip_headsign": "End Bus"},
            {"route_id": "BUS_FEED",  "service_id": "DAILY", "trip_id": "T_FEED", "direction_id": "0", "trip_headsign": "Xfer Bus"},
            {"route_id": "METRO_EXP", "service_id": "DAILY", "trip_id": "T_MEXP", "direction_id": "0", "trip_headsign": "End Metro"},
        ],
        "stop_times.txt": [
            {"trip_id": "T_SLOW", "arrival_time": "08:00:00", "departure_time": "08:00:00", "stop_id": "B_START", "stop_sequence": "1"},
            {"trip_id": "T_SLOW", "arrival_time": "08:20:00", "departure_time": "08:20:00", "stop_id": "B_MID",   "stop_sequence": "2"},
            {"trip_id": "T_SLOW", "arrival_time": "08:40:00", "departure_time": "08:40:00", "stop_id": "B_END",   "stop_sequence": "3"},
            {"trip_id": "T_FEED", "arrival_time": "08:00:00", "departure_time": "08:00:00", "stop_id": "B_START", "stop_sequence": "1"},
            {"trip_id": "T_FEED", "arrival_time": "08:05:00", "departure_time": "08:05:00", "stop_id": "B_XFER",  "stop_sequence": "2"},
            {"trip_id": "T_MEXP", "arrival_time": "08:00:00", "departure_time": "08:00:00", "stop_id": "M_XFER",  "stop_sequence": "1"},
            {"trip_id": "T_MEXP", "arrival_time": "08:08:00", "departure_time": "08:08:00", "stop_id": "M_END",   "stop_sequence": "2"},
        ],
        "frequencies.txt": [
            {"trip_id": "T_SLOW", "start_time": "07:00:00", "end_time": "12:00:00", "headway_secs": "900", "exact_times": "0"},
            {"trip_id": "T_FEED", "start_time": "07:00:00", "end_time": "12:00:00", "headway_secs": "600", "exact_times": "0"},
            {"trip_id": "T_MEXP", "start_time": "07:00:00", "end_time": "12:00:00", "headway_secs": "600", "exact_times": "0"},
        ],
        "fare_attributes.txt": [
            {"fare_id": "F_BUS",   "price": "25", "currency_type": "BDT", "payment_method": "0", "transfers": "0", "agency_id": "TEST_B"},
            {"fare_id": "F_METRO", "price": "50", "currency_type": "BDT", "payment_method": "1", "transfers": "0", "agency_id": "TEST_M"},
        ],
        "fare_rules.txt": [
            {"fare_id": "F_BUS",   "route_id": "BUS_SLOW",  "origin_id": "", "destination_id": ""},
            {"fare_id": "F_BUS",   "route_id": "BUS_FEED",  "origin_id": "", "destination_id": ""},
            {"fare_id": "F_METRO", "route_id": "METRO_EXP", "origin_id": "ZM1", "destination_id": "ZM2"},
        ],
        "provenance.txt": [
            {"entity_table": "trips", "entity_id": "T_SLOW", "field": "", "source_id": "S", "category": "community", "status": "verified"},
            {"entity_table": "trips", "entity_id": "T_FEED", "field": "", "source_id": "S", "category": "community", "status": "verified"},
            {"entity_table": "trips", "entity_id": "T_MEXP", "field": "", "source_id": "S", "category": "official",  "status": "verified"},
            {"entity_table": "fare_attributes", "entity_id": "F_BUS",   "field": "", "source_id": "S", "category": "official", "status": "verified"},
            {"entity_table": "fare_attributes", "entity_id": "F_METRO", "field": "", "source_id": "S", "category": "official", "status": "verified"},
        ],
    }
    return TransitFeed.from_feed_dict(raw)


def _index_of(tf: TransitFeed) -> GridIndex[str]:
    idx: GridIndex[str] = GridIndex(cell_size_m=300.0)
    for sid, s in tf.boarding_stops.items():
        idx.insert(sid, s.stop_lat, s.stop_lon)
    return idx


# ---------------------------------------------------------------------------
# 1. Dijkstra vs exhaustive brute-force simple-path search
# ---------------------------------------------------------------------------
def test_dijkstra_matches_brute_force_on_multimodal_graph():
    tf = _multimodal_feed()
    fps = build_footpaths(tf.boarding_stops, max_walk_m=500.0)
    graph = build_frequency_graph(tf, fps)
    weights = CostWeights()

    # Connect ORIGIN directly to B_START (0 walk) and M_END directly to DEST (0 walk)
    from transit.graph.frequency_graph import Edge
    access = [Edge(to=("S", "B_START"), kind="walk", walk_s=0)]
    egress = {"M_END": Edge(to=DEST, kind="walk", walk_s=0)}

    got = dijkstra_shortest_path(graph, tf, access, egress, weights=weights)
    assert got is not None

    # Independent brute-force DFS over all simple paths in state space (node, has_boarded)
    best_bf = float("inf")

    def dfs(state, cost, visited):
        nonlocal best_bf
        if cost >= best_bf:
            return
        u, boarded = state
        if u == DEST:
            best_bf = cost
            return
        edges = access if u == ORIGIN else list(graph.adj.get(u, ()))
        if u[0] == "S" and u[1] in egress:
            edges = edges + [egress[u[1]]]
        for e in edges:
            if e.kind == "board":
                c = weights.wait_weight * e.wait_s + (weights.transfer_penalty_s if boarded else 0.0)
                nb = True
            elif e.kind == "ride":
                c, nb = weights.in_vehicle_weight * e.in_vehicle_s, boarded
            elif e.kind == "walk":
                c, nb = weights.walk_weight * e.walk_s, boarded
            else:
                c, nb = 0.0, boarded
            nxt = (e.to, nb)
            if nxt not in visited:
                visited.add(nxt)
                dfs(nxt, cost + c, visited)
                visited.remove(nxt)

    dfs((ORIGIN, False), 0.0, {(ORIGIN, False)})
    assert got.cost == pytest.approx(best_bf)


def test_dijkstra_direct_walk_beats_transit_for_short_hops():
    tf = _multimodal_feed()
    fps = build_footpaths(tf.boarding_stops)
    graph = build_frequency_graph(tf, fps)
    idx = _index_of(tf)

    # 80 m walk between B_XFER and M_XFER's vicinity -> direct walk must win
    j = plan_dijkstra(graph, tf, idx, 23.8000, 90.3750, 23.8007, 90.3750)
    assert j is not None
    assert j.algorithm == "walk_only"
    assert j.transfers == 0
    assert len(j.legs) == 1 and j.legs[0].mode == "walk"


# ---------------------------------------------------------------------------
# 2. Independent Time-Expanded Dijkstra oracle vs RAPTOR
# ---------------------------------------------------------------------------
def _time_expanded_earliest_arrival(
    tt: Timetable,
    start_stop: str,
    end_stop: str,
    departure_s: int,
) -> int | None:
    """Simple, independent time-dependent Dijkstra on the timetable (golden oracle)."""
    best: dict[str, int] = {start_stop: departure_s}
    pq: list[tuple[int, str]] = [(departure_s, start_stop)]
    while pq:
        t, u = heapq.heappop(pq)
        if t > best.get(u, 10**12):
            continue
        if u == end_stop:
            return t
        # Relax footpaths
        for fp in tt.footpaths.get(u, ()):
            nt = t + fp.duration_s
            if nt < best.get(fp.to_stop_id, 10**12):
                best[fp.to_stop_id] = nt
                heapq.heappush(pq, (nt, fp.to_stop_id))
        # Relax every catchable trip departing from u
        for pat_idx, pos in tt.routes_by_stop.get(u, ()):
            pat = tt.patterns[pat_idx]
            for tr in pat.trips:
                if tr.departures[pos] >= t:
                    for j in range(pos + 1, len(pat.stop_ids)):
                        v = pat.stop_ids[j]
                        arr = tr.arrivals[j]
                        if arr < best.get(v, 10**12):
                            best[v] = arr
                            heapq.heappush(pq, (arr, v))
                    break  # earliest catchable trip on this pattern is optimal
    return None


@pytest.mark.parametrize("dep_s", [7 * 3600 + 58 * 60, 8 * 3600, 8 * 3600 + 3 * 60, 9 * 3600 + 12 * 60])
def test_raptor_matches_time_expanded_oracle_and_finds_pareto_set(dep_s):
    tf = _multimodal_feed()
    fps = build_footpaths(tf.boarding_stops, max_walk_m=500.0)
    tt = build_timetable(tf, fps, {"DAILY"})
    idx = _index_of(tf)

    orig = tf.stops["B_START"]
    dest = tf.stops["M_END"]
    # Tight access radius (50 m) so origin=B_START and dest=M_END exactly match the oracle
    weights = CostWeights(max_access_walk_m=50.0)
    journeys = plan_raptor(
        tt, tf, idx, orig.stop_lat, orig.stop_lon, dest.stop_lat, dest.stop_lon,
        departure_s=dep_s, weights=weights,
    )
    oracle_arr = _time_expanded_earliest_arrival(tt, "B_START", "M_END", dep_s)
    assert oracle_arr is not None
    assert journeys
    # Fastest RAPTOR journey must match the time-expanded oracle to the exact second!
    assert min(j.arrival_s for j in journeys) == oracle_arr

    # Check multimodal Pareto set at 08:00:
    # - 0 transfers: BUS_SLOW (B_START -> B_END + walk transfer B_END -> M_END)
    # - 1 transfer:  BUS_FEED (B_START -> B_XFER) + walk transfer + METRO_EXP (M_XFER -> M_END)
    if dep_s == 8 * 3600:
        assert len(journeys) == 2
        j0, j1 = journeys[0], journeys[1]
        assert j0.transfers == 0 and j1.transfers == 1
        assert j1.arrival_s < j0.arrival_s  # strict Pareto improvement!
        # Check multimodal leg structure and summed GTFS fares (25 + 50 = 75 BDT)
        modes = [l.mode for l in j1.legs]
        assert modes == ["bus", "transfer", "metro"]
        assert j1.fare.complete is True and j1.fare.total_bdt == 75.0
        assert j0.fare.complete is True and j0.fare.total_bdt == 25.0


def test_mode_filter_restricts_both_engines():
    tf = _multimodal_feed()
    fps = build_footpaths(tf.boarding_stops, max_walk_m=500.0)
    graph = build_frequency_graph(tf, fps)
    tt = build_timetable(tf, fps, {"DAILY"})
    idx = _index_of(tf)
    orig, dest = tf.stops["B_START"], tf.stops["M_END"]

    res = plan_journey(
        feed=tf, graph=graph, timetable=tt, boarding_index=idx,
        from_lat=orig.stop_lat, from_lon=orig.stop_lon, to_lat=dest.stop_lat, to_lon=dest.stop_lon,
        service_date=dt.date(2026, 11, 21), departure_s=8 * 3600,
        allowed_modes={"bus"},
    )
    assert res.recommended is not None
    assert all(l.mode != "metro" for l in res.recommended.legs)
    assert any(l.route_id == "BUS_SLOW" for l in res.recommended.legs)


# ---------------------------------------------------------------------------
# 3. A* walking optimizer vs Dijkstra on a 2D street grid
# ---------------------------------------------------------------------------
def test_astar_matches_dijkstra_and_expands_fewer_nodes():
    g = WalkGraph()
    n = 15
    blocked = {(r, 7) for r in range(2, 13)}  # vertical wall forcing a realistic detour
    for r in range(n):
        for c in range(n):
            if (r, c) not in blocked:
                g.add_node(f"{r}_{c}", 23.75 + r * 0.001, 90.38 + c * 0.001)
    for r in range(n):
        for c in range(n):
            if (r, c) in blocked:
                continue
            for dr, dc in ((1, 0), (0, 1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < n and 0 <= nc < n and (nr, nc) not in blocked:
                    g.add_edge(f"{r}_{c}", f"{nr}_{nc}", bidirectional=True)

    res_astar = astar_walk(g, "7_0", "7_14", use_heuristic=True)
    res_dijk = astar_walk(g, "7_0", "7_14", use_heuristic=False)
    assert res_astar is not None and res_dijk is not None
    assert res_astar.distance_m == pytest.approx(res_dijk.distance_m, abs=0.2)
    assert res_astar.duration_s == res_dijk.duration_s
    assert res_astar.nodes_expanded < res_dijk.nodes_expanded


def test_build_walk_graph_from_osm_filters_private_and_non_walkable():
    els = [
        {"type": "node", "id": 1, "lat": 23.8000, "lon": 90.4000},
        {"type": "node", "id": 2, "lat": 23.8010, "lon": 90.4000},
        {"type": "node", "id": 3, "lat": 23.8020, "lon": 90.4000},
        {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"highway": "footway"}},
        {"type": "way", "id": 11, "nodes": [2, 3], "tags": {"highway": "motorway"}},          # excluded
        {"type": "way", "id": 12, "nodes": [2, 3], "tags": {"highway": "path", "foot": "no"}},  # excluded
    ]
    wg = build_walk_graph_from_osm(els)
    assert astar_walk(wg, "1", "2") is not None
    assert astar_walk(wg, "1", "3") is None


# ---------------------------------------------------------------------------
# 4. Real Dhaka MRT-6 routing tests (Mirpur 10 <-> Motijheel)
# ---------------------------------------------------------------------------
def test_real_dhaka_mirpur10_to_motijheel_sat_fri_and_late_night():
    tf = TransitFeed.from_path(DHAKA_FEED)
    fps = build_footpaths(tf.boarding_stops)
    graph = build_frequency_graph(tf, fps)
    idx = _index_of(tf)

    mirpur = tf.stops["mrt6_mirpur_10"]
    motijheel = tf.stops["mrt6_motijheel"]

    # Saturday 08:30: active MRT-6 service
    sat = dt.date(2026, 11, 21)
    tt_sat = build_timetable(tf, fps, tf.active_service_ids(sat))
    res_sat = plan_journey(
        feed=tf, graph=graph, timetable=tt_sat, boarding_index=idx,
        from_lat=mirpur.stop_lat, from_lon=mirpur.stop_lon,
        to_lat=motijheel.stop_lat, to_lon=motijheel.stop_lon,
        from_name="Mirpur 10", to_name="Motijheel",
        service_date=sat, departure_s=8 * 3600 + 30 * 60,
    )
    rec = res_sat.recommended
    assert rec is not None
    assert rec.algorithm == "raptor"
    assert rec.transfers == 0
    assert rec.is_estimated is True
    assert rec.fare.complete is False and rec.fare.total_bdt is None  # honest: no verified fare table yet
    assert res_sat.comparison["same_best_route"] is True
    metro_legs = [l for l in rec.legs if l.mode == "metro"]
    assert len(metro_legs) == 1
    assert metro_legs[0].route_id == "mrt6"
    assert len(metro_legs[0].intermediate_stops) == 11  # Mirpur 10 .. Motijheel inclusive
    assert len(metro_legs[0].geometry) > 50            # sliced from real OSM track shape!

    # Friday 08:30: MRT-6 starts at 15:00 -> RAPTOR catches the first afternoon train (> 15:00)
    fri = dt.date(2026, 11, 20)
    tt_fri = build_timetable(tf, fps, tf.active_service_ids(fri))
    res_fri = plan_journey(
        feed=tf, graph=graph, timetable=tt_fri, boarding_index=idx,
        from_lat=mirpur.stop_lat, from_lon=mirpur.stop_lon,
        to_lat=motijheel.stop_lat, to_lon=motijheel.stop_lon,
        service_date=fri, departure_s=8 * 3600 + 30 * 60, algorithm="raptor",
    )
    assert res_fri.recommended is not None
    assert res_fri.recommended.arrival_s > 15 * 3600

    # Saturday 23:30 (outside service hours): falls back to Dijkstra with explicit warning
    res_late = plan_journey(
        feed=tf, graph=graph, timetable=tt_sat, boarding_index=idx,
        from_lat=mirpur.stop_lat, from_lon=mirpur.stop_lon,
        to_lat=motijheel.stop_lat, to_lon=motijheel.stop_lon,
        service_date=sat, departure_s=23 * 3600 + 30 * 60, algorithm="combined",
    )
    assert res_late.recommended is not None
    assert res_late.recommended.algorithm == "dijkstra"
    assert res_late.recommended.outside_service_hours is True
    assert any("outside service hours" in w.lower() for w in res_late.warnings)
