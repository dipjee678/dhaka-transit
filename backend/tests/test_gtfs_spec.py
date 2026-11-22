"""Phase 1 tests: GTFS spec helpers and provenance vocabulary."""
import pytest

from transit.gtfs.spec import (
    GTFS_FILES, PROJECT_REQUIRED_FILES, Presence, format_gtfs_time,
    is_valid_color, is_valid_gtfs_date, parse_gtfs_time,
)
from transit.provenance import SourceCategory, weakest


def test_project_files_are_described():
    for f in PROJECT_REQUIRED_FILES:
        assert f in GTFS_FILES


def test_core_required_fields():
    assert GTFS_FILES["stops.txt"].fields["stop_id"] is Presence.REQUIRED
    assert set(GTFS_FILES["trips.txt"].required_fields) == {"route_id", "service_id", "trip_id"}
    assert GTFS_FILES["stop_times.txt"].primary_key == ("trip_id", "stop_sequence")


@pytest.mark.parametrize("text,secs", [
    ("00:00:00", 0), ("7:05:09", 7 * 3600 + 5 * 60 + 9), ("23:59:59", 86399), ("25:10:00", 25 * 3600 + 600),
])
def test_time_roundtrip(text, secs):
    assert parse_gtfs_time(text) == secs
    assert parse_gtfs_time(format_gtfs_time(secs)) == secs


@pytest.mark.parametrize("bad", ["7:5:00", "12:60:00", "abc", "", "12:00"])
def test_time_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        parse_gtfs_time(bad)


def test_date_and_color_formats():
    assert is_valid_gtfs_date("20261116")
    assert not is_valid_gtfs_date("2026-11-16")
    assert not is_valid_gtfs_date("20261340")
    assert is_valid_color("0A7E8C") and not is_valid_color("#0A7E8C")


def test_weakest_provenance_labels_journey():
    legs = [SourceCategory.OFFICIAL, SourceCategory.ESTIMATED, SourceCategory.OSM]
    assert weakest(legs) is SourceCategory.ESTIMATED
    assert weakest([SourceCategory.OFFICIAL]) is SourceCategory.OFFICIAL
    with pytest.raises(ValueError):
        weakest([])
