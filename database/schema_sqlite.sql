-- =====================================================================
-- Dhaka Transit — SQLite schema (development / prototype database)
-- ---------------------------------------------------------------------
-- Layer 1: GTFS core tables  (column names identical to the GTFS spec,
--          so import/export is lossless).
-- Layer 2: Project extension tables (provenance, sources, footpaths,
--          contributions, validation runs).
-- Keep this file in sync with schema_postgis.sql.
-- =====================================================================
PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Layer 1: GTFS core
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agency (
    agency_id        TEXT PRIMARY KEY,
    agency_name      TEXT NOT NULL,
    agency_url       TEXT NOT NULL,
    agency_timezone  TEXT NOT NULL DEFAULT 'Asia/Dhaka',
    agency_lang      TEXT,
    agency_phone     TEXT,
    agency_fare_url  TEXT
);

CREATE TABLE IF NOT EXISTS stops (
    stop_id             TEXT PRIMARY KEY,
    stop_code           TEXT,
    stop_name           TEXT,
    stop_name_bn        TEXT,             -- extension: Bangla name (also exported via translations.txt later)
    stop_desc           TEXT,
    stop_lat            REAL CHECK (stop_lat IS NULL OR stop_lat BETWEEN -90 AND 90),
    stop_lon            REAL CHECK (stop_lon IS NULL OR stop_lon BETWEEN -180 AND 180),
    zone_id             TEXT,
    location_type       INTEGER NOT NULL DEFAULT 0 CHECK (location_type BETWEEN 0 AND 4),
    parent_station      TEXT REFERENCES stops(stop_id),
    wheelchair_boarding INTEGER CHECK (wheelchair_boarding IN (0, 1, 2)),
    platform_code       TEXT,
    -- GTFS: name/lat/lon are required for location_type 0,1,2
    CHECK (location_type > 2 OR (stop_name IS NOT NULL AND stop_lat IS NOT NULL AND stop_lon IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_stops_latlon ON stops (stop_lat, stop_lon);
CREATE INDEX IF NOT EXISTS idx_stops_parent ON stops (parent_station);

CREATE TABLE IF NOT EXISTS routes (
    route_id          TEXT PRIMARY KEY,
    agency_id         TEXT REFERENCES agency(agency_id),
    route_short_name  TEXT,
    route_long_name   TEXT,
    route_long_name_bn TEXT,              -- extension
    route_desc        TEXT,
    route_type        INTEGER NOT NULL,   -- 1 = Subway/Metro (MRT-6), 3 = Bus
    route_url         TEXT,
    route_color       TEXT,
    route_text_color  TEXT,
    route_sort_order  INTEGER,
    CHECK (route_short_name IS NOT NULL OR route_long_name IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS calendar (
    service_id  TEXT PRIMARY KEY,
    monday      INTEGER NOT NULL CHECK (monday    IN (0, 1)),
    tuesday     INTEGER NOT NULL CHECK (tuesday   IN (0, 1)),
    wednesday   INTEGER NOT NULL CHECK (wednesday IN (0, 1)),
    thursday    INTEGER NOT NULL CHECK (thursday  IN (0, 1)),
    friday      INTEGER NOT NULL CHECK (friday    IN (0, 1)),
    saturday    INTEGER NOT NULL CHECK (saturday  IN (0, 1)),
    sunday      INTEGER NOT NULL CHECK (sunday    IN (0, 1)),
    start_date  TEXT NOT NULL CHECK (length(start_date) = 8),   -- YYYYMMDD
    end_date    TEXT NOT NULL CHECK (length(end_date) = 8)
);

-- calendar_dates may reference service_ids that exist only here (GTFS allows
-- a feed without calendar.txt), so no FK to calendar.
CREATE TABLE IF NOT EXISTS calendar_dates (
    service_id      TEXT NOT NULL,
    date            TEXT NOT NULL CHECK (length(date) = 8),
    exception_type  INTEGER NOT NULL CHECK (exception_type IN (1, 2)),  -- 1 added, 2 removed
    PRIMARY KEY (service_id, date)
);

CREATE TABLE IF NOT EXISTS shapes (
    shape_id             TEXT NOT NULL,
    shape_pt_lat         REAL NOT NULL,
    shape_pt_lon         REAL NOT NULL,
    shape_pt_sequence    INTEGER NOT NULL CHECK (shape_pt_sequence >= 0),
    shape_dist_traveled  REAL,
    PRIMARY KEY (shape_id, shape_pt_sequence)
);

CREATE TABLE IF NOT EXISTS trips (
    trip_id               TEXT PRIMARY KEY,
    route_id              TEXT NOT NULL REFERENCES routes(route_id),
    service_id            TEXT NOT NULL,
    trip_headsign         TEXT,
    trip_short_name       TEXT,
    direction_id          INTEGER CHECK (direction_id IN (0, 1)),
    block_id              TEXT,
    shape_id              TEXT,
    wheelchair_accessible INTEGER CHECK (wheelchair_accessible IN (0, 1, 2))
);
CREATE INDEX IF NOT EXISTS idx_trips_route ON trips (route_id);

CREATE TABLE IF NOT EXISTS stop_times (
    trip_id              TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
    arrival_time         TEXT,   -- HH:MM:SS, may exceed 24:00:00
    departure_time       TEXT,
    stop_id              TEXT NOT NULL REFERENCES stops(stop_id),
    stop_sequence        INTEGER NOT NULL CHECK (stop_sequence >= 0),
    stop_headsign        TEXT,
    pickup_type          INTEGER CHECK (pickup_type BETWEEN 0 AND 3),
    drop_off_type        INTEGER CHECK (drop_off_type BETWEEN 0 AND 3),
    shape_dist_traveled  REAL,
    timepoint            INTEGER CHECK (timepoint IN (0, 1)),  -- 0 = approximate (used for buses)
    PRIMARY KEY (trip_id, stop_sequence)
);
CREATE INDEX IF NOT EXISTS idx_stop_times_stop ON stop_times (stop_id);

CREATE TABLE IF NOT EXISTS frequencies (
    trip_id       TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
    start_time    TEXT NOT NULL,
    end_time      TEXT NOT NULL,
    headway_secs  INTEGER NOT NULL CHECK (headway_secs > 0),
    exact_times   INTEGER DEFAULT 0 CHECK (exact_times IN (0, 1)),
    PRIMARY KEY (trip_id, start_time)
);

CREATE TABLE IF NOT EXISTS fare_attributes (
    fare_id            TEXT PRIMARY KEY,
    price              REAL NOT NULL CHECK (price >= 0),
    currency_type      TEXT NOT NULL DEFAULT 'BDT',
    payment_method     INTEGER NOT NULL CHECK (payment_method IN (0, 1)),
    transfers          INTEGER CHECK (transfers IN (0, 1, 2)),  -- NULL = unlimited
    agency_id          TEXT REFERENCES agency(agency_id),
    transfer_duration  INTEGER
);

CREATE TABLE IF NOT EXISTS fare_rules (
    fare_id         TEXT NOT NULL REFERENCES fare_attributes(fare_id),
    route_id        TEXT REFERENCES routes(route_id),
    origin_id       TEXT,   -- zone_id
    destination_id  TEXT,   -- zone_id
    contains_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_fare_rules_od ON fare_rules (origin_id, destination_id);

CREATE TABLE IF NOT EXISTS transfers (
    from_stop_id       TEXT REFERENCES stops(stop_id),
    to_stop_id         TEXT REFERENCES stops(stop_id),
    transfer_type      INTEGER NOT NULL CHECK (transfer_type BETWEEN 0 AND 5),
    min_transfer_time  INTEGER
);

CREATE TABLE IF NOT EXISTS feed_info (
    feed_publisher_name  TEXT NOT NULL,
    feed_publisher_url   TEXT NOT NULL,
    feed_lang            TEXT NOT NULL,
    feed_start_date      TEXT,
    feed_end_date        TEXT,
    feed_version         TEXT,
    feed_contact_email   TEXT
);

-- ---------------------------------------------------------------------
-- Layer 2: Project extensions
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_sources (
    source_id     TEXT PRIMARY KEY,         -- e.g. 'osm_overpass_2026-11-20', 'dmtcl_fare_chart'
    name          TEXT NOT NULL,
    url           TEXT,
    license       TEXT,                     -- e.g. 'ODbL-1.0', 'Public (Govt. of Bangladesh)'
    category      TEXT NOT NULL CHECK (category IN ('official', 'osm', 'community', 'estimated')),
    retrieved_at  TEXT,                     -- ISO-8601
    notes         TEXT
);

-- One row per record (field = NULL) or per individual field.
CREATE TABLE IF NOT EXISTS provenance (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_table  TEXT NOT NULL,            -- 'stops', 'routes', 'stop_times', ...
    entity_id     TEXT NOT NULL,            -- primary key value (composite keys joined with '|')
    field         TEXT,                     -- NULL = whole record
    source_id     TEXT REFERENCES data_sources(source_id),
    category      TEXT NOT NULL CHECK (category IN ('official', 'osm', 'community', 'estimated')),
    status        TEXT NOT NULL DEFAULT 'unverified' CHECK (status IN ('verified', 'unverified', 'disputed')),
    verified_at   TEXT,
    verified_by   TEXT,
    notes         TEXT,
    UNIQUE (entity_table, entity_id, field)
);
CREATE INDEX IF NOT EXISTS idx_provenance_entity ON provenance (entity_table, entity_id);

-- Derived walking links between stops (generated, not hand-edited).
CREATE TABLE IF NOT EXISTS footpaths (
    from_stop_id  TEXT NOT NULL REFERENCES stops(stop_id) ON DELETE CASCADE,
    to_stop_id    TEXT NOT NULL REFERENCES stops(stop_id) ON DELETE CASCADE,
    distance_m    REAL NOT NULL CHECK (distance_m >= 0),
    duration_s    INTEGER NOT NULL CHECK (duration_s >= 0),
    method        TEXT NOT NULL CHECK (method IN ('haversine', 'osm_astar', 'surveyed')),
    PRIMARY KEY (from_stop_id, to_stop_id)
);

CREATE TABLE IF NOT EXISTS contributors (
    contributor_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    display_name    TEXT NOT NULL,
    contact         TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contributions (
    contribution_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    contributor_id   INTEGER REFERENCES contributors(contributor_id),
    kind             TEXT NOT NULL CHECK (kind IN ('new_stop', 'stop_fix', 'new_route', 'route_fix', 'travel_time', 'headway', 'other')),
    payload_json     TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    submitted_at     TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_by      TEXT,
    review_notes     TEXT
);

CREATE TABLE IF NOT EXISTS validation_runs (
    run_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at         TEXT NOT NULL DEFAULT (datetime('now')),
    feed_version   TEXT,
    error_count    INTEGER NOT NULL DEFAULT 0,
    warning_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS validation_issues (
    issue_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        INTEGER NOT NULL REFERENCES validation_runs(run_id) ON DELETE CASCADE,
    severity      TEXT NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    code          TEXT NOT NULL,            -- e.g. 'duplicate_stop', 'missing_coordinates'
    entity_table  TEXT,
    entity_id     TEXT,
    message       TEXT NOT NULL
);
