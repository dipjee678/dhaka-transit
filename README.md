# Dhaka Transit — Open Public Transport Dataset & Smart Route Planner

[![CI](https://img.shields.io/badge/CI-pytest%20%2B%20GTFS%20validator-006E34)](#testing--ci)
[![Code License: MIT](https://img.shields.io/badge/Code-MIT-blue.svg)](LICENSE)
[![Data License: ODbL 1.0](https://img.shields.io/badge/Dataset-ODbL%201.0-006E34.svg)](DATA_LICENSE.md)

An open-source **GTFS public transport dataset** and **multimodal route planner** (Metro Rail Line 6 + Bus + Walking) for **Dhaka, Bangladesh**, built from scratch with **Dijkstra**, **RAPTOR**, and **A\*** routing engines, a **FastAPI** REST backend, **SQLite / PostGIS** storage, and a responsive **Leaflet.js** web interface.

> **Data Honesty First:** Every stop, route, schedule, and fare carries an explicit provenance tag (`official`, `osm`, `community`, or `estimated`). Missing bus routes and unverified third-party fare tables are never fabricated — they are logged transparently in [`data/reports/gap_report.md`](data/reports/gap_report.md) and labelled in the UI.

---

## Highlights & Real-World Findings

- **Validated Dhaka GTFS Feed ([`data/gtfs/dhaka/dhaka_gtfs.zip`](data/gtfs/dhaka/dhaka_gtfs.zip)):**
  - All **16 operational MRT Line 6 stations** (`Uttara North → Motijheel`), 32 directional platforms, 489 chained track-geometry points (`18.97 km`), English + Bangla (`translations.txt`), Sat–Thu vs. Friday calendars, and **0 validator errors / 0 warnings**.
  - **1,023 mapped OSM Dhaka bus stops** extracted into [`data/curated/bus_stops.csv`](data/curated/bus_stops.csv) and rendered on the interactive map.
  - **Empirical OSM gap finding:** Greater Dhaka currently has **0 usable `route=bus` relations** in OpenStreetMap — quantitatively motivating our volunteer survey pipeline ([`contributions/templates/route_template.csv`](contributions/templates/route_template.csv) + `pipeline.import_contribution`).
- **Real Transit Routing Algorithms (`backend/transit/routing/`):**
  - **Dijkstra** on a route-stop frequency graph with exact 1-bit `(node, has_boarded)` transfer-penalty handling — verified against exhaustive brute-force search.
  - **RAPTOR** (Delling et al., 2015) on an expanded calendar-aware timetable with transitively closed footpaths and Pareto-optimal transfer trade-offs — verified to the exact second against an independent time-expanded Dijkstra oracle.
  - **A\*** pedestrian search with an admissible haversine heuristic — verified optimal with 10–15% fewer expanded nodes on barrier grids.
- **Measured Performance ([`data/reports/evaluation_report.md`](data/reports/evaluation_report.md)):**
  - **720 real Dhaka MRT-6 queries** (all 240 OD pairs × 3 time slots): **100% route agreement**, median latency **0.081 ms** (RAPTOR) / **0.091 ms** (Dijkstra).
  - **Synthetic 100-route stress test (`11,586` trips):** RAPTOR averages **2.98 ms** vs. Dijkstra **4.59 ms**.

---

## Project Deliverables & Documentation

| Deliverable | Location |
|---|---|
| System Architecture & ER Diagram | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) |
| Routing Algorithms Explained (Dijkstra, RAPTOR, A\*) | [`docs/ALGORITHMS.md`](docs/ALGORITHMS.md) |
| Data Collection, Validation & Attribution Methodology | [`docs/DATA_METHODOLOGY.md`](docs/DATA_METHODOLOGY.md) |
| REST API Reference (+ live Swagger at `/docs`) | [`docs/API.md`](docs/API.md) |
| Installation, Docker, Render & PostGIS Deployment Guide | [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) |
| GTFS Validation, Gap & Performance Evaluation Reports | [`validation_report.md`](data/reports/validation_report.md) · [`gap_report.md`](data/reports/gap_report.md) · [`evaluation_report.md`](data/reports/evaluation_report.md) |
| Database Schemas & Loader | [`database/schema_sqlite.sql`](database/schema_sqlite.sql) · [`database/schema_postgis.sql`](database/schema_postgis.sql) · [`database/load_gtfs.py`](database/load_gtfs.py) |
| Thesis / Scholarship Presentation Outline | [`docs/PRESENTATION_OUTLINE.md`](docs/PRESENTATION_OUTLINE.md) |

---

## Quick Start

### Windows PowerShell
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt

# Load SQLite database from the validated GTFS feed
python database\load_gtfs.py --feed data\gtfs\dhaka --db dhaka_transit.sqlite

# Start the full-stack server (API + 5-page Leaflet web app)
cd backend
uvicorn app.main:app --reload --port 8000
```

Open **`http://127.0.0.1:8000/`** (Home, Map, Route Planner, Open Dataset, About) or **`http://127.0.0.1:8000/docs`** (Interactive Swagger API).

### Docker
```bash
docker build -t dhaka-transit .
docker run -p 8000:8000 dhaka-transit
```

---

## Testing & CI

```powershell
# Run all 82 automated unit, validation, loader, routing, API & integration tests
pytest -v

# Rebuild, validate GTFS, and regenerate gap & performance reports
cd backend
python -m pipeline.build_gtfs
python -m pipeline.validate_feed --strict
python -m pipeline.gap_report --snapshot 2026-11-16
python -m pipeline.evaluate
```

GitHub Actions (`.github/workflows/ci.yml`) automatically runs GTFS build + strict validation, SQLite loading, `pytest`, and JS syntax checks on every push and pull request.

---

## Repository Layout

```text
├── backend/
│   ├── transit/             # Framework-independent core (stdlib only)
│   │   ├── geo.py           # Haversine, polyline projection, GridIndex
│   │   ├── provenance.py    # 4-tier provenance vocabulary & weakest-link rollup
│   │   ├── gtfs/            # spec.py, io.py, validator.py, model.py
│   │   ├── graph/           # footpaths.py, frequency_graph.py, timetable.py
│   │   └── routing/         # dijkstra.py, raptor.py, astar.py, journey.py, planner.py
│   ├── pipeline/            # overpass.py, fetch_osm.py, curate_osm.py, build_gtfs.py,
│   │                        # import_contribution.py, validate_feed.py, gap_report.py, evaluate.py
│   ├── app/                 # FastAPI REST API + static frontend host
│   └── tests/               # 82 pytest checks across 10 test modules
├── database/                # schema_sqlite.sql, schema_postgis.sql, load_gtfs.py
├── data/
│   ├── raw/                 # Dated untouched OSM & official DMTCL snapshots
│   ├── curated/             # Human-reviewed CSVs with provenance columns (source of truth)
│   ├── gtfs/dhaka/          # Published GTFS feed + dhaka_gtfs.zip
│   └── reports/             # Validation, gap analysis & performance evaluation reports
├── frontend/                # 5 responsive HTML5/CSS3/JS + Leaflet pages
├── contributions/templates/ # Volunteer stop & route survey CSV templates
├── docs/                    # Architecture, algorithms, methodology, API, deployment, slides
├── Dockerfile & render.yaml # Container & cloud deployment blueprints
└── .github/workflows/ci.yml # Automated CI pipeline
```

---

## Development Status

| Phase | Scope | Status |
|---|---|---|
| 1 | Architecture, SQLite/PostGIS schemas, GTFS spec, geo & provenance core | ✅ Complete |
| 2 | OSM & DMTCL data collection, curation, GTFS builder, validator & gap report | ✅ Complete |
| 3 | SQLite loader/exporter, graph & timetable builders, FastAPI REST API | ✅ Complete |
| 4 | Dijkstra, RAPTOR, A\*, fare lookup, multimodal planner & `/api/plan` | ✅ Complete |
| 5 | 5-page responsive HTML/CSS/JS + Leaflet frontend served with FastAPI | ✅ Complete |
| 6 | End-to-end integration tests & reproducible performance evaluation report | ✅ Complete |
| 7 | CI workflow, Docker/Render deployment blueprints & portfolio documentation | ✅ Complete |

---

## Contributing & Licences

- **Contribute bus routes or stop surveys:** see [`CONTRIBUTING.md`](CONTRIBUTING.md) or submit directly on the web app's **Open Dataset** page.
- **Source code:** MIT License ([`LICENSE`](LICENSE)).
- **Dataset (`data/curated/`, `data/gtfs/`):** Open Database License — ODbL 1.0 ([`DATA_LICENSE.md`](DATA_LICENSE.md)). Contains OpenStreetMap data © OpenStreetMap contributors and official public transit information from DMTCL.
