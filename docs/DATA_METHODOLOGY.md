# Data Collection, Validation & Attribution Methodology

This document explains how the **Dhaka Transit** GTFS dataset (`data/gtfs/dhaka/`) is
collected, verified, built, and validated.

## 1. Core Principle: Provenance & Data Honesty

Every record in the dataset is tagged with two metadata dimensions stored in
`provenance.txt` (linked to `data_sources.txt`):

| Field | Values | Meaning |
|---|---|---|
| `category` | `official` | Directly published by a Bangladeshi transit authority (DMTCL, DTCA, BRTA, BRTC). |
| | `osm` | Derived from OpenStreetMap (ODbL 1.0) via dated Overpass snapshots. |
| | `community` | Field-surveyed by commuters/volunteers with GPS/observational evidence. |
| | `estimated` | Computed from documented assumptions (e.g. interpolated station times or assumed bus speed). |
| `status` | `verified` | Checked against an authoritative source or confirmed on-site by a reviewer. |
| | `unverified` | Ingested from a raw source or single submission; pending confirmation. |
| | `disputed` | Conflicting evidence reported; flagged for review. |

**Non-negotiable rules:**
1. **No fabricated transit data.** Missing routes, stops, timetables, or fares are left absent and logged in `data/reports/gap_report.md`.
2. **No licence-violating scraping.** Commercial maps (Google Maps) and unlicenced third-party bus scrapers (e.g., `imamhossain94/dhaka-city-local-bus-json-data`, which carries no open licence and was scraped from `busroutebd.com`) are never ingested.
3. **Estimates are always labelled.** Any calculated travel time or conservative headway carries `category=estimated` and `timepoint=0` (`exact_times=0` in `frequencies.txt`), so the API and UI display an explicit **"Estimated"** badge.

## 2. Pipeline Architecture

```text
  Raw snapshots (untouched, dated)       Curated CSVs (human-reviewed)          Published GTFS Feed
  data/raw/osm/YYYY-MM-DD/*.json   ───►  data/curated/*.csv               ───►  data/gtfs/dhaka/*.txt
  data/raw/official/YYYY-MM-DD/*   ───►  (with mandatory provenance cols) ───►  data/gtfs/dhaka/dhaka_gtfs.zip
  Volunteer CSV + GPX              ───►  pipeline.import_contribution     ───►  pipeline.validate_feed
```

1. **Snapshot (`pipeline.fetch_osm`)** — Queries Overpass API over Greater Dhaka (`23.60,90.20,24.05,90.60`) and saves raw JSON + the exact `.overpassql` query under `data/raw/osm/<YYYY-MM-DD>/`.
2. **Curate (`pipeline.curate_osm`)** — Deterministically converts OSM MRT Line 6 relations (`r15060935`, `r15060936`) + `stop_area` relations + official DMTCL station list (`data/curated/official/dmtcl_stations.csv`) into `metro_stations.csv` and `metro_shapes.csv`, and extracts the city-wide OSM bus-stop inventory into `bus_stops.csv`.
3. **Community Surveys (`pipeline.import_contribution`)** — Validates volunteer route surveys (`contributions/templates/route_template.csv`), snaps stops to existing OSM bus stops within 40 m (breaking ties by stop-name similarity), or adds new `community_stops.csv` entries, and appends curated bus route rows tagged `community` / `unverified`.
4. **Build GTFS (`pipeline.build_gtfs`)** — Produces all required GTFS Schedule files (`agency.txt`, `stops.txt`, `routes.txt`, `trips.txt`, `stop_times.txt`, `calendar.txt`, `calendar_dates.txt`, `shapes.txt`), plus `frequencies.txt`, `feed_info.txt`, `translations.txt` (Bangla names), optional `fare_attributes.txt` / `fare_rules.txt` (only when verified fares exist), and the provenance extensions (`provenance.txt`, `data_sources.txt`), packaged into `dhaka_gtfs.zip`.
5. **Validate (`pipeline.validate_feed`)** — Checks GTFS structural/referential integrity, coordinate validity inside Greater Dhaka, duplicate stops, monotonic times and shape distances, implausible segment speeds, stop-to-shape offsets, fare zone references, and 100% provenance coverage. Writes `data/reports/validation_report.{md,json}`.
6. **Gap Analysis (`pipeline.gap_report`)** — Quantifies OSM coverage gaps, duplicate clusters, candidate upstream OSM fixes, and nearby bus-stop density around each MRT-6 station (`data/reports/gap_report.{md,json}`).

## 3. Modelling Dhaka's Transit Modes

- **MRT Line 6 (Metro Rail):** Represented as `route_type=1`, with 16 parent stations (`location_type=1`) and 32 directional platforms (`location_type=0`, `parent_station` set). Track geometry is chained directly from OSM route ways (`18.97 km`). Until an official DMTCL timetable is transcribed, station-to-station times are distributed proportionally along track distance from the secondary-reported end-to-end duration (~38 min) and explicitly tagged `estimated` / `unverified` (`timepoint=0`, `exact_times=0`).
- **Dhaka City Buses:** Private buses in Dhaka operate on headways rather than fixed timetables. They are modelled using `frequencies.txt` (`exact_times=0`) and `stop_times.txt` (`timepoint=0`). Field-measured segment durations (`minutes_from_previous`) are used verbatim (`category=community`); unmeasured segments fall back to `haversine * 1.3 / (15 km/h)` and automatically downgrade the trip's provenance to `estimated`.

## 4. Field Survey Protocol (For Volunteers & Contributors)

Because OSM currently has **0 usable bus route relations** in Greater Dhaka (see `data/reports/gap_report.md`), field surveys along high-interchange MRT-6 corridors (Motijheel, Farmgate, Mirpur 10, Dhaka University, Bangladesh Secretariat) are the highest-value contribution:

1. Install **OsmAnd** or **GPSLogger** and enable GPX recording before boarding a bus.
2. At each stop where the bus boards/alights passengers, note the stop name (English & Bangla if known), GPS waypoint, and elapsed minutes since the previous stop.
3. Observe 2–3 departures of the same bus service at a terminus or major stop to record `observed_headway_min`.
4. Fill out a copy of `contributions/templates/route_template.csv` (or `stop_template.csv` for single stops), leaving unknown cells blank.
5. Dry-run and import locally:
   ```powershell
   cd backend
   python -m pipeline.import_contribution ..\path\to\my_survey.csv --dry-run
   python -m pipeline.import_contribution ..\path\to\my_survey.csv
   python -m pipeline.build_gtfs
   python -m pipeline.validate_feed
   ```
6. Open a pull request attaching the survey CSV and (optionally) anonymized GPX track. After a second reviewer checks the track, `status` is upgraded from `unverified` to `verified`.

## 5. Source Attribution & Upstream OSM Contributions

- **OpenStreetMap:** © OpenStreetMap contributors, available under the [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
- **DMTCL:** Official MRT Line 6 station list and Bangla names from [dmtcl.gov.bd](https://dmtcl.gov.bd/pages/static-pages/6922e038933eb65569e260f0).
- **Upstream OSM improvements identified in Phase 2:**
  - Station #2 (`r15058413`): OSM `name` is `উত্তরা মধ্য`, whereas DMTCL's official Bangla name is `উত্তরা সেন্টার`.
  - Station #10 (`r16007466` / stop nodes): OSM English spelling varies between `Bijoy Sarawni` and `Bijoy Shoroni` (standard transliteration: `Bijoy Sarani`).
  - Missing bus stops within 500 m of Uttara North, Uttara Center, Uttara South, and Agargaon MRT stations, plus 62 candidate duplicate bus-stop pairs across Dhaka.
