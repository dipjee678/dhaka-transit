# Installation & Deployment Guide

This guide covers local development, Docker deployment, free-tier cloud hosting,
and migrating from SQLite to PostgreSQL + PostGIS.

---

## 1. Local Development (Windows PowerShell / Linux / macOS)

### Prerequisites
- Python 3.12+ (developed and tested on Python 3.14)
- Git

### Setup
```powershell
git clone <your-repo-url> dhaka-transit
cd dhaka-transit

python -m venv .venv
.\.venv\Scripts\Activate.ps1       # Linux/macOS: source .venv/bin/activate
pip install -r backend\requirements.txt
```

### Build Feed, Database & Run Server
```powershell
# 1. Build and validate GTFS from curated CSVs
cd backend
python -m pipeline.build_gtfs
python -m pipeline.validate_feed --strict

# 2. Load SQLite database (from repo root)
cd ..
python database\load_gtfs.py --feed data\gtfs\dhaka --db dhaka_transit.sqlite

# 3. Start FastAPI (serves both REST API and the 5-page frontend)
cd backend
uvicorn app.main:app --reload --port 8000
```

Open:
- Web Application: `http://127.0.0.1:8000/`
- Swagger API Docs: `http://127.0.0.1:8000/docs`
- Health Check: `http://127.0.0.1:8000/api/health`

### Run Automated Tests & Evaluation
```powershell
cd "d:\Route Planner"
pytest -v                          # 82 automated tests
cd backend
python -m pipeline.evaluate        # regenerates data/reports/evaluation_report.{md,json}
```

---

## 2. Docker Deployment (Local or Cloud)

```bash
docker build -t dhaka-transit .
docker run -p 8000:8000 dhaka-transit
```

Verify:
```bash
curl http://127.0.0.1:8000/api/health
```

---

## 3. Cloud Deployment (Free Tier)

### Option A: Render (Single Container — Web + API + Dataset)
1. Push this repository to GitHub.
2. In Render, choose **New → Blueprint** and connect the repository (`render.yaml` is pre-configured).
3. Render builds the `Dockerfile`, runs `load_gtfs.py` during build, exposes `/api/health`, and serves the entire web app + API over HTTPS.

### Option B: Split Static Frontend + API
- Deploy `frontend/` to **GitHub Pages** or **Netlify**.
- Deploy `Dockerfile` to **Render**, **Railway**, or **Fly.io**.
- Set `window.DHAKA_API_BASE = "https://your-api.onrender.com"` in `frontend/js/api.js` (or pass `?api=https://...`) and restrict `DHAKA_CORS_ORIGINS` to your frontend domain.

### Tile Usage Notice (Production)
OpenStreetMap's public tile server (`tile.openstreetmap.org`) is suitable for development
and low-traffic university demos. For high public traffic, switch the tile URL in
`frontend/js/map.js` to a commercial/free-tier OSM tile host (e.g. Stadia Maps, MapTiler,
or Protomaps) to comply with the [OSMF Tile Usage Policy](https://operations.osmfoundation.org/policies/tiles/).

---

## 4. Migrating to PostgreSQL + PostGIS (Neon / Supabase / Self-Hosted)

1. Provision a PostgreSQL 15+ database with the `postgis` extension enabled (both **Neon** and **Supabase** include PostGIS on their free tiers).
2. Apply the schema:
   ```bash
   psql "$DATABASE_URL" -f database/schema_postgis.sql
   ```
3. Refresh materialized track geometries after loading `shapes`:
   ```sql
   REFRESH MATERIALIZED VIEW shape_lines;
   ```
4. Example nearest-stop KNN query powered by the GiST index:
   ```sql
   SELECT stop_id, stop_name, stop_name_bn,
          round(ST_Distance(geom, ST_SetSRID(ST_MakePoint(90.3682, 23.8083), 4326)::geography)::numeric, 1) AS dist_m
   FROM stops
   WHERE location_type = 1
   ORDER BY geom <-> ST_SetSRID(ST_MakePoint(90.3682, 23.8083), 4326)::geography
   LIMIT 5;
   ```

---

## 5. Official MobilityData GTFS Validation (Optional External Check)

To cross-check spec compliance against the official MobilityData Java validator:
```bash
java -jar gtfs-validator-cli.jar -i data/gtfs/dhaka/dhaka_gtfs.zip -o data/reports/mobilitydata
```
