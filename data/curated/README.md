# Curated data (source of truth)

The GTFS feed in `data/gtfs/dhaka/` is **generated** from the CSV files in this folder by
`backend/pipeline/build_gtfs.py`. Edit these CSVs, never the generated `.txt` files.

## Mandatory provenance columns

Every curated CSV row carries these columns:

| Column | Values | Meaning |
|---|---|---|
| `source_category` | `official` · `osm` · `community` · `estimated` | Where the value came from |
| `source_id` | id from `data_sources.csv` | Exact source, with URL, licence and retrieval date |
| `status` | `verified` · `unverified` · `disputed` | Whether a person has checked it |
| `verified_at` | `YYYY-MM-DD` or empty | Date of the last check |
| `notes` | free text | e.g. "OSM node 123456; confirmed on site" |

Rules:
1. A row with no `source_id` is rejected by the validator.
2. `estimated` values must say in `notes` how they were estimated (e.g. assumed speed).
3. Coordinates use WGS84 decimal degrees with 6 decimals (about 0.1 m).
4. Bangla names go in the `*_bn` columns, in UTF-8.

Files are added in Phase 2: `data_sources.csv`, `metro_stations.csv`, `metro_fares.csv`,
`bus_stops.csv`, `bus_routes.csv`, `bus_route_stops.csv`, `service_periods.csv`.
