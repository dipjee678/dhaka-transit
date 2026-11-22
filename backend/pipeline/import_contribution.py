"""Import a volunteer's route survey (contributions/templates/route_template.csv)
into the curated CSVs, tagged ``community`` / ``unverified``.

Run from backend/:
    python -m pipeline.import_contribution path/to/survey.csv [--dry-run]

What it does
1. Validates the survey rows (coordinates inside Dhaka, sequences 1..n, times).
2. Snaps each surveyed stop to an existing OSM stop from bus_stops.csv when one
   lies within SNAP_RADIUS_M (name similarity breaks ties); otherwise creates a
   new community stop.
3. Appends rows to bus_routes.csv, bus_route_stops.csv, community_stops.csv,
   bus_service.csv (only if service days/hours/headway were observed) and
   registers the contribution in data_sources.csv.
Nothing is invented: missing segment times stay empty (the GTFS builder then
estimates them and labels them 'estimated').
"""
from __future__ import annotations

import argparse
import csv
import difflib
import re
import statistics
from collections import defaultdict
from pathlib import Path

from transit.geo import GridIndex, in_bbox

ROOT = Path(__file__).resolve().parents[2]
CURATED = ROOT / "data" / "curated"
SNAP_RADIUS_M = 40.0

HEADERS = {
    "bus_routes.csv": ["route_id", "route_short_name", "route_long_name", "route_long_name_bn", "operator",
                       "operator_url", "source_category", "source_id", "status", "verified_at", "notes"],
    "bus_route_stops.csv": ["route_id", "direction_id", "sequence", "stop_ref", "minutes_from_previous",
                            "source_category", "source_id", "status", "verified_at", "notes"],
    "community_stops.csv": ["stop_ref", "name", "name_bn", "lat", "lon", "source_category", "source_id",
                            "status", "verified_at", "notes"],
    "bus_service.csv": ["route_id", "direction_id", "service_id", "days", "start_time", "end_time", "headway_min",
                        "shape_id", "source_category", "source_id", "status", "verified_at", "notes"],
    "data_sources.csv": ["source_id", "name", "url", "license", "category", "retrieved_at", "notes"],
}


class SurveyError(ValueError):
    pass


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]


def _append(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=HEADERS[path.name], extrasaction="ignore", lineterminator="\n")
        if new:
            w.writeheader()
        w.writerows(rows)


def check_survey(rows: list[dict]) -> dict[tuple[str, str, int], list[dict]]:
    if not rows:
        raise SurveyError("survey file is empty")
    groups: dict[tuple[str, str, int], list[dict]] = defaultdict(list)
    for i, r in enumerate(rows, start=2):
        for col in ("route_name", "direction", "sequence", "stop_name", "lat", "lon", "contributor", "observed_on"):
            if not r.get(col):
                raise SurveyError(f"line {i}: '{col}' is required")
        try:
            lat, lon = float(r["lat"]), float(r["lon"])
        except ValueError:
            raise SurveyError(f"line {i}: lat/lon must be numbers")
        if not in_bbox(lat, lon):
            raise SurveyError(f"line {i}: ({lat}, {lon}) is outside Dhaka — swapped lat/lon?")
        if r["direction"] not in ("0", "1"):
            raise SurveyError(f"line {i}: direction must be 0 or 1")
        if r.get("minutes_from_previous"):
            if float(r["minutes_from_previous"]) <= 0:
                raise SurveyError(f"line {i}: minutes_from_previous must be positive")
        groups[(r["route_name"], r.get("operator", ""), int(r["direction"]))].append(r)
    for key, g in groups.items():
        seqs = sorted(int(r["sequence"]) for r in g)
        if seqs != list(range(1, len(g) + 1)):
            raise SurveyError(f"{key}: sequence must be 1..{len(g)} without gaps, got {seqs}")
        if len(g) < 2:
            raise SurveyError(f"{key}: a route needs at least 2 stops")
    return groups


def snap_stop(name: str, lat: float, lon: float, idx: GridIndex, inventory: dict[str, dict]) -> str | None:
    """OSM stop id of the best match within SNAP_RADIUS_M, or None."""
    cands = idx.within(lat, lon, SNAP_RADIUS_M)
    if not cands:
        return None

    def score(n):
        sim = difflib.SequenceMatcher(None, name.casefold(), inventory[n.key]["name"].casefold()).ratio()
        return (-sim, n.distance_m)
    return min(cands, key=score).key


def import_survey(survey: Path, curated: Path = CURATED, dry_run: bool = False) -> dict:
    rows = _read(survey)
    groups = check_survey(rows)
    inventory = {r["osm_id"]: r for r in _read(curated / "bus_stops.csv") if r["kind"] != "bus_station"}
    idx: GridIndex[str] = GridIndex(cell_size_m=100)
    for k, r in inventory.items():
        idx.insert(k, float(r["lat"]), float(r["lon"]))
    existing_routes = {r["route_id"] for r in _read(curated / "bus_routes.csv")}
    existing_cstops = {r["stop_ref"] for r in _read(curated / "community_stops.csv")}
    sources = {r["source_id"] for r in _read(curated / "data_sources.csv")}

    contributor = rows[0]["contributor"]
    date = max(r["observed_on"] for r in rows)
    source_id = f"community_{slug(contributor)}_{date}"
    prov = {"source_category": "community", "source_id": source_id, "status": "unverified", "verified_at": ""}

    out = {k: [] for k in HEADERS}
    if source_id not in sources:
        out["data_sources.csv"].append({"source_id": source_id, "name": f"Field survey by {contributor}",
                                        "license": "ODbL-1.0 (contributor agreement)", "category": "community",
                                        "retrieved_at": date, "notes": f"Imported from {survey.name}"})
    snapped = new_stops = 0
    for (route_name, operator, direction), g in groups.items():
        ordered = sorted(g, key=lambda r: int(r["sequence"]))
        route_id = "bus_" + slug(f"{operator}_{route_name}" if operator else route_name)
        if route_id not in existing_routes:
            existing_routes.add(route_id)
            out["bus_routes.csv"].append({"route_id": route_id, "route_short_name": route_name,
                                          "route_long_name": f"{ordered[0]['stop_name']} - {ordered[-1]['stop_name']}",
                                          "operator": operator, **prov, "notes": ordered[0].get("evidence", "")})
        for r in ordered:
            lat, lon = float(r["lat"]), float(r["lon"])
            ref = snap_stop(r["stop_name"], lat, lon, idx, inventory)
            if ref:
                snapped += 1
            else:
                ref = f"c_{slug(r['stop_name'])}_{lat:.4f}_{lon:.4f}".replace(".", "")
                if ref not in existing_cstops:
                    existing_cstops.add(ref)
                    new_stops += 1
                    out["community_stops.csv"].append({"stop_ref": ref, "name": r["stop_name"],
                                                       "name_bn": r.get("stop_name_bn", ""),
                                                       "lat": f"{lat:.7f}", "lon": f"{lon:.7f}", **prov,
                                                       "notes": r.get("evidence", "")})
            out["bus_route_stops.csv"].append({"route_id": route_id, "direction_id": direction,
                                               "sequence": r["sequence"], "stop_ref": ref,
                                               "minutes_from_previous": r.get("minutes_from_previous", ""),
                                               **prov, "notes": r.get("notes", "")})
        first = g[0]
        heads = [float(r["observed_headway_min"]) for r in g if r.get("observed_headway_min")]
        if first.get("service_days") and first.get("service_start") and first.get("service_end") and heads:
            out["bus_service.csv"].append({
                "route_id": route_id, "direction_id": direction, "service_id": f"{route_id}_svc",
                "days": first["service_days"], "start_time": first["service_start"],
                "end_time": first["service_end"], "headway_min": f"{statistics.median(heads):g}", "shape_id": "",
                **prov, "notes": f"headway = median of {len(heads)} observation(s)"})

    if not dry_run:
        for name, new_rows in out.items():
            _append(curated / name, new_rows)
    return {"routes": len(out["bus_routes.csv"]), "route_stops": len(out["bus_route_stops.csv"]),
            "snapped_to_osm": snapped, "new_community_stops": new_stops, "services": len(out["bus_service.csv"]),
            "source_id": source_id}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("survey", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    print(import_survey(args.survey, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
