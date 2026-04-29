"""Tests for TIP-06 cache miss reason tracking.

Covers:
- CacheMissRecord construction and serialization
- aggregate_cache_miss_reasons grouping
- cache_stage_trace_to_miss_record conversion
- DB round-trip via TelemetryDB
"""

from __future__ import annotations

import time

import pytest

from tokenpak.telemetry.cache_miss import (
    CacheMissRecord,
    MissReasonSummary,
    aggregate_cache_miss_reasons,
    cache_stage_trace_to_miss_record,
    format_miss_reason_summary,
)
from tokenpak.telemetry.storage import TelemetryDB


# ---------------------------------------------------------------------------
# CacheMissRecord
# ---------------------------------------------------------------------------


def test_cache_miss_record_to_row():
    rec = CacheMissRecord(
        request_id="req-1",
        reason="route_not_cacheable",
        route_class="code_edit",
        platform="openclaw",
        model="gpt-5.5",
    )
    row = rec.to_row()
    assert row["request_id"] == "req-1"
    assert row["reason"] == "route_not_cacheable"
    assert row["route_class"] == "code_edit"
    assert row["cache_type"] == "semantic"


def test_cache_miss_record_default_timestamp():
    before = time.time()
    rec = CacheMissRecord(request_id="req-2", reason="flag_off")
    after = time.time()
    assert before <= rec.timestamp <= after


# ---------------------------------------------------------------------------
# aggregate_cache_miss_reasons
# ---------------------------------------------------------------------------


def test_aggregate_groups_by_reason():
    records = [
        CacheMissRecord("r1", reason="route_not_cacheable", route_class="code_edit"),
        CacheMissRecord("r2", reason="route_not_cacheable", route_class="code_edit"),
        CacheMissRecord("r3", reason="flag_off"),
        CacheMissRecord("r4", reason="no_scope_key"),
    ]
    by_reason = aggregate_cache_miss_reasons(records)
    assert by_reason["route_not_cacheable"].count == 2
    assert by_reason["flag_off"].count == 1
    assert by_reason["no_scope_key"].count == 1


def test_aggregate_top_routes():
    records = [
        CacheMissRecord("r1", reason="route_not_cacheable", route_class="code_edit"),
        CacheMissRecord("r2", reason="route_not_cacheable", route_class="code_edit"),
        CacheMissRecord("r3", reason="route_not_cacheable", route_class="debugging"),
    ]
    by_reason = aggregate_cache_miss_reasons(records)
    summary = by_reason["route_not_cacheable"]
    assert summary.top_routes["code_edit"] == 2
    assert summary.top_routes["debugging"] == 1


def test_aggregate_empty():
    assert aggregate_cache_miss_reasons([]) == {}


# ---------------------------------------------------------------------------
# cache_stage_trace_to_miss_record
# ---------------------------------------------------------------------------


class _FakeTrace:
    def __init__(self, hit, miss_reason, route="unknown"):
        self.hit = hit
        self.miss_reason = miss_reason
        self.route = route


def test_convert_miss_trace():
    trace = _FakeTrace(hit=False, miss_reason="route_not_cacheable", route="code_edit")
    record = cache_stage_trace_to_miss_record(
        "req-10", trace, platform="openclaw", model="gpt-5.5"
    )
    assert record is not None
    assert record.reason == "route_not_cacheable"
    assert record.route_class == "code_edit"
    assert record.platform == "openclaw"
    assert record.model == "gpt-5.5"


def test_convert_hit_trace_returns_none():
    trace = _FakeTrace(hit=True, miss_reason="")
    record = cache_stage_trace_to_miss_record("req-11", trace)
    assert record is None


def test_convert_context_reuse_only_returns_none():
    # context-reuse-only is not a true miss — don't record it
    trace = _FakeTrace(hit=True, miss_reason="context-reuse-only")
    record = cache_stage_trace_to_miss_record("req-12", trace)
    assert record is None


def test_convert_empty_reason_returns_none():
    trace = _FakeTrace(hit=False, miss_reason="")
    record = cache_stage_trace_to_miss_record("req-13", trace)
    assert record is None


# ---------------------------------------------------------------------------
# format_miss_reason_summary
# ---------------------------------------------------------------------------


def test_format_miss_reason_summary_shows_reasons():
    records = [
        CacheMissRecord("r1", reason="route_not_cacheable"),
        CacheMissRecord("r2", reason="route_not_cacheable"),
        CacheMissRecord("r3", reason="flag_off"),
    ]
    by_reason = aggregate_cache_miss_reasons(records)
    output = format_miss_reason_summary(by_reason)
    assert "route_not_cacheable" in output
    assert "flag_off" in output


def test_format_miss_reason_summary_empty():
    output = format_miss_reason_summary({})
    assert "No cache miss" in output


# ---------------------------------------------------------------------------
# TelemetryDB round-trip
# ---------------------------------------------------------------------------


def test_db_insert_and_query_cache_miss():
    db = TelemetryDB(":memory:")
    records = [
        CacheMissRecord("req-1", reason="route_not_cacheable", route_class="code_edit").to_row(),
        CacheMissRecord("req-2", reason="route_not_cacheable", route_class="code_edit").to_row(),
        CacheMissRecord("req-3", reason="flag_off").to_row(),
    ]
    for row in records:
        db.insert_cache_miss(row)

    summary = db.query_cache_miss_summary(days=7)
    by_reason = {r["reason"]: r for r in summary}

    assert by_reason["route_not_cacheable"]["count"] == 2
    assert by_reason["flag_off"]["count"] == 1


def test_db_cache_miss_summary_empty():
    db = TelemetryDB(":memory:")
    result = db.query_cache_miss_summary(days=7)
    assert result == []
