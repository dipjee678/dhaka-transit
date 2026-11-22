# System Architecture & Database Design

This document provides the high-level software architecture, request lifecycle, and
entity-relationship diagram for **Dhaka Transit**.

---

## 1. System Architecture

The codebase enforces a strict one-way dependency boundary:

- `backend/transit/` is **framework-independent** (Python standard library only — zero web, database, or pipeline imports).
- `backend/pipeline/` orchestrates Overpass snapshots, curation, community survey imports, GTFS builds, validation, and benchmarking.
- `backend/app/` is a thin FastAPI REST & static-file layer wrapping `transit/` and SQLite/PostGIS.
- `frontend/` is a zero-build HTML5/CSS3/ES-Module + Leaflet client served directly by FastAPI (or hostable standalone).

```mermaid
flowchart LR
    subgraph Sources["External & Community Sources"]
        OSM["OpenStreetMap (Overpass API)"]
        OFF["Official Agencies (DMTCL / DTCA / BRTA)"]
        VOL["Volunteers (CSV Surveys + GPX)"]
    end

    subgraph Pipeline["Data Engineering Pipeline (backend/pipeline/)"]
        FETCH["fetch_osm.py (raw snapshots)"]
        CUR["curate_osm.py + import_contribution.py"]
        BUILD["build_gtfs.py"]
        VAL["validate_feed.py + gap_report.py"]
        FETCH --> CUR --> BUILD --> VAL
    end

    Sources --> FETCH
    VOL --> CUR
    VAL --> GTFS[("Validated GTFS Feed\ndata/gtfs/dhaka/ + zip")]

    subgraph Core["Routing & Graph Core (backend/transit/)"]
        MODEL["gtfs/model.py (TransitFeed)"]
        FOOT["graph/footpaths.py (Transitive Closure)"]
        FGRAPH["graph/frequency_graph.py"]
        TTABLE["graph/timetable.py"]
        DIJ["routing/dijkstra.py"]
        RAP["routing/raptor.py"]
        ASTAR["routing/astar.py"]
        PLANNER["routing/planner.py (Merge + Fares)"]

        MODEL --> FOOT
        MODEL --> FGRAPH --> DIJ
        MODEL --> TTABLE --> RAP
        FOOT --> FGRAPH
        FOOT --> TTABLE
        DIJ --> PLANNER
        RAP --> PLANNER
    end

    GTFS --> MODEL
    GTFS --> LOADER["database/load_gtfs.py"] --> DB[("SQLite (dev) /\nPostgreSQL + PostGIS (prod)")]

    subgraph Web["FastAPI REST API & Frontend"]
        API["FastAPI (/api/plan, /api/stops, /api/routes, /api/dataset, /api/contributions)"]
        UI["Frontend (5 HTML pages + Leaflet.js)"]
        UI <-->|"JSON / GeoJSON"| API
    end

    PLANNER --> API
    DB <--> API
    GTFS --> API
```

---

## 2. Multimodal Query Sequence (`/api/plan`)

```mermaid
sequenceDiagram
    participant U as Commuter Browser
    participant API as FastAPI /api/plan
    participant IDX as GridIndex (Spatial)
    participant RAP as RAPTOR Engine
    participant DIJ as Dijkstra Engine
    participant RANK as Planner & Fare Lookup

    U->>API: GET /api/plan?from_stop=mrt6_mirpur_10&to_stop=mrt6_motijheel&time=08:30
    API->>IDX: Find access & egress boarding stops within walk radius
    IDX-->>API: Candidate stops + walking durations
    API->>RAP: Run round-based search on date-filtered Timetable
    RAP-->>API: Pareto-optimal timed journeys (arrival vs. transfers)
    API->>DIJ: Run (node, has_boarded) Dijkstra on FrequencyGraph
    DIJ-->>API: Minimum-generalized-cost typical journey
    API->>RANK: De-duplicate signatures, rank, slice track shapes, attach provenance & fares
    RANK-->>API: Recommended + alternatives + comparison + warnings
    API-->>U: JSON response -> Leaflet polyline + leg-by-leg breakdown
```

---

## 3. Database Entity-Relationship Diagram

Both `database/schema_sqlite.sql` and `database/schema_postgis.sql` share an identical
two-layer relational model: **GTFS Core** (lossless 1:1 feed mapping) + **Project Extensions**
(provenance, data sources, derived footpaths, community submissions, and validation runs).

```mermaid
erDiagram
    AGENCY ||--o{ ROUTES : operates
    ROUTES ||--o{ TRIPS : contains
    CALENDAR ||--o{ TRIPS : schedules
    CALENDAR ||--o{ CALENDAR_DATES : overrides
    TRIPS ||--o{ STOP_TIMES : visits
    STOPS ||--o{ STOP_TIMES : serves
    TRIPS ||--o{ FREQUENCIES : repeats
    STOPS ||--o{ STOPS : parent_station
    SHAPES ||--o{ TRIPS : traces
    FARE_ATTRIBUTES ||--o{ FARE_RULES : prices
    STOPS ||--o{ FOOTPATHS : walks_to
    DATA_SOURCES ||--o{ PROVENANCE : attributes
    CONTRIBUTORS ||--o{ CONTRIBUTIONS : submits
    VALIDATION_RUNS ||--o{ VALIDATION_ISSUES : logs

    STOPS {
        text stop_id PK
        text stop_name
        text stop_name_bn
        real stop_lat
        real stop_lon
        int location_type
        text parent_station FK
        text zone_id
    }
    ROUTES {
        text route_id PK
        text agency_id FK
        text route_short_name
        text route_long_name
        int route_type
        text route_color
    }
    TRIPS {
        text trip_id PK
        text route_id FK
        text service_id FK
        int direction_id
        text shape_id
    }
    STOP_TIMES {
        text trip_id PK
        int stop_sequence PK
        text stop_id FK
        text arrival_time
        text departure_time
        int timepoint
    }
    FREQUENCIES {
        text trip_id PK
        text start_time PK
        text end_time
        int headway_secs
        int exact_times
    }
    FOOTPATHS {
        text from_stop_id PK
        text to_stop_id PK
        real distance_m
        int duration_s
        text method
    }
    PROVENANCE {
        int id PK
        text entity_table
        text entity_id
        text field
        text source_id FK
        text category
        text status
    }
    CONTRIBUTIONS {
        int contribution_id PK
        int contributor_id FK
        text kind
        text payload_json
        text status
    }
```

In PostgreSQL/PostGIS (`database/schema_postgis.sql`), `stops` adds a stored generated
`geography(Point, 4326)` column with a GiST spatial index for `O(log N)` KNN nearest-stop
queries (`ORDER BY geom <-> ST_MakePoint(lon, lat)`), and `shape_lines` provides a
materialized `LineString` geography view per route shape.
