# Dhaka Transit — Data Coverage & Gap Report

- Generated: 2026-11-22T23:55:17
- OSM snapshot: `2026-11-16` (Greater Dhaka bbox `23.60,90.20,24.05,90.60`)

## 1. MRT Line 6 Status

- Operational stations mapped: **16 / 16** (cross-checked against DMTCL official station page).
- Planned/non-operational stations excluded: কমলাপুর.
- OSM track geometry length: `mrt6_d0` = 18.97 km, `mrt6_d1` = 18.97 km.
- Timetable provenance: `[('estimated', 'unverified')]` — currently based on secondary web reports (tagged `estimated` / `unverified`). **Gap:** obtain official DMTCL machine-readable or transcribed timetable.
- Fares: **0 / 240** OD pairs verified. **Gap:** transcribe official DMTCL fare chart directly (third-party web tables were rejected after finding asymmetric fare contradictions between reverse station pairs).

### Candidate upstream OpenStreetMap fixes (MRT Line 6)

- Station #2 (উত্তরা সেন্টার): OSM stop_area Bangla name is 'উত্তরা মধ্য' (differs from DMTCL) — candidate OSM fix
- Station #10 (বিজয় সরণি): English = transliteration of the official Bangla name; OSM has 'Bijoy Sarawni' (stop_area) / 'Bijoy Shoroni' (stops) — candidate OSM fix

## 2. Dhaka Bus Network Gap in OpenStreetMap

- `route=bus` relations found in OSM across Greater Dhaka: **1** (usable for GTFS: **0**).
- Curated bus routes currently in GTFS: **0**.

| OSM Relation | Ref | Name | Operator | Stop members | Way members | Issues |
|---|---|---|---|---:|---:|---|
| `r18732475` | 08 | Gabtoli to Jatrabari | Gabtoli Express | 0 | 1 | only 0 stop/platform member(s); only 1 way member(s) |

### OSM Bus Stop Inventory (`data/curated/bus_stops.csv`)

- Total bus-stop elements in Greater Dhaka: **1023**
  - Breakdown by kind: {'platform': 434, 'bus_station': 120, 'bus_stop': 152, 'stop_position': 317}
  - Unnamed elements: **48** (4.7%)
  - With `name:bn`: **45** (4.4%)
  - PTv2 `platform` + `stop_position` pairs within 30 m (expected): 1
  - Potential duplicate pairs within 30 m (same name or <= 3 m, excluding PTv2 pairs): **62**

### External Source Audit (Rejected for Direct Ingestion)

- `imamhossain94/dhaka-city-local-bus-json-data` (GitHub): **no licence** (`license: None`), scraped from `busroutebd.com`, last updated 2021-11-14. Cannot legally be redistributed under ODbL.
- Third-party MRT-6 fare blogs (`metrodhaka.com`, `dhakametro.online`): contained asymmetric OD prices (e.g. Mirpur 11 ↔ Motijheel and Kazipara ↔ Motijheel differed by direction); excluded pending official DMTCL chart.

## 3. Multimodal Interchange Potential (Bus Stops Near MRT-6 Stations)

Ranking MRT-6 stations by nearby OSM bus-stop density identifies the highest-impact corridors for Phase 2 field surveys (top 5: **Motijheel** (15), **Farmgate** (9), **Mirpur 10** (8), **Dhaka University** (6), **Bangladesh Secretariat** (5)).

| # | MRT-6 Station | OSM bus stops ≤ 250 m | ≤ 500 m | Closest named OSM stops |
|---:|---|---:|---:|---|
| 1 | Uttara North | 0 | 0 | none |
| 2 | Uttara Center | 0 | 0 | none |
| 3 | Uttara South | 0 | 0 | none |
| 4 | Pallabi | 1 | 1 | Pallabi Bus Station (241 m) |
| 5 | Mirpur 11 | 1 | 2 | Hall Purobi (6 m); Mirpur 11 Bus Station (391 m) |
| 6 | Mirpur 10 | 6 | 8 | Mirpur 10 Circle Bus stop (94 m); Mirpur 10 Bus Station (107 m); Mirpur 10 Circle, Bus stop (167 m) |
| 7 | Kazipara | 0 | 1 | Kazipara Bus Stand (258 m) |
| 8 | Shewrapara | 1 | 1 | Shewrapara (53 m) |
| 9 | Agargaon | 0 | 0 | none |
| 10 | Bijoy Sarani | 1 | 1 | Ziya Park Bus stop (232 m) |
| 11 | Farmgate | 2 | 9 | Leguna Tempu Stand (130 m); Tejgaon College Bus Stop (170 m); Indira Road Bus Stop (253 m) |
| 12 | Karwan Bazar | 2 | 2 | Kawran Bazaar Bus stop (110 m); Basnhundhara City South Bus Stop, Panthapath, Dhaka (155 m) |
| 13 | Shahbagh | 1 | 4 | Shahbag P.G. Hospital Bus stop, Shahbagh Rd, Dhaka (141 m); Poribagh Bus stop (400 m); Kataban Bus Stop (409 m) |
| 14 | Dhaka University | 3 | 6 | Varsity Bus Stops, Nilkhet Rd, Dhaka (186 m); Rokeya Hall (220 m); IML (300 m) |
| 15 | Bangladesh Secretariat | 3 | 5 | Press Club Bus stop (20 m); Pressclub Bus stop (65 m); Paltan Bus stop (239 m) |
| 16 | Motijheel | 2 | 15 | BRTC Bus Counter Bus stop (22 m); City Centre(Janata Bank) (150 m); Motijheel Junction Bus Station (292 m) |
