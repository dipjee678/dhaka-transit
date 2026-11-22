"""Build the Dhaka GTFS feed from curated CSVs.

Run from backend/:  python -m pipeline.build_gtfs  [--curated DIR] [--out DIR]

Input  (data/curated/):
  data_sources.csv, metro_stations.csv, metro_shapes.csv, metro_service.csv,
  bus_routes.csv, bus_route_stops.csv, bus_service.csv, bus_stops.csv,
  community_stops.csv, bus_shapes.csv (bus files may be header-only)
Output (data/gtfs/dhaka/):
  standard GTFS files + translations.txt (Bangla names) +
  extension files provenance.txt and data_sources.txt, and dhaka_gtfs.zip
"""
from __future__ import annotations

import argparse
import csv
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from pipeline import config
from pipeline.curate_osm import project_on_polyline
from transit.geo import haversine_m
from transit.gtfs.io import write_table, zip_feed
from transit.gtfs.spec import format_gtfs_time, parse_gtfs_time

ROOT = Path(__file__).resolve().parents[2]
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

COLUMNS = {
    "agency.txt": ["agency_id", "agency_name", "agency_url", "agency_timezone", "agency_lang", "agency_phone"],
    "stops.txt": ["stop_id", "stop_code", "stop_name", "stop_desc", "stop_lat", "stop_lon", "zone_id",
                  "location_type", "parent_station", "wheelchair_boarding", "platform_code"],
    "routes.txt": ["route_id", "agency_id", "route_short_name", "route_long_name", "route_desc", "route_type",
                   "route_url", "route_color", "route_text_color"],
    "trips.txt": ["route_id", "service_id", "trip_id", "trip_headsign", "direction_id", "shape_id"],
    "stop_times.txt": ["trip_id", "arrival_time", "departure_time", "stop_id", "stop_sequence", "timepoint",
                       "shape_dist_traveled"],
    "calendar.txt": ["service_id", *DAYS, "start_date", "end_date"],
    "calendar_dates.txt": ["service_id", "date", "exception_type"],
    "shapes.txt": ["shape_id", "shape_pt_lat", "shape_pt_lon", "shape_pt_sequence", "shape_dist_traveled"],
    "frequencies.txt": ["trip_id", "start_time", "end_time", "headway_secs", "exact_times"],
    "fare_attributes.txt": ["fare_id", "price", "currency_type", "payment_method", "transfers", "agency_id"],
    "fare_rules.txt": ["fare_id", "route_id", "origin_id", "destination_id"],
    "feed_info.txt": ["feed_publisher_name", "feed_publisher_url", "feed_lang", "feed_start_date",
                      "feed_end_date", "feed_version"],
    "translations.txt": ["table_name", "field_name", "language", "translation", "record_id"],
    "provenance.txt": ["entity_table", "entity_id", "field", "source_id", "category", "status", "verified_at", "notes"],
    "data_sources.txt": ["source_id", "name", "url", "license", "category", "retrieved_at", "notes"],
}


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [{k: (v or "").strip() for k, v in r.items()} for r in csv.DictReader(fh)]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


@dataclass
class FeedBuilder:
    tables: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    _services: dict[str, dict] = field(default_factory=dict)

    def prov(self, table: str, entity_id: str, row: dict, fld: str = "", **override) -> None:
        rec = {"entity_table": table, "entity_id": entity_id, "field": fld,
               "source_id": row.get("source_id", ""), "category": row.get("source_category", ""),
               "status": row.get("status", "unverified") or "unverified",
               "verified_at": row.get("verified_at", ""), "notes": row.get("notes", "")}
        rec.update(override)
        self.tables["provenance.txt"].append(rec)

    def service(self, service_id: str, days: str) -> None:
        wanted = {d.strip().lower() for d in days.split(";") if d.strip()}
        bad = wanted - set(DAYS)
        if bad:
            raise ValueError(f"unknown day names {bad} for service {service_id}")
        row = {"service_id": service_id, **{d: int(d in wanted) for d in DAYS},
               "start_date": config.FEED_START_DATE, "end_date": config.FEED_END_DATE}
        if service_id in self._services and self._services[service_id] != row:
            raise ValueError(f"service {service_id} defined with different days")
        if service_id not in self._services:
            self._services[service_id] = row
            self.tables["calendar.txt"].append(row)

    def add_frequency_trip(self, *, trip_id: str, route_id: str, service_id: str, direction_id: int,
                           headsign: str, shape_id: str, stop_ids: list[str], offsets_s: list[int],
                           shape_dists: list[float] | None, first: str, last: str, headway_min: float,
                           prov_row: dict) -> None:
        start = parse_gtfs_time(first + ":00" if first.count(":") == 1 else first)
        end = parse_gtfs_time(last + ":00" if last.count(":") == 1 else last) + 60  # include last departure
        self.tables["trips.txt"].append({"route_id": route_id, "service_id": service_id, "trip_id": trip_id,
                                         "trip_headsign": headsign, "direction_id": direction_id,
                                         "shape_id": shape_id})
        for i, (sid, off) in enumerate(zip(stop_ids, offsets_s)):
            t = format_gtfs_time(start + off)
            self.tables["stop_times.txt"].append({
                "trip_id": trip_id, "arrival_time": t, "departure_time": t, "stop_id": sid,
                "stop_sequence": i + 1, "timepoint": 0,
                "shape_dist_traveled": f"{shape_dists[i]:.1f}" if shape_dists else ""})
        self.tables["frequencies.txt"].append({"trip_id": trip_id, "start_time": format_gtfs_time(start),
                                               "end_time": format_gtfs_time(end),
                                               "headway_secs": int(round(float(headway_min) * 60)),
                                               "exact_times": 0})
        self.prov("trips", trip_id, prov_row)
        self.prov("stop_times", trip_id, prov_row)
        self.prov("frequencies", f"{trip_id}|{format_gtfs_time(start)}", prov_row)


# ---------------------------------------------------------------------------
def build_metro(b: FeedBuilder, cur: Path) -> None:
    stations = read_csv(cur / "metro_stations.csv")
    if not stations:
        return
    stations.sort(key=lambda r: int(r["seq"]))
    shapes = defaultdict(list)
    for r in read_csv(cur / "metro_shapes.csv"):
        shapes[r["shape_id"]].append(r)
    services = read_csv(cur / "metro_service.csv")

    b.tables["agency.txt"].append(config.METRO_AGENCY)
    b.tables["routes.txt"].append(config.METRO_ROUTE)
    b.tables["translations.txt"].append({"table_name": "routes", "field_name": "route_long_name", "language": "bn",
                                         "translation": config.METRO_ROUTE_NAME_BN, "record_id": "mrt6"})
    b.prov("routes", "mrt6", {"source_id": "dmtcl_station_page", "source_category": "official",
                              "status": "verified", "verified_at": "2026-11-16",
                              "notes": "Line, operator and station order from DMTCL; colour #006E34 from OSM r15060935"})

    for s in stations:
        b.tables["stops.txt"].append({"stop_id": s["station_id"], "stop_name": s["name_en"],
                                      "stop_lat": s["lat"], "stop_lon": s["lon"],
                                      "zone_id": s["station_id"], "location_type": 1})
        b.tables["translations.txt"].append({"table_name": "stops", "field_name": "stop_name", "language": "bn",
                                             "translation": s["name_bn"], "record_id": s["station_id"]})
        b.prov("stops", s["station_id"], s, notes=f"OSM {s['osm_stop_area']}; point: {s['station_point_method']}")
        b.prov("stops", s["station_id"], s, fld="stop_name_bn", source_id="dmtcl_station_page",
               category="official", status="verified", verified_at="2026-11-16", notes="")
        for d in ("0", "1"):
            pid = s[f"platform_d{d}_id"]
            b.tables["stops.txt"].append({
                "stop_id": pid, "stop_name": s["name_en"], "stop_lat": s[f"lat_d{d}"], "stop_lon": s[f"lon_d{d}"],
                "zone_id": s["station_id"], "location_type": 0, "parent_station": s["station_id"],
                "stop_desc": "Platform towards " + ("Motijheel" if d == "0" else "Uttara North")})
            b.tables["translations.txt"].append({"table_name": "stops", "field_name": "stop_name",
                                                 "language": "bn", "translation": s["name_bn"], "record_id": pid})
            b.prov("stops", pid, s, notes=f"OSM stop_position {s[f'osm_node_d{d}']}")

    for shape_id, pts in shapes.items():
        pts.sort(key=lambda r: int(r["seq"]))
        for p in pts:
            b.tables["shapes.txt"].append({"shape_id": shape_id, "shape_pt_lat": p["lat"], "shape_pt_lon": p["lon"],
                                           "shape_pt_sequence": int(p["seq"]), "shape_dist_traveled": p["dist_m"]})
        b.prov("shapes", shape_id, pts[0])

    for svc in services:
        d = int(svc["direction_id"])
        b.service(svc["service_id"], svc["days"])
        order = stations if d == 0 else list(reversed(stations))
        stop_ids = [s[f"platform_d{d}_id"] for s in order]
        shape_id = f"mrt6_d{d}"
        line = [(float(p["lat"]), float(p["lon"])) for p in sorted(shapes[shape_id], key=lambda r: int(r["seq"]))]
        dists = [project_on_polyline(float(s[f"lat_d{d}"]), float(s[f"lon_d{d}"]), line)[0] for s in order]
        if any(b2 <= a for a, b2 in zip(dists, dists[1:])):
            raise ValueError(f"metro platforms are not in order along shape {shape_id}")
        total = float(svc["end_to_end_min"]) * 60
        span = dists[-1] - dists[0]
        # Run time distributed in proportion to track distance (dwell folded in): ESTIMATE.
        offsets = [int(round(total * (x - dists[0]) / span)) for x in dists]
        b.add_frequency_trip(
            trip_id=f"{svc['service_id']}_d{d}", route_id="mrt6", service_id=svc["service_id"],
            direction_id=d, headsign=order[-1]["name_en"], shape_id=shape_id, stop_ids=stop_ids,
            offsets_s=offsets, shape_dists=dists, first=svc["first_departure"], last=svc["last_departure"],
            headway_min=float(svc["headway_min"]),
            prov_row={**svc, "notes": svc["notes"] + " Per-station times distributed by track distance (estimate)."})


# ---------------------------------------------------------------------------
def build_buses(b: FeedBuilder, cur: Path) -> None:
    routes = read_csv(cur / "bus_routes.csv")
    if not routes:
        return
    inventory = {r["osm_id"]: r for r in read_csv(cur / "bus_stops.csv")}
    community = {r["stop_ref"]: r for r in read_csv(cur / "community_stops.csv")}
    seqs = defaultdict(list)
    for r in read_csv(cur / "bus_route_stops.csv"):
        seqs[(r["route_id"], int(r["direction_id"]))].append(r)
    shapes = defaultdict(list)
    for r in read_csv(cur / "bus_shapes.csv"):
        shapes[r["shape_id"]].append(r)
    services = read_csv(cur / "bus_service.csv")
    added_stops: set[str] = set()
    agencies = {a["agency_id"] for a in b.tables["agency.txt"]}

    for r in routes:
        aid = "op_" + slug(r["operator"] or "unknown")
        if aid not in agencies:
            agencies.add(aid)
            # agency_url is required by GTFS; private operators rarely have one.
            b.tables["agency.txt"].append({"agency_id": aid, "agency_name": r["operator"] or "Unknown operator",
                                           "agency_url": r.get("operator_url") or config.FEED_PUBLISHER_URL,
                                           "agency_timezone": config.TIMEZONE, "agency_lang": "bn"})
        b.tables["routes.txt"].append({"route_id": r["route_id"], "agency_id": aid,
                                       "route_short_name": r["route_short_name"],
                                       "route_long_name": r["route_long_name"], "route_type": 3})
        if r.get("route_long_name_bn"):
            b.tables["translations.txt"].append({"table_name": "routes", "field_name": "route_long_name",
                                                 "language": "bn", "translation": r["route_long_name_bn"],
                                                 "record_id": r["route_id"]})
        b.prov("routes", r["route_id"], r)

    for svc in services:
        key = (svc["route_id"], int(svc["direction_id"]))
        rows = sorted(seqs.get(key, []), key=lambda x: int(x["sequence"]))
        if len(rows) < 2:
            raise ValueError(f"bus route {key} has fewer than 2 stops")
        b.service(svc["service_id"], svc["days"])
        stop_ids, coords, offsets, estimated = [], [], [], False
        for i, rs in enumerate(rows):
            ref = rs["stop_ref"]
            src = inventory.get(ref) or community.get(ref)
            if src is None:
                raise ValueError(f"stop_ref {ref!r} not in bus_stops.csv or community_stops.csv")
            sid = f"bus_{slug(ref)}"
            if sid not in added_stops:
                added_stops.add(sid)
                b.tables["stops.txt"].append({"stop_id": sid, "stop_name": src["name"] or ref,
                                              "stop_lat": src["lat"], "stop_lon": src["lon"], "location_type": 0})
                if src.get("name_bn"):
                    b.tables["translations.txt"].append({"table_name": "stops", "field_name": "stop_name",
                                                         "language": "bn", "translation": src["name_bn"],
                                                         "record_id": sid})
                b.prov("stops", sid, src)
            lat, lon = float(src["lat"]), float(src["lon"])
            if i == 0:
                offsets.append(0)
            elif rs.get("minutes_from_previous"):
                offsets.append(offsets[-1] + int(round(float(rs["minutes_from_previous"]) * 60)))
            else:
                d = haversine_m(*coords[-1], lat, lon) * config.BUS_DETOUR_FACTOR
                offsets.append(offsets[-1] + int(math.ceil(d / (config.BUS_ASSUMED_SPEED_KMH / 3.6))))
                estimated = True
            stop_ids.append(sid)
            coords.append((lat, lon))
        shape_id = svc.get("shape_id", "")
        dists = None
        if shape_id and shape_id in shapes:
            pts = sorted(shapes[shape_id], key=lambda x: int(x["seq"]))
            line = [(float(p["lat"]), float(p["lon"])) for p in pts]
            if not any(r["shape_id"] == shape_id for r in b.tables["shapes.txt"]):
                for p in pts:
                    b.tables["shapes.txt"].append({"shape_id": shape_id, "shape_pt_lat": p["lat"],
                                                   "shape_pt_lon": p["lon"], "shape_pt_sequence": int(p["seq"]),
                                                   "shape_dist_traveled": p.get("dist_m", "")})
                b.prov("shapes", shape_id, pts[0])
            dists = [project_on_polyline(la, lo, line)[0] for la, lo in coords]
            if any(y < x for x, y in zip(dists, dists[1:])):
                dists = None   # stops not monotonic along shape; omit distances
        note = svc.get("notes", "")
        if estimated:
            note += (f" Some segment times estimated at {config.BUS_ASSUMED_SPEED_KMH:g} km/h x "
                     f"{config.BUS_DETOUR_FACTOR:g} detour (assumption).")
        trip_id = (f"{svc['service_id']}_d{key[1]}" if svc["service_id"].startswith(svc["route_id"])
                   else f"{svc['route_id']}_{svc['service_id']}_d{key[1]}")
        b.add_frequency_trip(
            trip_id=trip_id, route_id=svc["route_id"],
            service_id=svc["service_id"], direction_id=key[1],
            headsign=(inventory.get(rows[-1]["stop_ref"]) or community.get(rows[-1]["stop_ref"]))["name"],
            shape_id=shape_id if dists is not None or shape_id in shapes else "", stop_ids=stop_ids,
            offsets_s=offsets, shape_dists=dists, first=svc["start_time"], last=svc["end_time"],
            headway_min=float(svc["headway_min"]),
            prov_row={**svc, "notes": note.strip(),
                      "source_category": "estimated" if estimated else svc["source_category"]})


# ---------------------------------------------------------------------------
def build_fares(b: FeedBuilder, cur: Path) -> None:
    """Emit GTFS Fares v1 tables from curated verified fare CSVs when present."""
    rows = read_csv(cur / "metro_fares.csv")
    if not rows:
        return
    # Group OD pairs sharing the same single-journey price into one fare_id.
    by_price: dict[tuple[str, str], str] = {}
    seen_pairs: dict[tuple[str, str], str] = {}
    for r in rows:
        orig, dest = r["origin_station_id"], r["destination_station_id"]
        price_val = float(r["fare_bdt"])
        price_str = f"{price_val:g}"
        currency = r.get("currency_type") or "BDT"
        if (dest, orig) in seen_pairs and seen_pairs[(dest, orig)] != price_str:
            raise ValueError(f"asymmetric metro fare between {orig} and {dest}: {price_str} vs {seen_pairs[(dest, orig)]}")
        seen_pairs[(orig, dest)] = price_str
        key = (price_str, currency)
        if key not in by_price:
            fid = f"mrt6_{currency.lower()}_{price_str}"
            by_price[key] = fid
            b.tables["fare_attributes.txt"].append({
                "fare_id": fid, "price": price_str, "currency_type": currency,
                "payment_method": 1, "transfers": 0, "agency_id": "DMTCL"})
            b.prov("fare_attributes", fid, r)
        b.tables["fare_rules.txt"].append({
            "fare_id": by_price[key], "route_id": "mrt6",
            "origin_id": orig, "destination_id": dest})


# ---------------------------------------------------------------------------
OPTIONAL_IF_EMPTY = {"fare_attributes.txt", "fare_rules.txt"}


def build(curated: Path, out: Path) -> dict[str, int]:
    b = FeedBuilder()
    for s in read_csv(curated / "data_sources.csv"):
        b.tables["data_sources.txt"].append(s)
    build_metro(b, curated)
    build_buses(b, curated)
    build_fares(b, curated)
    b.tables["feed_info.txt"].append({"feed_publisher_name": config.FEED_PUBLISHER_NAME,
                                      "feed_publisher_url": config.FEED_PUBLISHER_URL,
                                      "feed_lang": config.FEED_LANG, "feed_start_date": config.FEED_START_DATE,
                                      "feed_end_date": config.FEED_END_DATE, "feed_version": config.FEED_VERSION})
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.txt"):
        old.unlink()
    counts = {}
    for name, cols in COLUMNS.items():
        rows = b.tables.get(name, [])
        if name in OPTIONAL_IF_EMPTY and not rows:
            continue
        counts[name] = write_table(out / name, cols, rows)
    zip_feed(out, out / "dhaka_gtfs.zip")
    return counts


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--curated", type=Path, default=ROOT / "data" / "curated")
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "gtfs" / "dhaka")
    args = ap.parse_args(argv)
    counts = build(args.curated, args.out)
    for k, v in counts.items():
        print(f"  {k:<20} {v:>6} rows")
    print(f"feed written to {args.out}")


if __name__ == "__main__":
    main()
