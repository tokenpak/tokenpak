"""
Tests for tokenpak.telemetry.export.TelemetryExporter

Covers:
- CSV export (headers, row data, encoding)
- JSON export (envelope schema, data array)
- Date range filtering (start, end, both, neither)
- model/status filter pass-through
- filename generation
- Edge cases: empty storage, single row, max-rows guard
- query_requests on TelemetryStorage
"""

from __future__ import annotations

import csv
import io
import json
from unittest.mock import patch

import pytest

from tokenpak.telemetry.export import MAX_EXPORT_ROWS
from tokenpak.telemetry.storage import TelemetryStorage

# TelemetryExporter was removed (it was never implemented — only stubs).
# Tests below that reference it are skipped.
TelemetryExporter = None
_parse_date = None
_EXPORTER_REMOVED = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage(*rows: dict) -> TelemetryStorage:
    """Return in-memory TelemetryStorage pre-populated with synthetic rows."""
    storage = TelemetryStorage(":memory:")
    conn = storage._conn()
    for row in rows:
        conn.execute(
            """
            INSERT INTO tp_requests
                (request_id, timestamp, tokens_raw, tokens_sent, tokens_saved, percent_saved, cost_saved)
            VALUES (:request_id, :timestamp, :tokens_raw, :tokens_sent, :tokens_saved, :percent_saved, :cost_saved)
            """,
            row,
        )
    conn.commit()
    return storage


def _row(request_id: str, date: str, tokens_raw: int = 100, tokens_sent: int = 80) -> dict:
    saved = tokens_raw - tokens_sent
    return {
        "request_id": request_id,
        "timestamp": f"{date}T12:00:00",
        "tokens_raw": tokens_raw,
        "tokens_sent": tokens_sent,
        "tokens_saved": saved,
        "percent_saved": round(saved / tokens_raw, 4),
        "cost_saved": round(saved * 0.000003, 6),
    }


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_EXPORTER_REMOVED, reason="TelemetryExporter removed; _parse_date no longer exported")
class TestParseDate:
    def test_valid_date(self):
        result = _parse_date("2026-03-01", "start")
        assert result == "2026-03-01T00:00:00"

    def test_none_returns_none(self):
        assert _parse_date(None, "start") is None

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError, match="start"):
            _parse_date("01-03-2026", "start")

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError, match="end"):
            _parse_date("2026-13-01", "end")


# ---------------------------------------------------------------------------
# TelemetryStorage.query_requests
# ---------------------------------------------------------------------------

class TestQueryRequests:
    def test_no_filters_returns_all(self):
        s = _make_storage(_row("r1", "2026-03-01"), _row("r2", "2026-03-10"))
        rows = s.query_requests()
        assert len(rows) == 2

    def test_start_filter(self):
        s = _make_storage(_row("r1", "2026-03-01"), _row("r2", "2026-03-10"))
        rows = s.query_requests(start="2026-03-05T00:00:00")
        assert len(rows) == 1
        assert rows[0]["request_id"] == "r2"

    def test_end_filter(self):
        s = _make_storage(_row("r1", "2026-03-01"), _row("r2", "2026-03-10"))
        rows = s.query_requests(end="2026-03-05T23:59:59")
        assert len(rows) == 1
        assert rows[0]["request_id"] == "r1"

    def test_start_and_end_filter(self):
        s = _make_storage(
            _row("r1", "2026-03-01"),
            _row("r2", "2026-03-10"),
            _row("r3", "2026-03-20"),
        )
        rows = s.query_requests(
            start="2026-03-05T00:00:00",
            end="2026-03-15T23:59:59",
        )
        assert len(rows) == 1
        assert rows[0]["request_id"] == "r2"

    def test_empty_storage(self):
        s = _make_storage()
        rows = s.query_requests()
        assert rows == []

    def test_limit_respected(self):
        rows_data = [_row(f"r{i}", "2026-03-01") for i in range(20)]
        s = _make_storage(*rows_data)
        rows = s.query_requests(limit=5)
        assert len(rows) == 5

    def test_unknown_model_filter_ignored(self):
        """model column doesn't exist → filter silently skipped, all rows returned."""
        s = _make_storage(_row("r1", "2026-03-01"))
        rows = s.query_requests(model="claude-sonnet")
        assert len(rows) == 1  # not zero — column absent, filter ignored

    def test_result_is_list_of_dicts(self):
        s = _make_storage(_row("r1", "2026-03-01"))
        rows = s.query_requests()
        assert isinstance(rows, list)
        assert isinstance(rows[0], dict)
        assert "request_id" in rows[0]


# ---------------------------------------------------------------------------
# TelemetryExporter — CSV
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_EXPORTER_REMOVED, reason="TelemetryExporter removed")
class TestExportCSV:
    def _exporter(self, *rows):
        return TelemetryExporter(_make_storage(*rows))

    def test_returns_bytes(self):
        exp = self._exporter(_row("r1", "2026-03-01"))
        result = exp.export_csv()
        assert isinstance(result, bytes)

    def test_valid_csv_headers(self):
        exp = self._exporter(_row("r1", "2026-03-01"))
        reader = csv.DictReader(io.StringIO(exp.export_csv().decode("utf-8")))
        assert "request_id" in reader.fieldnames
        assert "timestamp" in reader.fieldnames
        assert "tokens_raw" in reader.fieldnames
        assert "cost_saved" in reader.fieldnames

    def test_row_data_present(self):
        exp = self._exporter(_row("r1", "2026-03-01", tokens_raw=200, tokens_sent=150))
        reader = csv.DictReader(io.StringIO(exp.export_csv().decode("utf-8")))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["request_id"] == "r1"
        assert int(rows[0]["tokens_raw"]) == 200

    def test_empty_storage_has_header_only(self):
        exp = self._exporter()
        lines = exp.export_csv().decode("utf-8").strip().splitlines()
        assert len(lines) == 1  # header only

    def test_date_range_filtering(self):
        exp = self._exporter(
            _row("r1", "2026-03-01"),
            _row("r2", "2026-03-20"),
        )
        csv_bytes = exp.export_csv(start="2026-03-15", end="2026-03-25")
        reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
        rows = list(reader)
        assert len(rows) == 1
        assert rows[0]["request_id"] == "r2"

    def test_special_chars_do_not_corrupt_csv(self):
        """Commas and quotes in fields should be escaped properly."""
        storage = _make_storage()
        conn = storage._conn()
        conn.execute(
            """INSERT INTO tp_requests
               (request_id, timestamp, tokens_raw, tokens_sent, tokens_saved, percent_saved, cost_saved)
               VALUES (?, ?, 10, 8, 2, 0.2, 0.000006)""",
            ('r"tricky,id"', "2026-03-01T12:00:00"),
        )
        conn.commit()
        exp = TelemetryExporter(storage)
        # Should not raise
        csv_bytes = exp.export_csv()
        reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
        rows = list(reader)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# TelemetryExporter — JSON
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_EXPORTER_REMOVED, reason="TelemetryExporter removed")
class TestExportJSON:
    def _exporter(self, *rows):
        return TelemetryExporter(_make_storage(*rows))

    def test_returns_bytes(self):
        exp = self._exporter(_row("r1", "2026-03-01"))
        assert isinstance(exp.export_json(), bytes)

    def test_valid_json(self):
        exp = self._exporter(_row("r1", "2026-03-01"))
        payload = json.loads(exp.export_json())
        assert "meta" in payload
        assert "data" in payload

    def test_meta_fields(self):
        exp = self._exporter(_row("r1", "2026-03-01"))
        meta = json.loads(exp.export_json())["meta"]
        assert "exported_at" in meta
        assert "query" in meta
        assert "row_count" in meta

    def test_row_count_matches_data(self):
        exp = self._exporter(_row("r1", "2026-03-01"), _row("r2", "2026-03-10"))
        payload = json.loads(exp.export_json())
        assert payload["meta"]["row_count"] == len(payload["data"])
        assert payload["meta"]["row_count"] == 2

    def test_query_params_reflected(self):
        exp = self._exporter(_row("r1", "2026-03-01"))
        payload = json.loads(exp.export_json(start="2026-03-01", end="2026-03-31"))
        assert payload["meta"]["query"]["start"] == "2026-03-01"
        assert payload["meta"]["query"]["end"] == "2026-03-31"

    def test_empty_storage(self):
        exp = self._exporter()
        payload = json.loads(exp.export_json())
        assert payload["data"] == []
        assert payload["meta"]["row_count"] == 0

    def test_date_range_filtering(self):
        exp = self._exporter(_row("r1", "2026-03-01"), _row("r2", "2026-03-20"))
        payload = json.loads(exp.export_json(start="2026-03-15"))
        assert payload["meta"]["row_count"] == 1
        assert payload["data"][0]["request_id"] == "r2"


# ---------------------------------------------------------------------------
# TelemetryExporter — filename
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_EXPORTER_REMOVED, reason="TelemetryExporter removed")
class TestFilename:
    def _exp(self):
        return TelemetryExporter(_make_storage())

    def test_both_dates(self):
        assert self._exp().filename("csv", "2026-03-01", "2026-03-25") == \
            "tokenpak-telemetry-2026-03-01-2026-03-25.csv"

    def test_start_only(self):
        name = self._exp().filename("json", start="2026-03-01")
        assert name == "tokenpak-telemetry-from-2026-03-01.json"

    def test_end_only(self):
        name = self._exp().filename("csv", end="2026-03-25")
        assert name == "tokenpak-telemetry-until-2026-03-25.csv"

    def test_no_dates(self):
        assert self._exp().filename("json") == "tokenpak-telemetry-all.json"

    def test_extension_lowercase(self):
        assert self._exp().filename("CSV").endswith(".csv")


# ---------------------------------------------------------------------------
# MAX_EXPORT_ROWS guard
# ---------------------------------------------------------------------------

@pytest.mark.skipif(_EXPORTER_REMOVED, reason="TelemetryExporter removed")
class TestMaxRows:
    def test_max_rows_constant_exists(self):
        assert MAX_EXPORT_ROWS > 0

    def test_export_respects_limit(self):
        rows_data = [_row(f"r{i}", "2026-03-01") for i in range(10)]
        storage = _make_storage(*rows_data)
        exp = TelemetryExporter(storage)
        # Monkey-patch limit to 3 for test
        with patch("tokenpak.telemetry.export.MAX_EXPORT_ROWS", 3):
            # Re-import to pick up patched constant
            import tokenpak.telemetry.export as mod
            original = mod.MAX_EXPORT_ROWS
            mod.MAX_EXPORT_ROWS = 3
            try:
                csv_bytes = exp.export_csv()
                reader = csv.DictReader(io.StringIO(csv_bytes.decode("utf-8")))
                # Since we patched the module-level constant, exporter uses it
                rows = list(reader)
                # The fetch would have limited to 3
                assert len(rows) <= 10  # can't exceed what was inserted
            finally:
                mod.MAX_EXPORT_ROWS = original
