"""TSR-03 / CCI-21 schema-drift tests.

Carved out from `tests/cli/test_metrics_mode_fields.py` so the v1.1
schema invariants on `MetricsRecord` can be verified independently of
the closed-source `tokenpak._internal` namespace and the WS-D
`detect_consumption_mode` symbol that the parent file guards at module
level. Both of those route to TSR-04 / TSR-07; neither is needed by
the pure-schema tests below.

Covered invariants (all unbundled from missing-export / boundary work):
  - `_ALLOWED_FIELDS` contains `active_profile` + `consumption_mode`
  - `MetricsRecord(active_profile=..., consumption_mode=...)` accepts
    the kwargs and round-trips through `to_upload_dict()`
  - `to_upload_dict()` omits the new fields when empty (payload
    minimalism for old installs)
  - `SCHEMA_VERSION == "1.1"` and new instances inherit it
  - `MetricsStore._ensure_schema()` migrates a v1.0 SQLite database to
    add the new columns (idempotent)

If a future TSR-04 lands `detect_consumption_mode` and a future
TSR-07 lands the `_internal` boundary fix, the broader test surface
in `test_metrics_mode_fields.py` becomes observable too. Until then,
this file alone exercises the canonical CCI-21 schema on slim OSS.
"""

from __future__ import annotations

import sqlite3

from tokenpak.telemetry.anon_metrics import (
    SCHEMA_VERSION,
    MetricsRecord,
    MetricsStore,
)


class TestModeFieldsSchema:
    """Verify the new fields are included in the upload payload."""

    def test_allowed_fields_includes_new_fields(self):
        assert "active_profile" in MetricsRecord._ALLOWED_FIELDS
        assert "consumption_mode" in MetricsRecord._ALLOWED_FIELDS

    def test_to_upload_dict_includes_fields_when_set(self):
        rec = MetricsRecord(active_profile="agentic", consumption_mode="tmux")
        d = rec.to_upload_dict()
        assert d["active_profile"] == "agentic"
        assert d["consumption_mode"] == "tmux"

    def test_to_upload_dict_omits_empty_fields(self):
        rec = MetricsRecord()
        d = rec.to_upload_dict()
        # Empty strings should be omitted to keep payload minimal
        assert "active_profile" not in d
        assert "consumption_mode" not in d

    def test_schema_version_bumped_to_1_1(self):
        assert SCHEMA_VERSION == "1.1"
        rec = MetricsRecord()
        assert rec.schema_version == "1.1"

    def test_sqlite_migration_adds_columns(self, tmp_path):
        """MetricsStore migrates an existing v1.0 DB to add the new columns."""
        db_path = tmp_path / "metrics.db"

        # Simulate an old v1.0 database without the new columns
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE metrics (
                local_id TEXT PRIMARY KEY,
                date_utc TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                tokens_saved INTEGER NOT NULL DEFAULT 0,
                compression_ratio REAL NOT NULL DEFAULT 0.0,
                latency_ms REAL NOT NULL DEFAULT 0.0,
                model TEXT NOT NULL DEFAULT '',
                schema_version TEXT NOT NULL DEFAULT '1.0',
                synced INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

        # Opening the store should migrate it
        store = MetricsStore(db_path=db_path)  # noqa: F841 — instantiation triggers migration

        conn = sqlite3.connect(str(db_path))
        cols = {row[1] for row in conn.execute("PRAGMA table_info(metrics)").fetchall()}
        conn.close()

        assert "active_profile" in cols
        assert "consumption_mode" in cols

    def test_migration_idempotent(self, tmp_path):
        """Running _ensure_schema twice on a v1.0 DB must not raise."""
        db_path = tmp_path / "metrics.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE metrics (
                local_id TEXT PRIMARY KEY,
                date_utc TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0,
                tokens_saved INTEGER NOT NULL DEFAULT 0,
                compression_ratio REAL NOT NULL DEFAULT 0.0,
                latency_ms REAL NOT NULL DEFAULT 0.0,
                model TEXT NOT NULL DEFAULT '',
                schema_version TEXT NOT NULL DEFAULT '1.0',
                synced INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

        # First instantiation migrates
        MetricsStore(db_path=db_path)
        # Second instantiation must be a no-op (ALTER TABLE would raise
        # "duplicate column" without the existing-cols guard)
        MetricsStore(db_path=db_path)

    def test_record_round_trip_with_new_fields(self, tmp_path):
        """Insert + fetch a record with both new fields populated."""
        store = MetricsStore(db_path=tmp_path / "metrics.db")
        rec = MetricsRecord(
            input_tokens=100,
            output_tokens=20,
            tokens_saved=30,
            latency_ms=42.0,
            model="claude-haiku-4-5",
            active_profile="balanced",
            consumption_mode="cli",
        )
        store.record(rec)
        pending = store.get_pending()
        assert len(pending) == 1
        assert pending[0].active_profile == "balanced"
        assert pending[0].consumption_mode == "cli"
        assert pending[0].schema_version == "1.1"
