"""Unified multimodal planner combining RAPTOR and Dijkstra.

Implements the combined query pipeline described in docs/PROJECT_PLAN.md §7.3:
1. Run RAPTOR when a departure time is given and transit service is running on
   the requested date -> Pareto set (earliest arrival vs number of transfers).
2. Run Dijkstra on the frequency graph -> typical expected-cost route (also
   acts as fallback when no time is provided or service has ended).
3. Merge, de-duplicate identical route sequences (preferring timed RAPTOR
   itineraries over untimed Dijkstra duplicates), rank by generalized cost,
   and return recommended + alternative journeys alongside algorithm comparison
   metrics and honest data-transparency warnings.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from transit.geo import GridIndex
from transit.graph.frequency_graph import FrequencyGraph
from transit.graph.timetable import Timetable
from transit.gtfs.model import TransitFeed
from transit.routing.dijkstra import plan_dijkstra
from transit.routing.journey import CostWeights, Journey
from transit.routing.raptor import plan_raptor


@dataclass(frozen=True)
class PlanResult:
    origin: dict
    destination: dict
    service_date: str
    departure_s: int | None
    algorithm_requested: str
    recommended: Journey | None
    alternatives: tuple[Journey, ...]
    comparison: dict
    warnings: tuple[str, ...]


def plan_journey(
    *,
    feed: TransitFeed,
    graph: FrequencyGraph,
    timetable: Timetable,
    boarding_index: GridIndex[str],
    from_lat: float,
    from_lon: float,
    to_lat: float,
    to_lon: float,
    from_name: str = "Origin",
    to_name: str = "Destination",
    service_date: dt.date,
    departure_s: int | None = None,
    algorithm: str = "combined",         # 'combined' | 'raptor' | 'dijkstra'
    allowed_modes: set[str] | None = None,
    max_rounds: int = 5,
    weights: CostWeights = CostWeights(),
) -> PlanResult:
    warnings: list[str] = []
    raptor_journeys: list[Journey] = []
    dijkstra_journey: Journey | None = None

    has_bus_routes = any(r.mode == "bus" for r in feed.routes.values())
    if not has_bus_routes and (allowed_modes is None or "bus" in allowed_modes):
        warnings.append(
            "Curated bus routes are not yet loaded in this GTFS feed; results currently use Metro Rail (MRT-6) and walking."
        )

    # 1. RAPTOR (schedule-based)
    run_raptor = algorithm in ("combined", "raptor") and departure_s is not None
    if algorithm == "raptor" and departure_s is None:
        # If caller explicitly asks for RAPTOR without a time, default to 08:30 and disclose it.
        departure_s = 8 * 3600 + 30 * 60
        run_raptor = True
        warnings.append("No departure time supplied for RAPTOR; defaulted to 08:30:00.")

    if run_raptor and departure_s is not None:
        if timetable.patterns:
            raptor_journeys = plan_raptor(
                timetable=timetable, feed=feed, boarding_index=boarding_index,
                from_lat=from_lat, from_lon=from_lon, to_lat=to_lat, to_lon=to_lon,
                departure_s=departure_s, from_name=from_name, to_name=to_name,
                max_rounds=max_rounds, weights=weights, allowed_modes=allowed_modes,
            )
        else:
            warnings.append(f"No scheduled transit service is active on {service_date.isoformat()}.")

    # Did RAPTOR fail to find a transit itinerary even though departure_s was provided?
    raptor_found_transit = any(j.algorithm == "raptor" for j in raptor_journeys)
    outside_hours = bool(run_raptor and not raptor_found_transit)

    # 2. Dijkstra (frequency-based expected travel time)
    if algorithm in ("combined", "dijkstra"):
        dijkstra_journey = plan_dijkstra(
            graph=graph, feed=feed, boarding_index=boarding_index,
            from_lat=from_lat, from_lon=from_lon, to_lat=to_lat, to_lon=to_lon,
            from_name=from_name, to_name=to_name,
            weights=weights, allowed_modes=allowed_modes,
            outside_service_hours=outside_hours,
        )
        if outside_hours and dijkstra_journey is not None and dijkstra_journey.algorithm == "dijkstra":
            warnings.append(
                "No scheduled departures found for the requested date/time (outside service hours); "
                "showing typical expected route from Dijkstra."
            )

    # 3. Merge & de-duplicate (prefer timed RAPTOR journeys when signatures match)
    merged: list[Journey] = []
    seen_sigs: set[tuple] = set()

    if algorithm == "raptor":
        candidates = list(raptor_journeys)
    elif algorithm == "dijkstra":
        candidates = [dijkstra_journey] if dijkstra_journey else []
    else:
        candidates = list(raptor_journeys)
        if dijkstra_journey is not None:
            candidates.append(dijkstra_journey)

    for j in candidates:
        sig = j.signature(feed)
        # Keep walk_only only if no transit journey exists, or if it's genuinely cheaper than transit
        if sig in seen_sigs:
            continue
        seen_sigs.add(sig)
        merged.append(j)

    # Rank: prefer active in-service journeys over outside-service-hours fallback, then by generalized cost.
    merged.sort(key=lambda j: (j.outside_service_hours, j.generalized_cost, j.total_duration_s))

    recommended = merged[0] if merged else None
    alternatives = tuple(merged[1:])

    if any(j.is_estimated for j in merged):
        warnings.append(
            "Travel durations include estimated values (marked Estimated) pending official timetable verification."
        )

    # 4. Side-by-side comparison block (Dijkstra vs RAPTOR)
    best_raptor = min(raptor_journeys, key=lambda j: j.generalized_cost) if raptor_journeys else None
    same_route = (
        best_raptor is not None
        and dijkstra_journey is not None
        and best_raptor.signature(feed) == dijkstra_journey.signature(feed)
    )
    comparison = {
        "raptor": (
            {
                "found": True,
                "pareto_count": len(raptor_journeys),
                "computation_ms": best_raptor.computation_ms,
                "total_duration_s": best_raptor.total_duration_s,
                "transfers": best_raptor.transfers,
                "generalized_cost": best_raptor.generalized_cost,
                "departure_s": best_raptor.departure_s,
                "arrival_s": best_raptor.arrival_s,
            }
            if best_raptor
            else {"found": False, "pareto_count": 0}
        ),
        "dijkstra": (
            {
                "found": True,
                "computation_ms": dijkstra_journey.computation_ms,
                "total_duration_s": dijkstra_journey.total_duration_s,
                "transfers": dijkstra_journey.transfers,
                "generalized_cost": dijkstra_journey.generalized_cost,
                "outside_service_hours": dijkstra_journey.outside_service_hours,
            }
            if dijkstra_journey
            else {"found": False}
        ),
        "same_best_route": same_route,
    }

    return PlanResult(
        origin={"name": from_name, "lat": from_lat, "lon": from_lon},
        destination={"name": to_name, "lat": to_lat, "lon": to_lon},
        service_date=service_date.isoformat(),
        departure_s=departure_s,
        algorithm_requested=algorithm,
        recommended=recommended,
        alternatives=alternatives,
        comparison=comparison,
        warnings=tuple(warnings),
    )
