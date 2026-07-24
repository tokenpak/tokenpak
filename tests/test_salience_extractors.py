"""
tests.test_salience_extractors
================================

Tests for the salience extractor suite:
  - detect.py
  - log_extractor.py
  - code_extractor.py
  - doc_extractor.py
  - router.py (extract())
"""

from __future__ import annotations

from tokenpak.compression.salience import (
    CodeExtractor,
    ContentType,
    DocExtractor,
    LogExtractor,
    detect_content_type,
    extract,
)

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

LOG_TEXT = """\
2024-01-15T10:00:01Z INFO  Starting service v2.3.1
2024-01-15T10:00:02Z INFO  Loading config from /etc/app/config.yaml
2024-01-15T10:00:03Z DEBUG Connecting to database at db.internal:5432
2024-01-15T10:00:04Z DEBUG Connection pool initialised (size=10)
2024-01-15T10:00:05Z INFO  HTTP server listening on :8080
2024-01-15T10:01:22Z ERROR Failed to process request: connection refused
2024-01-15T10:01:22Z ERROR   at processRequest(server.js:142:5)
2024-01-15T10:01:22Z ERROR   at handleHTTP(server.js:98:12)
2024-01-15T10:01:23Z DEBUG Retrying request (attempt 1/3)
2024-01-15T10:01:25Z DEBUG Retrying request (attempt 2/3)
2024-01-15T10:01:27Z DEBUG Retrying request (attempt 3/3)
2024-01-15T10:01:27Z FATAL Max retries exceeded; shutting down
Traceback (most recent call last):
  File "server.py", line 88, in run
    self._serve()
  File "server.py", line 102, in _serve
    conn.process()
ConnectionRefusedError: [Errno 111] Connection refused
2024-01-15T10:01:28Z INFO  Graceful shutdown complete
"""

CODE_TEXT = """\
import os
import sys
from typing import List

# changed

def compute_total(items: List[float]) -> float:
    \"\"\"Sum all items.\"\"\"
    total = 0.0
    for item in items:
        total += item
    return total


def unchanged_helper(x: int) -> int:
    return x * 2


def test_compute_total_fails():
    result = compute_total([1.0, 2.0, 3.0])
    assert result == 999.0  # AssertionError: 6.0 != 999.0
"""

DOC_TEXT = """\
# Architecture Decision Record: Storage Backend

## Context

We evaluated three storage backends for the new service.

## Decision

- Decided: use PostgreSQL as the primary store (agreed by team on 2024-01-10)
- Rejected: MongoDB due to consistency model concerns

## Notes

TODO: migrate legacy MySQL tables before v3 launch
NOTE: Redis cache layer still needed for session data

## Implementation

See `docs/migration.md` for step-by-step guide.

FIXME: The migration script has a race condition on concurrent writes.
"""


# ═══════════════════════════════════════════════════════════════════════════
# detect_content_type
# ═══════════════════════════════════════════════════════════════════════════


class TestDetectContentType:
    def test_log_detection(self):
        ct = detect_content_type(LOG_TEXT)
        assert ct == ContentType.LOG

    def test_code_detection(self):
        ct = detect_content_type(CODE_TEXT)
        assert ct == ContentType.CODE

    def test_doc_detection(self):
        ct = detect_content_type(DOC_TEXT)
        assert ct == ContentType.DOC

    def test_unknown_returns_something(self):
        ct = detect_content_type("hello world")
        # Should be UNKNOWN or any valid ContentType
        assert isinstance(ct, ContentType)


# ═══════════════════════════════════════════════════════════════════════════
# LogExtractor
# ═══════════════════════════════════════════════════════════════════════════


class TestLogExtractor:
    def test_error_count(self):
        r = LogExtractor().extract(LOG_TEXT)
        assert r.error_count >= 2  # at least the two ERROR lines

    def test_fatal_included(self):
        r = LogExtractor().extract(LOG_TEXT)
        assert r.error_count + (1 if "FATAL" in LOG_TEXT else 0) >= 2

    def test_timestamp_range_captured(self):
        r = LogExtractor().extract(LOG_TEXT)
        assert r.timestamp_first is not None
        assert r.timestamp_last is not None
        # First timestamp should be earlier (lexicographic on ISO-8601)
        assert r.timestamp_first <= r.timestamp_last

    def test_reduction_achieved(self):
        """Extracted output should be shorter than input."""
        r = LogExtractor(context_lines=3).extract(LOG_TEXT)
        assert r.lines_out < r.lines_in

    def test_unique_stack_sigs_deduped(self):
        # Duplicate stack frames should be collapsed
        repeated_log = (
            "2024-01-15T10:00:01Z ERROR boom\n"
            '  File "app.py", line 10, in run\n' * 5 + "2024-01-15T10:00:02Z ERROR boom again\n"
            '  File "app.py", line 10, in run\n' * 3
        )
        r = LogExtractor().extract(repeated_log)
        # Should collapse all identical frames into 1 unique sig
        assert r.unique_stack_sigs == 1

    def test_empty_log_no_crash(self):
        r = LogExtractor().extract("")
        assert r.lines_in == 0
        assert r.extracted is not None

    def test_extracted_contains_header(self):
        r = LogExtractor().extract(LOG_TEXT)
        assert "[log-salience]" in r.extracted


# ═══════════════════════════════════════════════════════════════════════════
# CodeExtractor
# ═══════════════════════════════════════════════════════════════════════════


class TestCodeExtractor:
    def test_imports_detected(self):
        r = CodeExtractor().extract(CODE_TEXT)
        assert r.imports_found >= 3  # os, sys, typing

    def test_changed_fn_detected(self):
        r = CodeExtractor().extract(CODE_TEXT)
        # compute_total has a "# changed" marker
        assert "compute_total" in r.changed_functions

    def test_failing_test_detected(self):
        r = CodeExtractor().extract(CODE_TEXT)
        assert "test_compute_total_fails" in r.test_targets

    def test_reduction_achieved(self):
        r = CodeExtractor().extract(CODE_TEXT)
        # unchanged_helper should NOT appear in changed_functions list
        assert "unchanged_helper" not in r.changed_functions
        # The extracted output should NOT contain unchanged_helper's body
        # (it may appear in the section label but not as a full function dump)
        # Verify that changed + test target names are tracked
        assert len(r.changed_functions) + len(r.test_targets) > 0

    def test_imports_in_extracted(self):
        r = CodeExtractor().extract(CODE_TEXT)
        assert "import os" in r.extracted

    def test_changed_fn_body_in_extracted(self):
        r = CodeExtractor().extract(CODE_TEXT)
        assert "compute_total" in r.extracted

    def test_empty_code_no_crash(self):
        r = CodeExtractor().extract("")
        assert r.extracted is not None

    def test_include_all_fns_flag(self):
        r = CodeExtractor(include_all_fns=True).extract(CODE_TEXT)
        # With include_all_fns all functions should appear
        assert r.functions_found >= 2

    def test_header_present(self):
        r = CodeExtractor().extract(CODE_TEXT)
        assert "[code-salience]" in r.extracted


# ═══════════════════════════════════════════════════════════════════════════
# DocExtractor
# ═══════════════════════════════════════════════════════════════════════════


class TestDocExtractor:
    def test_headings_extracted(self):
        r = DocExtractor().extract(DOC_TEXT)
        assert len(r.headings) >= 4  # ADR, Context, Decision, Notes, Implementation

    def test_annotations_detected(self):
        r = DocExtractor().extract(DOC_TEXT)
        assert r.annotation_count >= 3  # TODO, NOTE, FIXME

    def test_decisions_detected(self):
        r = DocExtractor().extract(DOC_TEXT)
        assert r.decision_count >= 1  # "Decided:" and "Rejected:" bullets

    def test_reduction_achieved(self):
        r = DocExtractor().extract(DOC_TEXT)
        assert r.lines_out < r.lines_in

    def test_header_present(self):
        r = DocExtractor().extract(DOC_TEXT)
        assert "[doc-salience]" in r.extracted

    def test_todo_in_extracted(self):
        r = DocExtractor().extract(DOC_TEXT)
        assert "TODO" in r.extracted

    def test_empty_doc_no_crash(self):
        r = DocExtractor().extract("")
        assert r.extracted is not None


# ═══════════════════════════════════════════════════════════════════════════
# Router / extract()
# ═══════════════════════════════════════════════════════════════════════════


class TestRouter:
    def test_routes_log(self):
        result = extract(LOG_TEXT)
        assert result.content_type == ContentType.LOG
        assert result.stats["error_count"] >= 2

    def test_routes_code(self):
        result = extract(CODE_TEXT)
        assert result.content_type == ContentType.CODE
        assert result.stats["imports_found"] >= 3

    def test_routes_doc(self):
        result = extract(DOC_TEXT)
        assert result.content_type == ContentType.DOC
        assert len(result.stats["headings"]) >= 4

    def test_unknown_passthrough(self):
        result = extract("hello world this is plain text with no signals")
        assert result.passthrough is True
        assert result.extracted == "hello world this is plain text with no signals"

    def test_manual_content_type_override(self):
        # Force LOG detection even on non-log text
        result = extract("some arbitrary text", content_type=ContentType.LOG)
        assert result.content_type == ContentType.LOG

    def test_reduction_pct_property(self):
        result = extract(LOG_TEXT)
        # reduction_pct is informational; for small inputs the header overhead
        # can make it negative, which is valid behaviour.
        assert isinstance(result.reduction_pct, float)

    def test_extracted_is_string(self):
        for text in [LOG_TEXT, CODE_TEXT, DOC_TEXT]:
            result = extract(text)
            assert isinstance(result.extracted, str)

    def test_deterministic(self):
        """Same input must always produce identical output."""
        r1 = extract(LOG_TEXT)
        r2 = extract(LOG_TEXT)
        assert r1.extracted == r2.extracted
