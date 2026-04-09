-- CALI-07 v1: Initial anonymous metrics ingest schema
-- Cloudflare D1 (SQLite-compatible)

CREATE TABLE IF NOT EXISTS metrics_events (
    id              TEXT PRIMARY KEY,         -- client-generated UUID (local_id stripped before upload)
    date_utc        TEXT NOT NULL,            -- YYYY-MM-DD day bucket
    received_at     TEXT NOT NULL DEFAULT (datetime('now')),
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    tokens_saved    INTEGER NOT NULL DEFAULT 0,
    compression_ratio REAL NOT NULL DEFAULT 0.0,
    latency_ms      REAL NOT NULL DEFAULT 0.0,
    model           TEXT NOT NULL DEFAULT '',
    schema_version  TEXT NOT NULL DEFAULT '1.0'
);

-- Hourly aggregation for the public counter
CREATE TABLE IF NOT EXISTS metrics_hourly (
    hour_utc        TEXT NOT NULL,            -- YYYY-MM-DDTHH:00Z
    total_requests  INTEGER NOT NULL DEFAULT 0,
    total_tokens_saved INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour_utc)
);
