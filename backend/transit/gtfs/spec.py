"""Machine-readable subset of the GTFS Schedule specification.

Reference: https://gtfs.org/documentation/schedule/reference/

Only the files used by Dhaka Transit are described. The validator (Phase 2)
and the reader/writer (Phase 3) are driven by these definitions, so the spec
lives in exactly one place.

Presence levels follow the official reference:
- REQUIRED      : column must exist and every value must be non-empty
- CONDITIONAL   : required in some situations (checked by validator rules)
- OPTIONAL      : may be omitted
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Presence(str, Enum):
    REQUIRED = "required"
    CONDITIONAL = "conditional"
    OPTIONAL = "optional"


R, C, O = Presence.REQUIRED, Presence.CONDITIONAL, Presence.OPTIONAL


@dataclass(frozen=True)
class FileSpec:
    filename: str
    file_presence: Presence
    fields: dict[str, Presence]
    primary_key: tuple[str, ...]

    @property
    def required_fields(self) -> list[str]:
        return [f for f, p in self.fields.items() if p is Presence.REQUIRED]


GTFS_FILES: dict[str, FileSpec] = {
    "agency.txt": FileSpec(
        "agency.txt", R,
        {"agency_id": C, "agency_name": R, "agency_url": R, "agency_timezone": R,
         "agency_lang": O, "agency_phone": O, "agency_fare_url": O, "agency_email": O},
        ("agency_id",),
    ),
    "stops.txt": FileSpec(
        "stops.txt", R,
        {"stop_id": R, "stop_code": O, "stop_name": C, "tts_stop_name": O, "stop_desc": O,
         "stop_lat": C, "stop_lon": C, "zone_id": O, "stop_url": O, "location_type": O,
         "parent_station": C, "stop_timezone": O, "wheelchair_boarding": O,
         "level_id": O, "platform_code": O},
        ("stop_id",),
    ),
    "routes.txt": FileSpec(
        "routes.txt", R,
        {"route_id": R, "agency_id": C, "route_short_name": C, "route_long_name": C,
         "route_desc": O, "route_type": R, "route_url": O, "route_color": O,
         "route_text_color": O, "route_sort_order": O},
        ("route_id",),
    ),
    "trips.txt": FileSpec(
        "trips.txt", R,
        {"route_id": R, "service_id": R, "trip_id": R, "trip_headsign": O,
         "trip_short_name": O, "direction_id": O, "block_id": O, "shape_id": C,
         "wheelchair_accessible": O, "bikes_allowed": O},
        ("trip_id",),
    ),
    "stop_times.txt": FileSpec(
        "stop_times.txt", R,
        {"trip_id": R, "arrival_time": C, "departure_time": C, "stop_id": C,
         "stop_sequence": R, "stop_headsign": O, "pickup_type": O, "drop_off_type": O,
         "shape_dist_traveled": O, "timepoint": O},
        ("trip_id", "stop_sequence"),
    ),
    "calendar.txt": FileSpec(
        "calendar.txt", C,
        {"service_id": R, "monday": R, "tuesday": R, "wednesday": R, "thursday": R,
         "friday": R, "saturday": R, "sunday": R, "start_date": R, "end_date": R},
        ("service_id",),
    ),
    "calendar_dates.txt": FileSpec(
        "calendar_dates.txt", C,
        {"service_id": R, "date": R, "exception_type": R},
        ("service_id", "date"),
    ),
    "shapes.txt": FileSpec(
        "shapes.txt", O,
        {"shape_id": R, "shape_pt_lat": R, "shape_pt_lon": R, "shape_pt_sequence": R,
         "shape_dist_traveled": O},
        ("shape_id", "shape_pt_sequence"),
    ),
    "frequencies.txt": FileSpec(
        "frequencies.txt", O,
        {"trip_id": R, "start_time": R, "end_time": R, "headway_secs": R, "exact_times": O},
        ("trip_id", "start_time"),
    ),
    "fare_attributes.txt": FileSpec(
        "fare_attributes.txt", O,
        {"fare_id": R, "price": R, "currency_type": R, "payment_method": R,
         "transfers": R, "agency_id": C, "transfer_duration": O},
        ("fare_id",),
    ),
    "fare_rules.txt": FileSpec(
        "fare_rules.txt", O,
        {"fare_id": R, "route_id": O, "origin_id": O, "destination_id": O, "contains_id": O},
        ("fare_id", "route_id", "origin_id", "destination_id", "contains_id"),
    ),
    "transfers.txt": FileSpec(
        "transfers.txt", O,
        {"from_stop_id": C, "to_stop_id": C, "from_route_id": O, "to_route_id": O,
         "from_trip_id": O, "to_trip_id": O, "transfer_type": R, "min_transfer_time": O},
        ("from_stop_id", "to_stop_id", "from_route_id", "to_route_id", "from_trip_id", "to_trip_id"),
    ),
    "feed_info.txt": FileSpec(
        "feed_info.txt", O,  # "Conditionally Required" when translations.txt is present
        {"feed_publisher_name": R, "feed_publisher_url": R, "feed_lang": R,
         "default_lang": O, "feed_start_date": O, "feed_end_date": O,
         "feed_version": O, "feed_contact_email": O, "feed_contact_url": O},
        (),
    ),
}

# Files the user asked for explicitly (project requirement), on top of the
# spec's own required set.
PROJECT_REQUIRED_FILES = (
    "agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt",
    "calendar.txt", "calendar_dates.txt", "shapes.txt",
)

# Non-standard extension files published with the feed. GTFS consumers ignore
# unknown files, so the feed remains spec-compliant.
EXTENSION_FILES = ("provenance.txt", "data_sources.txt")

# GTFS route_type values we use.
ROUTE_TYPE_METRO = 1   # "Subway, Metro" — MRT Line 6
ROUTE_TYPE_BUS = 3

# ---------------------------------------------------------------------------
# Value-format helpers
# ---------------------------------------------------------------------------
_TIME_RE = re.compile(r"^(\d{1,3}):([0-5]\d):([0-5]\d)$")
_DATE_RE = re.compile(r"^\d{4}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
_COLOR_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


def parse_gtfs_time(value: str) -> int:
    """Convert GTFS 'H:MM:SS' / 'HH:MM:SS' to seconds since "noon minus 12h"
    of the service day (the GTFS definition of the time origin).

    GTFS times may exceed 24:00:00 for trips running past midnight
    (e.g. '25:10:00'), so the result is not taken modulo one day.
    """
    m = _TIME_RE.match(value.strip())
    if not m:
        raise ValueError(f"invalid GTFS time: {value!r}")
    h, mi, s = (int(g) for g in m.groups())
    return h * 3600 + mi * 60 + s


def format_gtfs_time(seconds: int) -> str:
    """Inverse of :func:`parse_gtfs_time` (always zero-padded HH:MM:SS)."""
    if seconds < 0:
        raise ValueError("seconds must be non-negative")
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def is_valid_gtfs_date(value: str) -> bool:
    """YYYYMMDD with a plausible month/day (calendar validity checked later)."""
    return bool(_DATE_RE.match(value))


def is_valid_color(value: str) -> bool:
    return bool(_COLOR_RE.match(value))
