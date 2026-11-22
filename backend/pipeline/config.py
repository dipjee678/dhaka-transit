"""Pipeline settings and documented assumptions.

Every assumption here produces values tagged ``estimated`` in the feed.
"""
from __future__ import annotations

FEED_PUBLISHER_NAME = "Dhaka Transit (open data project)"
# TODO: replace with the public GitHub repository URL once it exists.
FEED_PUBLISHER_URL = "https://github.com/dhaka-transit/dhaka-transit"
FEED_LANG = "en"
FEED_VERSION = "0.2.0-phase2"
FEED_START_DATE = "20261116"
FEED_END_DATE = "20271115"

TIMEZONE = "Asia/Dhaka"

METRO_AGENCY = {
    "agency_id": "DMTCL",
    "agency_name": "Dhaka Mass Transit Company Limited",
    "agency_url": "https://dmtcl.gov.bd",
    "agency_timezone": TIMEZONE,
    "agency_lang": "bn",
}
METRO_ROUTE = {
    "route_id": "mrt6",
    "agency_id": "DMTCL",
    "route_short_name": "MRT-6",
    "route_long_name": "MRT Line 6: Uttara North - Motijheel",
    "route_type": 1,   # Subway / Metro
    "route_color": "006E34",      # from OSM route relation r15060935
    "route_text_color": "FFFFFF",
}
METRO_ROUTE_NAME_BN = "এমআরটি লাইন ৬"

# Used ONLY when a surveyed segment time is missing for a bus route.
BUS_ASSUMED_SPEED_KMH = 15.0      # assumption, not measured; tagged 'estimated'
BUS_DETOUR_FACTOR = 1.3           # straight line -> road distance
