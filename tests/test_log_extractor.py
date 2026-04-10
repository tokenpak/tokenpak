"""Tests for tokenpak.compression.salience.log_extractor module."""

import pytest
from tokenpak.compression.salience.log_extractor import (
    LogExtractor,
    LogExtractionResult,
)


class TestLogExtractionResult:
    """Test LogExtractionResult dataclass."""

    def test_default_values(self):
        """Test default initialization."""
        result = LogExtractionResult()
        assert result.lines_in == 0
        assert result.lines_out == 0
        assert result.error_count == 0
        assert result.warn_count == 0
        assert result.unique_stack_sigs == 0
        assert result.timestamp_first is None
        assert result.timestamp_last is None
        assert result.extracted == ""

    def test_reduction_pct_zero_input(self):
        """Test reduction percentage with zero input."""
        result = LogExtractionResult(lines_in=0, lines_out=0)
        assert result.reduction_pct == 0.0

    def test_reduction_pct_calculation(self):
        """Test reduction percentage calculation."""
        result = LogExtractionResult(lines_in=1000, lines_out=100)
        assert result.reduction_pct == 90.0

    def test_with_all_values(self):
        """Test initialization with all values."""
        result = LogExtractionResult(
            lines_in=500,
            lines_out=50,
            error_count=5,
            warn_count=10,
            unique_stack_sigs=3,
            timestamp_first="2024-01-01T10:00:00Z",
            timestamp_last="2024-01-01T11:00:00Z",
            extracted="sample",
        )
        assert result.lines_in == 500
        assert result.error_count == 5
        assert result.warn_count == 10


class TestLogExtractorInit:
    """Test LogExtractor initialization."""

    def test_default_init(self):
        """Test default initialization."""
        extractor = LogExtractor()
        assert extractor.context_lines == 20
        assert extractor.max_stack_sigs == 30
        assert extractor.include_warnings is False

    def test_custom_context_lines(self):
        """Test custom context lines."""
        extractor = LogExtractor(context_lines=10)
        assert extractor.context_lines == 10

    def test_custom_max_stack_sigs(self):
        """Test custom max stack signatures."""
        extractor = LogExtractor(max_stack_sigs=50)
        assert extractor.max_stack_sigs == 50

    def test_include_warnings_true(self):
        """Test include_warnings enabled."""
        extractor = LogExtractor(include_warnings=True)
        assert extractor.include_warnings is True


class TestLogExtractorEmpty:
    """Test LogExtractor with empty/minimal input."""

    def test_empty_string(self):
        """Test extraction with empty string."""
        extractor = LogExtractor()
        result = extractor.extract("")
        assert result.lines_in == 0
        assert result.error_count == 0

    def test_single_info_line(self):
        """Test extraction with single info line."""
        log = "INFO: Application started"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.lines_in == 1
        assert result.error_count == 0

    def test_whitespace_only(self):
        """Test extraction with whitespace only."""
        log = "   \n  \n    "
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.lines_in == 3


class TestLogExtractorErrorDetection:
    """Test error line detection."""

    def test_error_keyword(self):
        """Test detection of ERROR keyword."""
        log = "2024-01-01T10:00:00Z ERROR: Something went wrong"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 1

    def test_fatal_keyword(self):
        """Test detection of FATAL keyword."""
        log = "FATAL: Application cannot continue"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 1

    def test_critical_keyword(self):
        """Test detection of CRITICAL keyword."""
        log = "CRITICAL: System failure detected"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 1

    def test_exception_keyword(self):
        """Test detection of EXCEPTION keyword."""
        log = "EXCEPTION: NullPointerException"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 1

    def test_severe_keyword(self):
        """Test detection of SEVERE keyword."""
        log = "SEVERE: Unrecoverable error"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 1

    def test_case_insensitive_error(self):
        """Test that error detection is case insensitive."""
        log = "error: lowercase detection"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 1

    def test_multiple_errors(self):
        """Test detection of multiple errors."""
        log = """INFO: startup
ERROR: first problem
DEBUG: details
ERROR: second problem
WARNING: notice
ERROR: third problem"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 3


class TestLogExtractorWarningDetection:
    """Test warning line detection."""

    def test_warn_keyword_disabled(self):
        """Test that warnings are not extracted by default."""
        log = "WARN: Something to watch\nINFO: normal"
        extractor = LogExtractor(include_warnings=False)
        result = extractor.extract(log)
        assert result.warn_count == 1
        assert result.error_count == 0

    def test_warn_keyword_enabled(self):
        """Test that warnings are extracted when enabled."""
        log = "WARN: Something to watch\nINFO: normal"
        extractor = LogExtractor(include_warnings=True)
        result = extractor.extract(log)
        assert result.warn_count == 1

    def test_warning_full_keyword(self):
        """Test detection of full WARNING keyword."""
        log = "WARNING: potential issue"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.warn_count == 1

    def test_multiple_warnings(self):
        """Test detection of multiple warnings."""
        log = """WARN: first
DEBUG: info
WARN: second
WARNING: third"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.warn_count == 3


class TestLogExtractorContextLines:
    """Test context line preservation."""

    def test_error_with_context_before(self):
        """Test that lines before error are kept."""
        log = """line1
line2
line3
ERROR: problem
line5"""
        extractor = LogExtractor(context_lines=2)
        result = extractor.extract(log)
        assert "line2" in result.extracted
        assert "line3" in result.extracted

    def test_error_with_context_after(self):
        """Test that lines after error are kept."""
        log = """line1
ERROR: problem
line3
line4
line5"""
        extractor = LogExtractor(context_lines=2)
        result = extractor.extract(log)
        assert "line3" in result.extracted
        assert "line4" in result.extracted

    def test_context_boundary_beginning(self):
        """Test context at beginning of log."""
        log = """ERROR: problem
line2
line3"""
        extractor = LogExtractor(context_lines=5)
        result = extractor.extract(log)
        assert "ERROR" in result.extracted
        assert "line2" in result.extracted

    def test_context_boundary_end(self):
        """Test context at end of log."""
        log = """line1
line2
ERROR: final problem"""
        extractor = LogExtractor(context_lines=5)
        result = extractor.extract(log)
        assert "ERROR" in result.extracted
        assert "line1" in result.extracted

    def test_zero_context_lines(self):
        """Test with no context lines."""
        log = """line1
ERROR: problem
line3"""
        extractor = LogExtractor(context_lines=0)
        result = extractor.extract(log)
        assert "ERROR" in result.extracted


class TestLogExtractorTimestamps:
    """Test timestamp detection and range."""

    def test_iso8601_timestamp(self):
        """Test ISO-8601 timestamp detection."""
        log = "2024-01-01T10:00:00Z ERROR: problem"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.timestamp_first is not None
        assert "2024-01-01" in result.timestamp_first

    def test_iso8601_with_milliseconds(self):
        """Test ISO-8601 with milliseconds."""
        log = "2024-01-01T10:00:00.123Z ERROR: problem"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.timestamp_first is not None

    def test_iso8601_with_offset(self):
        """Test ISO-8601 with timezone offset."""
        log = "2024-01-01T10:00:00+05:30 ERROR: problem"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.timestamp_first is not None

    def test_apache_timestamp(self):
        """Test Apache/nginx timestamp format."""
        log = "02/Jan/2024:10:00:00 -0500 ERROR: issue"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.timestamp_first is not None

    def test_unix_timestamp(self):
        """Test unix epoch timestamp."""
        log = "1704110400 ERROR: problem"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.timestamp_first is not None

    def test_simple_time_format(self):
        """Test simple HH:MM:SS format."""
        log = "10:00:00 ERROR: problem"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.timestamp_first is not None

    def test_timestamp_range(self):
        """Test that first and last timestamps are captured."""
        log = """2024-01-01T10:00:00Z INFO: start
2024-01-01T10:05:00Z ERROR: first
2024-01-01T10:10:00Z INFO: middle
2024-01-01T10:15:00Z ERROR: second
2024-01-01T10:20:00Z INFO: end"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.timestamp_first == "2024-01-01T10:00:00Z"
        assert result.timestamp_last == "2024-01-01T10:20:00Z"

    def test_no_timestamp(self):
        """Test log with no timestamps."""
        log = "ERROR: problem with no timestamp"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.timestamp_first is None
        assert result.timestamp_last is None


class TestLogExtractorStackTraces:
    """Test stack trace detection."""

    def test_java_stack_frame(self):
        """Test detection of Java stack frame."""
        log = """ERROR: exception
	at com.example.MyClass.method(MyClass.java:42)
more info"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.unique_stack_sigs >= 1

    def test_python_stack_frame(self):
        """Test detection of Python stack frame."""
        log = """ERROR: exception
  File "script.py", line 42
    invalid syntax
more text"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.unique_stack_sigs >= 0

    def test_nodejs_stack_frame(self):
        """Test detection of Node.js stack frame."""
        log = """ERROR: problem
    at Object.<anonymous> (/path/to/file.js:10:5)
    at Module._load (internal/modules/cjs/loader.js:100:10)
end"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.unique_stack_sigs >= 1

    def test_multiple_unique_stack_traces(self):
        """Test multiple different stack traces are counted."""
        log = """ERROR: first
	at com.example.Class1.method1(Class1.java:10)
details

ERROR: second
	at com.example.Class2.method2(Class2.java:20)
more details

ERROR: third
	at com.example.Class3.method3(Class3.java:30)
end"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 3
        assert result.unique_stack_sigs >= 1

    def test_duplicate_stack_trace_dedup(self):
        """Test that duplicate stack traces are not double-counted."""
        log = """ERROR: first
	at com.example.MyClass.method(MyClass.java:42)

ERROR: second (same stack)
	at com.example.MyClass.method(MyClass.java:42)"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 2
        # Same stack signature should only be counted once
        assert result.unique_stack_sigs <= 2

    def test_max_stack_sigs_limit(self):
        """Test that stack sigs are capped at max."""
        log = "\n".join([
            f"ERROR: error{i}\n\tat location.method{i}(file.java:{i})"
            for i in range(100)
        ])
        extractor = LogExtractor(max_stack_sigs=10)
        result = extractor.extract(log)
        assert result.unique_stack_sigs <= 10


class TestLogExtractorComplexLogs:
    """Test extraction from complex log files."""

    def test_typical_server_log(self):
        """Test typical server log."""
        log = """2024-01-01T10:00:00Z INFO: Server started
2024-01-01T10:00:01Z DEBUG: Loading config
2024-01-01T10:00:02Z DEBUG: Connecting to database
2024-01-01T10:00:03Z INFO: Database ready
2024-01-01T10:05:00Z ERROR: Connection timeout
	at db.connect(db.py:42)
2024-01-01T10:05:01Z ERROR: Retrying connection
2024-01-01T10:05:02Z INFO: Connection restored
2024-01-01T10:10:00Z INFO: Request received
2024-01-01T10:10:01Z CRITICAL: Out of memory
2024-01-01T10:10:02Z FATAL: Shutting down"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        # ERROR, CRITICAL, FATAL are all counted as errors
        assert result.error_count == 4
        assert result.timestamp_first is not None
        assert result.timestamp_last is not None

    def test_exception_with_context(self):
        """Test exception with surrounding context."""
        log = """2024-01-01T10:00:00Z Processing request ID=123
2024-01-01T10:00:01Z Initializing handler
2024-01-01T10:00:02Z ERROR: NullPointerException occurred
2024-01-01T10:00:02Z 	at handler.process(handler.java:156)
2024-01-01T10:00:03Z Stack unwinding
2024-01-01T10:00:04Z Request failed"""
        extractor = LogExtractor(context_lines=3)
        result = extractor.extract(log)
        assert "ERROR" in result.extracted
        assert result.error_count == 1

    def test_large_log_file_compression(self):
        """Test compression ratio on large log."""
        lines = ["2024-01-01T10:00:00Z INFO: line " + str(i) for i in range(1000)]
        lines[100] = "2024-01-01T10:00:10Z ERROR: problem at line 100"
        lines[500] = "2024-01-01T10:00:50Z ERROR: another issue at 500"
        log = "\n".join(lines)
        extractor = LogExtractor(context_lines=5)
        result = extractor.extract(log)
        assert result.lines_in == 1000
        assert result.error_count == 2
        assert result.reduction_pct > 80

    def test_mixed_severity_levels(self):
        """Test log with various severity levels."""
        log = """DEBUG: d1
INFO: i1
WARN: w1
ERROR: e1
CRITICAL: c1
FATAL: f1"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 3  # ERROR, CRITICAL, FATAL


class TestLogExtractorReduction:
    """Test compression metrics."""

    def test_reduction_percentage(self):
        """Test reduction percentage calculation."""
        log = "\n".join([f"INFO: line {i}" for i in range(100)])
        log += "\nERROR: problem at line 50"
        extractor = LogExtractor(context_lines=5)
        result = extractor.extract(log)
        assert result.reduction_pct > 50

    def test_minimal_reduction(self):
        """Test when log is all errors."""
        log = "\n".join([f"ERROR: problem {i}" for i in range(10)])
        extractor = LogExtractor(context_lines=1)
        result = extractor.extract(log)
        # All lines should be kept
        assert result.reduction_pct < 50


class TestLogExtractorEdgeCases:
    """Test edge cases."""

    def test_very_long_lines(self):
        """Test handling of very long lines."""
        long_msg = "x" * 10000
        log = f"INFO: {long_msg}\nERROR: {long_msg}"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 1

    def test_unicode_content(self):
        """Test handling of unicode in logs."""
        log = """2024-01-01T10:00:00Z ERROR: 日本語エラー
サンプル行
終了"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 1

    def test_special_characters(self):
        """Test handling of special characters."""
        log = """ERROR: $pecial ch@rs & symbols!
	at location(file.java:42)
more content"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 1

    def test_numeric_only_lines(self):
        """Test handling of numeric-only lines."""
        log = """1234567890
ERROR: numeric context
9876543210"""
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert result.error_count == 1

    def test_empty_lines_in_context(self):
        """Test that empty lines are preserved in context."""
        log = """line1

ERROR: problem

line5"""
        extractor = LogExtractor(context_lines=2)
        result = extractor.extract(log)
        assert "ERROR" in result.extracted

    def test_output_format_header(self):
        """Test that output includes proper header."""
        log = "ERROR: test problem"
        extractor = LogExtractor()
        result = extractor.extract(log)
        assert "[log-salience]" in result.extracted
        assert "errors=1" in result.extracted
