"""Phase 3 tests: TransitFeed calendar resolution, footpaths, frequency graph, and RAPTOR timetable."""
from __future__ import annotations

import datetime as dt
from collections import deque
from pathlib import Path

from transit.graph.footpaths import build_footpaths
from transit.graph.frequency_graph import build_frequency_graph
from transit.graph.timetable import build_timetable
from transit.gtfs.model import Stop, TransitFeed

ROOT = Path(__file__).resolve().parents[2]
DHAKA_FEED = ROOT / "data" / "gtfs" / "dhaka"


def test_active_service_ids_sat_thu_vs_friday():
    tf = TransitFeed.from_path(DHAKA_FEED)
    # 2026-11-21 is Saturday; 2026-11-20 is Friday
    assert tf.active_service_ids(dt.date(2026, 11, 21)) == {"mrt6_sat_thu"}
    assert tf.active_service_ids(dt.date(2026, 11, 20)) == {"mrt6_fri"}
    # Outside feed date window
    assert tf.active_service_ids(dt.date(2025, 1, 1)) == set()


def test_active_service_ids_applies_calendar_dates(feed):
    # In toy_feed, TEST_SVC runs Mon-Thu+Sat-Sun and is REMOVED on 20261216 (Wednesday)
    feed["calendar_dates.txt"].append({"service_id": "EXTRA_SVC", "date": "20261216", "exception_type": "1"})
    tf = TransitFeed.from_feed_dict(feed)
    assert "TEST_SVC" in tf.active_service_ids(dt.date(2026, 12, 15))
    assert tf.active_service_ids(dt.date(2026, 12, 16)) == {"EXTRA_SVC"}


def test_footpaths_station_transfer_and_transitive_closure():
    # Three stops in a straight line: A (0 m) -- 160 m -- B -- 160 m -- C (320 m straight line)
    # With detour 1.3: A-B walk = 208 m, B-C walk = 208 m, A-C direct walk = 416 m.
    # Set max_walk_m = 350 so A-C is NOT a direct edge, then check transitive closure:
    # A-B (~167 s) + B-C (~167 s) = 334 s > max_duration_s (280 s), so NOT closed when > budget;
    # Now use 120 m spacing: A-B walk=156 m (125 s), B-C=156 m (125 s), A-C direct=312 m (250 s).
    # With max_walk_m=300 (budget 240 s): wait — let's test closure with 100 m spacing & 250 m max_walk!
    # 100 m straight -> 130 m walk (104 s). 200 m straight -> 260 m walk (> 250 m direct),
    # wait: 104 + 104 = 208 s > 200 s (250/1.25). Use detour_factor=1.0, max_walk_m=220 -> 176 s budget,
    # A-B = 100 m (80 s), B-C = 100 m (80 s), A-C direct = 200 m (160 s) -> already direct.
    # For a true transitive-closure case: B has parent_station transfer (0 m / 90 s) to B2,
    # and A is 50 m from B (40 s), C is 50 m from B2 (40 s), while A and C are 250 m apart.
    stops = {
        "A": Stop("A", "Stop A", 23.8000, 90.4000, 0),
        "B": Stop("B", "Stop B", 23.8004, 90.4000, 0, parent_station="ST"),
        "B2": Stop("B2", "Stop B2", 23.8020, 90.4000, 0, parent_station="ST"),  # ~178 m from B, same station!
        "C": Stop("C", "Stop C", 23.8024, 90.4000, 0),                           # ~267 m from A
    }
    fps = build_footpaths(stops, max_walk_m=220.0, detour_factor=1.0, station_transfer_s=60, transitive_closure=True)
    pairs = {(f.from_stop_id, f.to_stop_id): f.duration_s for f in fps}
    assert ("B", "B2") in pairs and pairs[("B", "B2")] >= 60
    # A -> B (45 m, 36 s) -> B2 (178 m, 143 s) -> wait: 36 + 143 = 179 s > 176 s; use max_walk_m=260 instead
    fps2 = build_footpaths(stops, max_walk_m=260.0, detour_factor=1.0, station_transfer_s=60, transitive_closure=True)
    pairs2 = {(f.from_stop_id, f.to_stop_id): f.duration_s for f in fps2}
    # A->C direct is ~267 m (> 260 m), but A->B (36 s) + B->B2 (143 s) = 179 s <= 208 s!
    assert ("A", "B2") in pairs2
    assert pairs2[("A", "B2")] <= pairs2[("A", "B")] + pairs2[("B", "B2")]


def test_frequency_graph_connects_all_mrt6_stations():
    tf = TransitFeed.from_path(DHAKA_FEED)
    fps = build_footpaths(tf.boarding_stops)
    g = build_frequency_graph(tf, fps)
    stats = g.stats()
    assert stats["stop_nodes"] == 32
    assert stats["routes"] == 1
    assert stats["edges_by_kind"]["ride"] == 30     # 15 segments x 2 directions
    assert stats["edges_by_kind"]["board"] == 32
    assert stats["edges_by_kind"]["alight"] == 32
    assert stats["edges_by_kind"]["walk"] == 32

    # Expected wait for MRT-6 (headway 600 s) must be 300 s
    board_edges = [e for es in g.adj.values() for e in es if e.kind == "board"]
    assert all(e.wait_s == 300 for e in board_edges)

    # BFS from Uttara North platform d0 must reach every single boarding platform (both directions!)
    start = ("S", "mrt6_uttara_north_d0")
    seen = {start}
    q = deque([start])
    while q:
        u = q.popleft()
        for e in g.adj.get(u, ()):
            if e.to not in seen:
                seen.add(e.to)
                q.append(e.to)
    reached_stops = {n[1] for n in seen if n[0] == "S"}
    assert reached_stops == set(tf.boarding_stops.keys())


def test_timetable_expands_frequencies_for_weekday_and_friday():
    tf = TransitFeed.from_path(DHAKA_FEED)
    fps = build_footpaths(tf.boarding_stops)

    sat_tt = build_timetable(tf, fps, tf.active_service_ids(dt.date(2026, 11, 21)))
    assert len(sat_tt.patterns) == 2
    p0, p1 = sat_tt.patterns
    assert p0.direction_id == 0 and p1.direction_id == 1
    # 06:30 to 21:51 every 10 min -> 93 departures (06:30, 06:40, ..., 21:50)
    assert len(p0.trips) == 93
    assert p0.trips[0].departures[0] == 6 * 3600 + 30 * 60
    assert p0.trips[-1].departures[0] == 21 * 3600 + 50 * 60
    # Trips must be sorted strictly ascending
    assert all(b.departures[0] > a.departures[0] for a, b in zip(p0.trips, p0.trips[1:]))
    # 07:15 to 22:31 every 10 min -> 92 departures (07:15 .. 22:25)
    assert len(p1.trips) == 92

    # Friday starts in the afternoon: 15:00 (d0) and 15:20 (d1)
    fri_tt = build_timetable(tf, fps, tf.active_service_ids(dt.date(2026, 11, 20)))
    assert fri_tt.patterns[0].trips[0].departures[0] == 15 * 3600
    assert fri_tt.patterns[1].trips[0].departures[0] == 15 * 3600 + 20 * 60
    assert "mrt6_mirpur_10_d0" in sat_tt.routes_by_stop
