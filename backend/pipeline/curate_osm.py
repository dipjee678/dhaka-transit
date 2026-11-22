"""Curate raw OSM snapshots into reviewed-friendly CSVs (data/curated/).

Run from backend/:  python -m pipeline.curate_osm --snapshot 2026-11-16

Outputs
- metro_stations.csv : MRT-6 stations with per-direction platforms, official
                       Bangla names (DMTCL) + OSM coordinates
- metro_shapes.csv   : track geometry per direction, chained from OSM ways
- bus_stops.csv      : OSM bus-stop inventory (not yet linked to routes)

The script is deterministic: the same snapshot always produces the same CSVs,
so reviewers can diff changes between snapshots.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from transit.geo import haversine_m, in_bbox, project_on_polyline
from transit.gtfs.io import write_table

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw" / "osm"
CURATED = ROOT / "data" / "curated"

PROV_COLS = ["source_category", "source_id", "status", "verified_at", "notes"]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


class OsmIndex:
    def __init__(self, elements: list[dict]):
        self.nodes = {e["id"]: e for e in elements if e["type"] == "node"}
        self.ways = {e["id"]: e for e in elements if e["type"] == "way"}
        self.rels = {e["id"]: e for e in elements if e["type"] == "relation"}

    def latlon(self, node_id: int) -> tuple[float, float]:
        n = self.nodes[node_id]
        return n["lat"], n["lon"]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def chain_ways(way_node_lists: list[list[int]]) -> list[int]:
    """Join ordered way node-lists into one node sequence, flipping ways as needed."""
    if not way_node_lists:
        return []
    seq = list(way_node_lists[0])
    if len(way_node_lists) > 1:
        nxt = way_node_lists[1]
        if seq[0] in (nxt[0], nxt[-1]):   # first way is reversed
            seq.reverse()
    for w in way_node_lists[1:]:
        if seq[-1] == w[0]:
            seq.extend(w[1:])
        elif seq[-1] == w[-1]:
            seq.extend(reversed(w[:-1]))
        else:
            raise ValueError(f"route ways are not contiguous at node {seq[-1]}")
    return seq


# ---------------------------------------------------------------------------
# Metro
# ---------------------------------------------------------------------------
def curate_metro(osm: OsmIndex, source_id: str) -> tuple[list[dict], list[dict]]:
    routes = [r for r in osm.rels.values()
              if r.get("tags", {}).get("type") == "route" and r["tags"].get("ref") == "6"]
    if len(routes) != 2:
        raise SystemExit(f"expected 2 MRT-6 direction relations, found {len(routes)}")
    official = list(csv.DictReader(open(CURATED / "official" / "dmtcl_stations.csv", encoding="utf-8")))
    operational = [o for o in official if o["operational"] == "1"]

    def stops_of(rel):
        return [m["ref"] for m in rel["members"] if m["type"] == "node" and m["role"].startswith("stop")]

    # Direction 0 = the relation whose first stop matches official station #1.
    first_name = lambda rel: osm.nodes[stops_of(rel)[0]]["tags"].get("name", "")
    routes.sort(key=lambda rel: 0 if first_name(rel).startswith("উত্তরা") else 1)
    d0, d1 = routes
    s0, s1 = stops_of(d0), list(reversed(stops_of(d1)))
    if not (len(s0) == len(s1) == len(operational)):
        raise SystemExit(f"stop counts differ: d0={len(s0)} d1={len(s1)} official={len(operational)}")

    # stop node -> stop_area relation
    area_of: dict[int, dict] = {}
    for r in osm.rels.values():
        if r.get("tags", {}).get("public_transport") == "stop_area":
            for m in r["members"]:
                if m["type"] == "node":
                    area_of[m["ref"]] = r

    stations: list[dict] = []
    for i, (n0, n1, off) in enumerate(zip(s0, s1, operational), start=1):
        t0 = osm.nodes[n0].get("tags", {})
        area = area_of.get(n0) or area_of.get(n1)
        name_en = (off.get("name_en_override") or (area or {}).get("tags", {}).get("name:en")
                   or t0.get("name:en") or t0.get("name"))
        # Station point: the stop_area's station node if mapped, else midpoint of platforms.
        st_node = None
        if area:
            for m in area["members"]:
                n = osm.nodes.get(m["ref"]) if m["type"] == "node" else None
                if n and (n.get("tags", {}).get("public_transport") == "station" or n.get("tags", {}).get("railway") == "station"):
                    st_node = n
                    break
        lat0, lon0 = osm.latlon(n0)
        lat1, lon1 = osm.latlon(n1)
        gap = haversine_m(lat0, lon0, lat1, lon1)
        if gap > 150:
            raise SystemExit(f"station {i}: direction platforms {gap:.0f} m apart — check OSM")
        if st_node:
            slat, slon, method = st_node["lat"], st_node["lon"], f"osm station node n{st_node['id']}"
        else:
            slat, slon, method = (lat0 + lat1) / 2, (lon0 + lon1) / 2, "midpoint of the two OSM stop_positions"
        code = slug(name_en)
        stations.append({
            "seq": i, "station_id": f"mrt6_{code}", "name_en": name_en, "name_bn": off["name_bn"],
            "lat": f"{slat:.7f}", "lon": f"{slon:.7f}", "station_point_method": method,
            "osm_stop_area": f"r{area['id']}" if area else "",
            "platform_d0_id": f"mrt6_{code}_d0", "osm_node_d0": f"n{n0}", "lat_d0": f"{lat0:.7f}", "lon_d0": f"{lon0:.7f}",
            "platform_d1_id": f"mrt6_{code}_d1", "osm_node_d1": f"n{n1}", "lat_d1": f"{lat1:.7f}", "lon_d1": f"{lon1:.7f}",
            "source_category": "osm", "source_id": source_id, "status": "unverified", "verified_at": "",
            "notes": "Coordinates and English name from OSM; Bangla name and order from DMTCL station page (dmtcl_station_page)",
        })

    shapes: list[dict] = []
    for direction, rel in ((0, d0), (1, d1)):
        ways = [osm.ways[m["ref"]]["nodes"] for m in rel["members"] if m["type"] == "way" and m["ref"] in osm.ways]
        node_seq = chain_ways(ways)
        pts = [osm.latlon(n) for n in node_seq]
        # Orient so the shape runs in the direction of travel.
        first_stop = osm.latlon(stops_of(rel)[0])
        if haversine_m(*pts[0], *first_stop) > haversine_m(*pts[-1], *first_stop):
            pts.reverse()
        dist = 0.0
        for k, (lat, lon) in enumerate(pts):
            if k:
                dist += haversine_m(*pts[k - 1], lat, lon)
            shapes.append({"shape_id": f"mrt6_d{direction}", "seq": k, "lat": f"{lat:.7f}", "lon": f"{lon:.7f}",
                           "dist_m": f"{dist:.1f}", "source_category": "osm", "source_id": source_id,
                           "status": "unverified", "verified_at": "", "notes": f"chained from OSM route relation r{rel['id']}"})
    return stations, shapes


# ---------------------------------------------------------------------------
# Bus stop inventory
# ---------------------------------------------------------------------------
def classify_bus_element(t: dict) -> str:
    if t.get("amenity") == "bus_station" or t.get("public_transport") == "station":
        return "bus_station"
    if t.get("public_transport") == "platform":
        return "platform"
    if t.get("public_transport") == "stop_position":
        return "stop_position"
    return "bus_stop"


def curate_bus_stops(elements: list[dict], source_id: str) -> list[dict]:
    out = []
    for e in elements:
        t = e.get("tags", {})
        if e["type"] == "node":
            lat, lon = e["lat"], e["lon"]
        elif "center" in e:
            lat, lon = e["center"]["lat"], e["center"]["lon"]
        else:
            continue
        if not in_bbox(lat, lon):
            continue
        name = t.get("name:en") or t.get("name") or ""
        out.append({
            "osm_id": f"{e['type'][0]}{e['id']}", "name": name, "name_bn": t.get("name:bn", ""),
            "lat": f"{lat:.7f}", "lon": f"{lon:.7f}", "kind": classify_bus_element(t),
            "shelter": t.get("shelter", ""), "operator": t.get("operator", ""),
            "source_category": "osm", "source_id": source_id, "status": "unverified", "verified_at": "",
            "notes": "" if name else "unnamed in OSM",
        })
    out.sort(key=lambda r: r["osm_id"])
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True, help="folder name under data/raw/osm, e.g. 2026-11-16")
    args = ap.parse_args(argv)
    snap = RAW / args.snapshot
    source_id = f"osm_{args.snapshot}"

    metro = OsmIndex(json.loads((snap / "metro.json").read_text("utf-8"))["elements"])
    stations, shapes = curate_metro(metro, source_id)
    write_table(CURATED / "metro_stations.csv", list(stations[0].keys()), stations)
    write_table(CURATED / "metro_shapes.csv", list(shapes[0].keys()), shapes)

    stops = curate_bus_stops(json.loads((snap / "bus_stops.json").read_text("utf-8"))["elements"], source_id)
    write_table(CURATED / "bus_stops.csv", list(stops[0].keys()), stops)

    print(f"metro stations: {len(stations)}; shape points: {len(shapes)}; bus stop inventory: {len(stops)}")
    for s in stations:
        print(f"  {s['seq']:>2} {s['name_en']:<24} {s['name_bn']:<20} ({s['lat']}, {s['lon']})  [{s['station_point_method']}]")


if __name__ == "__main__":
    main()
