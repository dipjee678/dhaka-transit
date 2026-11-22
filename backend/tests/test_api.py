"""Phase 3 tests: FastAPI REST API endpoints and OpenAPI docs."""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path):
    cfg = Settings(db_path=tmp_path / "api_test.sqlite")
    app = create_app(cfg)
    with TestClient(app) as tc:
        yield tc


def test_root_health_and_openapi(client):
    r = client.get("/api")
    assert r.status_code == 200 and r.json()["docs"] == "/docs"

    # All 5 frontend pages + CSS + JS modules serve cleanly from the app
    for page in ("/", "/index.html", "/map.html", "/results.html", "/dataset.html", "/about.html"):
        pr = client.get(page)
        assert pr.status_code == 200 and "Dhaka Transit" in pr.text
    for asset in ("/css/styles.css", "/js/api.js", "/js/map.js", "/js/search.js", "/js/map_page.js", "/js/results.js", "/js/dataset.js"):
        assert client.get(asset).status_code == 200

    h = client.get("/api/health").json()
    assert h["status"] == "ok"
    assert h["stations"] == 16
    assert h["boarding_stops"] == 32
    assert h["routes"] == 1
    assert h["osm_bus_stop_inventory"] == 1023
    assert h["graph"]["stop_nodes"] == 32

    assert client.get("/openapi.json").status_code == 200
    assert client.get("/docs").status_code == 200


def test_stops_search_nearby_and_detail(client):
    # All stations
    stations = client.get("/api/stops", params={"location_type": 1}).json()
    assert len(stations) == 16

    # English & Bangla search
    en = client.get("/api/stops", params={"q": "mirpur", "location_type": 1}).json()
    assert {s["stop_id"] for s in en} == {"mrt6_mirpur_10", "mrt6_mirpur_11"}
    bn = client.get("/api/stops", params={"q": "মিরপুর-১০", "location_type": 1}).json()
    assert [s["stop_id"] for s in bn] == ["mrt6_mirpur_10"]

    # Detail with platforms
    det = client.get("/api/stops/mrt6_mirpur_10").json()
    assert det["stop_name_bn"] == "মিরপুর-১০"
    assert len(det["platforms"]) == 2
    assert det["provenance"]["category"] == "osm"
    assert client.get("/api/stops/no_such_stop").status_code == 404

    # Nearby search right at Mirpur 10 coordinates
    near = client.get("/api/stops/nearby", params={"lat": 23.8083, "lon": 90.3682, "radius_m": 1500}).json()
    assert near[0]["stop_id"] == "mrt6_mirpur_10"
    assert near[0]["distance_m"] < 50
    assert near[0]["walk_time_s"] >= 1

    # With include_platforms=True, both directional platforms appear
    near_p = client.get(
        "/api/stops/nearby", params={"lat": 23.8083, "lon": 90.3682, "radius_m": 200, "include_platforms": True}
    ).json()
    assert {s["stop_id"] for s in near_p} == {"mrt6_mirpur_10_d0", "mrt6_mirpur_10_d1"}


def test_osm_bus_stop_inventory(client):
    res = client.get("/api/stops/osm-inventory", params={"q": "Farmgate", "named_only": True}).json()
    assert len(res) >= 1
    assert all(r["source_category"] == "osm" for r in res)


def test_routes_detail_and_geojson(client):
    routes = client.get("/api/routes").json()
    assert len(routes) == 1 and routes[0]["route_id"] == "mrt6"
    assert routes[0]["route_color"] == "006E34"
    assert routes[0]["stop_count"] == 16

    det = client.get("/api/routes/mrt6").json()
    assert len(det["directions"]) == 2
    assert len(det["directions"][0]["stops"]) == 16
    assert det["directions"][0]["headsign"] == "Motijheel"
    assert len(det["directions"][0]["shape_coordinates"]) > 100
    assert det["directions"][0]["frequencies"][0]["provenance"]["category"] == "estimated"
    assert client.get("/api/routes/missing").status_code == 404

    gj = client.get("/api/routes/mrt6/geojson").json()
    assert gj["type"] == "FeatureCollection"
    lines = [f for f in gj["features"] if f["geometry"]["type"] == "LineString"]
    pts = [f for f in gj["features"] if f["geometry"]["type"] == "Point"]
    assert len(lines) == 2 and len(pts) == 16
    # Verify GeoJSON [lon, lat] order (Dhaka lon ~90, lat ~23)
    lon, lat = pts[0]["geometry"]["coordinates"]
    assert 90.0 < lon < 91.0 and 23.0 < lat < 25.0


def test_dataset_downloads_and_reports(client):
    ds = client.get("/api/dataset").json()
    assert ds["counts"]["stations"] == 16
    assert ds["counts"]["osm_bus_stop_inventory"] == 1023
    assert ds["validation_summary"]["errors"] == 0
    assert any(f["filename"] == "stops.txt" for f in ds["files"])

    # Zip download
    zresp = client.get("/api/dataset/download")
    assert zresp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zresp.content)) as zf:
        assert "stops.txt" in zf.namelist() and "provenance.txt" in zf.namelist()

    # Single file download + traversal protection
    fresp = client.get("/api/dataset/files/routes.txt")
    assert fresp.status_code == 200 and "mrt6" in fresp.text
    assert client.get("/api/dataset/files/..%2F..%2FREADME.md").status_code in (400, 404)
    assert client.get("/api/dataset/files/nope.txt").status_code == 404

    # Reports
    assert client.get("/api/dataset/validation").json()["summary"]["errors"] == 0
    assert client.get("/api/dataset/gaps").json()["metro"]["operational_stations_mapped"] == 16
    assert client.get("/api/dataset/evaluation").json()["real_dhaka"]["queries_run"] == 720


def test_contributions_submit_list_and_validate_bbox(client):
    assert client.get("/api/contributions").json() == []

    # Valid submission inside Dhaka
    payload = {
        "contributor_name": "Dhaka Commuter",
        "contact": "commuter@example.org",
        "kind": "new_stop",
        "payload": {"stop_name": "Agargaon Bus Stand", "lat": 23.7785, "lon": 90.3802, "notes": "East side"},
    }
    post = client.post("/api/contributions", json=payload)
    assert post.status_code == 201
    created = post.json()
    assert created["status"] == "pending"
    assert created["contributor_name"] == "Dhaka Commuter"

    listed = client.get("/api/contributions", params={"status": "pending"}).json()
    assert len(listed) == 1 and listed[0]["contribution_id"] == created["contribution_id"]

    # Reject coordinates outside Dhaka (swapped lat/lon)
    bad = {**payload, "payload": {"stop_name": "Swapped", "lat": 90.38, "lon": 23.77}}
    assert client.post("/api/contributions", json=bad).status_code == 422


def test_plan_and_compare_endpoints(client):
    # Mirpur 10 -> Motijheel by stop_id on a Saturday at 08:30
    resp = client.get(
        "/api/plan",
        params={"from_stop": "mrt6_mirpur_10", "to_stop": "mrt6_motijheel", "date": "2026-11-21", "time": "08:30"},
    )
    assert resp.status_code == 200
    body = resp.json()
    rec = body["recommended"]
    assert rec is not None
    assert rec["algorithm"] == "raptor"
    assert rec["transfers"] == 0
    assert rec["is_estimated"] is True
    assert rec["fare"]["total_bdt"] is None
    assert body["comparison"]["same_best_route"] is True
    assert any(l["mode"] == "metro" and len(l["geometry"]) > 50 for l in rec["legs"])

    # Coordinate query (offset ~200 m from Mirpur 10 & Motijheel -> includes access & egress walk legs)
    c_resp = client.get(
        "/api/plan",
        params={
            "from_lat": 23.8095, "from_lon": 90.3695,
            "to_lat": 23.7290, "to_lon": 90.4205,
            "date": "2026-11-21", "time": "09:00",
        },
    )
    assert c_resp.status_code == 200
    modes = [l["mode"] for l in c_resp.json()["recommended"]["legs"]]
    assert modes == ["walk", "metro", "walk"]

    # Side-by-side comparison endpoint
    cmp = client.get(
        "/api/plan/compare",
        params={"from_stop": "mrt6_mirpur_10", "to_stop": "mrt6_motijheel", "date": "2026-11-21", "time": "08:30"},
    )
    assert cmp.status_code == 200
    cdata = cmp.json()
    assert len(cdata["raptor_journeys"]) >= 1
    assert cdata["dijkstra_journey"]["algorithm"] == "dijkstra"
    assert cdata["comparison"]["same_best_route"] is True

    # Error handling: bad coordinates, unknown stop, bad time
    assert client.get("/api/plan", params={"from_lat": 90.0, "from_lon": 23.0, "to_stop": "mrt6_motijheel"}).status_code == 422
    assert client.get("/api/plan", params={"from_stop": "unknown", "to_stop": "mrt6_motijheel"}).status_code == 404
    assert client.get("/api/plan", params={"from_stop": "mrt6_mirpur_10", "to_stop": "mrt6_motijheel", "time": "99:99"}).status_code == 422
