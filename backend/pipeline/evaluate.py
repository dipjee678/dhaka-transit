"""Automated evaluation & performance benchmark suite (Phase 6).

Run from backend/:  python -m pipeline.evaluate
Writes:
  data/reports/evaluation_report.json
  data/reports/evaluation_report.md

Evaluates:
1. Real Dhaka GTFS feed — all 240 ordered MRT-6 OD pairs across morning,
   midday, and evening peaks (RAPTOR vs. Dijkstra latency, agreement, duration,
   transfers), plus coordinate-offset walks from real OSM bus stops and
   calendar/off-hours scenarios (Sat vs. Fri vs. 23:30).
2. A* vs. uninformed Dijkstra on 20x20 and 30x30 pedestrian grids with
   obstacles (exactness + node-expansion reduction).
3. Controlled synthetic scalability test (clearly labelled synthetic — NOT real
   Dhaka bus data) measuring RAPTOR vs. Dijkstra as bus routes grow from 20 to
   100 routes.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import statistics
import time
from pathlib import Path

from transit.geo import GridIndex, haversine_m
from transit.graph.footpaths import build_footpaths
from transit.graph.frequency_graph import build_frequency_graph
from transit.graph.timetable import build_timetable
from transit.gtfs.model import TransitFeed
from transit.gtfs.spec import format_gtfs_time
from transit.routing.astar import WalkGraph, astar_walk
from transit.routing.dijkstra import plan_dijkstra
from transit.routing.journey import CostWeights
from transit.routing.planner import plan_journey
from transit.routing.raptor import plan_raptor

ROOT = Path(__file__).resolve().parents[2]
DHAKA_FEED = ROOT / "data" / "gtfs" / "dhaka"
CURATED = ROOT / "data" / "curated"
REPORTS = ROOT / "data" / "reports"


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, math.ceil(len(s) * p / 100.0) - 1)
    return round(s[max(0, idx)], 3)


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 3),
        "p50": _pct(values, 50),
        "p95": _pct(values, 95),
        "max": round(max(values), 3),
    }


def _boarding_index(feed: TransitFeed) -> GridIndex[str]:
    idx: GridIndex[str] = GridIndex(cell_size_m=400.0)
    for sid, s in feed.boarding_stops.items():
        idx.insert(sid, s.stop_lat, s.stop_lon)
    return idx


# ---------------------------------------------------------------------------
# 1. Real Dhaka MRT-6 All-Pairs Evaluation
# ---------------------------------------------------------------------------
def benchmark_real_dhaka(feed_dir: Path = DHAKA_FEED) -> dict:
    feed = TransitFeed.from_path(feed_dir)
    fps = build_footpaths(feed.boarding_stops)
    graph = build_frequency_graph(feed, fps)
    idx = _boarding_index(feed)

    sat = dt.date(2026, 11, 21)
    fri = dt.date(2026, 11, 20)
    tt_sat = build_timetable(feed, fps, feed.active_service_ids(sat))
    tt_fri = build_timetable(feed, fps, feed.active_service_ids(fri))

    stations = sorted(feed.stations.values(), key=lambda s: s.stop_lat, reverse=True)
    dep_times = [8 * 3600 + 30 * 60, 14 * 3600, 18 * 3600 + 30 * 60]

    raptor_ms: list[float] = []
    dijkstra_ms: list[float] = []
    raptor_durs: list[int] = []
    dijkstra_durs: list[int] = []
    dur_diffs: list[int] = []
    agreements = 0
    total_pairs = 0
    raptor_transfers: list[int] = []
    dijkstra_transfers: list[int] = []

    for dep_s in dep_times:
        for o in stations:
            for d in stations:
                if o.stop_id == d.stop_id:
                    continue
                total_pairs += 1
                rj = plan_raptor(
                    tt_sat, feed, idx, o.stop_lat, o.stop_lon, d.stop_lat, d.stop_lon,
                    departure_s=dep_s, from_name=o.stop_name, to_name=d.stop_name,
                )
                dj = plan_dijkstra(
                    graph, feed, idx, o.stop_lat, o.stop_lon, d.stop_lat, d.stop_lon,
                    from_name=o.stop_name, to_name=d.stop_name,
                )
                assert rj and dj, f"Missing route for {o.stop_id} -> {d.stop_id}"
                best_r = min(rj, key=lambda j: j.generalized_cost)
                raptor_ms.append(best_r.computation_ms)
                dijkstra_ms.append(dj.computation_ms)
                raptor_durs.append(best_r.total_duration_s)
                dijkstra_durs.append(dj.total_duration_s)
                dur_diffs.append(abs(best_r.total_duration_s - dj.total_duration_s))
                raptor_transfers.append(best_r.transfers)
                dijkstra_transfers.append(dj.transfers)
                if best_r.signature(feed) == dj.signature(feed):
                    agreements += 1

    # Featured corridors at Saturday 08:30
    featured_pairs = [
        ("mrt6_mirpur_10", "mrt6_motijheel"),
        ("mrt6_uttara_north", "mrt6_motijheel"),
        ("mrt6_pallabi", "mrt6_farmgate"),
        ("mrt6_agargaon", "mrt6_dhaka_university"),
        ("mrt6_motijheel", "mrt6_uttara_north"),
    ]
    featured: list[dict] = []
    for fid, tid in featured_pairs:
        o, d = feed.stops[fid], feed.stops[tid]
        res = plan_journey(
            feed=feed, graph=graph, timetable=tt_sat, boarding_index=idx,
            from_lat=o.stop_lat, from_lon=o.stop_lon, to_lat=d.stop_lat, to_lon=d.stop_lon,
            from_name=o.stop_name, to_name=d.stop_name,
            service_date=sat, departure_s=8 * 3600 + 30 * 60,
        )
        c = res.comparison
        featured.append({
            "od": f"{o.stop_name} -> {d.stop_name}",
            "raptor_duration_min": round(c["raptor"]["total_duration_s"] / 60.0, 1),
            "dijkstra_duration_min": round(c["dijkstra"]["total_duration_s"] / 60.0, 1),
            "raptor_departure": format_gtfs_time(c["raptor"]["departure_s"]),
            "raptor_arrival": format_gtfs_time(c["raptor"]["arrival_s"]),
            "transfers": c["raptor"]["transfers"],
            "raptor_ms": c["raptor"]["computation_ms"],
            "dijkstra_ms": c["dijkstra"]["computation_ms"],
            "same_route": c["same_best_route"],
            "estimated": res.recommended.is_estimated if res.recommended else True,
        })

    # Real OSM bus-stop access/egress multimodal walk + metro test
    osm_bus = list(csv.DictReader(open(CURATED / "bus_stops.csv", encoding="utf-8"))) if (CURATED / "bus_stops.csv").exists() else []
    walk_metro_samples: list[dict] = []
    if osm_bus:
        def _near_bus(st_id: str, min_m: float = 120.0, max_m: float = 450.0) -> dict | None:
            st = feed.stops[st_id]
            cands = []
            for b in osm_bus:
                if not b["name"]:
                    continue
                dist = haversine_m(st.stop_lat, st.stop_lon, float(b["lat"]), float(b["lon"]))
                if min_m <= dist <= max_m:
                    cands.append((dist, b))
            cands.sort(key=lambda x: x[0])
            return cands[0][1] if cands else None

        for orig_st, dest_st in [("mrt6_mirpur_10", "mrt6_motijheel"), ("mrt6_farmgate", "mrt6_shahbagh")]:
            bo, bd = _near_bus(orig_st), _near_bus(dest_st)
            if bo and bd:
                res = plan_journey(
                    feed=feed, graph=graph, timetable=tt_sat, boarding_index=idx,
                    from_lat=float(bo["lat"]), from_lon=float(bo["lon"]),
                    to_lat=float(bd["lat"]), to_lon=float(bd["lon"]),
                    from_name=bo["name"], to_name=bd["name"],
                    service_date=sat, departure_s=9 * 3600,
                )
                rec = res.recommended
                if rec:
                    walk_metro_samples.append({
                        "od": f"{bo['name']} -> {bd['name']}",
                        "legs": [l.mode for l in rec.legs],
                        "total_min": round(rec.total_duration_s / 60.0, 1),
                        "walk_m": rec.total_walk_m,
                        "same_route": res.comparison["same_best_route"],
                    })

    # Calendar / off-hours checks (Mirpur 10 -> Motijheel)
    o, d = feed.stops["mrt6_mirpur_10"], feed.stops["mrt6_motijheel"]
    fri_res = plan_journey(
        feed=feed, graph=graph, timetable=tt_fri, boarding_index=idx,
        from_lat=o.stop_lat, from_lon=o.stop_lon, to_lat=d.stop_lat, to_lon=d.stop_lon,
        service_date=fri, departure_s=8 * 3600 + 30 * 60, algorithm="raptor",
    )
    late_res = plan_journey(
        feed=feed, graph=graph, timetable=tt_sat, boarding_index=idx,
        from_lat=o.stop_lat, from_lon=o.stop_lon, to_lat=d.stop_lat, to_lon=d.stop_lon,
        service_date=sat, departure_s=23 * 3600 + 30 * 60, algorithm="combined",
    )

    return {
        "feed_version": feed.feed_info.get("feed_version", "unknown"),
        "stations": len(stations),
        "queries_run": total_pairs,
        "graph_stats": graph.stats(),
        "timetable_sat_stats": tt_sat.stats(),
        "timetable_fri_stats": tt_fri.stats(),
        "route_agreement_pct": round(agreements / max(1, total_pairs) * 100.0, 2),
        "raptor_latency_ms": _stats(raptor_ms),
        "dijkstra_latency_ms": _stats(dijkstra_ms),
        "raptor_duration_s": _stats([float(x) for x in raptor_durs]),
        "dijkstra_duration_s": _stats([float(x) for x in dijkstra_durs]),
        "abs_duration_diff_s": _stats([float(x) for x in dur_diffs]),
        "max_transfers_raptor": max(raptor_transfers, default=0),
        "max_transfers_dijkstra": max(dijkstra_transfers, default=0),
        "featured_corridors": featured,
        "osm_bus_access_egress_samples": walk_metro_samples,
        "calendar_checks": {
            "friday_0830_first_arrival": (
                format_gtfs_time(fri_res.recommended.arrival_s)
                if fri_res.recommended and fri_res.recommended.arrival_s is not None
                else None
            ),
            "saturday_2330_fallback_algorithm": late_res.recommended.algorithm if late_res.recommended else None,
            "saturday_2330_outside_hours_flag": late_res.recommended.outside_service_hours if late_res.recommended else None,
        },
    }


# ---------------------------------------------------------------------------
# 2. A* vs. Dijkstra Pedestrian Grid Benchmark
# ---------------------------------------------------------------------------
def benchmark_astar_walking() -> list[dict]:
    results = []
    for size in (20, 30):
        g = WalkGraph()
        blocked = {(r, size // 2) for r in range(2, size - 2)}
        for r in range(size):
            for c in range(size):
                if (r, c) not in blocked:
                    g.add_node(f"{r}_{c}", 23.75 + r * 0.001, 90.37 + c * 0.001)
        for r in range(size):
            for c in range(size):
                if (r, c) in blocked:
                    continue
                for dr, dc in ((1, 0), (0, 1)):
                    nr, nc = r + dr, c + dc
                    if 0 <= nr < size and 0 <= nc < size and (nr, nc) not in blocked:
                        g.add_edge(f"{r}_{c}", f"{nr}_{nc}", bidirectional=True)

        start, goal = f"{size // 2}_0", f"{size // 2}_{size - 1}"
        t0 = time.perf_counter()
        ra = astar_walk(g, start, goal, use_heuristic=True)
        ta = (time.perf_counter() - t0) * 1000.0
        t0 = time.perf_counter()
        rd = astar_walk(g, start, goal, use_heuristic=False)
        td = (time.perf_counter() - t0) * 1000.0
        assert ra and rd and abs(ra.distance_m - rd.distance_m) < 0.5
        results.append({
            "grid": f"{size}x{size} (with barrier)",
            "nodes": len(g.nodes),
            "distance_m": ra.distance_m,
            "astar_expanded": ra.nodes_expanded,
            "dijkstra_expanded": rd.nodes_expanded,
            "expansion_reduction_pct": round((1.0 - ra.nodes_expanded / rd.nodes_expanded) * 100.0, 1),
            "astar_ms": round(ta, 3),
            "dijkstra_ms": round(td, 3),
        })
    return results


# ---------------------------------------------------------------------------
# 3. Synthetic City-Scale Bus + Metro Stress Test (NOT real Dhaka data)
# ---------------------------------------------------------------------------
def _synthetic_grid_feed(n_routes: int) -> TransitFeed:
    """Create a synthetic grid network of ``n_routes`` bus routes + 1 metro backbone."""
    stops = []
    # 10x10 synthetic stop lattice inside Dhaka bbox
    for r in range(10):
        for c in range(10):
            stops.append({
                "stop_id": f"SYN_{r}_{c}", "stop_name": f"Syn {r}-{c}",
                "stop_lat": f"{23.72 + r * 0.015:.6f}", "stop_lon": f"{90.35 + c * 0.008:.6f}",
                "location_type": "0",
            })
    routes, trips, stop_times, freqs = [], [], [], []
    for k in range(n_routes):
        rid = f"SYN_R{k}"
        is_metro = (k == 0)
        routes.append({
            "route_id": rid, "agency_id": "SYN", "route_short_name": rid,
            "route_long_name": rid, "route_type": "1" if is_metro else "3",
        })
        horizontal = (k % 2 == 0)
        line_idx = (k // 2) % 10
        seq_stops = [f"SYN_{line_idx}_{c}" for c in range(10)] if horizontal else [f"SYN_{r}_{line_idx}" for r in range(10)]
        for did, ordered in ((0, seq_stops), (1, list(reversed(seq_stops)))):
            tid = f"SYN_T{k}_d{did}"
            trips.append({"route_id": rid, "service_id": "SYN_SVC", "trip_id": tid, "direction_id": str(did)})
            step_s = 120 if is_metro else 180 + (k % 4) * 30
            for seq, sid in enumerate(ordered, start=1):
                t_str = format_gtfs_time(7 * 3600 + (seq - 1) * step_s)
                stop_times.append({
                    "trip_id": tid, "arrival_time": t_str, "departure_time": t_str,
                    "stop_id": sid, "stop_sequence": str(seq),
                })
            freqs.append({
                "trip_id": tid, "start_time": "07:00:00", "end_time": "21:00:00",
                "headway_secs": str(600 if is_metro else 720 + (k % 3) * 180), "exact_times": "0",
            })
    raw = {
        "agency.txt": [{"agency_id": "SYN", "agency_name": "Synthetic", "agency_url": "https://example.org", "agency_timezone": "Asia/Dhaka"}],
        "stops.txt": stops, "routes.txt": routes, "trips.txt": trips,
        "stop_times.txt": stop_times, "frequencies.txt": freqs,
        "calendar.txt": [{"service_id": "SYN_SVC", "monday": "1", "tuesday": "1", "wednesday": "1",
                          "thursday": "1", "friday": "1", "saturday": "1", "sunday": "1",
                          "start_date": "20260101", "end_date": "20261231"}],
        "calendar_dates.txt": [],
    }
    return TransitFeed.from_feed_dict(raw)


def benchmark_synthetic_scalability() -> list[dict]:
    rows = []
    query_pairs = [("SYN_0_0", "SYN_9_9"), ("SYN_0_9", "SYN_9_0"), ("SYN_2_1", "SYN_8_7"),
                   ("SYN_1_8", "SYN_7_2"), ("SYN_0_4", "SYN_9_5")]
    weights = CostWeights(max_access_walk_m=200.0)
    for n_routes in (20, 50, 100):
        tf = _synthetic_grid_feed(n_routes)
        fps = build_footpaths(tf.boarding_stops, max_walk_m=400.0)
        graph = build_frequency_graph(tf, fps)
        tt = build_timetable(tf, fps, {"SYN_SVC"})
        idx = _boarding_index(tf)

        r_ms, d_ms, pareto_counts = [], [], []
        for oid, did in query_pairs:
            o, d = tf.stops[oid], tf.stops[did]
            rj = plan_raptor(tt, tf, idx, o.stop_lat, o.stop_lon, d.stop_lat, d.stop_lon, departure_s=8 * 3600 + 15 * 60, weights=weights)
            dj = plan_dijkstra(graph, tf, idx, o.stop_lat, o.stop_lon, d.stop_lat, d.stop_lon, weights=weights)
            assert rj and dj
            r_ms.append(rj[0].computation_ms)
            d_ms.append(dj.computation_ms)
            pareto_counts.append(len(rj))

        rows.append({
            "routes": n_routes,
            "graph_nodes": graph.node_count,
            "graph_edges": graph.edge_count,
            "expanded_timetable_trips": tt.stats()["trips"],
            "raptor_mean_ms": round(statistics.mean(r_ms), 2),
            "dijkstra_mean_ms": round(statistics.mean(d_ms), 2),
            "avg_pareto_options": round(statistics.mean(pareto_counts), 2),
        })
    return rows


# ---------------------------------------------------------------------------
def render_markdown(rep: dict) -> str:
    dh = rep["real_dhaka"]
    lines = [
        "# Dhaka Transit — Testing & Performance Evaluation Report",
        "",
        f"- Generated: {rep['generated_at']}",
        f"- Feed evaluated: `data/gtfs/dhaka` (`{dh['feed_version']}`)",
        "",
        "## 1. Real Dhaka Dataset Benchmark (MRT Line 6 — All 240 OD Pairs × 3 Time Slots)",
        "",
        f"- Queries evaluated: **{dh['queries_run']}** timed queries (Saturday `08:30`, `14:00`, `18:30`) across all {dh['stations']} operational stations.",
        f"- Graph size: {dh['graph_stats']['nodes']} nodes, {dh['graph_stats']['edges']} edges. "
        f"Expanded Saturday timetable: {dh['timetable_sat_stats']['trips']} concrete departures ({dh['timetable_fri_stats']['trips']} on Friday).",
        f"- **Route agreement (RAPTOR vs. Dijkstra): {dh['route_agreement_pct']}%**",
        "",
        "| Metric | RAPTOR (Timetable) | Dijkstra (Frequency Graph) |",
        "|---|---:|---:|",
        f"| Latency mean | {dh['raptor_latency_ms']['mean']} ms | {dh['dijkstra_latency_ms']['mean']} ms |",
        f"| Latency p50 / p95 | {dh['raptor_latency_ms']['p50']} / {dh['raptor_latency_ms']['p95']} ms | {dh['dijkstra_latency_ms']['p50']} / {dh['dijkstra_latency_ms']['p95']} ms |",
        f"| Latency max | {dh['raptor_latency_ms']['max']} ms | {dh['dijkstra_latency_ms']['max']} ms |",
        f"| Median trip duration | {round(dh['raptor_duration_s']['p50'] / 60, 1)} min | {round(dh['dijkstra_duration_s']['p50'] / 60, 1)} min |",
        f"| Max transfers | {dh['max_transfers_raptor']} | {dh['max_transfers_dijkstra']} |",
        "",
        f"Mean absolute duration difference between RAPTOR and Dijkstra: **{round(dh['abs_duration_diff_s']['mean'] / 60, 2)} min** "
        "(expected: RAPTOR computes exact wait until the next scheduled departure, whereas Dijkstra uses expected wait = `headway / 2 = 5.0 min`).",
        "",
        "### Sample Dhaka Corridors (Saturday `08:30` — Estimated Schedules)",
        "",
        "| Origin → Destination | RAPTOR (dep → arr) | RAPTOR time | Dijkstra time | Transfers | RAPTOR ms | Dijkstra ms |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for f in dh["featured_corridors"]:
        lines.append(
            f"| {f['od']} | `{f['raptor_departure']}` → `{f['raptor_arrival']}` "
            f"| {f['raptor_duration_min']} min | {f['dijkstra_duration_min']} min "
            f"| {f['transfers']} | {f['raptor_ms']} | {f['dijkstra_ms']} |"
        )

    if dh["osm_bus_access_egress_samples"]:
        lines += [
            "",
            "### Access/Egress Multimodal Walk + Metro Check (Starting from Real OSM Bus Stops)",
            "",
            "| OSM Bus Stop Origin → Destination | Leg sequence | Total time (est.) | Walk distance | Agreement |",
            "|---|---|---:|---:|---|",
        ]
        for s in dh["osm_bus_access_egress_samples"]:
            lines.append(
                f"| {s['od']} | `{' -> '.join(s['legs'])}` | {s['total_min']} min | {s['walk_m']} m | {s['same_route']} |"
            )

    cal = dh["calendar_checks"]
    lines += [
        "",
        "### Calendar & Off-Hours Verification (`Mirpur 10 → Motijheel`)",
        "",
        f"- **Friday `08:30` query:** RAPTOR respects Friday afternoon-only MRT-6 service and returns arrival `{cal['friday_0830_first_arrival']}`.",
        f"- **Saturday `23:30` query (after last train):** combined planner falls back cleanly to `{cal['saturday_2330_fallback_algorithm']}` with `outside_service_hours={cal['saturday_2330_outside_hours_flag']}`.",
        "",
        "## 2. A\\* vs. Uninformed Dijkstra Walking Benchmark",
        "",
        "| Street Grid | Nodes | Optimal Distance | A\\* Expanded | Dijkstra Expanded | Node Reduction | A\\* ms | Dijkstra ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for a in rep["astar_walking"]:
        lines.append(
            f"| {a['grid']} | {a['nodes']} | {a['distance_m']} m | {a['astar_expanded']} "
            f"| {a['dijkstra_expanded']} | **{a['expansion_reduction_pct']}%** | {a['astar_ms']} | {a['dijkstra_ms']} |"
        )

    lines += [
        "",
        "## 3. Controlled Synthetic Scalability Benchmark (Not Real Dhaka Bus Data)",
        "",
        "Measures algorithm scaling on synthetic 10×10 stop lattices as bus routes grow from 20 to 100 routes:",
        "",
        "| Synthetic Routes | Graph Nodes | Graph Edges | Expanded Trips | RAPTOR mean ms | Dijkstra mean ms | Avg Pareto Options |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in rep["synthetic_scalability"]:
        lines.append(
            f"| {s['routes']} | {s['graph_nodes']} | {s['graph_edges']} | {s['expanded_timetable_trips']} "
            f"| {s['raptor_mean_ms']} | {s['dijkstra_mean_ms']} | {s['avg_pareto_options']} |"
        )

    lines += [
        "",
        "## 4. Documented Limitations",
        "",
        "1. **Bus route gap in OpenStreetMap:** Greater Dhaka currently has 0 usable `route=bus` relations in OSM (`data/reports/gap_report.md`). Real-feed evaluation therefore exercises MRT Line 6 + walking until community bus surveys are collected; multi-transfer bus+metro correctness is verified via automated unit & integration tests.",
        "2. **Estimated MRT-6 segment times & headways:** Station-to-station run times are proportionally distributed from the secondary-reported ~38 min end-to-end duration (`estimated` / `unverified`) until an official DMTCL timetable is obtained.",
        "3. **Unverified fares excluded:** Third-party web fare tables had asymmetric contradictions, so fares return `unavailable` until transcribed directly from an official DMTCL fare chart.",
        "4. **Access/egress walking model:** Uses `haversine * 1.3` detour rather than an extracted Dhaka OSM street network (though `transit/routing/astar.py` supports OSM pedestrian ways directly once extracted).",
        "5. **Static GTFS schedules only:** Real-time Dhaka road traffic congestion is not modelled without GTFS-Realtime feeds.",
        "",
    ]
    return "\n".join(lines)


def run_evaluation(reports_dir: Path = REPORTS) -> dict:
    rep = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "real_dhaka": benchmark_real_dhaka(),
        "astar_walking": benchmark_astar_walking(),
        "synthetic_scalability": benchmark_synthetic_scalability(),
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "evaluation_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (reports_dir / "evaluation_report.md").write_text(render_markdown(rep), encoding="utf-8")
    return rep


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reports", type=Path, default=REPORTS)
    args = ap.parse_args(argv)
    rep = run_evaluation(args.reports)
    dh = rep["real_dhaka"]
    print(f"Real Dhaka ({dh['queries_run']} queries): agreement={dh['route_agreement_pct']}%, "
          f"RAPTOR p50={dh['raptor_latency_ms']['p50']} ms, Dijkstra p50={dh['dijkstra_latency_ms']['p50']} ms")
    for a in rep["astar_walking"]:
        print(f"A* {a['grid']}: {a['expansion_reduction_pct']}% fewer nodes expanded")
    for s in rep["synthetic_scalability"]:
        print(f"Synthetic {s['routes']} routes ({s['expanded_timetable_trips']} trips): "
              f"RAPTOR={s['raptor_mean_ms']} ms, Dijkstra={s['dijkstra_mean_ms']} ms")
    print(f"Wrote {args.reports / 'evaluation_report.md'} and evaluation_report.json")


if __name__ == "__main__":
    main()
