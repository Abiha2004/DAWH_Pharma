-- ============================================================================
-- OPS schema: every pipeline component writes its metrics here.
-- The four Grafana Data-Engineering dashboards read only from these tables
-- (plus pg_stat_statements), so the dashboards update live while DAGs run.
-- ============================================================================

-- One row per refresh cycle (initial load, R1, R2 ...)
CREATE TABLE IF NOT EXISTS ops.refresh_log (
    refresh_id      TEXT PRIMARY KEY,
    refresh_month   TEXT,
    started_at      TIMESTAMPTZ DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          TEXT DEFAULT 'RUNNING',
    notes           TEXT
);

-- Files seen by the file-watcher (idempotency: the same file hash is never loaded twice)
CREATE TABLE IF NOT EXISTS ops.file_registry (
    file_id         SERIAL PRIMARY KEY,
    file_name       TEXT NOT NULL,
    file_hash       TEXT NOT NULL UNIQUE,
    source_domain   TEXT,
    refresh_id      TEXT,
    size_bytes      BIGINT,
    detected_at     TIMESTAMPTZ DEFAULT now(),
    detected_by     TEXT,                  -- watchdog | api_upload | airflow_scan
    status          TEXT DEFAULT 'DETECTED', -- DETECTED | LOADED | FAILED
    batch_id        INTEGER
);

-- BATCH INGESTION metrics: one row per file load
CREATE TABLE IF NOT EXISTS ops.batch_load_log (
    batch_id        SERIAL PRIMARY KEY,
    refresh_id      TEXT,
    source_domain   TEXT,
    file_name       TEXT,
    file_size_mb    NUMERIC(12,3),
    start_ts        TIMESTAMPTZ,
    end_ts          TIMESTAMPTZ,
    duration_s      NUMERIC(12,3),
    rows_read       INTEGER,
    rows_loaded     INTEGER,
    rows_malformed  INTEGER DEFAULT 0,
    rows_per_sec    NUMERIC(14,1),
    ingest_mode     TEXT DEFAULT 'batch',  -- batch | stream (stream runs also get a batch id)
    status          TEXT,                  -- RUNNING | SUCCESS | FAILED
    error_message   TEXT,
    airflow_run_id  TEXT,
    validated_at    TIMESTAMPTZ            -- set when the batch has been promoted to silver
);

-- STREAMING INGESTION metrics: one row per micro-batch
CREATE TABLE IF NOT EXISTS ops.stream_metrics (
    id               SERIAL PRIMARY KEY,
    refresh_id       TEXT,
    stream_name      TEXT,
    micro_batch_no   INTEGER,
    batch_start_ts   TIMESTAMPTZ,
    batch_end_ts     TIMESTAMPTZ,
    events_received  INTEGER,
    events_loaded    INTEGER,
    events_malformed INTEGER,
    throughput_eps   NUMERIC(12,2),
    avg_latency_s    NUMERIC(12,4),
    p95_latency_s    NUMERIC(12,4),
    max_latency_s    NUMERIC(12,4),
    airflow_run_id   TEXT
);

-- Individual malformed stream events (for drill-down)
CREATE TABLE IF NOT EXISTS ops.stream_dead_letter (
    id            SERIAL PRIMARY KEY,
    refresh_id    TEXT,
    stream_name   TEXT,
    received_at   TIMESTAMPTZ DEFAULT now(),
    reason        TEXT,
    payload       TEXT
);

-- DATA QUALITY results: one row per rule per batch
CREATE TABLE IF NOT EXISTS ops.dq_results (
    id            SERIAL PRIMARY KEY,
    batch_id      INTEGER,
    refresh_id    TEXT,
    source_domain TEXT,
    rule_name     TEXT,
    rule_type     TEXT,     -- completeness | validity | consistency | uniqueness | referential
    checked_rows  INTEGER,
    failed_rows   INTEGER,
    checked_at    TIMESTAMPTZ DEFAULT now()
);

-- STORAGE metrics: snapshot of every table after each layer load
CREATE TABLE IF NOT EXISTS ops.storage_snapshot (
    id             SERIAL PRIMARY KEY,
    captured_at    TIMESTAMPTZ DEFAULT now(),
    refresh_id     TEXT,
    layer          TEXT,     -- bronze | silver | quarantine | gold | mart
    schema_name    TEXT,
    table_name     TEXT,
    source_domain  TEXT,
    row_count      BIGINT,
    size_mb        NUMERIC(14,3),
    min_date       DATE,
    max_date       DATE,
    distinct_months INTEGER
);

CREATE TABLE IF NOT EXISTS ops.database_size_log (
    id           SERIAL PRIMARY KEY,
    captured_at  TIMESTAMPTZ DEFAULT now(),
    refresh_id   TEXT,
    db_size_mb   NUMERIC(14,3),
    data_dir_mb  NUMERIC(14,3)       -- os.path.getsize walk of the Postgres data directory when reachable
);

-- TRANSFORMATION metrics: one row per dimension/fact load step
CREATE TABLE IF NOT EXISTS ops.transform_log (
    id             SERIAL PRIMARY KEY,
    refresh_id     TEXT,
    step           TEXT,
    target_table   TEXT,
    rows_before    BIGINT,
    rows_after     BIGINT,
    rows_inserted  BIGINT,
    scd2_new_versions INTEGER DEFAULT 0,
    duration_s     NUMERIC(12,3),
    started_at     TIMESTAMPTZ DEFAULT now(),
    status         TEXT DEFAULT 'SUCCESS'
);

-- QUERY LAYER metrics: one row per query execution
CREATE TABLE IF NOT EXISTS ops.query_log (
    id             SERIAL PRIMARY KEY,
    refresh_id     TEXT,
    query_id       TEXT,
    query_name     TEXT,
    widget         TEXT,       -- the BI widget/KPI card this query feeds
    variant        TEXT,       -- baseline (star joins) | optimized (MV / summary / index)
    executed_at    TIMESTAMPTZ DEFAULT now(),
    client_ms      NUMERIC(14,3),   -- time.perf_counter() wrapper around the call
    server_ms      NUMERIC(14,3),   -- EXPLAIN ANALYZE execution time
    rows_returned  INTEGER,
    status         TEXT,            -- SUCCESS | FAILED
    error_message  TEXT,
    triggered_by   TEXT             -- airflow_benchmark | dashboard_refresh | adhoc
);

-- Watermark per source: last batch that reached the gold layer (incremental loading)
CREATE TABLE IF NOT EXISTS ops.watermark (
    target_table   TEXT PRIMARY KEY,
    last_batch_id  INTEGER DEFAULT 0,
    updated_at     TIMESTAMPTZ DEFAULT now()
);
