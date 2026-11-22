# Dhaka Transit REST API Reference

Base URL (local development): `http://127.0.0.1:8000`
Interactive Swagger UI: `http://127.0.0.1:8000/docs` · OpenAPI JSON: `/openapi.json`

Run locally:

```powershell
cd "d:\Route Planner\backend"
uvicorn app.main:app --reload --port 8000
```

## Endpoints (Phase 3)

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | API metadata and links to `/docs`, `/api/health`, `/api/dataset`. |
| `GET` | `/api/health` | Service status, feed version, stop/route counts, frequency graph & RAPTOR timetable diagnostics. |
| `GET` | `/api/stops` | List/search GTFS stops & stations. Query params: `q` (English or Bangla), `location_type` (`0` or `1`), `route_type` (`1`=metro, `3`=bus), `limit`, `offset`. |
| `GET` | `/api/stops/nearby` | Spatial nearest-stop search. Params: `lat`, `lon`, `radius_m` (default `800`), `limit` (default `10`), `include_platforms` (`false` groups metro platforms by station). Returns straight-line distance, detour-adjusted walk distance, and walk time. |
| `GET` | `/api/stops/osm-inventory` | Returns the mapped OpenStreetMap Dhaka bus-stop inventory (`data/curated/bus_stops.csv`, tagged `source_category=osm`). Params: `q`, `kind`, `named_only`, `limit`. |
| `GET` | `/api/stops/{stop_id}` | Detailed stop/station metadata including Bangla name, child platforms or parent station, serving routes, and provenance. |
| `GET` | `/api/routes` | List transit routes (`route_type` optional filter) with mode, colour, stop count, and provenance. |
| `GET` | `/api/routes/{route_id}` | Full route breakdown: ordered stops per direction, service frequencies with provenance, and `[lat, lon]` track coordinates. |
| `GET` | `/api/routes/{route_id}/geojson` | RFC 7946 GeoJSON `FeatureCollection` (`LineString` shapes + `Point` stops in `[lon, lat]`) ready for `L.geoJSON()`. |
| `GET` | `/api/dataset` | Feed metadata, file inventory with row counts, provenance breakdown, source list, validation & gap summaries, and ODbL attribution. |
| `GET` | `/api/dataset/download` | Download `dhaka_gtfs.zip`. |
| `GET` | `/api/dataset/files/{filename}` | Download an individual `.txt` GTFS file (path-traversal protected). |
| `GET` | `/api/dataset/validation` | Full GTFS validation report JSON. |
| `GET` | `/api/dataset/gaps` | Coverage and interchange gap report JSON. |
| `GET` | `/api/dataset/evaluation` | Algorithm performance and accuracy evaluation report JSON. |
| `GET` | `/api/contributions` | List community submissions (`status`: `pending` / `approved` / `rejected`). |
| `POST` | `/api/contributions` | Submit a new stop/route/travel-time/headway contribution (saved as `pending` in SQLite; rejects coordinates outside Greater Dhaka). |
| `GET` | `/api/plan` | **Multimodal route planner** (Metro + Bus + Walking) combining RAPTOR and Dijkstra. Params: `from_stop` or `from_lat`+`from_lon`, `to_stop` or `to_lat`+`to_lon`, optional `date` (`YYYY-MM-DD`), `time` (`HH:MM`), `algorithm` (`combined` \| `raptor` \| `dijkstra`), `modes` (`metro,bus`), `max_transfers`, `max_walk_m`. |
| `GET` | `/api/plan/compare` | Runs both RAPTOR and Dijkstra on the same query and returns full journeys + side-by-side comparison metrics (duration, transfers, runtime in ms, agreement). |

## Example calls

```powershell
# Health and graph diagnostics
curl http://127.0.0.1:8000/api/health

# Plan Mirpur 10 -> Motijheel at 08:30 on Saturday (RAPTOR + Dijkstra combined)
curl "http://127.0.0.1:8000/api/plan?from_stop=mrt6_mirpur_10&to_stop=mrt6_motijheel&date=2026-11-21&time=08:30"

# Plan by arbitrary Dhaka coordinates
curl "http://127.0.0.1:8000/api/plan?from_lat=23.8095&from_lon=90.3695&to_lat=23.7290&to_lon=90.4205&time=09:00"

# Side-by-side RAPTOR vs Dijkstra comparison
curl "http://127.0.0.1:8000/api/plan/compare?from_stop=mrt6_mirpur_10&to_stop=mrt6_motijheel&date=2026-11-21&time=08:30"
```
