"""Common journey model, generalized cost, shape slicing, and fare lookup.

Shared by Dijkstra, RAPTOR, and the unified multimodal planner so both
algorithms return identically structured, provenance-tagged journeys.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from transit.geo import haversine_m, project_on_polyline
from transit.gtfs.model import Stop, TransitFeed
from transit.provenance import SourceCategory, weakest


@dataclass(frozen=True)
class CostWeights:
    """Configurable generalized-cost weights (documented planning assumptions)."""
    in_vehicle_weight: float = 1.0
    wait_weight: float = 1.5
    walk_weight: float = 1.8
    transfer_penalty_s: int = 300        # 5 min equivalent per additional boarding
    max_access_walk_m: float = 1200.0
    max_direct_walk_m: float = 2500.0


@dataclass(frozen=True)
class Leg:
    mode: str                            # 'walk' | 'transfer' | 'metro' | 'bus'
    from_name: str
    from_lat: float
    from_lon: float
    to_name: str
    to_lat: float
    to_lon: float
    duration_s: int                      # ride or walk duration (excluding wait_s)
    distance_m: float = 0.0
    wait_s: int = 0
    from_stop_id: str | None = None
    to_stop_id: str | None = None
    departure_s: int | None = None
    arrival_s: int | None = None
    route_id: str | None = None
    route_short_name: str | None = None
    route_long_name: str | None = None
    route_color: str | None = None
    direction_id: int | None = None
    trip_id: str | None = None
    headsign: str | None = None
    intermediate_stops: tuple[dict, ...] = ()
    geometry: tuple[tuple[float, float], ...] = ()  # (lat, lon) pairs for Leaflet
    provenance: SourceCategory = SourceCategory.ESTIMATED

    @property
    def is_transit(self) -> bool:
        return self.mode not in ("walk", "transfer")

    @property
    def is_estimated(self) -> bool:
        return self.provenance.is_estimate


@dataclass(frozen=True)
class FareEstimate:
    total_bdt: float | None
    currency: str = "BDT"
    complete: bool = False
    leg_fares: tuple[dict, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Journey:
    algorithm: str                       # 'raptor' | 'dijkstra' | 'walk_only'
    legs: tuple[Leg, ...]
    total_duration_s: int
    total_walk_m: float
    total_walk_s: int
    total_wait_s: int
    total_in_vehicle_s: int
    transfers: int
    generalized_cost: float
    provenance: SourceCategory
    fare: FareEstimate
    departure_s: int | None = None
    arrival_s: int | None = None
    outside_service_hours: bool = False
    computation_ms: float = 0.0

    @property
    def is_estimated(self) -> bool:
        return self.provenance.is_estimate

    def signature(self, feed: TransitFeed) -> tuple:
        """Canonical route-leg signature used to de-duplicate RAPTOR and Dijkstra results."""
        sig = []
        for leg in self.legs:
            if not leg.is_transit:
                continue
            fs = feed.stops.get(leg.from_stop_id or "")
            ts = feed.stops.get(leg.to_stop_id or "")
            f_key = (fs.parent_station or fs.stop_id) if fs else leg.from_stop_id
            t_key = (ts.parent_station or ts.stop_id) if ts else leg.to_stop_id
            sig.append((leg.route_id, leg.direction_id, f_key, t_key))
        return tuple(sig)


# ---------------------------------------------------------------------------
# Geometry & fare helpers
# ---------------------------------------------------------------------------
def slice_route_geometry(
    feed: TransitFeed,
    shape_id: str,
    from_stop: Stop,
    to_stop: Stop,
    stop_coords: list[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    """Slice ``shapes.txt`` between ``from_stop`` and ``to_stop``, falling back to stop coords."""
    pts_obj = feed.shapes.get(shape_id) if shape_id else None
    if not pts_obj or len(pts_obj) < 2:
        return tuple(stop_coords)

    pts = [(p.lat, p.lon) for p in pts_obj]
    d_start, off_start = project_on_polyline(from_stop.stop_lat, from_stop.stop_lon, pts)
    d_end, off_end = project_on_polyline(to_stop.stop_lat, to_stop.stop_lon, pts)
    if d_end <= d_start or off_start > 500 or off_end > 500:
        return tuple(stop_coords)

    # Compute cumulative distance along shape points
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + haversine_m(*a, *b))

    sliced: list[tuple[float, float]] = [(from_stop.stop_lat, from_stop.stop_lon)]
    for p, c in zip(pts, cum):
        if d_start < c < d_end:
            sliced.append(p)
    sliced.append((to_stop.stop_lat, to_stop.stop_lon))
    return tuple(sliced)


def polyline_length_m(coords: tuple[tuple[float, float], ...]) -> float:
    return sum(haversine_m(*a, *b) for a, b in zip(coords, coords[1:]))


def compute_fare(feed: TransitFeed, legs: list[Leg]) -> FareEstimate:
    """Attach fares strictly from GTFS fare_rules/fare_attributes — never fabricate."""
    transit_legs = [leg for leg in legs if leg.is_transit]
    if not transit_legs:
        return FareEstimate(total_bdt=0.0, complete=True, note="Walking only (no transit fare).")

    leg_fares: list[dict] = []
    total = 0.0
    missing = 0
    currency = "BDT"

    for leg in transit_legs:
        fs = feed.stops.get(leg.from_stop_id or "")
        ts = feed.stops.get(leg.to_stop_id or "")
        orig_zone = fs.zone_id if fs else ""
        dest_zone = ts.zone_id if ts else ""

        matched_attr = None
        for rule in feed.fare_rules:
            if rule.route_id and rule.route_id != leg.route_id:
                continue
            if rule.origin_id and rule.origin_id != orig_zone:
                continue
            if rule.destination_id and rule.destination_id != dest_zone:
                continue
            attr = feed.fare_attributes.get(rule.fare_id)
            if attr is not None:
                matched_attr = attr
                break

        if matched_attr is None:
            missing += 1
            leg_fares.append({
                "route_id": leg.route_id, "route_short_name": leg.route_short_name,
                "price": None, "currency": currency, "status": "unavailable",
            })
        else:
            prov = feed.provenance_for("fare_attributes", matched_attr.fare_id)
            currency = matched_attr.currency_type
            total += matched_attr.price
            leg_fares.append({
                "route_id": leg.route_id, "route_short_name": leg.route_short_name,
                "fare_id": matched_attr.fare_id, "price": matched_attr.price,
                "currency": currency,
                "status": prov.status.value if prov else "unverified",
                "category": prov.category.value if prov else "estimated",
            })

    if missing > 0:
        return FareEstimate(
            total_bdt=None,
            currency=currency,
            complete=False,
            leg_fares=tuple(leg_fares),
            note=f"Fare unavailable for {missing} transit leg(s): verified fare data not yet loaded.",
        )
    return FareEstimate(
        total_bdt=round(total, 2),
        currency=currency,
        complete=True,
        leg_fares=tuple(leg_fares),
        note="Computed from GTFS fare rules.",
    )


def summarize_journey(
    *,
    algorithm: str,
    legs: list[Leg],
    feed: TransitFeed,
    weights: CostWeights,
    departure_s: int | None = None,
    arrival_s: int | None = None,
    outside_service_hours: bool = False,
    computation_ms: float = 0.0,
) -> Journey:
    """Compute totals, generalized cost, weakest provenance, and fare for a list of legs."""
    total_walk_m = round(sum(l.distance_m for l in legs if not l.is_transit), 1)
    total_walk_s = sum(l.duration_s for l in legs if not l.is_transit)
    total_wait_s = sum(l.wait_s for l in legs)
    total_in_veh = sum(l.duration_s for l in legs if l.is_transit)
    transit_count = sum(1 for l in legs if l.is_transit)
    transfers = max(0, transit_count - 1)

    if departure_s is not None and arrival_s is not None:
        total_duration_s = max(0, arrival_s - departure_s)
    else:
        total_duration_s = total_walk_s + total_wait_s + total_in_veh

    gen_cost = round(
        weights.in_vehicle_weight * total_in_veh
        + weights.wait_weight * total_wait_s
        + weights.walk_weight * total_walk_s
        + weights.transfer_penalty_s * transfers,
        2,
    )
    prov = weakest([l.provenance for l in legs]) if legs else SourceCategory.ESTIMATED
    fare = compute_fare(feed, legs)

    return Journey(
        algorithm=algorithm,
        legs=tuple(legs),
        total_duration_s=total_duration_s,
        total_walk_m=total_walk_m,
        total_walk_s=total_walk_s,
        total_wait_s=total_wait_s,
        total_in_vehicle_s=total_in_veh,
        transfers=transfers,
        generalized_cost=gen_cost,
        provenance=prov,
        fare=fare,
        departure_s=departure_s,
        arrival_s=arrival_s,
        outside_service_hours=outside_service_hours,
        computation_ms=round(computation_ms, 3),
    )
