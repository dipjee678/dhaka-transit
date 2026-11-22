"""Walking footpath generator with transitive closure (required by RAPTOR).

Connects boarding stops (location_type == 0) that are within ``max_walk_m``
of each other, plus platforms sharing a ``parent_station`` (in-station transfer).
Then applies transitive closure via bounded Dijkstra so that whenever
``a -> b`` and ``b -> c`` are valid transfer walks whose total duration stays
within the transfer limit, a direct edge ``a -> c`` exists.
"""
from __future__ import annotations

import heapq
from collections import defaultdict
from dataclasses import dataclass

from transit.geo import DETOUR_FACTOR, WALK_SPEED_MPS, GridIndex, walk_distance_m, walk_time_s
from transit.gtfs.model import Stop

DEFAULT_MAX_TRANSFER_M = 500.0
DEFAULT_STATION_TRANSFER_S = 90   # walking between opposite platforms in a metro station


@dataclass(frozen=True)
class Footpath:
    from_stop_id: str
    to_stop_id: str
    distance_m: float
    duration_s: int
    method: str = "haversine"  # 'haversine' | 'osm_astar' | 'surveyed'


def build_footpaths(
    boarding_stops: dict[str, Stop],
    max_walk_m: float = DEFAULT_MAX_TRANSFER_M,
    walk_speed_mps: float = WALK_SPEED_MPS,
    detour_factor: float = DETOUR_FACTOR,
    station_transfer_s: int = DEFAULT_STATION_TRANSFER_S,
    transitive_closure: bool = True,
) -> list[Footpath]:
    """Generate transfer footpaths between all boarding stops."""
    idx: GridIndex[str] = GridIndex(cell_size_m=max(100.0, max_walk_m / 2))
    for sid, s in boarding_stops.items():
        idx.insert(sid, s.stop_lat, s.stop_lon)

    # Straight-line radius upper bound that could still yield walk_distance <= max_walk_m
    straight_cap_m = max_walk_m / max(1.0, detour_factor)
    max_duration_s = walk_time_s(max_walk_m, walk_speed_mps)

    direct: dict[tuple[str, str], Footpath] = {}
    for sid, s in boarding_stops.items():
        for n in idx.within(s.stop_lat, s.stop_lon, straight_cap_m):
            other_id = n.key
            if other_id == sid:
                continue
            other = boarding_stops[other_id]
            if s.parent_station and s.parent_station == other.parent_station:
                dist = n.distance_m
                dur = max(station_transfer_s, walk_time_s(dist, walk_speed_mps))
            else:
                dist = walk_distance_m(s.stop_lat, s.stop_lon, other.stop_lat, other.stop_lon, detour_factor)
                if dist > max_walk_m:
                    continue
                dur = walk_time_s(dist, walk_speed_mps)
            direct[(sid, other_id)] = Footpath(sid, other_id, round(dist, 1), dur, "haversine")

    # Guarantee same-parent platforms are linked even if slightly farther than straight_cap_m.
    by_parent: dict[str, list[Stop]] = defaultdict(list)
    for s in boarding_stops.values():
        if s.parent_station:
            by_parent[s.parent_station].append(s)
    for group in by_parent.values():
        for a in group:
            for b in group:
                if a.stop_id != b.stop_id and (a.stop_id, b.stop_id) not in direct:
                    dist = walk_distance_m(a.stop_lat, a.stop_lon, b.stop_lat, b.stop_lon, 1.0)
                    dur = max(station_transfer_s, walk_time_s(dist, walk_speed_mps))
                    direct[(a.stop_id, b.stop_id)] = Footpath(a.stop_id, b.stop_id, round(dist, 1), dur, "haversine")

    if not transitive_closure or not direct:
        return sorted(direct.values(), key=lambda f: (f.from_stop_id, f.to_stop_id))

    adj: dict[str, list[Footpath]] = defaultdict(list)
    for fp in direct.values():
        adj[fp.from_stop_id].append(fp)

    closed: list[Footpath] = []
    # Bounded Dijkstra from each stop with outgoing walks enforces triangle inequality.
    for src in sorted(adj):
        best_dur: dict[str, int] = {src: 0}
        best_dist: dict[str, float] = {src: 0.0}
        pq: list[tuple[int, float, str]] = [(0, 0.0, src)]
        while pq:
            d_s, d_m, u = heapq.heappop(pq)
            if d_s > best_dur.get(u, 10**9):
                continue
            for fp in adj.get(u, ()):
                nd_s = d_s + fp.duration_s
                nd_m = d_m + fp.distance_m
                if nd_s > max(max_duration_s, station_transfer_s) and (u != src):
                    continue
                if nd_s < best_dur.get(fp.to_stop_id, 10**9):
                    best_dur[fp.to_stop_id] = nd_s
                    best_dist[fp.to_stop_id] = nd_m
                    heapq.heappush(pq, (nd_s, nd_m, fp.to_stop_id))
        for dst, dur in sorted(best_dur.items()):
            if dst != src:
                orig = direct.get((src, dst))
                method = orig.method if orig else "haversine"
                closed.append(Footpath(src, dst, round(best_dist[dst], 1), dur, method))
    return closed


def footpaths_by_stop(footpaths: list[Footpath]) -> dict[str, list[Footpath]]:
    out: dict[str, list[Footpath]] = defaultdict(list)
    for fp in footpaths:
        out[fp.from_stop_id].append(fp)
    return dict(out)
