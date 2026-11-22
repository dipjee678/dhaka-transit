"""Fetch dated raw OpenStreetMap snapshots for Dhaka transit.

Run from backend/:   python -m pipeline.fetch_osm [--only metro|bus_routes|bus_stops]
Writes to:           data/raw/osm/<YYYY-MM-DD>/<name>.json (+ .overpassql)

Nothing is edited here: these files are the untouched evidence that the
curation step (pipeline.curate_osm) reads from.
"""
from __future__ import annotations

import argparse
import datetime as dt
import time
from pathlib import Path

from pipeline.overpass import DHAKA_BBOX_OVERPASS as BB, run_query, save_snapshot

ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw" / "osm"

QUERIES: dict[str, str] = {
    # MRT Line 6: both direction relations, their members (stop positions,
    # platforms, track ways + way nodes), the stop_area relations that group
    # each station, and the route_master.
    "metro": f"""
[out:json][timeout:180];
relation["type"="route"]["route"~"^(subway|light_rail)$"]["ref"="6"]({BB})->.line;
(.line; .line >;)->.all;
.all out body;
rel(br.line)["type"="route_master"];
out body;
node(r.line)->.stopnodes;
rel(bn.stopnodes)["public_transport"="stop_area"]->.areas;
.areas out body;
node(r.areas);
out body;
way(r.areas);
out tags center;
""",
    # All bus route relations (PTv2) with members and geometry.
    "bus_routes": f"""
[out:json][timeout:180];
relation["type"="route"]["route"="bus"]({BB})->.r;
(.r; .r >;);
out body;
rel(br.r)["type"="route_master"];
out body;
""",
    # Bus stops / platforms / bus stations.
    "bus_stops": f"""
[out:json][timeout:180];
(
  node["highway"="bus_stop"]({BB});
  node["public_transport"="platform"]["bus"="yes"]({BB});
  node["public_transport"="stop_position"]["bus"="yes"]({BB});
  node["amenity"="bus_station"]({BB});
);
out body;
way["amenity"="bus_station"]({BB});
out tags center;
""",
}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", choices=sorted(QUERIES))
    ap.add_argument("--date", default=dt.date.today().isoformat(), help="snapshot folder name")
    args = ap.parse_args(argv)
    out_dir = RAW_DIR / args.date
    names = [args.only] if args.only else list(QUERIES)
    for i, name in enumerate(names):
        if i:
            time.sleep(5)  # be polite to the public Overpass servers
        data = run_query(QUERIES[name])
        save_snapshot(data, out_dir / f"{name}.json", QUERIES[name])
        print(f"{name}: {len(data.get('elements', []))} elements -> {out_dir / (name + '.json')}"
              f"  (OSM data timestamp: {data.get('osm3s', {}).get('timestamp_osm_base')})")


if __name__ == "__main__":
    main()
