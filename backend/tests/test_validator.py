"""Phase 2 tests: GTFS validator rules and reader/writer round-trip."""
from transit.gtfs.io import read_feed, write_table, zip_feed
from transit.gtfs.validator import summarize, validate_feed


def codes(issues, severity=None):
    return {i.code for i in issues if severity is None or i.severity == severity}


def test_toy_feed_is_clean(feed):
    issues = validate_feed(feed)
    assert codes(issues, "error") == set(), [i for i in issues if i.severity == "error"]
    assert codes(issues, "warning") == set()


def test_missing_coordinates(feed):
    feed["stops.txt"][0]["stop_lat"] = ""
    assert "missing_coordinates" in codes(validate_feed(feed), "error")


def test_swapped_coordinates_rejected(feed):
    s = feed["stops.txt"][0]
    s["stop_lat"], s["stop_lon"] = s["stop_lon"], s["stop_lat"]   # lat 90.4 is out of range
    assert "invalid_coordinates" in codes(validate_feed(feed), "error")


def test_point_outside_dhaka(feed):
    feed["stops.txt"][0]["stop_lat"], feed["stops.txt"][0]["stop_lon"] = "22.300000", "91.800000"
    assert "outside_dhaka" in codes(validate_feed(feed), "error")


def test_duplicate_stop_same_name_nearby(feed):
    feed["stops.txt"].append({"stop_id": "TEST_S1b", "stop_name": "alpha", "stop_lat": "23.800100",
                              "stop_lon": "90.400050", "location_type": "0", "parent_station": ""})
    feed["provenance.txt"].append({"entity_table": "stops", "entity_id": "TEST_S1b", "field": "",
                                   "source_id": "TEST_SRC", "category": "osm", "status": "unverified"})
    issues = validate_feed(feed)
    assert "duplicate_stop" in codes(issues, "warning")


def test_duplicate_primary_key(feed):
    feed["stops.txt"].append(dict(feed["stops.txt"][0]))
    assert "duplicate_key" in codes(validate_feed(feed), "error")


def test_invalid_route_references(feed):
    feed["trips.txt"][0]["route_id"] = "NOPE"
    feed["routes.txt"][0]["route_type"] = "42"
    c = codes(validate_feed(feed), "error")
    assert {"unknown_route", "invalid_route_type"} <= c


def test_inconsistent_schedule_time_travel(feed):
    feed["stop_times.txt"][2]["arrival_time"] = "07:59:00"
    feed["stop_times.txt"][2]["departure_time"] = "07:59:00"
    assert "time_travel" in codes(validate_feed(feed), "error")


def test_missing_first_last_times(feed):
    feed["stop_times.txt"][-1]["arrival_time"] = ""
    feed["stop_times.txt"][-1]["departure_time"] = ""
    assert "missing_time" in codes(validate_feed(feed), "error")


def test_implausible_bus_speed(feed):
    # ~1.1 km in 10 seconds
    feed["stop_times.txt"][1]["arrival_time"] = feed["stop_times.txt"][1]["departure_time"] = "08:00:10"
    assert "implausible_speed" in codes(validate_feed(feed), "warning")


def test_trip_needs_two_stops(feed):
    feed["stop_times.txt"] = feed["stop_times.txt"][:1]
    feed["stop_times.txt"][0]["timepoint"] = "0"
    assert "trip_too_few_stops" in codes(validate_feed(feed), "error")


def test_stop_far_from_shape(feed):
    feed["stops.txt"][1]["stop_lon"] = "90.410000"  # ~1 km east of the straight shape
    assert "stop_far_from_shape" in codes(validate_feed(feed), "warning")


def test_bad_frequency(feed):
    feed["frequencies.txt"][0]["headway_secs"] = "0"
    feed["frequencies.txt"][0]["end_time"] = "06:00:00"
    c = codes(validate_feed(feed), "error")
    assert {"invalid_headway", "invalid_frequency_window"} <= c


def test_unknown_service_and_bad_calendar(feed):
    feed["calendar.txt"][0]["end_date"] = "20251231"
    feed["trips.txt"][0]["service_id"] = "OTHER"
    c = codes(validate_feed(feed), "error")
    assert {"start_after_end", "unknown_service"} <= c


def test_provenance_required(feed):
    feed["provenance.txt"] = [p for p in feed["provenance.txt"] if p["entity_id"] != "TEST_S2"]
    assert "missing_provenance" in codes(validate_feed(feed), "error")
    feed["provenance.txt"][0]["category"] = "guess"
    assert "invalid_provenance_category" in codes(validate_feed(feed), "error")


def test_fare_zone_references(feed):
    feed["fare_attributes.txt"] = [{"fare_id": "F1", "price": "20", "currency_type": "BDT", "payment_method": "1", "transfers": "0"}]
    feed["fare_rules.txt"] = [{"fare_id": "F1", "origin_id": "Z1", "destination_id": "Z9"}]
    feed["provenance.txt"].append({"entity_table": "fare_attributes", "entity_id": "F1", "field": "",
                                   "source_id": "TEST_SRC", "category": "estimated", "status": "unverified"})
    assert codes(validate_feed(feed), "error") == {"unknown_zone"}


def test_summary_counts(feed):
    feed["stops.txt"][0]["stop_lat"] = ""
    s = summarize(validate_feed(feed))
    assert s["errors"] >= 1 and any(r["code"] == "missing_coordinates" for r in s["by_code"])


def test_io_roundtrip(tmp_path, feed):
    out = tmp_path / "feed"
    for name, rows in feed.items():
        write_table(out / name, list(rows[0].keys()), rows)
    zip_feed(out, tmp_path / "feed.zip")
    for src in (out, tmp_path / "feed.zip"):
        back = read_feed(src)
        assert back == feed
