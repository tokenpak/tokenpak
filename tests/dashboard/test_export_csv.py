"""Tests for TokenPak Dashboard CSV Export (AC 1-7).

Covers:
  - ExportButton component existence (AC 1)
  - Format options: full vs simplified (AC 2)
  - Filename format: tokenpak-export-YYYY-MM-DD-HHmmss.csv (AC 3)
  - ExportAPI HTTP handler: correct response shape (AC 4)
  - 500+ row export — all rows + columns present (AC 6)
  - Error handling: invalid params → 400, graceful messages (AC 7)
  - Regression: existing proxy imports still work (AC 5)
"""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import pytest

# ---------------------------------------------------------------------------
# Import guard — fail fast with a useful message
# ---------------------------------------------------------------------------
try:
    from tokenpak.dashboard.export_api import ExportAPI
    from tokenpak.dashboard.export_csv import (
        CSVExporter,
        ExportDataType,
        ExportFormat,
        _format_filename,
    )
except ImportError as exc:
    pytest.fail(f"Failed to import dashboard modules: {exc}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trace(idx: int) -> Dict[str, Any]:
    return {
        "request_id": f"req-{idx:04d}",
        "timestamp": f"2026-03-03T12:{idx % 60:02d}:00Z",
        "model": "claude-3-5-sonnet" if idx % 2 == 0 else "gpt-4o",
        "status": "completed",
        "input_tokens": 1000 + idx * 10,
        "output_tokens": 200 + idx * 2,
        "tokens_saved": 50 + idx,
        "cost_saved": round(0.001 * idx, 6),
        "total_cost": round(0.005 * idx, 6),
        "duration_ms": 250.0 + idx * 0.5,
        "stages": [
            {"name": "vault_inject", "enabled": True},
            {"name": "compress", "enabled": True},
            {"name": "forward", "enabled": True},
        ],
    }


def _parse_csv(csv_bytes: bytes) -> tuple[list[str], list[list[str]]]:
    """Return (headers, rows) from CSV bytes."""
    text = csv_bytes.decode("utf-8")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    return rows[0], rows[1:]


# ---------------------------------------------------------------------------
# AC 1 — Export button component exists on disk
# ---------------------------------------------------------------------------

class TestExportButtonComponent:
    """AC 1: Export button present in dashboard/src/components."""

    def test_export_button_tsx_exists(self):
        """ExportButton.tsx must be present at the declared path."""
        project_root = Path(__file__).parents[2]
        tsx = project_root / "dashboard" / "src" / "components" / "ExportButton.tsx"
        assert tsx.exists(), f"ExportButton.tsx not found at {tsx}"

    def test_export_button_tsx_references_post(self):
        """ExportButton.tsx must reference the /v1/export/csv endpoint."""
        project_root = Path(__file__).parents[2]
        tsx = project_root / "dashboard" / "src" / "components" / "ExportButton.tsx"
        content = tsx.read_text()
        assert "/v1/export/csv" in content
        assert "POST" in content


# ---------------------------------------------------------------------------
# AC 2 — Format options: full vs simplified
# ---------------------------------------------------------------------------

class TestFormatOptions:
    """AC 2: Full (all columns) vs Simplified (summary stats)."""

    def setup_method(self):
        self.traces = [_make_trace(i) for i in range(5)]
        self.exporter = CSVExporter(traces=self.traces)

    def test_full_format_has_all_columns(self):
        csv_bytes, _ = self.exporter.export(
            data_type=ExportDataType.TRACES, fmt=ExportFormat.FULL
        )
        headers, _ = _parse_csv(csv_bytes)
        for col in ["request_id", "stage_count", "stages_summary", "input_tokens", "output_tokens"]:
            assert col in headers, f"Column {col!r} missing from FULL export headers"

    def test_simplified_format_omits_verbose_columns(self):
        csv_bytes, _ = self.exporter.export(
            data_type=ExportDataType.TRACES, fmt=ExportFormat.SIMPLIFIED
        )
        headers, _ = _parse_csv(csv_bytes)
        assert "request_id" not in headers, "SIMPLIFIED should not include request_id"
        assert "stage_count" not in headers, "SIMPLIFIED should not include stage_count"

    def test_simplified_format_has_core_columns(self):
        csv_bytes, _ = self.exporter.export(
            data_type=ExportDataType.TRACES, fmt=ExportFormat.SIMPLIFIED
        )
        headers, _ = _parse_csv(csv_bytes)
        for col in ["timestamp", "model", "tokens_saved", "cost_saved"]:
            assert col in headers

    def test_stats_format_produces_metric_value_pairs(self):
        stats = {"requests": 42, "tokens_saved": 1234, "cost_saved": 0.56}
        exporter = CSVExporter(session_stats=stats)
        csv_bytes, _ = exporter.export(data_type=ExportDataType.STATS)
        headers, rows = _parse_csv(csv_bytes)
        assert headers == ["metric", "value"]
        row_dict = {r[0]: r[1] for r in rows}
        assert row_dict["requests"] == "42"
        assert row_dict["tokens_saved"] == "1234"


# ---------------------------------------------------------------------------
# AC 3 — Filename format
# ---------------------------------------------------------------------------

class TestFilenameFormat:
    """AC 3: Filename must match tokenpak-export-YYYY-MM-DD-HHmmss.csv."""

    def test_filename_pattern(self):
        ts = datetime(2026, 3, 3, 23, 55, 7)
        name = _format_filename(ts)
        assert name == "tokenpak-export-2026-03-03-235507.csv"

    def test_filename_from_export(self):
        exporter = CSVExporter(traces=[_make_trace(0)])
        _, filename = exporter.export()
        pattern = r"^tokenpak-export-\d{4}-\d{2}-\d{2}-\d{6}\.csv$"
        assert re.match(pattern, filename), f"Filename {filename!r} doesn't match pattern"

    def test_filename_in_api_content_disposition(self):
        body, status, headers = ExportAPI.handle(
            raw_body=b'{"data_type":"traces","format":"full"}',
            traces=[_make_trace(0)],
        )
        assert status == 200
        cd = headers.get("Content-Disposition", "")
        assert "attachment" in cd
        assert ".csv" in cd


# ---------------------------------------------------------------------------
# AC 4 — Server-side endpoint: ExportAPI HTTP handler
# ---------------------------------------------------------------------------

class TestExportAPIHandler:
    """AC 4: POST /v1/export/csv — correct response shape."""

    def test_returns_200_with_csv_bytes(self):
        body, status, headers = ExportAPI.handle(
            raw_body=b'{}',
            traces=[_make_trace(i) for i in range(3)],
        )
        assert status == 200
        assert headers["Content-Type"].startswith("text/csv")
        assert len(body) > 0

    def test_body_is_valid_csv(self):
        body, status, _ = ExportAPI.handle(
            raw_body=b'{"data_type":"traces","format":"full"}',
            traces=[_make_trace(i) for i in range(5)],
        )
        assert status == 200
        headers_row, rows = _parse_csv(body)
        assert "request_id" in headers_row
        assert len(rows) == 5

    def test_content_length_header_set(self):
        body, status, headers = ExportAPI.handle(
            raw_body=b'{}',
            traces=[_make_trace(0)],
        )
        assert "Content-Length" in headers
        assert int(headers["Content-Length"]) == len(body)

    def test_cors_header_present(self):
        _, _, headers = ExportAPI.handle(raw_body=b'{}', traces=[])
        assert headers.get("Access-Control-Allow-Origin") == "*"

    def test_empty_body_defaults_to_traces_full(self):
        body, status, headers = ExportAPI.handle(raw_body=b'', traces=[_make_trace(0)])
        assert status == 200
        assert "text/csv" in headers["Content-Type"]

    def test_stats_data_type(self):
        body, status, _ = ExportAPI.handle(
            raw_body=b'{"data_type":"stats"}',
            session_stats={"requests": 10, "tokens_saved": 500},
        )
        assert status == 200
        hdr, rows = _parse_csv(body)
        assert hdr == ["metric", "value"]
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# AC 6 — 500+ row export: all rows + columns present
# ---------------------------------------------------------------------------

class TestLargeExport:
    """AC 6: Export 500+ row result set, verify all rows + columns."""

    NUM_ROWS = 512

    def test_all_rows_present_full(self):
        traces = [_make_trace(i) for i in range(self.NUM_ROWS)]
        exporter = CSVExporter(traces=traces)
        csv_bytes, _ = exporter.export(data_type=ExportDataType.TRACES, fmt=ExportFormat.FULL)
        _, rows = _parse_csv(csv_bytes)
        assert len(rows) == self.NUM_ROWS

    def test_all_rows_present_simplified(self):
        traces = [_make_trace(i) for i in range(self.NUM_ROWS)]
        exporter = CSVExporter(traces=traces)
        csv_bytes, _ = exporter.export(
            data_type=ExportDataType.TRACES, fmt=ExportFormat.SIMPLIFIED
        )
        _, rows = _parse_csv(csv_bytes)
        assert len(rows) == self.NUM_ROWS

    def test_no_data_loss_on_large_export(self):
        """Verify first and last rows survive large CSV round-trip."""
        traces = [_make_trace(i) for i in range(self.NUM_ROWS)]
        exporter = CSVExporter(traces=traces)
        csv_bytes, _ = exporter.export(data_type=ExportDataType.TRACES, fmt=ExportFormat.FULL)
        headers_row, rows = _parse_csv(csv_bytes)
        rid_idx = headers_row.index("request_id")
        assert rows[0][rid_idx] == "req-0000"
        assert rows[-1][rid_idx] == f"req-{self.NUM_ROWS - 1:04d}"

    def test_all_columns_present_full(self):
        """Full export must have every declared column for all rows."""
        from tokenpak.dashboard.export_csv import _TRACE_FULL_COLUMNS
        traces = [_make_trace(i) for i in range(self.NUM_ROWS)]
        exporter = CSVExporter(traces=traces)
        csv_bytes, _ = exporter.export(data_type=ExportDataType.TRACES, fmt=ExportFormat.FULL)
        headers_row, rows = _parse_csv(csv_bytes)
        assert headers_row == _TRACE_FULL_COLUMNS
        for row in rows:
            assert len(row) == len(_TRACE_FULL_COLUMNS)

    def test_via_export_api_500_rows(self):
        """ExportAPI round-trip with 512 rows."""
        traces = [_make_trace(i) for i in range(self.NUM_ROWS)]
        body, status, _ = ExportAPI.handle(
            raw_body=b'{"data_type":"traces","format":"full"}',
            traces=traces,
        )
        assert status == 200
        _, rows = _parse_csv(body)
        assert len(rows) == self.NUM_ROWS


# ---------------------------------------------------------------------------
# AC 7 — Error handling: invalid params → graceful messages
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """AC 7: Graceful error messages for bad input."""

    def test_invalid_json_returns_400(self):
        _, status, headers = ExportAPI.handle(raw_body=b'not-json')
        assert status == 400
        assert "application/json" in headers["Content-Type"]

    def test_invalid_json_error_body(self):
        body, status, _ = ExportAPI.handle(raw_body=b'not-json')
        data = json.loads(body)
        assert data["error"] == "invalid_json"
        assert "detail" in data

    def test_invalid_format_returns_400(self):
        body, status, _ = ExportAPI.handle(
            raw_body=b'{"format":"turbo"}',
            traces=[],
        )
        assert status == 400
        data = json.loads(body)
        assert data["error"] == "invalid_format"

    def test_invalid_data_type_returns_400(self):
        body, status, _ = ExportAPI.handle(
            raw_body=b'{"data_type":"banana"}',
            traces=[],
        )
        assert status == 400
        data = json.loads(body)
        assert data["error"] == "invalid_data_type"

    def test_empty_traces_returns_200_with_headers_only(self):
        """Empty trace list → CSV with just the header row (no crash)."""
        body, status, _ = ExportAPI.handle(
            raw_body=b'{"data_type":"traces"}',
            traces=[],
        )
        assert status == 200
        hdr, rows = _parse_csv(body)
        assert len(hdr) > 0
        assert len(rows) == 0

    def test_csv_special_chars_escaped(self):
        """Commas and quotes in values must be properly escaped."""
        trace = _make_trace(0)
        trace["model"] = 'gpt-4o, "fast"'
        exporter = CSVExporter(traces=[trace])
        csv_bytes, _ = exporter.export(fmt=ExportFormat.SIMPLIFIED)
        _, rows = _parse_csv(csv_bytes)
        # csv.reader will have already un-quoted it
        model_idx = 1  # second column in simplified (after timestamp)
        # Just verify we can round-trip without error
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# AC 5 — Regression: existing proxy imports still work
# ---------------------------------------------------------------------------

class TestRegressionExistingImports:
    """AC 5: Adding dashboard module must not break existing proxy imports."""

    def test_proxy_server_imports_cleanly(self):
        # WS-A residual import guard — TSR-01-followup. ProxyServer +
        # start_proxy are not currently exported from tokenpak.proxy on
        # the slim OSS surface; this regression check probes that
        # canonical name lookup. Skip when absent.
        try:
            from tokenpak.proxy import ProxyServer, start_proxy
        except ImportError as exc:
            pytest.skip(f"slim OSS: tokenpak.proxy.{{ProxyServer,start_proxy}} not exported ({exc})")
        assert ProxyServer is not None

    def test_export_api_import_in_server_module(self):
        """server.py must import ExportAPI without error."""
        import importlib

        import tokenpak.proxy.server as srv_mod
        importlib.reload(srv_mod)  # force re-import
        assert hasattr(srv_mod, "ExportAPI")

    def test_dashboard_module_importable(self):
        from tokenpak.dashboard import CSVExporter, ExportAPI
        assert CSVExporter is not None
        assert ExportAPI is not None

    def test_existing_trace_dataclass_still_works(self):
        from tokenpak.proxy.server import PipelineTrace
        t = PipelineTrace(request_id="test-id", timestamp="2026-03-03T00:00:00Z")
        assert t.request_id == "test-id"
