"""Shared transit state loaded once at FastAPI startup.

Holds:
- typed :class:`TransitFeed`
- spatial :class:`GridIndex` for boarding stops and top-level stops/stations
- precomputed :class:`Footpath` list
- :class:`FrequencyGraph` (for Dijkstra)
- cached :class:`Timetable` instances keyed by active service_id set (for RAPTOR)
- OSM bus-stop inventory loaded from ``data/curated/bus_stops.csv``
- SQLite connection helper (auto-initializes the DB from GTFS if missing)
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

from app.config import ROOT, Settings
from app.schemas.models import ProvenanceOut, StopOut
from transit.geo import GridIndex
from transit.graph.footpaths import Footpath, build_footpaths
from transit.graph.frequency_graph import FrequencyGraph, build_frequency_graph
from transit.graph.timetable import Timetable, build_timetable
from transit.gtfs.model import Provenance, Stop, TransitFeed

if str(ROOT / "database") not in sys.path:
    sys.path.insert(0, str(ROOT / "database"))


class TransitService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.feed: TransitFeed = TransitFeed.from_path(settings.gtfs_dir)
        self.footpaths: list[Footpath] = build_footpaths(
            self.feed.boarding_stops,
            max_walk_m=settings.max_transfer_walk_m,
        )
        self.frequency_graph: FrequencyGraph = build_frequency_graph(self.feed, self.footpaths)
        self._timetable_cache: dict[frozenset[str], Timetable] = {}

        # Spatial indexes:
        # 1) boarding_index: every location_type == 0 stop (used by routing engines)
        # 2) place_index: stations (location_type == 1) + standalone bus stops (location_type == 0, no parent)
        self.boarding_index: GridIndex[str] = GridIndex(cell_size_m=400.0)
        self.place_index: GridIndex[str] = GridIndex(cell_size_m=400.0)
        for sid, s in self.feed.stops.items():
            if s.location_type == 0:
                self.boarding_index.insert(sid, s.stop_lat, s.stop_lon)
                if not s.parent_station:
                    self.place_index.insert(sid, s.stop_lat, s.stop_lon)
            elif s.location_type == 1:
                self.place_index.insert(sid, s.stop_lat, s.stop_lon)

        self.osm_bus_stops: list[dict] = self._load_osm_bus_inventory()
        self._ensure_database()

    def _ensure_database(self) -> None:
        if self.settings.db_path.exists():
            # Verify schema exists (e.g., contributions table present)
            con = sqlite3.connect(self.settings.db_path)
            try:
                has_tables = con.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='stops'"
                ).fetchone()[0]
                if has_tables:
                    return
            finally:
                con.close()
        from load_gtfs import load_gtfs_to_sqlite  # local module under database/
        load_gtfs_to_sqlite(self.settings.gtfs_dir, self.settings.db_path, self.settings.schema_path)

    def _load_osm_bus_inventory(self) -> list[dict]:
        path = self.settings.curated_dir / "bus_stops.csv"
        if not path.exists():
            return []
        out = []
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for r in csv.DictReader(fh):
                out.append({
                    "osm_id": r["osm_id"],
                    "name": r.get("name", ""),
                    "name_bn": r.get("name_bn", ""),
                    "lat": float(r["lat"]),
                    "lon": float(r["lon"]),
                    "kind": r.get("kind", "bus_stop"),
                    "shelter": r.get("shelter", ""),
                    "operator": r.get("operator", ""),
                    "source_category": r.get("source_category", "osm"),
                    "source_id": r.get("source_id", ""),
                    "status": r.get("status", "unverified"),
                    "notes": r.get("notes", ""),
                })
        return out

    def connect_db(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.settings.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def timetable_for_date(self, service_date: dt.date) -> Timetable:
        active = frozenset(self.feed.active_service_ids(service_date))
        if active not in self._timetable_cache:
            self._timetable_cache[active] = build_timetable(self.feed, self.footpaths, set(active))
        return self._timetable_cache[active]

    def read_report_json(self, filename: str) -> dict | None:
        path = self.settings.reports_dir / filename
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # --- Serialization helpers ---
    @staticmethod
    def prov_out(p: Provenance | None) -> ProvenanceOut | None:
        if p is None:
            return None
        return ProvenanceOut(
            category=p.category.value,
            status=p.status.value,
            source_id=p.source_id,
            verified_at=p.verified_at,
            notes=p.notes,
        )

    def stop_out(self, s: Stop) -> StopOut:
        return StopOut(
            stop_id=s.stop_id,
            stop_name=s.stop_name,
            stop_name_bn=s.stop_name_bn,
            stop_lat=s.stop_lat,
            stop_lon=s.stop_lon,
            location_type=s.location_type,
            parent_station=s.parent_station,
            zone_id=s.zone_id,
            stop_desc=s.stop_desc,
            routes=sorted(self.feed.routes_by_stop.get(s.stop_id, ())),
            provenance=self.prov_out(self.feed.provenance_for("stops", s.stop_id)),
        )
