"""Time-independent frequency graph for Dijkstra multimodal routing.

Graph structure (see docs/PROJECT_PLAN.md §7.1):
- Stop nodes        : ``("S", stop_id)``
- Route-stop nodes  : ``("R", route_id, direction_id, stop_id)``

Edges carry raw time components (``in_vehicle_s``, ``wait_s``, ``walk_s``) and
``kind`` (``board | ride | alight | walk``) so the Dijkstra solver (Phase 4) can
apply configurable generalized-cost multipliers without rebuilding the graph.
"""
from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from transit.graph.footpaths import Footpath
from transit.gtfs.model import TransitFeed
from transit.gtfs.spec import format_gtfs_time
from transit.provenance import SourceCategory, weakest

StopNode = tuple[str, str]                  # ("S", stop_id)
RouteNode = tuple[str, str, int, str]       # ("R", route_id, direction_id, stop_id)
Node = StopNode | RouteNode

DEFAULT_UNSCHEDULED_WAIT_S = 600  # fallback only when a trip has neither frequency nor multiple departures


@dataclass(frozen=True)
class Edge:
    to: Node
    kind: str               # 'board' | 'ride' | 'alight' | 'walk'
    in_vehicle_s: int = 0
    wait_s: int = 0
    walk_s: int = 0
    distance_m: float = 0.0
    route_id: str = ""
    direction_id: int = 0
    provenance: SourceCategory = SourceCategory.ESTIMATED

    @property
    def raw_time_s(self) -> int:
        return self.in_vehicle_s + self.wait_s + self.walk_s


@dataclass
class FrequencyGraph:
    adj: dict[Node, list[Edge]] = field(default_factory=dict)
    stop_ids: set[str] = field(default_factory=set)
    route_count: int = 0

    @property
    def node_count(self) -> int:
        return len(self.adj)

    @property
    def edge_count(self) -> int:
        return sum(len(es) for es in self.adj.values())

    def stats(self) -> dict:
        by_kind: dict[str, int] = defaultdict(int)
        for es in self.adj.values():
            for e in es:
                by_kind[e.kind] += 1
        return {
            "nodes": self.node_count,
            "stop_nodes": len(self.stop_ids),
            "route_stop_nodes": self.node_count - len(self.stop_ids),
            "edges": self.edge_count,
            "edges_by_kind": dict(by_kind),
            "routes": self.route_count,
        }


def build_frequency_graph(
    feed: TransitFeed,
    footpaths: list[Footpath],
    service_ids: set[str] | None = None,
) -> FrequencyGraph:
    """Build the frequency-based transit graph from a :class:`TransitFeed`.

    If ``service_ids`` is given, only trips belonging to those active services
    are included; otherwise all trips in the feed are aggregated.
    """
    adj: dict[Node, list[Edge]] = defaultdict(list)
    boarding = feed.boarding_stops
    for sid in boarding:
        adj[("S", sid)]  # ensure every boarding stop exists as a node

    # Aggregate ride durations & headways per (route_id, direction_id).
    seg_times: dict[tuple[str, int, str, str], list[int]] = defaultdict(list)
    seg_provs: dict[tuple[str, int, str, str], list[SourceCategory]] = defaultdict(list)
    rd_headways: dict[tuple[str, int], list[int]] = defaultdict(list)
    rd_wait_provs: dict[tuple[str, int], list[SourceCategory]] = defaultdict(list)
    rd_stops: dict[tuple[str, int], set[str]] = defaultdict(set)
    rd_dep_times: dict[tuple[str, int], list[int]] = defaultdict(list)

    for trip in feed.trips.values():
        if service_ids is not None and trip.service_id not in service_ids:
            continue
        sts = feed.stop_times_by_trip.get(trip.trip_id, [])
        if len(sts) < 2:
            continue
        rd = (trip.route_id, trip.direction_id)
        trip_prov_rec = feed.provenance_for("stop_times", trip.trip_id) or feed.provenance_for("trips", trip.trip_id)
        trip_prov = trip_prov_rec.category if trip_prov_rec else SourceCategory.ESTIMATED

        for st in sts:
            rd_stops[rd].add(st.stop_id)
        rd_dep_times[rd].append(sts[0].departure_s)

        for a, b in zip(sts, sts[1:]):
            dt = max(0, b.arrival_s - a.departure_s)
            key = (trip.route_id, trip.direction_id, a.stop_id, b.stop_id)
            seg_times[key].append(dt)
            seg_provs[key].append(trip_prov)

        freqs = feed.frequencies_by_trip.get(trip.trip_id, [])
        for f in freqs:
            rd_headways[rd].append(f.headway_secs)
            fprov = feed.provenance_for("frequencies", f"{trip.trip_id}|{format_gtfs_time(f.start_s)}")
            rd_wait_provs[rd].append(fprov.category if fprov else trip_prov)

    # Compute expected waiting time = median(headway) / 2 for each (route, dir).
    expected_wait: dict[tuple[str, int], tuple[int, SourceCategory]] = {}
    for rd in rd_stops:
        if rd_headways[rd]:
            hw = statistics.median(rd_headways[rd])
            prov = weakest(rd_wait_provs[rd]) if rd_wait_provs[rd] else SourceCategory.ESTIMATED
            expected_wait[rd] = (int(round(hw / 2.0)), prov)
        else:
            deps = sorted(rd_dep_times[rd])
            if len(deps) >= 2:
                gaps = [b - a for a, b in zip(deps, deps[1:]) if b > a]
                hw = statistics.median(gaps) if gaps else DEFAULT_UNSCHEDULED_WAIT_S * 2
                expected_wait[rd] = (int(round(hw / 2.0)), SourceCategory.ESTIMATED)
            else:
                expected_wait[rd] = (DEFAULT_UNSCHEDULED_WAIT_S, SourceCategory.ESTIMATED)

    # Board & alight edges
    for (rid, did), stops_set in rd_stops.items():
        wait_s, wait_prov = expected_wait[(rid, did)]
        for sid in sorted(stops_set):
            snode: StopNode = ("S", sid)
            rnode: RouteNode = ("R", rid, did, sid)
            adj[snode].append(Edge(
                to=rnode, kind="board", wait_s=wait_s, route_id=rid, direction_id=did, provenance=wait_prov,
            ))
            adj[rnode].append(Edge(
                to=snode, kind="alight", route_id=rid, direction_id=did, provenance=SourceCategory.OFFICIAL,
            ))

    # Ride edges
    for (rid, did, u_stop, v_stop), dts in seg_times.items():
        avg_s = int(round(statistics.mean(dts)))
        prov = weakest(seg_provs[(rid, did, u_stop, v_stop)])
        adj[("R", rid, did, u_stop)].append(Edge(
            to=("R", rid, did, v_stop), kind="ride", in_vehicle_s=avg_s,
            route_id=rid, direction_id=did, provenance=prov,
        ))

    # Walk / transfer footpath edges
    for fp in footpaths:
        if fp.from_stop_id in boarding and fp.to_stop_id in boarding:
            adj[("S", fp.from_stop_id)].append(Edge(
                to=("S", fp.to_stop_id), kind="walk", walk_s=fp.duration_s,
                distance_m=fp.distance_m, provenance=SourceCategory.ESTIMATED,
            ))

    return FrequencyGraph(
        adj=dict(adj),
        stop_ids=set(boarding.keys()),
        route_count=len({rid for rid, _ in rd_stops}),
    )
