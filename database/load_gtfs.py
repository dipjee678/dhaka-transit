"""Load a GTFS feed into SQLite (and export it back losslessly).

Usage (from repository root):
    python database/load_gtfs.py --feed data/gtfs/dhaka --db dhaka_transit.sqlite
    python database/load_gtfs.py --export --db dhaka_transit.sqlite --out /tmp/exported_gtfs

Reloading a feed refreshes all GTFS, provenance, footpath, and validation
tables inside a single transaction while preserving ``contributors`` and
``contributions`` rows.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from transit.graph.footpaths import build_footpaths  # noqa: E402
from transit.gtfs.io import read_feed, write_table  # noqa: E402
from transit.gtfs.model import TransitFeed  # noqa: E402
from transit.gtfs.validator import summarize, validate_feed  # noqa: E402

SCHEMA_SQLITE = Path(__file__).resolve().parent / "schema_sqlite.sql"

# Tables refreshed when reloading a GTFS feed (in safe delete order for FKs).
_CLEAR_ORDER = (
    "footpaths",
    "provenance",
    "data_sources",
    "transfers",
    "fare_rules",
    "fare_attributes",
    "frequencies",
    "stop_times",
    "trips",
    "shapes",
    "calendar_dates",
    "calendar",
    "routes",
    "stops",
    "agency",
    "feed_info",
)


def _none(val: str | None) -> str | None:
    return None if val in (None, "") else val


def _int(val: str | None) -> int | None:
    return None if val in (None, "") else int(val)


def _float(val: str | None) -> float | None:
    return None if val in (None, "") else float(val)


def load_gtfs_to_sqlite(
    feed_path: Path,
    db_path: Path,
    schema_path: Path = SCHEMA_SQLITE,
    generate_footpaths: bool = True,
    record_validation: bool = True,
) -> dict[str, int]:
    """Load a GTFS directory or ``.zip`` into SQLite and return row counts."""
    raw = read_feed(Path(feed_path))
    typed = TransitFeed.from_feed_dict(raw)

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(Path(schema_path).read_text(encoding="utf-8"))

        with con:
            for tbl in _CLEAR_ORDER:
                con.execute(f"DELETE FROM {tbl}")

            # Bangla translations map
            bn: dict[tuple[str, str, str], str] = {}
            for r in raw.get("translations.txt", []):
                if r.get("language") == "bn" and r.get("record_id"):
                    bn[(r.get("table_name", ""), r.get("field_name", ""), r["record_id"])] = r.get("translation", "")

            counts: dict[str, int] = {}

            rows = raw.get("agency.txt", [])
            con.executemany(
                "INSERT INTO agency (agency_id, agency_name, agency_url, agency_timezone, agency_lang, agency_phone, agency_fare_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(r["agency_id"], r["agency_name"], r["agency_url"], r["agency_timezone"],
                  _none(r.get("agency_lang")), _none(r.get("agency_phone")), _none(r.get("agency_fare_url")))
                 for r in rows],
            )
            counts["agency"] = len(rows)

            # Insert parent stations (location_type == 1) before child stops so parent_station FK is satisfied.
            stops_rows = sorted(
                raw.get("stops.txt", []),
                key=lambda r: (0 if (r.get("location_type") or "0") == "1" else 1, r["stop_id"]),
            )
            con.executemany(
                "INSERT INTO stops (stop_id, stop_code, stop_name, stop_name_bn, stop_desc, stop_lat, stop_lon, "
                "zone_id, location_type, parent_station, wheelchair_boarding, platform_code) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(r["stop_id"], _none(r.get("stop_code")), _none(r.get("stop_name")),
                  _none(bn.get(("stops", "stop_name", r["stop_id"]))), _none(r.get("stop_desc")),
                  _float(r.get("stop_lat")), _float(r.get("stop_lon")), _none(r.get("zone_id")),
                  _int(r.get("location_type")) or 0, _none(r.get("parent_station")),
                  _int(r.get("wheelchair_boarding")), _none(r.get("platform_code")))
                 for r in stops_rows],
            )
            counts["stops"] = len(stops_rows)

            rows = raw.get("routes.txt", [])
            con.executemany(
                "INSERT INTO routes (route_id, agency_id, route_short_name, route_long_name, route_long_name_bn, "
                "route_desc, route_type, route_url, route_color, route_text_color, route_sort_order) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(r["route_id"], _none(r.get("agency_id")), _none(r.get("route_short_name")),
                  _none(r.get("route_long_name")), _none(bn.get(("routes", "route_long_name", r["route_id"]))),
                  _none(r.get("route_desc")), int(r["route_type"]), _none(r.get("route_url")),
                  _none(r.get("route_color")), _none(r.get("route_text_color")), _int(r.get("route_sort_order")))
                 for r in rows],
            )
            counts["routes"] = len(rows)

            rows = raw.get("calendar.txt", [])
            con.executemany(
                "INSERT INTO calendar (service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(r["service_id"], int(r["monday"]), int(r["tuesday"]), int(r["wednesday"]), int(r["thursday"]),
                  int(r["friday"]), int(r["saturday"]), int(r["sunday"]), r["start_date"], r["end_date"])
                 for r in rows],
            )
            counts["calendar"] = len(rows)

            rows = raw.get("calendar_dates.txt", [])
            con.executemany(
                "INSERT INTO calendar_dates (service_id, date, exception_type) VALUES (?, ?, ?)",
                [(r["service_id"], r["date"], int(r["exception_type"])) for r in rows],
            )
            counts["calendar_dates"] = len(rows)

            rows = raw.get("shapes.txt", [])
            con.executemany(
                "INSERT INTO shapes (shape_id, shape_pt_lat, shape_pt_lon, shape_pt_sequence, shape_dist_traveled) "
                "VALUES (?, ?, ?, ?, ?)",
                [(r["shape_id"], float(r["shape_pt_lat"]), float(r["shape_pt_lon"]),
                  int(r["shape_pt_sequence"]), _float(r.get("shape_dist_traveled")))
                 for r in rows],
            )
            counts["shapes"] = len(rows)

            rows = raw.get("trips.txt", [])
            con.executemany(
                "INSERT INTO trips (trip_id, route_id, service_id, trip_headsign, trip_short_name, direction_id, "
                "block_id, shape_id, wheelchair_accessible) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(r["trip_id"], r["route_id"], r["service_id"], _none(r.get("trip_headsign")),
                  _none(r.get("trip_short_name")), _int(r.get("direction_id")), _none(r.get("block_id")),
                  _none(r.get("shape_id")), _int(r.get("wheelchair_accessible")))
                 for r in rows],
            )
            counts["trips"] = len(rows)

            rows = raw.get("stop_times.txt", [])
            con.executemany(
                "INSERT INTO stop_times (trip_id, arrival_time, departure_time, stop_id, stop_sequence, "
                "stop_headsign, pickup_type, drop_off_type, shape_dist_traveled, timepoint) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(r["trip_id"], _none(r.get("arrival_time")), _none(r.get("departure_time")), r["stop_id"],
                  int(r["stop_sequence"]), _none(r.get("stop_headsign")), _int(r.get("pickup_type")),
                  _int(r.get("drop_off_type")), _float(r.get("shape_dist_traveled")), _int(r.get("timepoint")))
                 for r in rows],
            )
            counts["stop_times"] = len(rows)

            rows = raw.get("frequencies.txt", [])
            con.executemany(
                "INSERT INTO frequencies (trip_id, start_time, end_time, headway_secs, exact_times) "
                "VALUES (?, ?, ?, ?, ?)",
                [(r["trip_id"], r["start_time"], r["end_time"], int(r["headway_secs"]),
                  _int(r.get("exact_times")) or 0)
                 for r in rows],
            )
            counts["frequencies"] = len(rows)

            rows = raw.get("fare_attributes.txt", [])
            con.executemany(
                "INSERT INTO fare_attributes (fare_id, price, currency_type, payment_method, transfers, agency_id, transfer_duration) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(r["fare_id"], float(r["price"]), r.get("currency_type") or "BDT", int(r["payment_method"]),
                  _int(r.get("transfers")), _none(r.get("agency_id")), _int(r.get("transfer_duration")))
                 for r in rows],
            )
            counts["fare_attributes"] = len(rows)

            rows = raw.get("fare_rules.txt", [])
            con.executemany(
                "INSERT INTO fare_rules (fare_id, route_id, origin_id, destination_id, contains_id) "
                "VALUES (?, ?, ?, ?, ?)",
                [(r["fare_id"], _none(r.get("route_id")), _none(r.get("origin_id")),
                  _none(r.get("destination_id")), _none(r.get("contains_id")))
                 for r in rows],
            )
            counts["fare_rules"] = len(rows)

            rows = raw.get("transfers.txt", [])
            con.executemany(
                "INSERT INTO transfers (from_stop_id, to_stop_id, transfer_type, min_transfer_time) VALUES (?, ?, ?, ?)",
                [(_none(r.get("from_stop_id")), _none(r.get("to_stop_id")),
                  int(r["transfer_type"]), _int(r.get("min_transfer_time")))
                 for r in rows],
            )
            counts["transfers"] = len(rows)

            rows = raw.get("feed_info.txt", [])
            con.executemany(
                "INSERT INTO feed_info (feed_publisher_name, feed_publisher_url, feed_lang, feed_start_date, "
                "feed_end_date, feed_version, feed_contact_email) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(r["feed_publisher_name"], r["feed_publisher_url"], r["feed_lang"],
                  _none(r.get("feed_start_date")), _none(r.get("feed_end_date")),
                  _none(r.get("feed_version")), _none(r.get("feed_contact_email")))
                 for r in rows],
            )
            counts["feed_info"] = len(rows)

            rows = raw.get("data_sources.txt", [])
            con.executemany(
                "INSERT INTO data_sources (source_id, name, url, license, category, retrieved_at, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(r["source_id"], r["name"], _none(r.get("url")), _none(r.get("license")),
                  r["category"], _none(r.get("retrieved_at")), _none(r.get("notes")))
                 for r in rows],
            )
            counts["data_sources"] = len(rows)

            rows = raw.get("provenance.txt", [])
            con.executemany(
                "INSERT INTO provenance (entity_table, entity_id, field, source_id, category, status, verified_at, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(r["entity_table"], r["entity_id"], _none(r.get("field")), _none(r.get("source_id")),
                  r["category"], r.get("status") or "unverified", _none(r.get("verified_at")), _none(r.get("notes")))
                 for r in rows],
            )
            counts["provenance"] = len(rows)

            if generate_footpaths:
                fps = build_footpaths(typed.boarding_stops)
                con.executemany(
                    "INSERT INTO footpaths (from_stop_id, to_stop_id, distance_m, duration_s, method) "
                    "VALUES (?, ?, ?, ?, ?)",
                    [(fp.from_stop_id, fp.to_stop_id, fp.distance_m, fp.duration_s, fp.method) for fp in fps],
                )
                counts["footpaths"] = len(fps)

            if record_validation:
                issues = validate_feed(raw)
                summ = summarize(issues)
                version = raw["feed_info.txt"][0].get("feed_version", "") if raw.get("feed_info.txt") else ""
                cur = con.execute(
                    "INSERT INTO validation_runs (feed_version, error_count, warning_count) VALUES (?, ?, ?)",
                    (version, summ["errors"], summ["warnings"]),
                )
                run_id = cur.lastrowid
                con.executemany(
                    "INSERT INTO validation_issues (run_id, severity, code, entity_table, entity_id, message) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [(run_id, i.severity, i.code, i.file.removesuffix(".txt"), i.entity_id, i.message)
                     for i in issues],
                )
                counts["validation_issues"] = len(issues)

        return counts
    finally:
        con.close()


_EXPORT_SPEC: list[tuple[str, str, list[str], bool]] = [
    # (filename, sql_query, columns, write_if_empty)
    ("agency.txt",
     "SELECT agency_id, agency_name, agency_url, agency_timezone, agency_lang, agency_phone FROM agency ORDER BY agency_id",
     ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang", "agency_phone"], True),
    ("stops.txt",
     "SELECT stop_id, stop_code, stop_name, stop_desc, stop_lat, stop_lon, zone_id, location_type, "
     "parent_station, wheelchair_boarding, platform_code FROM stops ORDER BY stop_id",
     ["stop_id", "stop_code", "stop_name", "stop_desc", "stop_lat", "stop_lon", "zone_id",
      "location_type", "parent_station", "wheelchair_boarding", "platform_code"], True),
    ("routes.txt",
     "SELECT route_id, agency_id, route_short_name, route_long_name, route_desc, route_type, "
     "route_url, route_color, route_text_color FROM routes ORDER BY route_id",
     ["route_id", "agency_id", "route_short_name", "route_long_name", "route_desc", "route_type",
      "route_url", "route_color", "route_text_color"], True),
    ("trips.txt",
     "SELECT route_id, service_id, trip_id, trip_headsign, direction_id, shape_id FROM trips ORDER BY trip_id",
     ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id"], True),
    ("stop_times.txt",
     "SELECT trip_id, arrival_time, departure_time, stop_id, stop_sequence, timepoint, shape_dist_traveled "
     "FROM stop_times ORDER BY trip_id, stop_sequence",
     ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence", "timepoint", "shape_dist_traveled"], True),
    ("calendar.txt",
     "SELECT service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date "
     "FROM calendar ORDER BY service_id",
     ["service_id", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
      "start_date", "end_date"], True),
    ("calendar_dates.txt",
     "SELECT service_id, date, exception_type FROM calendar_dates ORDER BY service_id, date",
     ["service_id", "date", "exception_type"], True),
    ("shapes.txt",
     "SELECT shape_id, shape_pt_lat, shape_pt_lon, shape_pt_sequence, shape_dist_traveled "
     "FROM shapes ORDER BY shape_id, shape_pt_sequence",
     ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence", "shape_dist_traveled"], True),
    ("frequencies.txt",
     "SELECT trip_id, start_time, end_time, headway_secs, exact_times FROM frequencies ORDER BY trip_id, start_time",
     ["trip_id", "start_time", "end_time", "headway_secs", "exact_times"], True),
    ("fare_attributes.txt",
     "SELECT fare_id, price, currency_type, payment_method, transfers, agency_id FROM fare_attributes ORDER BY fare_id",
     ["fare_id", "price", "currency_type", "payment_method", "transfers", "agency_id"], False),
    ("fare_rules.txt",
     "SELECT fare_id, route_id, origin_id, destination_id FROM fare_rules ORDER BY fare_id, origin_id, destination_id",
     ["fare_id", "route_id", "origin_id", "destination_id"], False),
    ("feed_info.txt",
     "SELECT feed_publisher_name, feed_publisher_url, feed_lang, feed_start_date, feed_end_date, feed_version "
     "FROM feed_info",
     ["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date", "feed_end_date", "feed_version"], True),
    ("data_sources.txt",
     "SELECT source_id, name, url, license, category, retrieved_at, notes FROM data_sources ORDER BY source_id",
     ["source_id", "name", "url", "license", "category", "retrieved_at", "notes"], True),
    ("provenance.txt",
     "SELECT entity_table, entity_id, field, source_id, category, status, verified_at, notes "
     "FROM provenance ORDER BY entity_table, entity_id, field",
     ["entity_table", "entity_id", "field", "source_id", "category", "status", "verified_at", "notes"], True),
]


def export_gtfs_from_sqlite(db_path: Path, out_dir: Path) -> dict[str, int]:
    """Export all GTFS + extension tables from SQLite back to ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        counts: dict[str, int] = {}
        for filename, sql, cols, write_if_empty in _EXPORT_SPEC:
            rows = [dict(r) for r in con.execute(sql)]
            if not rows and not write_if_empty:
                continue
            counts[filename] = write_table(out_dir / filename, cols, rows)

        # Reconstruct Bangla translations.txt from stops.stop_name_bn & routes.route_long_name_bn
        trans: list[dict] = []
        for r in con.execute("SELECT route_id, route_long_name_bn FROM routes WHERE route_long_name_bn IS NOT NULL ORDER BY route_id"):
            trans.append({"table_name": "routes", "field_name": "route_long_name", "language": "bn",
                          "translation": r["route_long_name_bn"], "record_id": r["route_id"]})
        for r in con.execute("SELECT stop_id, stop_name_bn FROM stops WHERE stop_name_bn IS NOT NULL ORDER BY stop_id"):
            trans.append({"table_name": "stops", "field_name": "stop_name", "language": "bn",
                          "translation": r["stop_name_bn"], "record_id": r["stop_id"]})
        if trans:
            counts["translations.txt"] = write_table(
                out_dir / "translations.txt",
                ["table_name", "field_name", "language", "translation", "record_id"],
                trans,
            )
        return counts
    finally:
        con.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--feed", type=Path, default=ROOT / "data" / "gtfs" / "dhaka")
    ap.add_argument("--db", type=Path, default=ROOT / "dhaka_transit.sqlite")
    ap.add_argument("--export", action="store_true", help="export GTFS from --db into --out instead of loading")
    ap.add_argument("--out", type=Path, help="output directory for --export")
    args = ap.parse_args(argv)

    if args.export:
        if not args.out:
            ap.error("--out is required with --export")
        counts = export_gtfs_from_sqlite(args.db, args.out)
        print(f"Exported GTFS from {args.db} -> {args.out}:")
    else:
        counts = load_gtfs_to_sqlite(args.feed, args.db)
        print(f"Loaded GTFS from {args.feed} -> {args.db}:")
    for k, v in counts.items():
        print(f"  {k:<20} {v:>6}")


if __name__ == "__main__":
    main()
