"""Shared test fixtures.

`toy_feed()` is a SYNTHETIC feed used only for unit tests. Its stops, times and
coordinates are made up and are not Dhaka data (ids are prefixed TEST_).
"""
from __future__ import annotations

import copy

import pytest

_TOY = {
    "agency.txt": [{"agency_id": "TEST_A", "agency_name": "Test Agency", "agency_url": "https://example.org",
                    "agency_timezone": "Asia/Dhaka"}],
    "stops.txt": [
        {"stop_id": "TEST_S1", "stop_name": "Alpha", "stop_lat": "23.800000", "stop_lon": "90.400000", "location_type": "0", "parent_station": "", "zone_id": "Z1"},
        {"stop_id": "TEST_S2", "stop_name": "Beta", "stop_lat": "23.810000", "stop_lon": "90.400000", "location_type": "0", "parent_station": "", "zone_id": "Z2"},
        {"stop_id": "TEST_S3", "stop_name": "Gamma", "stop_lat": "23.820000", "stop_lon": "90.400000", "location_type": "0", "parent_station": "", "zone_id": "Z3"},
    ],
    "routes.txt": [{"route_id": "TEST_R1", "agency_id": "TEST_A", "route_short_name": "T1", "route_long_name": "", "route_type": "3", "route_color": "0A7E8C"}],
    "trips.txt": [{"route_id": "TEST_R1", "service_id": "TEST_SVC", "trip_id": "TEST_T1", "direction_id": "0", "shape_id": "TEST_SH1"}],
    "stop_times.txt": [
        {"trip_id": "TEST_T1", "arrival_time": "08:00:00", "departure_time": "08:00:00", "stop_id": "TEST_S1", "stop_sequence": "1", "timepoint": "0"},
        {"trip_id": "TEST_T1", "arrival_time": "08:05:00", "departure_time": "08:05:00", "stop_id": "TEST_S2", "stop_sequence": "2", "timepoint": "0"},
        {"trip_id": "TEST_T1", "arrival_time": "08:10:00", "departure_time": "08:10:00", "stop_id": "TEST_S3", "stop_sequence": "3", "timepoint": "0"},
    ],
    "calendar.txt": [{"service_id": "TEST_SVC", "monday": "1", "tuesday": "1", "wednesday": "1", "thursday": "1",
                      "friday": "0", "saturday": "1", "sunday": "1", "start_date": "20260101", "end_date": "20261231"}],
    "calendar_dates.txt": [{"service_id": "TEST_SVC", "date": "20261216", "exception_type": "2"}],
    "shapes.txt": [
        {"shape_id": "TEST_SH1", "shape_pt_lat": "23.800000", "shape_pt_lon": "90.400000", "shape_pt_sequence": "1", "shape_dist_traveled": "0"},
        {"shape_id": "TEST_SH1", "shape_pt_lat": "23.820000", "shape_pt_lon": "90.400000", "shape_pt_sequence": "2", "shape_dist_traveled": "2224"},
    ],
    "frequencies.txt": [{"trip_id": "TEST_T1", "start_time": "07:00:00", "end_time": "10:00:00", "headway_secs": "600", "exact_times": "0"}],
    "data_sources.txt": [{"source_id": "TEST_SRC", "name": "unit test", "category": "estimated"}],
    "provenance.txt": [
        {"entity_table": t, "entity_id": i, "field": "", "source_id": "TEST_SRC", "category": "estimated", "status": "unverified"}
        for t, i in [("stops", "TEST_S1"), ("stops", "TEST_S2"), ("stops", "TEST_S3"), ("routes", "TEST_R1"),
                     ("trips", "TEST_T1"), ("shapes", "TEST_SH1"), ("frequencies", "TEST_T1|07:00:00")]
    ],
}


def toy_feed() -> dict:
    return copy.deepcopy(_TOY)


@pytest.fixture()
def feed():
    return toy_feed()
