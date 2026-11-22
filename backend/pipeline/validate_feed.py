"""Validate the built feed and write human + machine readable reports.

Run from backend/:  python -m pipeline.validate_feed [--feed DIR] [--strict]
Writes: data/reports/validation_report.json and validation_report.md
Exit code 1 if there are errors (or warnings with --strict) — used by CI.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

from transit.gtfs.io import read_feed
from transit.gtfs.validator import summarize, validate_feed

ROOT = Path(__file__).resolve().parents[2]


def dataset_stats(feed: dict, curated: Path) -> dict:
    stops = feed.get("stops.txt", [])
    routes = feed.get("routes.txt", [])
    prov = feed.get("provenance.txt", [])
    inv_path = curated / "bus_stops.csv"
    inventory = list(csv.DictReader(open(inv_path, encoding="utf-8"))) if inv_path.exists() else []
    return {
        "stations": sum(s.get("location_type") == "1" for s in stops),
        "boarding_stops": sum(s.get("location_type", "0") in ("", "0") for s in stops),
        "routes_by_type": dict(Counter({"1": "metro", "3": "bus"}.get(r["route_type"], r["route_type"]) for r in routes)),
        "trips": len(feed.get("trips.txt", [])),
        "shapes": len({r["shape_id"] for r in feed.get("shapes.txt", [])}),
        "provenance_by_category": dict(Counter(p["category"] for p in prov if not p.get("field"))),
        "provenance_by_status": dict(Counter(p["status"] for p in prov if not p.get("field"))),
        "osm_bus_stop_inventory": len(inventory),
        "osm_bus_stop_inventory_unnamed": sum(not r["name"] for r in inventory),
        "osm_bus_stop_inventory_by_kind": dict(Counter(r["kind"] for r in inventory)),
    }


def to_markdown(summary: dict, issues: list, stats: dict, feed_dir: Path) -> str:
    lines = [f"# GTFS Validation Report", "",
             f"- Feed: `{feed_dir.relative_to(ROOT) if feed_dir.is_relative_to(ROOT) else feed_dir}`",
             f"- Generated: {dt.datetime.now().isoformat(timespec='seconds')}",
             f"- **Errors: {summary['errors']} · Warnings: {summary['warnings']} · Info: {summary['infos']}**", "",
             "## Dataset statistics", "", "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in stats.items()]
    lines += ["", "## Issues by type", "", "| Severity | Code | Count |", "|---|---|---|"]
    lines += [f"| {r['severity']} | `{r['code']}` | {r['count']} |" for r in summary["by_code"]] or ["| – | none | 0 |"]
    shown = [i for i in issues if i.severity != "info"][:200]
    if shown:
        lines += ["", "## Details (errors and warnings, first 200)", "", "| Severity | Code | File | Entity | Message |",
                  "|---|---|---|---|---|"]
        lines += [f"| {i.severity} | `{i.code}` | {i.file} | {i.entity_id} | {i.message} |" for i in shown]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", type=Path, default=ROOT / "data" / "gtfs" / "dhaka")
    ap.add_argument("--reports", type=Path, default=ROOT / "data" / "reports")
    ap.add_argument("--strict", action="store_true", help="fail on warnings too")
    args = ap.parse_args(argv)

    feed = read_feed(args.feed)
    issues = validate_feed(feed)
    summary = summarize(issues)
    stats = dataset_stats(feed, ROOT / "data" / "curated")
    args.reports.mkdir(parents=True, exist_ok=True)
    (args.reports / "validation_report.json").write_text(json.dumps(
        {"summary": summary, "stats": stats, "issues": [i.to_dict() for i in issues]},
        ensure_ascii=False, indent=1), encoding="utf-8")
    (args.reports / "validation_report.md").write_text(to_markdown(summary, issues, stats, args.feed), encoding="utf-8")

    print(f"errors={summary['errors']} warnings={summary['warnings']} info={summary['infos']}")
    for r in summary["by_code"]:
        print(f"  {r['severity']:<8} {r['code']:<28} {r['count']}")
    print(json.dumps(stats, ensure_ascii=False, indent=1))
    failed = summary["errors"] > 0 or (args.strict and summary["warnings"] > 0)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
