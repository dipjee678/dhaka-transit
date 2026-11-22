"""Exploration helper: list how transit is tagged in OSM around Dhaka.

Run:  python -m pipeline.explore_osm        (from the backend/ folder)
Prints a summary only; nothing is written to the dataset.
"""
from __future__ import annotations

from collections import Counter

from pipeline.overpass import DHAKA_BBOX_OVERPASS as BB, run_query

QUERY = f"""
[out:json][timeout:120];
(
  relation["type"="route"]["route"~"^(subway|light_rail|train|monorail|bus)$"]({BB});
  relation["type"="route_master"]({BB});
);
out tags;
"""

QUERY_STATIONS = f"""
[out:json][timeout:120];
(
  node["railway"="station"]({BB});
  node["public_transport"="station"]({BB});
);
out tags;
"""


def main() -> None:
    rels = run_query(QUERY)["elements"]
    kinds = Counter((e["tags"].get("type"), e["tags"].get("route") or e["tags"].get("route_master")) for e in rels)
    print("Relation counts:", dict(kinds))
    print("\nRail-type route relations:")
    for e in rels:
        t = e["tags"]
        if t.get("route") in ("subway", "light_rail", "train", "monorail") or t.get("route_master") in ("subway", "light_rail", "train"):
            print(f"  r{e['id']}: type={t.get('type')} route={t.get('route') or t.get('route_master')} "
                  f"ref={t.get('ref')} name={t.get('name')} name:en={t.get('name:en')} operator={t.get('operator')}")
    print("\nSample bus route relations (first 25):")
    for e in [e for e in rels if e["tags"].get("route") == "bus"][:25]:
        t = e["tags"]
        print(f"  r{e['id']}: ref={t.get('ref')} name={t.get('name')} from={t.get('from')} to={t.get('to')} operator={t.get('operator')}")

    st = run_query(QUERY_STATIONS)["elements"]
    print(f"\nStation nodes: {len(st)}")
    for e in st:
        t = e["tags"]
        print(f"  n{e['id']}: railway={t.get('railway')} station={t.get('station')} pt={t.get('public_transport')} "
              f"name:en={t.get('name:en')} name={t.get('name')} subway={t.get('subway')} light_rail={t.get('light_rail')}")


if __name__ == "__main__":
    main()
