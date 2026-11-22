"""Phase 6 end-to-end integration tests.

Verifies the complete data-to-routing lifecycle:
  volunteer bus survey CSV
    -> pipeline.import_contribution (snapping to real OSM stops)
    -> pipeline.build_gtfs (combined with real MRT Line 6)
    -> transit.gtfs.validator (0 errors)
    -> database.load_gtfs (SQLite load + footpaths)
    -> Dijkstra & RAPTOR multimodal routing (Bus -> Walk Transfer -> MRT Line 6)
Plus all-pairs MRT-6 connectivity and evaluation report generation.
"""
from __future__ import annotations

import csv
import datetime as dt
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "database"))

from load_gtfs import load_gtfs_to_sqlite  # noqa: E402
from pipeline.build_gtfs import build  # noqa: E402
from pipeline.evaluate import run_evaluation  # noqa: E402
from pipeline.import_contribution import import_survey  # noqa: E402
from transit.geo import GridIndex  # noqa: E402
from transit.graph.footpaths import build_footpaths  # noqa: E402
from transit.graph.frequency_graph import build_frequency_graph  # noqa: E402
from transit.graph.timetable import build_timetable  # noqa: E402
from transit.gtfs.io import read_feed  # noqa: E402
from transit.gtfs.model import TransitFeed  # noqa: E402
from transit.gtfs.validator import validate_feed  # noqa: E402
from transit.routing.planner import plan_journey  # noqa: E402

CURATED = ROOT / "data" / "curated"
DHAKA_FEED = ROOT / "data" / "gtfs" / "dhaka"


def test_end_to_end_surveyed_feeder_bus_connects_into_real_mrt6(tmp_path):
    """Simulate a volunteer surveying a Mirpur-1 -> Mirpur-10 feeder bus, and verify
    that both RAPTOR and Dijkstra plan a multimodal Bus -> Transfer -> MRT-6 trip
    from Mirpur-1 all the way to Motijheel!"""
    cur = tmp_path / "curated"
    shutil.copytree(CURATED, cur)

    # Create a synthetic test survey for a feeder bus ending ~100 m from real Mirpur 10 MRT-6 station
    survey = tmp_path / "feeder_survey.csv"
    cols = ["route_name", "operator", "direction", "sequence", "stop_name", "stop_name_bn",
            "lat", "lon", "minutes_from_previous", "service_days", "service_start",
            "service_end", "observed_headway_min", "observed_on", "contributor", "evidence", "notes"]
    with open(survey, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows([
            {
                "route_name": "TEST-FEEDER", "operator": "Synthetic Feeder", "direction": "0", "sequence": "1",
                "stop_name": "Mirpur 1 Synthetic Origin", "stop_name_bn": "",
                "lat": "23.795600", "lon": "90.353600", "minutes_from_previous": "",
                "service_days": "saturday;sunday;monday;tuesday;wednesday;thursday",
                "service_start": "07:00", "service_end": "21:00", "observed_headway_min": "10",
                "observed_on": "2026-11-23", "contributor": "Integration Test", "evidence": "synthetic", "notes": "",
            },
            {
                "route_name": "TEST-FEEDER", "operator": "Synthetic Feeder", "direction": "0", "sequence": "2",
                "stop_name": "Mirpur 10 Circle", "stop_name_bn": "",
                # Right beside real MRT-6 Mirpur 10 station (23.80836, 90.36826) -> ~100 m walk transfer
                "lat": "23.807600", "lon": "90.368400", "minutes_from_previous": "8",
                "service_days": "saturday;sunday;monday;tuesday;wednesday;thursday",
                "service_start": "07:00", "service_end": "21:00", "observed_headway_min": "10",
                "observed_on": "2026-11-23", "contributor": "Integration Test", "evidence": "synthetic", "notes": "",
            },
        ])

    # 1. Import survey -> 2. Build GTFS -> 3. Validate -> 4. Load SQLite
    import_survey(survey, curated=cur)
    gtfs_out = tmp_path / "gtfs"
    build(cur, gtfs_out)
    issues = validate_feed(read_feed(gtfs_out))
    assert [i for i in issues if i.severity == "error"] == []

    db_counts = load_gtfs_to_sqlite(gtfs_out, tmp_path / "e2e.sqlite")
    assert db_counts["routes"] == 2  # MRT-6 + TEST-FEEDER

    # 5. Build graph & timetable and route Mirpur 1 -> Motijheel!
    tf = TransitFeed.from_path(gtfs_out)
    fps = build_footpaths(tf.boarding_stops, max_walk_m=500.0)
    graph = build_frequency_graph(tf, fps)
    sat = dt.date(2026, 11, 21)
    tt = build_timetable(tf, fps, tf.active_service_ids(sat))
    idx: GridIndex[str] = GridIndex(cell_size_m=300.0)
    for sid, s in tf.boarding_stops.items():
        idx.insert(sid, s.stop_lat, s.stop_lon)

    motijheel = tf.stops["mrt6_motijheel"]
    res = plan_journey(
        feed=tf, graph=graph, timetable=tt, boarding_index=idx,
        from_lat=23.795600, from_lon=90.353600,
        to_lat=motijheel.stop_lat, to_lon=motijheel.stop_lon,
        from_name="Mirpur 1", to_name="Motijheel",
        service_date=sat, departure_s=8 * 3600 + 30 * 60,
    )
    assert res.recommended is not None
    assert res.recommended.algorithm == "raptor"
    assert res.recommended.transfers == 1
    assert res.comparison["same_best_route"] is True
    transit_modes = [l.mode for l in res.recommended.legs if l.is_transit]
    assert transit_modes == ["bus", "metro"]
    assert any(l.mode == "transfer" for l in res.recommended.legs)


def test_evaluation_harness_produces_valid_reports(tmp_path):
    rep = run_evaluation(reports_dir=tmp_path)
    assert (tmp_path / "evaluation_report.json").is_file()
    assert (tmp_path / "evaluation_report.md").is_file()
    assert rep["real_dhaka"]["queries_run"] == 720
    assert rep["real_dhaka"]["route_agreement_pct"] == 100.0
    assert all(a["expansion_reduction_pct"] > 0 for a in rep["astar_walking"])
    assert len(rep["synthetic_scalability"]) == 3
