"""Phase 1 tests: database schema builds and enforces GTFS + provenance rules."""
import sqlite3
from pathlib import Path

import pytest

from transit.gtfs.spec import GTFS_FILES

ROOT = Path(__file__).resolve().parents[2]
SQLITE_SCHEMA = ROOT / "database" / "schema_sqlite.sql"
POSTGIS_SCHEMA = ROOT / "database" / "schema_postgis.sql"

GTFS_TABLES = {f.removesuffix(".txt") for f in GTFS_FILES}
EXTENSION_TABLES = {"data_sources", "provenance", "footpaths", "contributors",
                    "contributions", "validation_runs", "validation_issues"}


@pytest.fixture()
def db():
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SQLITE_SCHEMA.read_text(encoding="utf-8"))
    yield con
    con.close()


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_all_tables_created(db):
    missing = (GTFS_TABLES | EXTENSION_TABLES) - _tables(db)
    assert not missing, f"missing tables: {missing}"


def test_gtfs_columns_match_spec(db):
    """Every required GTFS column exists in its table, with the spec's name."""
    for filename, spec in GTFS_FILES.items():
        table = filename.removesuffix(".txt")
        cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
        for field in spec.required_fields:
            assert field in cols, f"{table}.{field} missing"


def test_postgis_schema_declares_same_tables():
    text = POSTGIS_SCHEMA.read_text(encoding="utf-8")
    for t in GTFS_TABLES | EXTENSION_TABLES:
        assert f"CREATE TABLE IF NOT EXISTS {t} (" in text, f"{t} missing from PostGIS schema"


def test_stop_requires_coordinates(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO stops (stop_id, stop_name, location_type) VALUES ('s1', 'No coords', 0)")
    # A generic node (location_type=3) may legitimately have no coordinates.
    db.execute("INSERT INTO stops (stop_id, location_type, parent_station) VALUES ('n1', 3, NULL)")


def test_foreign_keys_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO trips (trip_id, route_id, service_id) VALUES ('t1', 'no_such_route', 'svc')")


def test_route_needs_a_name(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO routes (route_id, route_type) VALUES ('r1', 3)")


def test_provenance_category_constrained(db):
    db.execute("INSERT INTO provenance (entity_table, entity_id, category) VALUES ('stops', 'x', 'osm')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO provenance (entity_table, entity_id, category) VALUES ('stops', 'y', 'guess')")


def test_frequency_headway_positive(db):
    db.execute("INSERT INTO agency VALUES ('a', 'Test agency', 'https://example.org', 'Asia/Dhaka', NULL, NULL, NULL)")
    db.execute("INSERT INTO routes (route_id, agency_id, route_short_name, route_type) VALUES ('r', 'a', 'T', 3)")
    db.execute("INSERT INTO trips (trip_id, route_id, service_id) VALUES ('t', 'r', 'svc')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO frequencies VALUES ('t', '07:00:00', '09:00:00', 0, 0)")
