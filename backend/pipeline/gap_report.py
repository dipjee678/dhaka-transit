"""Generate a data-coverage and gap report for Dhaka Transit (Phase 2).

Run from backend/:  python -m pipeline.gap_report --snapshot 2026-11-16
Writes: data/reports/gap_report.md and data/reports/gap_report.json

Quantifies what is currently available in OpenStreetMap and official sources,
what is missing or unverified, candidate upstream OSM fixes, and which MRT-6
interchange stations have the highest nearby bus-stop density for field surveys.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from collections import Counter
from pathlib import Path

from transit.geo import GridIndex, haversine_m

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "osm"
CURATED = ROOT / "data" / "curated"
REPORTS = ROOT / "data" / "reports"


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]


def analyze_bus_relations(bus_routes_json: Path) -> list[dict]:
    if not bus_routes_json.exists():
        return []
    els = json.loads(bus_routes_json.read_text("utf-8")).get("elements", [])
    out = []
    for e in els:
        if e["type"] != "relation" or e.get("tags", {}).get("route") != "bus":
            continue
        t = e.get("tags", {})
        members = e.get("members", [])
        stop_members = [m for m in members if m["type"] == "node" and ("stop" in m["role"] or "platform" in m["role"])]
        way_members = [m for m in members if m["type"] == "way"]
        issues = []
        if len(stop_members) < 2:
            issues.append(f"only {len(stop_members)} stop/platform member(s)")
        if len(way_members) < 2:
            issues.append(f"only {len(way_members)} way member(s)")
        out.append({
            "osm_id": f"r{e['id']}",
            "ref": t.get("ref", ""),
            "name": t.get("name", ""),
            "operator": t.get("operator", ""),
            "from": t.get("from", ""),
            "to": t.get("to", ""),
            "stop_members": len(stop_members),
            "way_members": len(way_members),
            "usable": not issues,
            "issues": issues,
        })
    return out


def analyze_bus_inventory(stops: list[dict]) -> dict:
    idx: GridIndex[int] = GridIndex(cell_size_m=100)
    for i, s in enumerate(stops):
        idx.insert(i, float(s["lat"]), float(s["lon"]))

    same_kind_dups = 0
    ptv2_pairs = 0
    seen: set[tuple[int, int]] = set()
    for i, s in enumerate(stops):
        lat, lon = float(s["lat"]), float(s["lon"])
        for n in idx.within(lat, lon, 30.0):
            j = n.key
            if j <= i or (i, j) in seen:
                continue
            other = stops[j]
            same_name = bool(s["name"]) and s["name"].casefold() == other["name"].casefold()
            if not (same_name or n.distance_m <= 3.0):
                continue
            seen.add((i, j))
            kinds = {s["kind"], other["kind"]}
            if kinds == {"platform", "stop_position"}:
                ptv2_pairs += 1
            else:
                same_kind_dups += 1

    return {
        "total": len(stops),
        "by_kind": dict(Counter(s["kind"] for s in stops)),
        "unnamed": sum(not s["name"] for s in stops),
        "with_bangla_name": sum(bool(s["name_bn"]) for s in stops),
        "ptv2_platform_stop_pairs_within_30m": ptv2_pairs,
        "potential_duplicate_pairs_within_30m": same_kind_dups,
    }


def analyze_interchanges(stations: list[dict], bus_stops: list[dict]) -> list[dict]:
    idx: GridIndex[int] = GridIndex(cell_size_m=250)
    for i, b in enumerate(bus_stops):
        idx.insert(i, float(b["lat"]), float(b["lon"]))

    rows = []
    for st in sorted(stations, key=lambda r: int(r["seq"])):
        lat, lon = float(st["lat"]), float(st["lon"])
        near_500 = idx.within(lat, lon, 500.0)
        near_250 = [n for n in near_500 if n.distance_m <= 250.0]
        # Distinct nearby stop names (closest occurrence of each) for quick reference.
        sample_names: list[str] = []
        seen_names: set[str] = set()
        for n in near_500:
            name = bus_stops[n.key]["name"]
            if name and name.casefold() not in seen_names:
                seen_names.add(name.casefold())
                sample_names.append(f"{name} ({n.distance_m:.0f} m)")
            if len(sample_names) == 3:
                break
        rows.append({
            "seq": int(st["seq"]),
            "station_id": st["station_id"],
            "name_en": st["name_en"],
            "bus_stops_250m": len(near_250),
            "bus_stops_500m": len(near_500),
            "closest_named": sample_names,
        })
    return rows


def analyze_metro(curated: Path) -> dict:
    stations = _read_csv(curated / "metro_stations.csv")
    official = _read_csv(curated / "official" / "dmtcl_stations.csv")
    shapes = _read_csv(curated / "metro_shapes.csv")
    service = _read_csv(curated / "metro_service.csv")
    fares = _read_csv(curated / "metro_fares.csv")

    lengths_km = {}
    for sid in sorted({r["shape_id"] for r in shapes}):
        pts = sorted((r for r in shapes if r["shape_id"] == sid), key=lambda r: int(r["seq"]))
        lengths_km[sid] = round(float(pts[-1]["dist_m"]) / 1000.0, 2) if pts else 0.0

    osm_fixes = [
        {"seq": int(o["seq"]), "name_bn": o["name_bn"], "note": o["notes"]}
        for o in official if "candidate OSM fix" in o.get("notes", "")
    ]
    return {
        "operational_stations_mapped": len(stations),
        "official_operational_stations": sum(o["operational"] == "1" for o in official),
        "official_planned_excluded": [o["name_bn"] for o in official if o["operational"] != "1"],
        "track_length_km": lengths_km,
        "schedule_status": sorted({(r["source_category"], r["status"]) for r in service}),
        "verified_fare_pairs": sum(r.get("status") == "verified" for r in fares),
        "total_fare_pairs_needed": len(stations) * (len(stations) - 1),
        "candidate_osm_fixes": osm_fixes,
    }


def build_report(snapshot: str, raw_dir: Path = RAW, curated_dir: Path = CURATED) -> dict:
    stations = _read_csv(curated_dir / "metro_stations.csv")
    bus_stops = _read_csv(curated_dir / "bus_stops.csv")
    bus_routes_curated = _read_csv(curated_dir / "bus_routes.csv")
    return {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "osm_snapshot": snapshot,
        "metro": analyze_metro(curated_dir),
        "osm_bus_relations": analyze_bus_relations(raw_dir / snapshot / "bus_routes.json"),
        "curated_bus_routes": len(bus_routes_curated),
        "osm_bus_inventory": analyze_bus_inventory(bus_stops),
        "mrt6_interchanges": analyze_interchanges(stations, bus_stops),
    }


def render_markdown(rep: dict) -> str:
    m = rep["metro"]
    inv = rep["osm_bus_inventory"]
    rels = rep["osm_bus_relations"]
    inter = rep["mrt6_interchanges"]
    top5 = sorted(inter, key=lambda r: (-r["bus_stops_500m"], -r["bus_stops_250m"]))[:5]

    lines = [
        "# Dhaka Transit — Data Coverage & Gap Report",
        "",
        f"- Generated: {rep['generated_at']}",
        f"- OSM snapshot: `{rep['osm_snapshot']}` (Greater Dhaka bbox `23.60,90.20,24.05,90.60`)",
        "",
        "## 1. MRT Line 6 Status",
        "",
        f"- Operational stations mapped: **{m['operational_stations_mapped']} / {m['official_operational_stations']}** "
        "(cross-checked against DMTCL official station page).",
        f"- Planned/non-operational stations excluded: {', '.join(m['official_planned_excluded']) or 'none'}.",
        f"- OSM track geometry length: " + ", ".join(f"`{k}` = {v} km" for k, v in m["track_length_km"].items()) + ".",
        f"- Timetable provenance: `{m['schedule_status']}` — currently based on secondary web reports "
        "(tagged `estimated` / `unverified`). **Gap:** obtain official DMTCL machine-readable or transcribed timetable.",
        f"- Fares: **{m['verified_fare_pairs']} / {m['total_fare_pairs_needed']}** OD pairs verified. "
        "**Gap:** transcribe official DMTCL fare chart directly (third-party web tables were rejected after "
        "finding asymmetric fare contradictions between reverse station pairs).",
        "",
        "### Candidate upstream OpenStreetMap fixes (MRT Line 6)",
        "",
    ]
    for fix in m["candidate_osm_fixes"]:
        lines.append(f"- Station #{fix['seq']} ({fix['name_bn']}): {fix['note']}")

    lines += [
        "",
        "## 2. Dhaka Bus Network Gap in OpenStreetMap",
        "",
        f"- `route=bus` relations found in OSM across Greater Dhaka: **{len(rels)}** "
        f"(usable for GTFS: **{sum(r['usable'] for r in rels)}**).",
        f"- Curated bus routes currently in GTFS: **{rep['curated_bus_routes']}**.",
        "",
    ]
    if rels:
        lines += [
            "| OSM Relation | Ref | Name | Operator | Stop members | Way members | Issues |",
            "|---|---|---|---|---:|---:|---|",
        ]
        for r in rels:
            lines.append(
                f"| `{r['osm_id']}` | {r['ref']} | {r['name']} | {r['operator']} "
                f"| {r['stop_members']} | {r['way_members']} | {'; '.join(r['issues']) or 'ok'} |"
            )
        lines.append("")

    lines += [
        "### OSM Bus Stop Inventory (`data/curated/bus_stops.csv`)",
        "",
        f"- Total bus-stop elements in Greater Dhaka: **{inv['total']}**",
        f"  - Breakdown by kind: {inv['by_kind']}",
        f"  - Unnamed elements: **{inv['unnamed']}** ({inv['unnamed'] / max(1, inv['total']) * 100:.1f}%)",
        f"  - With `name:bn`: **{inv['with_bangla_name']}** ({inv['with_bangla_name'] / max(1, inv['total']) * 100:.1f}%)",
        f"  - PTv2 `platform` + `stop_position` pairs within 30 m (expected): {inv['ptv2_platform_stop_pairs_within_30m']}",
        f"  - Potential duplicate pairs within 30 m (same name or <= 3 m, excluding PTv2 pairs): "
        f"**{inv['potential_duplicate_pairs_within_30m']}**",
        "",
        "### External Source Audit (Rejected for Direct Ingestion)",
        "",
        "- `imamhossain94/dhaka-city-local-bus-json-data` (GitHub): **no licence** (`license: None`), "
        "scraped from `busroutebd.com`, last updated 2021-11-14. Cannot legally be redistributed under ODbL.",
        "- Third-party MRT-6 fare blogs (`metrodhaka.com`, `dhakametro.online`): contained asymmetric OD prices "
        "(e.g. Mirpur 11 ↔ Motijheel and Kazipara ↔ Motijheel differed by direction); excluded pending official DMTCL chart.",
        "",
        "## 3. Multimodal Interchange Potential (Bus Stops Near MRT-6 Stations)",
        "",
        "Ranking MRT-6 stations by nearby OSM bus-stop density identifies the highest-impact corridors "
        "for Phase 2 field surveys (top 5: " + ", ".join(f"**{r['name_en']}** ({r['bus_stops_500m']})" for r in top5) + ").",
        "",
        "| # | MRT-6 Station | OSM bus stops ≤ 250 m | ≤ 500 m | Closest named OSM stops |",
        "|---:|---|---:|---:|---|",
    ]
    for r in inter:
        lines.append(
            f"| {r['seq']} | {r['name_en']} | {r['bus_stops_250m']} | {r['bus_stops_500m']} "
            f"| {'; '.join(r['closest_named']) or 'none'} |"
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", default="2026-11-16")
    ap.add_argument("--reports", type=Path, default=REPORTS)
    args = ap.parse_args(argv)

    rep = build_report(args.snapshot)
    args.reports.mkdir(parents=True, exist_ok=True)
    (args.reports / "gap_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    md = render_markdown(rep)
    (args.reports / "gap_report.md").write_text(md, encoding="utf-8")
    print(f"wrote {args.reports / 'gap_report.md'} and gap_report.json")
    print("Top 5 MRT-6 stations by OSM bus stops within 500 m:")
    for r in sorted(rep["mrt6_interchanges"], key=lambda x: -x["bus_stops_500m"])[:5]:
        print(f"  {r['name_en']:<24} <=250m: {r['bus_stops_250m']:>2}   <=500m: {r['bus_stops_500m']:>2}")


if __name__ == "__main__":
    main()
