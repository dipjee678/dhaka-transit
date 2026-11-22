"""Phase 2 tests: curation, GTFS build, fare support, survey import, and real Dhaka feed."""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from pipeline.build_gtfs import build
from pipeline.curate_osm import chain_ways, classify_bus_element, project_on_polyline
from pipeline.gap_report import build_report, render_markdown
from pipeline.import_contribution import SurveyError, import_survey
from transit.geo import in_bbox
from transit.gtfs.io import read_feed
from transit.gtfs.validator import validate_feed

ROOT = Path(__file__).resolve().parents[2]
DHAKA_FEED = ROOT / "data" / "gtfs" / "dhaka"
CURATED = ROOT / "data" / "curated"
RAW_SNAP = ROOT / "data" / "raw" / "osm" / "2026-11-16"


# ---------------------------------------------------------------------------
# Curate helpers
# ---------------------------------------------------------------------------
def test_chain_ways_handles_reversed_segments():
    assert chain_ways([[3, 2, 1], [5, 4, 3], [5, 6]]) == [1, 2, 3, 4, 5, 6]
    with pytest.raises(ValueError):
        chain_ways([[1, 2], [9, 10]])


def test_project_on_polyline_monotonic():
    line = [(23.80, 90.40), (23.81, 90.40), (23.82, 90.40)]
    d1, off1 = project_on_polyline(23.805, 90.40, line)
    d2, off2 = project_on_polyline(23.815, 90.40, line)
    assert off1 < 1.0 and off2 < 1.0
    assert d1 < d2


def test_classify_bus_element():
    assert classify_bus_element({"amenity": "bus_station"}) == "bus_station"
    assert classify_bus_element({"public_transport": "platform"}) == "platform"
    assert classify_bus_element({"public_transport": "stop_position"}) == "stop_position"
    assert classify_bus_element({"highway": "bus_stop"}) == "bus_stop"


# ---------------------------------------------------------------------------
# Real Dhaka GTFS feed integration checks (Phase 2 exit gate)
# ---------------------------------------------------------------------------
def test_real_dhaka_gtfs_feed_validates_cleanly():
    for src in (DHAKA_FEED, DHAKA_FEED / "dhaka_gtfs.zip"):
        feed = read_feed(src)
        issues = validate_feed(feed)
        assert [i for i in issues if i.severity == "error"] == []
        assert [i for i in issues if i.severity == "warning"] == []


def test_real_dhaka_mrt6_stations_and_order():
    feed = read_feed(DHAKA_FEED)
    stations = [s for s in feed["stops.txt"] if s["location_type"] == "1"]
    platforms = [s for s in feed["stops.txt"] if s["location_type"] == "0"]
    assert len(stations) == 16
    assert len(platforms) == 32
    for s in feed["stops.txt"]:
        assert in_bbox(float(s["stop_lat"]), float(s["stop_lon"]))

    # Verify stop order along direction 0: Uttara North -> Motijheel
    d0_times = sorted(
        [st for st in feed["stop_times.txt"] if st["trip_id"] == "mrt6_sat_thu_d0"],
        key=lambda r: int(r["stop_sequence"]),
    )
    assert len(d0_times) == 16
    assert d0_times[0]["stop_id"] == "mrt6_uttara_north_d0"
    assert d0_times[-1]["stop_id"] == "mrt6_motijheel_d0"
    dists = [float(r["shape_dist_traveled"]) for r in d0_times]
    assert all(b > a for a, b in zip(dists, dists[1:]))

    # Every schedule/frequency record for MRT-6 is honestly marked estimated until official timetable is obtained.
    prov_freq = [p for p in feed["provenance.txt"] if p["entity_table"] == "frequencies"]
    assert len(prov_freq) == 4
    assert all(p["category"] == "estimated" for p in prov_freq)


# ---------------------------------------------------------------------------
# Community contribution -> build_gtfs -> validate_feed round-trip (synthetic)
# ---------------------------------------------------------------------------
def _copy_curated(dst: Path) -> None:
    shutil.copytree(CURATED, dst)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def test_survey_import_snaps_to_osm_and_builds_valid_bus_feed(tmp_path):
    cur = tmp_path / "curated"
    _copy_curated(cur)

    # Pick one real OSM stop from bus_stops.csv so snapping is exercised on real coordinates.
    osm_stops = list(csv.DictReader(open(cur / "bus_stops.csv", encoding="utf-8")))
    real_stop = next(s for s in osm_stops if s["kind"] != "bus_station" and s["name"])

    survey = tmp_path / "test_survey.csv"
    cols = ["route_name", "operator", "direction", "sequence", "stop_name", "stop_name_bn",
            "lat", "lon", "minutes_from_previous", "service_days", "service_start",
            "service_end", "observed_headway_min", "observed_on", "contributor", "evidence", "notes"]
    _write_csv(survey, cols, [
        {
            "route_name": "TEST-1", "operator": "Test Bus Co", "direction": "0", "sequence": "1",
            "stop_name": real_stop["name"], "stop_name_bn": "",
            "lat": real_stop["lat"], "lon": real_stop["lon"], "minutes_from_previous": "",
            "service_days": "saturday;sunday;monday;tuesday;wednesday;thursday",
            "service_start": "07:00", "service_end": "21:00", "observed_headway_min": "12",
            "observed_on": "2026-11-22", "contributor": "Unit Tester", "evidence": "synthetic test", "notes": "",
        },
        {
            "route_name": "TEST-1", "operator": "Test Bus Co", "direction": "0", "sequence": "2",
            "stop_name": "Synthetic Far Stop", "stop_name_bn": "",
            # ~1.5 km away inside Dhaka bbox, unlikely to collide with an OSM stop within 40 m
            "lat": "23.835123", "lon": "90.351234", "minutes_from_previous": "",
            "service_days": "saturday;sunday;monday;tuesday;wednesday;thursday",
            "service_start": "07:00", "service_end": "21:00", "observed_headway_min": "14",
            "observed_on": "2026-11-22", "contributor": "Unit Tester", "evidence": "synthetic test", "notes": "",
        },
    ])

    summary = import_survey(survey, curated=cur)
    assert summary["routes"] == 1
    assert summary["snapped_to_osm"] == 1
    assert summary["new_community_stops"] == 1
    assert summary["services"] == 1

    out = tmp_path / "gtfs"
    build(cur, out)
    feed = read_feed(out)
    issues = validate_feed(feed)
    # No errors; only expected warning is trip_without_shape since no GPX shape was supplied.
    assert [i for i in issues if i.severity == "error"] == []
    assert {i.code for i in issues if i.severity == "warning"} == {"trip_without_shape"}
    # Because minutes_from_previous was omitted, trip provenance must downgrade to 'estimated'.
    bus_prov = [p for p in feed["provenance.txt"] if p["entity_table"] == "trips" and p["entity_id"].startswith("bus_")]
    assert len(bus_prov) == 1 and bus_prov[0]["category"] == "estimated"


def test_survey_rejects_bad_coordinates_and_gaps(tmp_path):
    cols = ["route_name", "operator", "direction", "sequence", "stop_name", "stop_name_bn",
            "lat", "lon", "minutes_from_previous", "service_days", "service_start",
            "service_end", "observed_headway_min", "observed_on", "contributor", "evidence", "notes"]
    bad = tmp_path / "bad.csv"
    _write_csv(bad, cols, [{
        "route_name": "X", "operator": "Op", "direction": "0", "sequence": "1",
        "stop_name": "Outside", "stop_name_bn": "", "lat": "90.4", "lon": "23.8",
        "minutes_from_previous": "", "service_days": "", "service_start": "", "service_end": "",
        "observed_headway_min": "", "observed_on": "2026-11-22", "contributor": "T", "evidence": "", "notes": "",
    }])
    with pytest.raises(SurveyError, match="outside Dhaka"):
        import_survey(bad, curated=CURATED, dry_run=True)


# ---------------------------------------------------------------------------
# Fare pipeline (synthetic test fares verifying GTFS Fares v1 + symmetry guard)
# ---------------------------------------------------------------------------
def test_build_fares_emits_valid_gtfs_and_catches_asymmetry(tmp_path):
    cur = tmp_path / "curated"
    _copy_curated(cur)
    cols = ["origin_station_id", "destination_station_id", "fare_bdt", "currency_type",
            "source_category", "source_id", "status", "verified_at", "notes"]
    _write_csv(cur / "metro_fares.csv", cols, [
        {"origin_station_id": "mrt6_mirpur_10", "destination_station_id": "mrt6_motijheel",
         "fare_bdt": "70", "currency_type": "BDT", "source_category": "estimated",
         "source_id": "dhaka_transit_estimate", "status": "unverified", "verified_at": "", "notes": "test"},
        {"origin_station_id": "mrt6_motijheel", "destination_station_id": "mrt6_mirpur_10",
         "fare_bdt": "70", "currency_type": "BDT", "source_category": "estimated",
         "source_id": "dhaka_transit_estimate", "status": "unverified", "verified_at": "", "notes": "test"},
    ])
    out = tmp_path / "gtfs"
    build(cur, out)
    feed = read_feed(out)
    assert len(feed["fare_attributes.txt"]) == 1
    assert len(feed["fare_rules.txt"]) == 2
    assert [i for i in validate_feed(feed) if i.severity == "error"] == []

    # Asymmetric fare must raise
    _write_csv(cur / "metro_fares.csv", cols, [
        {"origin_station_id": "mrt6_mirpur_10", "destination_station_id": "mrt6_motijheel",
         "fare_bdt": "70", "currency_type": "BDT", "source_category": "estimated",
         "source_id": "dhaka_transit_estimate", "status": "unverified", "verified_at": "", "notes": "test"},
        {"origin_station_id": "mrt6_motijheel", "destination_station_id": "mrt6_mirpur_10",
         "fare_bdt": "80", "currency_type": "BDT", "source_category": "estimated",
         "source_id": "dhaka_transit_estimate", "status": "unverified", "verified_at": "", "notes": "test"},
    ])
    with pytest.raises(ValueError, match="asymmetric"):
        build(cur, out)


def test_gap_report_runs_on_real_snapshot():
    if not RAW_SNAP.exists():
        pytest.skip("raw OSM snapshot not present")
    rep = build_report("2026-11-16")
    assert rep["metro"]["operational_stations_mapped"] == 16
    assert len(rep["mrt6_interchanges"]) == 16
    assert rep["osm_bus_inventory"]["total"] > 0
    md = render_markdown(rep)
    assert "MRT Line 6" in md and "Motijheel" in md
