"""Phase 3 tests: SQLite GTFS loader and lossless round-trip export."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "database"))

from load_gtfs import export_gtfs_from_sqlite, load_gtfs_to_sqlite  # noqa: E402
from transit.gtfs.io import read_feed  # noqa: E402
from transit.gtfs.validator import validate_feed  # noqa: E402

DHAKA_FEED = ROOT / "data" / "gtfs" / "dhaka"


def test_load_and_export_roundtrip_is_lossless(tmp_path):
    db_path = tmp_path / "test.sqlite"
    counts = load_gtfs_to_sqlite(DHAKA_FEED, db_path)
    assert counts["stops"] == 48
    assert counts["routes"] == 1
    assert counts["trips"] == 4
    assert counts["stop_times"] == 64
    assert counts["shapes"] == 489
    assert counts["footpaths"] == 32
    assert counts["validation_issues"] == 0

    # Check Bangla names and validation_runs were populated in SQLite.
    con = sqlite3.connect(db_path)
    try:
        bn = con.execute("SELECT stop_name_bn FROM stops WHERE stop_id = 'mrt6_mirpur_10'").fetchone()[0]
        assert bn == "মিরপুর-১০"
        run = con.execute("SELECT error_count, warning_count FROM validation_runs").fetchone()
        assert run == (0, 0)
    finally:
        con.close()

    # Export back out to GTFS and compare semantically against the source feed.
    exported_dir = tmp_path / "exported"
    export_gtfs_from_sqlite(db_path, exported_dir)

    orig = read_feed(DHAKA_FEED)
    back = read_feed(exported_dir)
    assert set(orig.keys()) == set(back.keys())

    issues = validate_feed(back)
    assert [i for i in issues if i.severity in ("error", "warning")] == []

    # Same row counts and primary-key sets across every table.
    for fname in orig:
        assert len(back[fname]) == len(orig[fname]), f"row count mismatch in {fname}"
    assert {r["stop_id"] for r in back["stops.txt"]} == {r["stop_id"] for r in orig["stops.txt"]}
    assert {r["trip_id"] for r in back["trips.txt"]} == {r["trip_id"] for r in orig["trips.txt"]}
    assert {(r["table_name"], r["record_id"], r["translation"]) for r in back["translations.txt"]} == {
        (r["table_name"], r["record_id"], r["translation"]) for r in orig["translations.txt"]
    }


def test_reload_preserves_community_contributions(tmp_path):
    db_path = tmp_path / "test.sqlite"
    load_gtfs_to_sqlite(DHAKA_FEED, db_path)

    con = sqlite3.connect(db_path)
    with con:
        cur = con.execute("INSERT INTO contributors (display_name) VALUES ('Tester')")
        con.execute(
            "INSERT INTO contributions (contributor_id, kind, payload_json) VALUES (?, 'new_stop', '{\"name\":\"X\"}')",
            (cur.lastrowid,),
        )
    con.close()

    # Reload GTFS feed: contributions must survive untouched.
    load_gtfs_to_sqlite(DHAKA_FEED, db_path)
    con = sqlite3.connect(db_path)
    try:
        n = con.execute("SELECT count(*) FROM contributions").fetchone()[0]
        assert n == 1
    finally:
        con.close()
