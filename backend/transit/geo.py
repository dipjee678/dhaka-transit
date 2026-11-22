"""Geographic utilities used throughout the routing core.

Everything here is pure-Python (stdlib only) so it runs anywhere, including
inside the SQLite prototype where PostGIS is not available.

Assumptions (documented, configurable, *not* measured Dhaka data):
- WALK_SPEED_MPS  : 1.25 m/s (~4.5 km/h), a commonly used planning value.
- DETOUR_FACTOR   : 1.3, ratio of street-network distance to straight-line
                    distance. Replaced by A* on the OSM walking network in a
                    later phase.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Generic, Hashable, Iterable, Iterator, TypeVar

EARTH_RADIUS_M = 6_371_008.8  # mean Earth radius (IUGG)

WALK_SPEED_MPS = 1.25
DETOUR_FACTOR = 1.3

# Rough bounding box of Greater Dhaka used as a sanity check for coordinates.
# (min_lat, min_lon, max_lat, max_lon) — deliberately generous; it only catches
# obvious errors such as swapped lat/lon or (0, 0) placeholders.
DHAKA_BBOX = (23.60, 90.20, 24.05, 90.60)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


def walk_distance_m(lat1: float, lon1: float, lat2: float, lon2: float,
                    detour_factor: float = DETOUR_FACTOR) -> float:
    """Estimated walking distance (straight line x detour factor)."""
    return haversine_m(lat1, lon1, lat2, lon2) * detour_factor


def walk_time_s(distance_m: float, speed_mps: float = WALK_SPEED_MPS) -> int:
    """Walking time in whole seconds (rounded up) for a given distance."""
    if distance_m < 0:
        raise ValueError("distance must be non-negative")
    if speed_mps <= 0:
        raise ValueError("speed must be positive")
    return math.ceil(distance_m / speed_mps)


def in_bbox(lat: float, lon: float, bbox: tuple[float, float, float, float] = DHAKA_BBOX) -> bool:
    """True if the point lies inside (min_lat, min_lon, max_lat, max_lon)."""
    min_lat, min_lon, max_lat, max_lon = bbox
    return min_lat <= lat <= max_lat and min_lon <= lon <= max_lon


def project_on_polyline(lat: float, lon: float, pts: list[tuple[float, float]]) -> tuple[float, float]:
    """Return ``(distance_along_polyline_m, perpendicular_offset_m)`` of the closest point."""
    kx = 111_320.0 * math.cos(math.radians(lat))
    ky = 110_540.0
    best = (float("inf"), 0.0)
    run = 0.0
    for (alat, alon), (blat, blon) in zip(pts, pts[1:]):
        seg = haversine_m(alat, alon, blat, blon)
        ax, ay = (alon - lon) * kx, (alat - lat) * ky
        bx, by = (blon - lon) * kx, (blat - lat) * ky
        dx, dy = bx - ax, by - ay
        l2 = dx * dx + dy * dy
        t = 0.0 if l2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / l2))
        off = math.hypot(ax + t * dx, ay + t * dy)
        if off < best[0]:
            best = (off, run + t * seg)
        run += seg
    return best[1], best[0]


K = TypeVar("K", bound=Hashable)


@dataclass(frozen=True)
class Nearby(Generic[K]):
    key: K
    distance_m: float


class GridIndex(Generic[K]):
    """Simple uniform-grid spatial index for radius / nearest queries.

    Dhaka fits in a small area, so a fixed grid (default ~500 m cells) is
    fast and dependency-free. PostGIS replaces this in production with a
    GiST index and the KNN operator ``<->``.
    """

    def __init__(self, cell_size_m: float = 500.0, ref_lat: float = 23.8):
        if cell_size_m <= 0:
            raise ValueError("cell_size_m must be positive")
        self._dlat = cell_size_m / 111_320.0
        self._dlon = cell_size_m / (111_320.0 * math.cos(math.radians(ref_lat)))
        self._cells: dict[tuple[int, int], list[tuple[K, float, float]]] = defaultdict(list)
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def _cell(self, lat: float, lon: float) -> tuple[int, int]:
        return (math.floor(lat / self._dlat), math.floor(lon / self._dlon))

    def insert(self, key: K, lat: float, lon: float) -> None:
        self._cells[self._cell(lat, lon)].append((key, lat, lon))
        self._size += 1

    def bulk_insert(self, items: Iterable[tuple[K, float, float]]) -> None:
        for key, lat, lon in items:
            self.insert(key, lat, lon)

    def _candidates(self, lat: float, lon: float, radius_m: float) -> Iterator[tuple[K, float, float]]:
        r_lat = math.ceil((radius_m / 111_320.0) / self._dlat)
        r_lon = math.ceil((radius_m / (111_320.0 * max(0.01, math.cos(math.radians(lat))))) / self._dlon)
        ci, cj = self._cell(lat, lon)
        for i in range(ci - r_lat, ci + r_lat + 1):
            for j in range(cj - r_lon, cj + r_lon + 1):
                yield from self._cells.get((i, j), ())

    def within(self, lat: float, lon: float, radius_m: float) -> list[Nearby[K]]:
        """All items within ``radius_m`` (straight-line), sorted by distance."""
        out = []
        for key, plat, plon in self._candidates(lat, lon, radius_m):
            d = haversine_m(lat, lon, plat, plon)
            if d <= radius_m:
                out.append(Nearby(key, d))
        out.sort(key=lambda n: n.distance_m)
        return out

    def nearest(self, lat: float, lon: float, k: int = 1, max_radius_m: float = 5_000.0) -> list[Nearby[K]]:
        """Up to ``k`` nearest items, searching outward to ``max_radius_m``."""
        radius = 500.0
        while True:
            found = self.within(lat, lon, radius)
            if len(found) >= k or radius >= max_radius_m:
                return found[:k]
            radius = min(radius * 2, max_radius_m)
