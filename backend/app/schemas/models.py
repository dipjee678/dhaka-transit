"""Pydantic v2 models for the Dhaka Transit REST API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from transit.geo import in_bbox


class ProvenanceOut(BaseModel):
    category: Literal["official", "osm", "community", "estimated"]
    status: Literal["verified", "unverified", "disputed"]
    source_id: str = ""
    verified_at: str = ""
    notes: str = ""


class StopOut(BaseModel):
    stop_id: str
    stop_name: str
    stop_name_bn: str = ""
    stop_lat: float
    stop_lon: float
    location_type: int
    parent_station: str = ""
    zone_id: str = ""
    stop_desc: str = ""
    routes: list[str] = Field(default_factory=list)
    provenance: ProvenanceOut | None = None


class StopDetailOut(StopOut):
    platforms: list[StopOut] = Field(default_factory=list)
    parent: StopOut | None = None


class NearbyStopOut(StopOut):
    distance_m: float
    walk_distance_m: float
    walk_time_s: int


class OsmBusStopOut(BaseModel):
    osm_id: str
    name: str
    name_bn: str = ""
    lat: float
    lon: float
    kind: str
    shelter: str = ""
    operator: str = ""
    source_category: str = "osm"
    source_id: str = ""
    status: str = "unverified"
    notes: str = ""


class RouteOut(BaseModel):
    route_id: str
    agency_id: str
    agency_name: str = ""
    route_short_name: str
    route_long_name: str
    route_long_name_bn: str = ""
    route_type: int
    mode: str
    route_color: str = ""
    route_text_color: str = ""
    stop_count: int = 0
    provenance: ProvenanceOut | None = None


class RouteFrequencyOut(BaseModel):
    service_id: str
    days: list[str]
    start_time: str
    end_time: str
    headway_secs: int
    exact_times: int
    provenance: ProvenanceOut | None = None


class RouteDirectionOut(BaseModel):
    direction_id: int
    headsign: str
    shape_id: str = ""
    stops: list[StopOut]
    frequencies: list[RouteFrequencyOut] = Field(default_factory=list)
    shape_coordinates: list[list[float]] = Field(
        default_factory=list, description="Ordered [lat, lon] coordinates for Leaflet polylines"
    )


class RouteDetailOut(RouteOut):
    directions: list[RouteDirectionOut]


class DatasetFileOut(BaseModel):
    filename: str
    rows: int
    size_bytes: int


class DatasetSummaryOut(BaseModel):
    feed_version: str
    publisher_name: str
    feed_start_date: str
    feed_end_date: str
    license_code: str = "MIT"
    license_data: str = "ODbL-1.0"
    attribution: str
    counts: dict[str, int]
    provenance_by_category: dict[str, int]
    provenance_by_status: dict[str, int]
    files: list[DatasetFileOut]
    data_sources: list[dict[str, Any]]
    validation_summary: dict[str, Any] | None = None
    gap_summary: dict[str, Any] | None = None


class ContributionIn(BaseModel):
    contributor_name: str = Field(..., min_length=2, max_length=100)
    contact: str = Field("", max_length=120)
    kind: Literal["new_stop", "stop_fix", "new_route", "route_fix", "travel_time", "headway", "other"]
    payload: dict[str, Any] = Field(..., description="Structured details (e.g. stop_name, lat, lon, route_name, notes)")

    @field_validator("payload")
    @classmethod
    def check_dhaka_coords_if_present(cls, v: dict[str, Any]) -> dict[str, Any]:
        if "lat" in v or "lon" in v:
            if "lat" not in v or "lon" not in v:
                raise ValueError("both 'lat' and 'lon' must be provided together")
            lat, lon = float(v["lat"]), float(v["lon"])
            if not in_bbox(lat, lon):
                raise ValueError(f"coordinates ({lat}, {lon}) are outside the Greater Dhaka bounding box")
        return v


class ContributionOut(BaseModel):
    contribution_id: int
    contributor_name: str
    kind: str
    payload: dict[str, Any]
    status: str
    submitted_at: str


class HealthOut(BaseModel):
    status: str = "ok"
    feed_version: str
    stations: int
    boarding_stops: int
    routes: int
    osm_bus_stop_inventory: int
    graph: dict[str, Any]
    timetable_sample: dict[str, Any]


# ---------------------------------------------------------------------------
# Phase 4: Multimodal Route Planning schemas
# ---------------------------------------------------------------------------
class LegOut(BaseModel):
    mode: str
    from_name: str
    from_lat: float
    from_lon: float
    from_stop_id: str | None = None
    to_name: str
    to_lat: float
    to_lon: float
    to_stop_id: str | None = None
    duration_s: int
    wait_s: int = 0
    distance_m: float = 0.0
    departure_s: int | None = None
    arrival_s: int | None = None
    departure_time: str | None = None
    arrival_time: str | None = None
    route_id: str | None = None
    route_short_name: str | None = None
    route_long_name: str | None = None
    route_color: str | None = None
    direction_id: int | None = None
    trip_id: str | None = None
    headsign: str | None = None
    intermediate_stops: list[dict[str, Any]] = Field(default_factory=list)
    geometry: list[list[float]] = Field(default_factory=list, description="Ordered [lat, lon] points for Leaflet")
    provenance: str
    is_estimated: bool


class FareEstimateOut(BaseModel):
    total_bdt: float | None = None
    currency: str = "BDT"
    complete: bool = False
    leg_fares: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class JourneyOut(BaseModel):
    algorithm: str
    total_duration_s: int
    total_walk_m: float
    total_walk_s: int
    total_wait_s: int
    total_in_vehicle_s: int
    transfers: int
    generalized_cost: float
    departure_s: int | None = None
    arrival_s: int | None = None
    departure_time: str | None = None
    arrival_time: str | None = None
    provenance: str
    is_estimated: bool
    outside_service_hours: bool = False
    computation_ms: float = 0.0
    fare: FareEstimateOut
    legs: list[LegOut]


class PlanResponseOut(BaseModel):
    origin: dict[str, Any]
    destination: dict[str, Any]
    service_date: str
    departure_time: str | None = None
    algorithm_requested: str
    recommended: JourneyOut | None = None
    alternatives: list[JourneyOut] = Field(default_factory=list)
    comparison: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class CompareResponseOut(BaseModel):
    origin: dict[str, Any]
    destination: dict[str, Any]
    service_date: str
    departure_time: str
    raptor_journeys: list[JourneyOut] = Field(default_factory=list)
    dijkstra_journey: JourneyOut | None = None
    comparison: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
