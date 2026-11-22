"""Phase 1 tests: geographic utilities."""
import math

import pytest

from transit.geo import (
    DHAKA_BBOX, GridIndex, haversine_m, in_bbox, walk_distance_m, walk_time_s,
)


def test_haversine_zero_distance():
    assert haversine_m(23.8, 90.4, 23.8, 90.4) == 0.0


def test_haversine_one_degree_latitude():
    # One degree of latitude is ~111.2 km on a spherical Earth.
    d = haversine_m(23.0, 90.0, 24.0, 90.0)
    assert d == pytest.approx(111_195, rel=1e-3)


def test_haversine_symmetric():
    a = haversine_m(23.70, 90.35, 23.88, 90.45)
    b = haversine_m(23.88, 90.45, 23.70, 90.35)
    assert a == pytest.approx(b)


def test_walk_distance_applies_detour():
    straight = haversine_m(23.80, 90.40, 23.81, 90.40)
    assert walk_distance_m(23.80, 90.40, 23.81, 90.40, detour_factor=1.3) == pytest.approx(straight * 1.3)


def test_walk_time_rounds_up_and_validates():
    assert walk_time_s(125.0, speed_mps=1.25) == 100
    assert walk_time_s(126.0, speed_mps=1.25) == 101
    with pytest.raises(ValueError):
        walk_time_s(-1)
    with pytest.raises(ValueError):
        walk_time_s(10, speed_mps=0)


def test_bbox_catches_swapped_and_null_island():
    assert in_bbox(23.8, 90.4)
    assert not in_bbox(90.4, 23.8)   # swapped lat/lon
    assert not in_bbox(0.0, 0.0)     # placeholder coordinates
    assert DHAKA_BBOX[0] < DHAKA_BBOX[2] and DHAKA_BBOX[1] < DHAKA_BBOX[3]


def _brute_within(points, lat, lon, r):
    return {k for k, plat, plon in points if haversine_m(lat, lon, plat, plon) <= r}


def test_grid_index_matches_brute_force():
    # Synthetic grid of test points (not real stops) every ~0.002 deg.
    points = [(f"p{i}_{j}", 23.75 + i * 0.002, 90.38 + j * 0.002) for i in range(30) for j in range(30)]
    idx = GridIndex(cell_size_m=300)
    idx.bulk_insert(points)
    assert len(idx) == len(points)
    for (qlat, qlon, r) in [(23.78, 90.41, 400), (23.75, 90.38, 250), (23.81, 90.44, 1200)]:
        got = [n.key for n in idx.within(qlat, qlon, r)]
        assert set(got) == _brute_within(points, qlat, qlon, r)
        dists = [n.distance_m for n in idx.within(qlat, qlon, r)]
        assert dists == sorted(dists)


def test_grid_index_nearest_expands_radius():
    idx = GridIndex(cell_size_m=500)
    idx.insert("far", 23.80, 90.43)       # ~3 km east of the query point
    res = idx.nearest(23.80, 90.40, k=1, max_radius_m=5000)
    assert [n.key for n in res] == ["far"]
    assert math.isclose(res[0].distance_m, haversine_m(23.80, 90.40, 23.80, 90.43))
    assert idx.nearest(23.80, 90.40, k=1, max_radius_m=1000) == []
