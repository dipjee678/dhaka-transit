"""Provenance vocabulary shared by the pipeline, the database and the API.

Every record in the Dhaka Transit dataset is tagged with *where it came from*
(category) and *whether a human has checked it* (status). The UI uses these
tags to label values, e.g. "Estimated" next to a bus travel time.
"""
from __future__ import annotations

from enum import Enum


class SourceCategory(str, Enum):
    OFFICIAL = "official"      # published by DMTCL, DTCA, BRTA, BRTC, ...
    OSM = "osm"                # derived from OpenStreetMap (ODbL)
    COMMUNITY = "community"    # field surveys / volunteer submissions
    ESTIMATED = "estimated"    # computed from assumptions, not observed

    @property
    def is_estimate(self) -> bool:
        return self is SourceCategory.ESTIMATED


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    DISPUTED = "disputed"


# Ranking used when two sources disagree about the same field: higher wins,
# unless a reviewer explicitly overrides it.
SOURCE_PRIORITY: dict[SourceCategory, int] = {
    SourceCategory.OFFICIAL: 4,
    SourceCategory.COMMUNITY: 3,   # field-verified on the ground
    SourceCategory.OSM: 2,
    SourceCategory.ESTIMATED: 1,
}


def weakest(categories: list[SourceCategory]) -> SourceCategory:
    """The least reliable category in a set, used to label a whole journey.

    A journey is only as trustworthy as its weakest leg: one estimated bus
    segment makes the whole journey 'estimated'.
    """
    if not categories:
        raise ValueError("no categories given")
    return min(categories, key=lambda c: SOURCE_PRIORITY[c])
