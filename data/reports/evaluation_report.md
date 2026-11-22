# Dhaka Transit — Testing & Performance Evaluation Report

- Generated: 2026-11-23T00:29:12
- Feed evaluated: `data/gtfs/dhaka` (`0.2.0-phase2`)

## 1. Real Dhaka Dataset Benchmark (MRT Line 6 — All 240 OD Pairs × 3 Time Slots)

- Queries evaluated: **720** timed queries (Saturday `08:30`, `14:00`, `18:30`) across all 16 operational stations.
- Graph size: 64 nodes, 126 edges. Expanded Saturday timetable: 185 concrete departures (86 on Friday).
- **Route agreement (RAPTOR vs. Dijkstra): 100.0%**

| Metric | RAPTOR (Timetable) | Dijkstra (Frequency Graph) |
|---|---:|---:|
| Latency mean | 0.09 ms | 0.1 ms |
| Latency p50 / p95 | 0.081 / 0.165 ms | 0.091 / 0.18 ms |
| Latency max | 0.313 ms | 0.444 ms |
| Median trip duration | 17.9 min | 17.3 min |
| Max transfers | 0 | 0 |

Mean absolute duration difference between RAPTOR and Dijkstra: **2.6 min** (expected: RAPTOR computes exact wait until the next scheduled departure, whereas Dijkstra uses expected wait = `headway / 2 = 5.0 min`).

### Sample Dhaka Corridors (Saturday `08:30` — Estimated Schedules)

| Origin → Destination | RAPTOR (dep → arr) | RAPTOR time | Dijkstra time | Transfers | RAPTOR ms | Dijkstra ms |
|---|---|---:|---:|---:|---:|---:|
| Mirpur 10 -> Motijheel | `08:30:00` → `08:58:00` | 28.0 min | 29.2 min | 0 | 0.116 | 0.15 |
| Uttara North -> Motijheel | `08:30:00` → `09:18:00` | 48.0 min | 43.3 min | 0 | 0.118 | 0.135 |
| Pallabi -> Farmgate | `08:30:00` → `08:56:05` | 26.1 min | 21.4 min | 0 | 0.117 | 0.105 |
| Agargaon -> Dhaka University | `08:30:00` → `08:42:51` | 12.8 min | 17.1 min | 0 | 0.077 | 0.081 |
| Motijheel -> Uttara North | `08:30:00` → `09:13:21` | 43.4 min | 43.4 min | 0 | 0.09 | 0.108 |

### Access/Egress Multimodal Walk + Metro Check (Starting from Real OSM Bus Stops)

| OSM Bus Stop Origin → Destination | Leg sequence | Total time (est.) | Walk distance | Agreement |
|---|---|---:|---:|---|
| Mirpur 10 Circle Bus stop -> City Centre(Janata Bank) | `walk -> metro -> walk` | 30.6 min | 386.0 m | True |
| Leguna Tempu Stand -> Shahbag P.G. Hospital Bus stop, Shahbagh Rd, Dhaka | `walk -> metro -> walk` | 14.5 min | 442.0 m | True |

### Calendar & Off-Hours Verification (`Mirpur 10 → Motijheel`)

- **Friday `08:30` query:** RAPTOR respects Friday afternoon-only MRT-6 service and returns arrival `15:38:00`.
- **Saturday `23:30` query (after last train):** combined planner falls back cleanly to `dijkstra` with `outside_service_hours=True`.

## 2. A\* vs. Uninformed Dijkstra Walking Benchmark

| Street Grid | Nodes | Optimal Distance | A\* Expanded | Dijkstra Expanded | Node Reduction | A\* ms | Dijkstra ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| 20x20 (with barrier) | 384 | 3712.6 m | 325 | 381 | **14.7%** | 0.681 | 0.385 |
| 30x30 (with barrier) | 874 | 5842.0 m | 785 | 871 | **9.9%** | 2.059 | 1.244 |

## 3. Controlled Synthetic Scalability Benchmark (Not Real Dhaka Bus Data)

Measures algorithm scaling on synthetic 10×10 stop lattices as bus routes grow from 20 to 100 routes:

| Synthetic Routes | Graph Nodes | Graph Edges | Expanded Trips | RAPTOR mean ms | Dijkstra mean ms | Avg Pareto Options |
|---:|---:|---:|---:|---:|---:|---:|
| 20 | 500 | 1160 | 2356 | 0.66 | 0.76 | 1 |
| 50 | 1100 | 2900 | 5816 | 1.21 | 2.51 | 1.8 |
| 100 | 2100 | 5800 | 11586 | 2.98 | 4.59 | 1.8 |

## 4. Documented Limitations

1. **Bus route gap in OpenStreetMap:** Greater Dhaka currently has 0 usable `route=bus` relations in OSM (`data/reports/gap_report.md`). Real-feed evaluation therefore exercises MRT Line 6 + walking until community bus surveys are collected; multi-transfer bus+metro correctness is verified via automated unit & integration tests.
2. **Estimated MRT-6 segment times & headways:** Station-to-station run times are proportionally distributed from the secondary-reported ~38 min end-to-end duration (`estimated` / `unverified`) until an official DMTCL timetable is obtained.
3. **Unverified fares excluded:** Third-party web fare tables had asymmetric contradictions, so fares return `unavailable` until transcribed directly from an official DMTCL fare chart.
4. **Access/egress walking model:** Uses `haversine * 1.3` detour rather than an extracted Dhaka OSM street network (though `transit/routing/astar.py` supports OSM pedestrian ways directly once extracted).
5. **Static GTFS schedules only:** Real-time Dhaka road traffic congestion is not modelled without GTFS-Realtime feeds.
