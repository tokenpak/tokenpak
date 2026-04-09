-- CCI-21 v2: Add active_profile and consumption_mode fields to metrics_events
-- Run after v1_initial.sql is applied.
-- Both columns are nullable so existing rows (schema_version 1.0) remain valid.

ALTER TABLE metrics_events
    ADD COLUMN active_profile   TEXT NOT NULL DEFAULT '';

ALTER TABLE metrics_events
    ADD COLUMN consumption_mode TEXT NOT NULL DEFAULT '';

-- Mode breakdown view — used by GET /metrics/public
CREATE VIEW IF NOT EXISTS mode_breakdown_24h AS
SELECT
    consumption_mode,
    COUNT(*) AS request_count
FROM metrics_events
WHERE received_at >= datetime('now', '-24 hours')
  AND consumption_mode != ''
GROUP BY consumption_mode;
