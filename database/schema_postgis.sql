-- =====================================================================
-- Dhaka Transit — PostgreSQL + PostGIS schema (deployment database)
-- Mirrors schema_sqlite.sql; adds geography columns + GiST indexes.
-- Usage:  psql -d dhaka_transit -f database/schema_postgis.sql
-- =====================================================================
CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------- Layer 1: GTFS core ----------------
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
    stop_name_bn        TEXT,
    stop_desc           TEXT,
    stop_lat            DOUBLE PRECISION CHECK (stop_lat IS NULL OR stop_lat BETWEEN -90 AND 90),
    stop_lon            DOUBLE PRECISION CHECK (stop_lon IS NULL OR stop_lon BETWEEN -180 AND 180),
    zone_id             TEXT,
    location_type       SMALLINT NOT NULL DEFAULT 0 CHECK (location_type BETWEEN 0 AND 4),
    parent_station      TEXT REFERENCES stops(stop_id),
    wheelchair_boarding SMALLINT CHECK (wheelchair_boarding IN (0, 1, 2)),
    platform_code       TEXT,
    -- Generated geography point: always consistent with lat/lon.
    geom geography(Point, 4326) GENERATED ALWAYS AS (
        CASE WHEN stop_lat IS NULL OR stop_lon IS NULL THEN NULL
             ELSE ST_SetSRID(ST_MakePoint(stop_lon, stop_lat), 4326)::geography END
    ) STORED,
    CHECK (location_type > 2 OR (stop_name IS NOT NULL AND stop_lat IS NOT NULL AND stop_lon IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_stops_geom   ON stops USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_stops_parent ON stops (parent_station);

CREATE TABLE IF NOT EXISTS routes (
    route_id           TEXT PRIMARY KEY,
    agency_id          TEXT REFERENCES agency(agency_id),
    route_short_name   TEXT,
    route_long_name    TEXT,
    route_long_name_bn TEXT,
    route_desc         TEXT,
    route_type         SMALLINT NOT NULL,
    route_url          TEXT,
    route_color        CHAR(6),
    route_text_color   CHAR(6),
    route_sort_order   INTEGER,
    CHECK (route_short_name IS NOT NULL OR route_long_name IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS calendar (
    service_id  TEXT PRIMARY KEY,
    monday      SMALLINT NOT NULL CHECK (monday    IN (0, 1)),
    tuesday     SMALLINT NOT NULL CHECK (tuesday   IN (0, 1)),
    wednesday   SMALLINT NOT NULL CHECK (wednesday IN (0, 1)),
    thursday    SMALLINT NOT NULL CHECK (thursday  IN (0, 1)),
    friday      SMALLINT NOT NULL CHECK (friday    IN (0, 1)),
    saturday    SMALLINT NOT NULL CHECK (saturday  IN (0, 1)),
    sunday      SMALLINT NOT NULL CHECK (sunday    IN (0, 1)),
    start_date  CHAR(8) NOT NULL,
    end_date    CHAR(8) NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_dates (
    service_id      TEXT NOT NULL,
    date            CHAR(8) NOT NULL,
    exception_type  SMALLINT NOT NULL CHECK (exception_type IN (1, 2)),
    PRIMARY KEY (service_id, date)
);

CREATE TABLE IF NOT EXISTS shapes (
    shape_id             TEXT NOT NULL,
    shape_pt_lat         DOUBLE PRECISION NOT NULL,
    shape_pt_lon         DOUBLE PRECISION NOT NULL,
    shape_pt_sequence    INTEGER NOT NULL CHECK (shape_pt_sequence >= 0),
    shape_dist_traveled  DOUBLE PRECISION,
    PRIMARY KEY (shape_id, shape_pt_sequence)
);

-- Materialised line geometry per shape (refresh after loading shapes).
CREATE MATERIALIZED VIEW IF NOT EXISTS shape_lines AS
SELECT shape_id,
       ST_MakeLine(ST_SetSRID(ST_MakePoint(shape_pt_lon, shape_pt_lat), 4326)
                   ORDER BY shape_pt_sequence)::geography AS geom
FROM shapes
GROUP BY shape_id;

CREATE TABLE IF NOT EXISTS trips (
    trip_id               TEXT PRIMARY KEY,
    route_id              TEXT NOT NULL REFERENCES routes(route_id),
    service_id            TEXT NOT NULL,
    trip_headsign         TEXT,
    trip_short_name       TEXT,
    direction_id          SMALLINT CHECK (direction_id IN (0, 1)),
    block_id              TEXT,
    shape_id              TEXT,
    wheelchair_accessible SMALLINT CHECK (wheelchair_accessible IN (0, 1, 2))
);
CREATE INDEX IF NOT EXISTS idx_trips_route ON trips (route_id);

CREATE TABLE IF NOT EXISTS stop_times (
    trip_id              TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
    arrival_time         TEXT,
    departure_time       TEXT,
    stop_id              TEXT NOT NULL REFERENCES stops(stop_id),
    stop_sequence        INTEGER NOT NULL CHECK (stop_sequence >= 0),
    stop_headsign        TEXT,
    pickup_type          SMALLINT CHECK (pickup_type BETWEEN 0 AND 3),
    drop_off_type        SMALLINT CHECK (drop_off_type BETWEEN 0 AND 3),
    shape_dist_traveled  DOUBLE PRECISION,
    timepoint            SMALLINT CHECK (timepoint IN (0, 1)),
    PRIMARY KEY (trip_id, stop_sequence)
);
CREATE INDEX IF NOT EXISTS idx_stop_times_stop ON stop_times (stop_id);

CREATE TABLE IF NOT EXISTS frequencies (
    trip_id       TEXT NOT NULL REFERENCES trips(trip_id) ON DELETE CASCADE,
    start_time    TEXT NOT NULL,
    end_time      TEXT NOT NULL,
    headway_secs  INTEGER NOT NULL CHECK (headway_secs > 0),
    exact_times   SMALLINT DEFAULT 0 CHECK (exact_times IN (0, 1)),
    PRIMARY KEY (trip_id, start_time)
);

CREATE TABLE IF NOT EXISTS fare_attributes (
    fare_id            TEXT PRIMARY KEY,
    price              NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    currency_type      CHAR(3) NOT NULL DEFAULT 'BDT',
    payment_method     SMALLINT NOT NULL CHECK (payment_method IN (0, 1)),
    transfers          SMALLINT CHECK (transfers IN (0, 1, 2)),
    agency_id          TEXT REFERENCES agency(agency_id),
    transfer_duration  INTEGER
);

CREATE TABLE IF NOT EXISTS fare_rules (
    fare_id         TEXT NOT NULL REFERENCES fare_attributes(fare_id),
    route_id        TEXT REFERENCES routes(route_id),
    origin_id       TEXT,
    destination_id  TEXT,
    contains_id     TEXT
);
CREATE INDEX IF NOT EXISTS idx_fare_rules_od ON fare_rules (origin_id, destination_id);

CREATE TABLE IF NOT EXISTS transfers (
    from_stop_id       TEXT REFERENCES stops(stop_id),
    to_stop_id         TEXT REFERENCES stops(stop_id),
    transfer_type      SMALLINT NOT NULL CHECK (transfer_type BETWEEN 0 AND 5),
    min_transfer_time  INTEGER
);

CREATE TABLE IF NOT EXISTS feed_info (
    feed_publisher_name  TEXT NOT NULL,
    feed_publisher_url   TEXT NOT NULL,
    feed_lang            TEXT NOT NULL,
    feed_start_date      CHAR(8),
    feed_end_date        CHAR(8),
    feed_version         TEXT,
    feed_contact_email   TEXT
);

-- ---------------- Layer 2: Project extensions ----------------
CREATE TABLE IF NOT EXISTS data_sources (
    source_id     TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    url           TEXT,
    license       TEXT,
    category      TEXT NOT NULL CHECK (category IN ('official', 'osm', 'community', 'estimated')),
    retrieved_at  TIMESTAMPTZ,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS provenance (
    id            BIGSERIAL PRIMARY KEY,
    entity_table  TEXT NOT NULL,
    entity_id     TEXT NOT NULL,
    field         TEXT,
    source_id     TEXT REFERENCES data_sources(source_id),
    category      TEXT NOT NULL CHECK (category IN ('official', 'osm', 'community', 'estimated')),
    status        TEXT NOT NULL DEFAULT 'unverified' CHECK (status IN ('verified', 'unverified', 'disputed')),
    verified_at   TIMESTAMPTZ,
    verified_by   TEXT,
    notes         TEXT,
    UNIQUE NULLS NOT DISTINCT (entity_table, entity_id, field)
);
CREATE INDEX IF NOT EXISTS idx_provenance_entity ON provenance (entity_table, entity_id);

CREATE TABLE IF NOT EXISTS footpaths (
    from_stop_id  TEXT NOT NULL REFERENCES stops(stop_id) ON DELETE CASCADE,
    to_stop_id    TEXT NOT NULL REFERENCES stops(stop_id) ON DELETE CASCADE,
    distance_m    DOUBLE PRECISION NOT NULL CHECK (distance_m >= 0),
    duration_s    INTEGER NOT NULL CHECK (duration_s >= 0),
    method        TEXT NOT NULL CHECK (method IN ('haversine', 'osm_astar', 'surveyed')),
    PRIMARY KEY (from_stop_id, to_stop_id)
);

CREATE TABLE IF NOT EXISTS contributors (
    contributor_id  BIGSERIAL PRIMARY KEY,
    display_name    TEXT NOT NULL,
    contact         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS contributions (
    contribution_id  BIGSERIAL PRIMARY KEY,
    contributor_id   BIGINT REFERENCES contributors(contributor_id),
    kind             TEXT NOT NULL CHECK (kind IN ('new_stop', 'stop_fix', 'new_route', 'route_fix', 'travel_time', 'headway', 'other')),
    payload_json     JSONB NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    submitted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_by      TEXT,
    review_notes     TEXT
);

CREATE TABLE IF NOT EXISTS validation_runs (
    run_id         BIGSERIAL PRIMARY KEY,
    run_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    feed_version   TEXT,
    error_count    INTEGER NOT NULL DEFAULT 0,
    warning_count  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS validation_issues (
    issue_id      BIGSERIAL PRIMARY KEY,
    run_id        BIGINT NOT NULL REFERENCES validation_runs(run_id) ON DELETE CASCADE,
    severity      TEXT NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    code          TEXT NOT NULL,
    entity_table  TEXT,
    entity_id     TEXT,
    message       TEXT NOT NULL
);

-- Example: 5 nearest stops to an arbitrary query point (lon, lat), distance in metres.
-- SELECT stop_id, stop_name, ST_Distance(geom, ST_MakePoint(90.3667, 23.8069)::geography) AS d_m
-- FROM stops ORDER BY geom <-> ST_MakePoint(90.3667, 23.8069)::geography LIMIT 5;
