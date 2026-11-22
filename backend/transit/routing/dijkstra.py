"""Dijkstra shortest-path engine on the frequency-based multimodal graph.

State space: ``(node, has_boarded: bool)``
Using a 1-bit ``has_boarded`` flag makes the first transit boarding strictly
free of the transfer penalty, while every subsequent boarding pays
``weights.transfer_penalty_s`` — keeping Dijkstra 100% exact without distorting
the comparison between direct walking and single-leg transit.

Edge weights (always >= 0):
- access / egress / transfer walk : ``walk_weight * walk_s``
- first transit boarding          : ``wait_weight * wait_s``
- subsequent transit boarding     : ``wait_weight * wait_s + transfer_penalty_s``
- in-vehicle ride                 : ``in_vehicle_weight * in_vehicle_s``
- alighting                       : ``0``
"""
from __future__ import annotations

import heapq
import time
from dataclasses import dataclass

from transit.geo import DETOUR_FACTOR, GridIndex, walk_distance_m, walk_time_s
from transit.graph.frequency_graph import Edge, FrequencyGraph, Node
from transit.gtfs.model import TransitFeed
from transit.provenance import SourceCategory, weakest
from transit.routing.journey import (
    CostWeights, Journey, Leg, polyline_length_m, slice_route_geometry, summarize_journey,
)

ORIGIN: Node = ("ORIGIN", "")
DEST: Node = ("DEST", "")
State = tuple[Node, bool]  # (graph_node, has_boarded_transit)


@dataclass(frozen=True)
class RawPath:
    cost: float
    steps: tuple[tuple[Node, Edge], ...]   # (from_node, edge) sequence


def dijkstra_shortest_path(
    graph: FrequencyGraph,
    feed: TransitFeed,
    access_edges: list[Edge],
    egress_by_stop: dict[str, Edge],
    direct_walk_edge: Edge | None = None,
    weights: CostWeights = CostWeights(),
    allowed_modes: set[str] | None = None,
) -> RawPath | None:
    """Exact binary-heap Dijkstra on ``graph`` with virtual ORIGIN/DEST nodes."""
    start: State = (ORIGIN, False)
    dist: dict[State, float] = {start: 0.0}
    prev: dict[State, tuple[State, Edge]] = {}
    pq: list[tuple[float, State]] = [(0.0, start)]
    goal_state: State | None = None

    def edge_cost(edge: Edge, has_boarded: bool) -> tuple[float, bool]:
        if edge.kind == "board":
            pen = weights.transfer_penalty_s if has_boarded else 0.0
            return weights.wait_weight * edge.wait_s + pen, True
        if edge.kind == "ride":
            return weights.in_vehicle_weight * edge.in_vehicle_s, has_boarded
        if edge.kind == "walk":
            return weights.walk_weight * edge.walk_s, has_boarded
        return 0.0, has_boarded  # alight

    while pq:
        d, state = heapq.heappop(pq)
        if d > dist.get(state, float("inf")):
            continue
        u, boarded = state
        if u == DEST:
            goal_state = state
            break

        if u == ORIGIN:
            out_edges = list(access_edges)
            if direct_walk_edge is not None:
                out_edges.append(direct_walk_edge)
        else:
            out_edges = list(graph.adj.get(u, ()))
            if u[0] == "S" and u[1] in egress_by_stop:
                out_edges.append(egress_by_stop[u[1]])

        for e in out_edges:
            if allowed_modes is not None and e.kind == "board":
                route = feed.routes.get(e.route_id)
                if route is not None and route.mode not in allowed_modes:
                    continue
            c, next_boarded = edge_cost(e, boarded)
            nxt: State = (e.to, next_boarded)
            nd = d + c
            if nd < dist.get(nxt, float("inf")):
                dist[nxt] = nd
                prev[nxt] = (state, e)
                heapq.heappush(pq, (nd, nxt))

    if goal_state is None:
        return None

    rev: list[tuple[Node, Edge]] = []
    cur = goal_state
    while cur != start:
        p_state, edge = prev[cur]
        rev.append((p_state[0], edge))
        cur = p_state
    rev.reverse()
    return RawPath(cost=dist[goal_state], steps=tuple(rev))


def _representative_shape_and_headsign(feed: TransitFeed, route_id: str, direction_id: int) -> tuple[str, str]:
    for t in feed.trips_by_route.get(route_id, ()):
        if t.direction_id == direction_id:
            return t.shape_id, t.trip_headsign
    return "", ""


def plan_dijkstra(
    graph: FrequencyGraph,
    feed: TransitFeed,
    boarding_index: GridIndex[str],
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    from_name: str = "Origin",
    to_name: str = "Destination",
    weights: CostWeights = CostWeights(),
    allowed_modes: set[str] | None = None,
    outside_service_hours: bool = False,
) -> Journey | None:
    """Compute the minimum-generalized-cost journey using Dijkstra."""
    t0 = time.perf_counter()

    # Access & egress walks (with fallback to nearest stop up to 3 km if sparse area)
    access_hits = boarding_index.within(from_lat, from_lon, weights.max_access_walk_m / DETOUR_FACTOR)
    if not access_hits:
        access_hits = boarding_index.nearest(from_lat, from_lon, k=4, max_radius_m=3000.0)

    egress_hits = boarding_index.within(to_lat, to_lon, weights.max_access_walk_m / DETOUR_FACTOR)
    if not egress_hits:
        egress_hits = boarding_index.nearest(to_lat, to_lon, k=4, max_radius_m=3000.0)

    access_edges: list[Edge] = []
    for h in access_hits:
        s = feed.stops[h.key]
        wd = walk_distance_m(from_lat, from_lon, s.stop_lat, s.stop_lon)
        if wd <= weights.max_access_walk_m or len(access_edges) < 2:
            ws = 0 if wd < 5.0 else walk_time_s(wd)
            access_edges.append(Edge(
                to=("S", h.key), kind="walk", walk_s=ws, distance_m=0.0 if wd < 5.0 else round(wd, 1),
                provenance=SourceCategory.ESTIMATED,
            ))

    egress_by_stop: dict[str, Edge] = {}
    for h in egress_hits:
        s = feed.stops[h.key]
        wd = walk_distance_m(s.stop_lat, s.stop_lon, to_lat, to_lon)
        if wd <= weights.max_access_walk_m or len(egress_by_stop) < 2:
            ws = 0 if wd < 5.0 else walk_time_s(wd)
            egress_by_stop[h.key] = Edge(
                to=DEST, kind="walk", walk_s=ws, distance_m=0.0 if wd < 5.0 else round(wd, 1),
                provenance=SourceCategory.ESTIMATED,
            )

    direct_wd = walk_distance_m(from_lat, from_lon, to_lat, to_lon)
    direct_edge = (
        Edge(to=DEST, kind="walk", walk_s=walk_time_s(direct_wd), distance_m=round(direct_wd, 1),
             provenance=SourceCategory.ESTIMATED)
        if direct_wd <= weights.max_direct_walk_m
        else None
    )

    raw = dijkstra_shortest_path(
        graph, feed, access_edges, egress_by_stop, direct_edge, weights, allowed_modes
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    if raw is None:
        return None

    legs = _reconstruct_legs(raw, feed, from_lat, from_lon, from_name, to_lat, to_lon, to_name)
    alg = "walk_only" if all(not l.is_transit for l in legs) else "dijkstra"
    return summarize_journey(
        algorithm=alg,
        legs=legs,
        feed=feed,
        weights=weights,
        outside_service_hours=outside_service_hours,
        computation_ms=elapsed_ms,
    )


def _reconstruct_legs(
    raw: RawPath,
    feed: TransitFeed,
    from_lat: float,
    from_lon: float,
    from_name: str,
    to_lat: float,
    to_lon: float,
    to_name: str,
) -> list[Leg]:
    legs: list[Leg] = []
    i = 0
    steps = raw.steps
    while i < len(steps):
        u, e = steps[i]
        if e.kind == "walk":
            if u == ORIGIN and e.to == DEST:
                legs.append(Leg(
                    mode="walk", from_name=from_name, from_lat=from_lat, from_lon=from_lon,
                    to_name=to_name, to_lat=to_lat, to_lon=to_lon,
                    duration_s=e.walk_s, distance_m=e.distance_m,
                    geometry=((from_lat, from_lon), (to_lat, to_lon)),
                    provenance=SourceCategory.ESTIMATED,
                ))
            elif u == ORIGIN:
                st = feed.stops[e.to[1]]
                if e.distance_m >= 5.0:
                    legs.append(Leg(
                        mode="walk", from_name=from_name, from_lat=from_lat, from_lon=from_lon,
                        to_name=st.stop_name, to_lat=st.stop_lat, to_lon=st.stop_lon,
                        to_stop_id=st.stop_id, duration_s=e.walk_s, distance_m=e.distance_m,
                        geometry=((from_lat, from_lon), (st.stop_lat, st.stop_lon)),
                        provenance=SourceCategory.ESTIMATED,
                    ))
            elif e.to == DEST:
                sf = feed.stops[u[1]]
                if e.distance_m >= 5.0:
                    legs.append(Leg(
                        mode="walk", from_name=sf.stop_name, from_lat=sf.stop_lat, from_lon=sf.stop_lon,
                        from_stop_id=sf.stop_id, to_name=to_name, to_lat=to_lat, to_lon=to_lon,
                        duration_s=e.walk_s, distance_m=e.distance_m,
                        geometry=((sf.stop_lat, sf.stop_lon), (to_lat, to_lon)),
                        provenance=SourceCategory.ESTIMATED,
                    ))
            else:
                sf, st = feed.stops[u[1]], feed.stops[e.to[1]]
                legs.append(Leg(
                    mode="transfer", from_name=sf.stop_name, from_lat=sf.stop_lat, from_lon=sf.stop_lon,
                    from_stop_id=sf.stop_id, to_name=st.stop_name, to_lat=st.stop_lat, to_lon=st.stop_lon,
                    to_stop_id=st.stop_id, duration_s=e.walk_s, distance_m=e.distance_m,
                    geometry=((sf.stop_lat, sf.stop_lon), (st.stop_lat, st.stop_lon)),
                    provenance=SourceCategory.ESTIMATED,
                ))
            i += 1
        elif e.kind == "board":
            wait_s = e.wait_s
            route_id, dir_id = e.route_id, e.direction_id
            board_stop = feed.stops[u[1]]
            provs = [e.provenance]
            ride_s = 0
            passed_stops = [board_stop]
            i += 1
            while i < len(steps) and steps[i][1].kind == "ride":
                re = steps[i][1]
                ride_s += re.in_vehicle_s
                provs.append(re.provenance)
                passed_stops.append(feed.stops[re.to[3]])
                i += 1
            if i < len(steps) and steps[i][1].kind == "alight":
                i += 1
            alight_stop = passed_stops[-1]
            route = feed.routes[route_id]
            shape_id, headsign = _representative_shape_and_headsign(feed, route_id, dir_id)
            stop_coords = [(s.stop_lat, s.stop_lon) for s in passed_stops]
            geom = slice_route_geometry(feed, shape_id, board_stop, alight_stop, stop_coords)
            legs.append(Leg(
                mode=route.mode,
                from_name=board_stop.stop_name, from_lat=board_stop.stop_lat, from_lon=board_stop.stop_lon,
                from_stop_id=board_stop.stop_id,
                to_name=alight_stop.stop_name, to_lat=alight_stop.stop_lat, to_lon=alight_stop.stop_lon,
                to_stop_id=alight_stop.stop_id,
                duration_s=ride_s,
                wait_s=wait_s,
                distance_m=round(polyline_length_m(geom), 1),
                route_id=route_id,
                route_short_name=route.route_short_name,
                route_long_name=route.route_long_name,
                route_color=route.route_color,
                direction_id=dir_id,
                headsign=headsign,
                intermediate_stops=tuple(
                    {"stop_id": s.stop_id, "stop_name": s.stop_name, "lat": s.stop_lat, "lon": s.stop_lon}
                    for s in passed_stops
                ),
                geometry=geom,
                provenance=weakest(provs),
            ))
        else:
            i += 1
    return legs
