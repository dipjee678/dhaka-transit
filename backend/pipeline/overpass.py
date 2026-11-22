"""Minimal Overpass API client (stdlib only).

Respects the Overpass usage policy: one request at a time, an identifying
User-Agent, and a retry with back-off when the server is busy (HTTP 429/504).
Raw responses are saved unchanged so every derived value can be traced back
to a dated snapshot.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
USER_AGENT = "DhakaTransit/0.1 (open GTFS dataset; university project)"

# (south, west, north, east) — same box as transit.geo.DHAKA_BBOX
DHAKA_BBOX_OVERPASS = "23.60,90.20,24.05,90.60"


def run_query(query: str, retries: int = 3, timeout: int = 180) -> dict:
    """POST an Overpass QL query and return the decoded JSON."""
    last_err: Exception | None = None
    for attempt in range(retries):
        endpoint = ENDPOINTS[attempt % len(ENDPOINTS)]
        req = urllib.request.Request(
            endpoint,
            data=urllib.parse.urlencode({"data": query}).encode(),
            headers={"User-Agent": USER_AGENT},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code not in (429, 502, 503, 504):
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
        time.sleep(10 * (attempt + 1))
    raise RuntimeError(f"Overpass query failed after {retries} attempts: {last_err}")


def save_snapshot(data: dict, path: Path, query: str) -> None:
    """Save the raw response together with the query that produced it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    path.with_suffix(".overpassql").write_text(query, encoding="utf-8")
