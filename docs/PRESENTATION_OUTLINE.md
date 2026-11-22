# Thesis Defense & Scholarship Presentation Outline

Use this 10-slide structure for a CSE final-year defense, portfolio walkthrough,
or international scholarship interview.

1. **Problem Statement — Dhaka's Public Transport Information Gap**
   - 20M+ metropolitan residents, dense private bus network + new MRT Line 6, yet **no open GTFS feed**.
   - Empirical finding from our `2026-11-16` OSM audit: **1,023 mapped bus stops**, but **0 usable `route=bus` relations** across all of Greater Dhaka.
2. **Project Objective & Open Infrastructure Impact**
   - Build a reusable, ODbL-licensed GTFS dataset + transparent multimodal route planner + volunteer survey pipeline.
   - Strict **4-tier data provenance** (`official`, `osm`, `community`, `estimated`) — never fabricate routes, schedules, or contradictory third-party fares.
3. **System Architecture & Modular Engineering**
   - Show the Mermaid diagram from `docs/ARCHITECTURE.md`: framework-independent `backend/transit/`, automated `pipeline/`, SQLite/PostGIS dual schema, FastAPI REST API, zero-build Leaflet frontend.
4. **GTFS Data Engineering & Quality Assurance**
   - MRT Line 6 (16 operational stations, 32 platforms, 18.97 km chained OSM geometry, Bangla `translations.txt`, 0 validator errors).
   - Identified 2 candidate upstream OSM naming fixes and ranked top 5 MRT-6 interchange stations for field surveys (Motijheel, Farmgate, Mirpur 10, Dhaka University, Bangladesh Secretariat).
5. **Algorithm 1 — Frequency-Graph Dijkstra**
   - Route-stop node splitting `R(r, d, p)` + exact 1-bit `(node, has_boarded)` state so the first boarding pays zero transfer penalty.
   - Handles untimed queries and graceful outside-service-hours fallback.
6. **Algorithm 2 — Round-Based RAPTOR**
   - Schedule-based rounds, binary-search trip lookup, local & target pruning, transitively closed footpaths, calendar resolution (Sat–Thu vs. Friday afternoon MRT-6).
   - Multi-criteria Pareto set trading off arrival time vs. transfers; verified to the exact second against an independent time-expanded Dijkstra oracle.
7. **Algorithm 3 — Admissible A\* Walking Optimizer**
   - Great-circle haversine heuristic guarantees optimal street walking routes while expanding 10–15% fewer nodes on barrier grids.
8. **Live Demo (`Mirpur 10 → Motijheel`)**
   - Walk through Home autocomplete → Interactive Map (MRT-6 + 1,023 OSM bus stops) → Route Results (sliced track geometry, leg breakdown, provenance badges, RAPTOR vs. Dijkstra table) → Open Dataset download & community contribution form.
9. **Empirical Evaluation & Scalability (`data/reports/evaluation_report.md`)**
   - 720 real Dhaka queries: 100% route agreement, sub-millisecond median latency (`0.081 ms` RAPTOR / `0.091 ms` Dijkstra).
   - Synthetic scaling to 100 bus routes (`11,586` concrete trips): RAPTOR `2.98 ms` vs. Dijkstra `4.59 ms`, confirming architectural readiness for citywide Dhaka bus data.
10. **Limitations & Future Work**
    - Field-survey priority bus corridors via `route_template.csv` + `import_contribution.py` and contribute routes upstream to OpenStreetMap.
    - Transcribe official DMTCL timetable & fare chart PDFs; add Kamalapur station once operational; integrate OSM pedestrian graph for A\* access walks and GTFS-Realtime feeds.
