"""A* walking-route optimizer for street / pedestrian networks.

Uses the **admissible and consistent haversine heuristic**:
    h(u, goal) = haversine_m(u, goal) / walk_speed_mps

Because great-circle distance never exceeds actual street distance, A* is
guaranteed to find the optimal walking path while exploring far fewer nodes
than uninformed Dijkstra. Can be fed an OpenStreetMap pedestrian-way extract
via :func:`build_walk_graph_from_osm` to replace straight-line detour walks.
"""
from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field

from transit.geo import WALK_SPEED_MPS, GridIndex, haversine_m

WALKABLE_HIGHWAYS = frozenset({
    "footway", "pedestrian", "path", "steps", "living_street",
    "residential", "service", "unclassified", "tertiary", "tertiary_link",
    "secondary", "secondary_link", "primary", "primary_link", "track",
})


@dataclass(frozen=True)
class WalkResult:
    node_ids: tuple[str, ...]
    coordinates: tuple[tuple[float, float], ...]
    distance_m: float
    duration_s: int
    nodes_expanded: int


@dataclass
class WalkGraph:
    nodes: dict[str, tuple[float, float]] = field(default_factory=dict)  # node_id -> (lat, lon)
    adj: dict[str, list[tuple[str, float]]] = field(default_factory=lambda: defaultdict(list))  # (to_id, dist_m)
    _index: GridIndex[str] | None = None

    def add_node(self, node_id: str, lat: float, lon: float) -> None:
        self.nodes[node_id] = (lat, lon)
        self._index = None

    def add_edge(self, u: str, v: str, bidirectional: bool = True) -> None:
        d = haversine_m(*self.nodes[u], *self.nodes[v])
        self.adj[u].append((v, d))
        if bidirectional:
            self.adj[v].append((u, d))

    def nearest_node(self, lat: float, lon: float, max_radius_m: float = 500.0) -> str | None:
        if self._index is None:
            idx: GridIndex[str] = GridIndex(cell_size_m=200.0)
            for nid, (la, lo) in self.nodes.items():
                idx.insert(nid, la, lo)
            self._index = idx
        hits = self._index.nearest(lat, lon, k=1, max_radius_m=max_radius_m)
        return hits[0].key if hits else None


def build_walk_graph_from_osm(elements: list[dict]) -> WalkGraph:
    """Build a :class:`WalkGraph` from Overpass JSON elements (nodes + walkable ways)."""
    raw_nodes = {e["id"]: (e["lat"], e["lon"]) for e in elements if e.get("type") == "node" and "lat" in e}
    g = WalkGraph()
    for e in elements:
        if e.get("type") != "way":
            continue
        tags = e.get("tags", {})
        if tags.get("highway") not in WALKABLE_HIGHWAYS:
            continue
        if tags.get("foot") == "no" or tags.get("access") == "private":
            continue
        nids = [n for n in e.get("nodes", []) if n in raw_nodes]
        for nid in nids:
            sid = str(nid)
            if sid not in g.nodes:
                g.nodes[sid] = raw_nodes[nid]
        for a, b in zip(nids, nids[1:]):
            g.add_edge(str(a), str(b), bidirectional=True)
    return g


def astar_walk(
    graph: WalkGraph,
    start_id: str,
    goal_id: str,
    walk_speed_mps: float = WALK_SPEED_MPS,
    use_heuristic: bool = True,
) -> WalkResult | None:
    """Run A* (or Dijkstra when ``use_heuristic=False``) between two nodes."""
    if start_id not in graph.nodes or goal_id not in graph.nodes:
        return None
    goal_lat, goal_lon = graph.nodes[goal_id]

    def h(nid: str) -> float:
        if not use_heuristic:
            return 0.0
        return haversine_m(*graph.nodes[nid], goal_lat, goal_lon)

    g_score: dict[str, float] = {start_id: 0.0}
    came_from: dict[str, str] = {}
    pq: list[tuple[float, float, str]] = [(h(start_id), 0.0, start_id)]
    expanded = 0

    while pq:
        _, g_u, u = heapq.heappop(pq)
        if g_u > g_score.get(u, float("inf")):
            continue
        expanded += 1
        if u == goal_id:
            path = [u]
            while u in came_from:
                u = came_from[u]
                path.append(u)
            path.reverse()
            coords = tuple(graph.nodes[n] for n in path)
            return WalkResult(
                node_ids=tuple(path),
                coordinates=coords,
                distance_m=round(g_u, 1),
                duration_s=math.ceil(g_u / walk_speed_mps),
                nodes_expanded=expanded,
            )
        for v, dist_m in graph.adj.get(u, ()):
            ng = g_u + dist_m
            if ng < g_score.get(v, float("inf")):
                g_score[v] = ng
                came_from[v] = u
                heapq.heappush(pq, (ng + h(v), ng, v))
    return None
