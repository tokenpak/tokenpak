"""
Tests for tokenpak.compression.schema_extractor

Covers:
  1. Meeting note detection + field extraction
  2. Pull request detection + field extraction
  3. Bug report detection + field extraction
  4. Log output detection + field extraction
  5. Config file detection + field extraction
  6. Unknown / low-confidence passthrough
  7. Empty input passthrough
  8. extract_message integration
  9. Compression ratio is < 1.0 for verbose docs
 10. detect_type smoke test for all five types
"""

from __future__ import annotations

import pytest
from tokenpak.compression.schema_extractor import (
    CONFIDENCE_THRESHOLD,
    TEMPLATES,
    ExtractionResult,
    SchemaExtractor,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MEETING_TEXT = """
Meeting Notes — Q1 Planning Sync

Attendees: Alice, Bob, Carol, Dave

Decisions:
- We will ship the new dashboard by March 15
- Backend refactor is approved for Q2

Blockers:
- Alice is blocked on the API contract review
- Dave needs access to the staging environment

Follow-ups:
- Bob to draft the API spec by Friday
- Carol to schedule a demo with the customer
"""

PR_TEXT = """
Pull Request #427 — Add OAuth token refresh

Files changed: auth/token.py, auth/middleware.py, tests/test_auth.py
Tests added for edge case where refresh token is expired.
Risk level: medium
Dependencies: requests>=2.28, cryptography>=40
"""

BUG_REPORT_TEXT = """
Bug Report: Login fails on mobile Safari

Symptom: Users cannot log in when using Mobile Safari on iOS 17.

Steps to Reproduce:
1. Open the app in Safari on iPhone (iOS 17)
2. Enter valid credentials
3. Tap "Sign In"

Expected: User is redirected to the dashboard.
Actual: A blank white screen appears and login does not complete.

Environment: iOS 17.2, Safari 17, iPhone 14 Pro
"""

LOG_TEXT = """
2026-03-10 14:01:00 INFO  Server started on port 8080
2026-03-10 14:01:05 INFO  Accepting connections
2026-03-10 14:02:15 ERROR Failed to connect to database: timeout
2026-03-10 14:02:16 ERROR Failed to connect to database: timeout
2026-03-10 14:03:00 CRITICAL Max retries exceeded — shutting down
2026-03-10 14:03:01 INFO  Shutdown complete
"""

CONFIG_TEXT = """
Changed config (yaml):
+ max_connections: 100
+ timeout_seconds: 30
- max_connections: 50
- timeout_seconds: 10
"""

UNRELATED_TEXT = """
Once upon a time in a land far away, there was a small village nestled between
two great mountains. The villagers were known for their extraordinary bread,
which they baked every morning before the sun rose.
"""


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMeetingExtraction:
    def setup_method(self):
        self.extractor = SchemaExtractor()

    def test_detects_meeting_type(self):
        result = self.extractor.extract(MEETING_TEXT)
        assert result.doc_type == "meeting", f"Expected 'meeting', got '{result.doc_type}'"

    def test_meeting_not_passthrough(self):
        result = self.extractor.extract(MEETING_TEXT)
        assert not result.passthrough

    def test_meeting_extracts_attendees(self):
        result = self.extractor.extract(MEETING_TEXT)
        assert "attendees" in result.fields
        assert len(result.fields["attendees"]) >= 2

    def test_meeting_extracts_decisions(self):
        result = self.extractor.extract(MEETING_TEXT)
        assert "decisions" in result.fields
        assert len(result.fields["decisions"]) >= 1

    def test_meeting_extracts_blockers(self):
        result = self.extractor.extract(MEETING_TEXT)
        assert "blockers" in result.fields

    def test_meeting_extracts_follow_ups(self):
        result = self.extractor.extract(MEETING_TEXT)
        assert "follow_ups" in result.fields

    def test_meeting_compact_starts_with_type_header(self):
        result = self.extractor.extract(MEETING_TEXT)
        assert result.compact.startswith("[MEETING]")

    def test_meeting_compact_contains_attendees(self):
        result = self.extractor.extract(MEETING_TEXT)
        assert "attendees:" in result.compact.lower()


class TestPullRequestExtraction:
    def setup_method(self):
        self.extractor = SchemaExtractor()

    def test_detects_pr_type(self):
        result = self.extractor.extract(PR_TEXT)
        assert result.doc_type == "pull_request"

    def test_pr_extracts_risk_level(self):
        result = self.extractor.extract(PR_TEXT)
        assert "risk_level" in result.fields
        assert result.fields["risk_level"].lower() != "unknown"

    def test_pr_extracts_dependencies(self):
        result = self.extractor.extract(PR_TEXT)
        assert "dependencies" in result.fields
        assert len(result.fields["dependencies"]) >= 1

    def test_pr_compact_header(self):
        result = self.extractor.extract(PR_TEXT)
        assert "[PULL_REQUEST]" in result.compact


class TestBugReportExtraction:
    def setup_method(self):
        self.extractor = SchemaExtractor()

    def test_detects_bug_report(self):
        result = self.extractor.extract(BUG_REPORT_TEXT)
        assert result.doc_type == "bug_report"

    def test_bug_report_symptom(self):
        result = self.extractor.extract(BUG_REPORT_TEXT)
        assert "symptom" in result.fields
        assert len(result.fields["symptom"]) > 5

    def test_bug_report_expected_actual(self):
        result = self.extractor.extract(BUG_REPORT_TEXT)
        assert result.fields.get("expected", "unknown") != "unknown"
        assert result.fields.get("actual", "unknown") != "unknown"

    def test_bug_report_environment(self):
        result = self.extractor.extract(BUG_REPORT_TEXT)
        assert result.fields.get("environment", "unknown") != "unknown"

    def test_bug_report_repro_steps(self):
        result = self.extractor.extract(BUG_REPORT_TEXT)
        assert isinstance(result.fields.get("repro_steps"), list)


class TestLogOutputExtraction:
    def setup_method(self):
        self.extractor = SchemaExtractor()

    def test_detects_log_type(self):
        result = self.extractor.extract(LOG_TEXT)
        assert result.doc_type == "log_output"

    def test_log_error_count(self):
        result = self.extractor.extract(LOG_TEXT)
        # 2 ERROR + 1 CRITICAL = 3
        assert result.fields.get("error_count", 0) >= 2

    def test_log_first_last_error(self):
        result = self.extractor.extract(LOG_TEXT)
        assert result.fields.get("first_error", "none") != "none"
        assert result.fields.get("last_error", "none") != "none"

    def test_log_timespan(self):
        result = self.extractor.extract(LOG_TEXT)
        assert "→" in result.fields.get("timespan", "")

    def test_log_unique_errors_capped(self):
        result = self.extractor.extract(LOG_TEXT)
        assert len(result.fields.get("unique_errors", [])) <= 5


class TestConfigFileExtraction:
    def setup_method(self):
        self.extractor = SchemaExtractor()

    def test_detects_config_type(self):
        result = self.extractor.extract(CONFIG_TEXT)
        assert result.doc_type == "config_file"

    def test_config_detects_added_removed_keys(self):
        result = self.extractor.extract(CONFIG_TEXT)
        assert len(result.fields.get("added_keys", [])) >= 1
        assert len(result.fields.get("removed_keys", [])) >= 1

    def test_config_format_detected(self):
        result = self.extractor.extract(CONFIG_TEXT)
        assert result.fields.get("format") is not None


class TestPassthrough:
    def setup_method(self):
        self.extractor = SchemaExtractor()

    def test_unrelated_text_is_passthrough(self):
        result = self.extractor.extract(UNRELATED_TEXT)
        assert result.passthrough

    def test_empty_string_is_passthrough(self):
        result = self.extractor.extract("")
        assert result.passthrough
        assert result.doc_type == "unknown"

    def test_whitespace_only_is_passthrough(self):
        result = self.extractor.extract("   \n\t  ")
        assert result.passthrough

    def test_passthrough_has_no_compact(self):
        result = self.extractor.extract(UNRELATED_TEXT)
        assert result.compact == ""


class TestExtractionResult:
    def setup_method(self):
        self.extractor = SchemaExtractor()

    def test_compression_ratio_less_than_one_for_verbose_meeting(self):
        result = self.extractor.extract(MEETING_TEXT)
        # Compact should be shorter than original
        assert result.compression_ratio < 1.0, (
            f"Expected compression_ratio < 1.0, got {result.compression_ratio}"
        )

    def test_confidence_range(self):
        for text in [MEETING_TEXT, PR_TEXT, BUG_REPORT_TEXT, LOG_TEXT, CONFIG_TEXT]:
            result = self.extractor.extract(text)
            assert 0.0 <= result.confidence <= 1.0


class TestExtractMessage:
    def setup_method(self):
        self.extractor = SchemaExtractor()

    def test_message_content_replaced(self):
        msg = {"role": "user", "content": MEETING_TEXT}
        out = self.extractor.extract_message(msg)
        assert out["content"] != MEETING_TEXT
        assert "[MEETING]" in out["content"]

    def test_message_metadata_added(self):
        msg = {"role": "user", "content": MEETING_TEXT}
        out = self.extractor.extract_message(msg)
        assert "_schema_extraction" in out
        assert out["_schema_extraction"]["doc_type"] == "meeting"

    def test_passthrough_message_unchanged(self):
        msg = {"role": "user", "content": UNRELATED_TEXT}
        out = self.extractor.extract_message(msg)
        assert out["content"] == UNRELATED_TEXT
        assert "_schema_extraction" not in out

    def test_message_with_non_string_content_passthrough(self):
        msg = {"role": "user", "content": None}
        out = self.extractor.extract_message(msg)
        assert out is msg  # returned unchanged


class TestDetectTypeSmokeTest:
    """Quick smoke test: detect_type returns the right type for each fixture."""

    def setup_method(self):
        self.extractor = SchemaExtractor()

    @pytest.mark.parametrize("text,expected_type", [
        (MEETING_TEXT, "meeting"),
        (PR_TEXT, "pull_request"),
        (BUG_REPORT_TEXT, "bug_report"),
        (LOG_TEXT, "log_output"),
        (CONFIG_TEXT, "config_file"),
    ])
    def test_detect_type(self, text, expected_type):
        doc_type, confidence = self.extractor.detect_type(text)
        assert doc_type == expected_type, (
            f"For {expected_type}: got '{doc_type}' (confidence={confidence:.3f})"
        )


class TestTemplates:
    def test_templates_cover_all_types(self):
        expected = {"meeting", "pull_request", "bug_report", "log_output", "config_file"}
        assert set(TEMPLATES.keys()) == expected

    def test_each_template_has_fields(self):
        for doc_type, fields in TEMPLATES.items():
            assert len(fields) >= 3, f"{doc_type} template has fewer than 3 fields"

    def test_custom_template_override(self):
        extractor = SchemaExtractor(templates={"custom_type": ["field_a", "field_b"]})
        assert "custom_type" in extractor.templates
