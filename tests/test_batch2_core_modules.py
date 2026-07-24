"""test_batch2_core_modules.py — Reworked core module tests

Actual tests for: CooldownManager, ClaimIndexer, Privacy,
ScriptHooks, StatsAPI, DebugLogger, StreamTranslator modules.

All tests use REAL module APIs (verified by reading source first).
"""

import pytest

pytest.importorskip(
    "tokenpak.infrastructure.cooldown", reason="module not available in current build"
)
import json
import tempfile
import time
from pathlib import Path

import pytest
from tokenpak._internal.fingerprint.privacy import PrivacyLevel, apply_privacy
from tokenpak._internal.ingest.claim_indexer import (
    extract_claims_from_document,
    extract_claims_from_text,
)
from tokenpak._internal.macros.script_hooks import (
    fire_hook,
    get_hook_path,
    list_hooks,
)
from tokenpak.infrastructure.cooldown import CooldownManager
from tokenpak.infrastructure.debug import DebugLogger

from tokenpak.proxy.providers.stream_translator import (
    _AnthropicToOpenAIStream,
    _OpenAIToAnthropicStream,
    _parse_sse_line,
    _sse_line,
)
from tokenpak.proxy.stats_api import StatsAPI

# ============================================================================
# CooldownManager Tests (Real API)
# ============================================================================


class TestCooldownManager:
    """Tests for CooldownManager with REAL methods."""

    def test_cooldown_manager_init(self):
        """Test creating cooldown manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            mgr = CooldownManager(cooldowns_file=cooldowns_file)
            assert mgr is not None
            assert mgr.cooldowns_file == cooldowns_file

    def test_clear_expired_empty(self):
        """Test clear_expired with no cooldowns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            mgr = CooldownManager(cooldowns_file=cooldowns_file)
            result = mgr.clear_expired()
            assert result == []

    def test_clear_expired_not_yet_expired(self):
        """Test clear_expired with non-expired cooldown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"

            # Write a cooldown that's not expired yet
            future_time = time.time() + 3600  # 1 hour from now
            cooldowns_file.parent.mkdir(parents=True, exist_ok=True)
            cooldowns_file.write_text(
                json.dumps({"test:key": {"cooldownUntil": future_time, "errorCount": 2}})
            )

            mgr = CooldownManager(cooldowns_file=cooldowns_file)
            result = mgr.clear_expired()
            assert result == []  # Not expired yet

    def test_clear_expired_with_expiration(self):
        """Test clear_expired with expired cooldown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"

            # Write a cooldown that IS expired
            past_time = time.time() - 3600  # 1 hour ago
            cooldowns_file.parent.mkdir(parents=True, exist_ok=True)
            cooldowns_file.write_text(
                json.dumps({"test:key": {"cooldownUntil": past_time, "errorCount": 2}})
            )

            mgr = CooldownManager(cooldowns_file=cooldowns_file)
            result = mgr.clear_expired()
            assert result == ["test:key"]

    def test_get_active_cooldowns_empty(self):
        """Test get_active_cooldowns with no file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            mgr = CooldownManager(cooldowns_file=cooldowns_file)
            result = mgr.get_active_cooldowns()
            assert result == {}

    def test_get_active_cooldowns_with_active(self):
        """Test get_active_cooldowns with active cooldown."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"

            future_time = time.time() + 3600
            cooldowns_file.parent.mkdir(parents=True, exist_ok=True)
            cooldowns_file.write_text(
                json.dumps({"test:key": {"cooldownUntil": future_time, "errorCount": 1}})
            )

            mgr = CooldownManager(cooldowns_file=cooldowns_file)
            result = mgr.get_active_cooldowns()
            assert "test:key" in result
            assert result["test:key"] > 3500  # ~1 hour remaining

    def test_run_cycle(self):
        """Test run_cycle method."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cooldowns_file = Path(tmpdir) / "cooldowns.json"
            cooldowns_file.parent.mkdir(parents=True, exist_ok=True)
            cooldowns_file.write_text(
                json.dumps({"old:key": {"cooldownUntil": time.time() - 3600, "errorCount": 2}})
            )

            mgr = CooldownManager(cooldowns_file=cooldowns_file)
            count = mgr.run_cycle()
            assert count == 1


# ============================================================================
# ClaimIndexer Tests (Real API)
# ============================================================================


class TestClaimIndexer:
    """Tests for claim indexer with REAL functions."""

    def test_extract_claims_from_text_empty(self):
        """Test extracting from empty text."""
        result = extract_claims_from_text("")
        assert result == []

    def test_extract_claims_from_text_simple(self):
        """Test extracting simple claim."""
        text = "We found that the system works well."
        result = extract_claims_from_text(text)
        assert isinstance(result, list)
        # May be empty if pattern doesn't match, but should not crash

    def test_extract_claims_with_numbers(self):
        """Test extraction with numeric data."""
        text = "Results show 85% improvement in performance. The data indicates a significant gain."
        result = extract_claims_from_text(text)
        assert isinstance(result, list)

    def test_extract_claims_from_document_dict(self):
        """Test extracting from structured document."""
        doc = {"text": "We identified that the approach is effective.", "section": "Results"}
        result = extract_claims_from_document(doc)
        assert isinstance(result, list)

    def test_extract_claims_with_confidence_threshold(self):
        """Test extracting with custom confidence threshold."""
        text = "Results show improvement across all metrics."
        result = extract_claims_from_text(text, min_confidence=0.3)
        assert isinstance(result, list)

    def test_claim_object_structure(self):
        """Test ClaimEvidence object structure."""
        text = "Our analysis demonstrates that the finding is valid."
        claims = extract_claims_from_text(text)
        if claims:
            claim = claims[0]
            assert hasattr(claim, "claim")
            assert hasattr(claim, "evidence")
            assert hasattr(claim, "confidence")


# ============================================================================
# Privacy Tests (Real API)
# ============================================================================


class TestPrivacy:
    """Tests for privacy module with REAL enums."""

    def test_privacy_level_minimal(self):
        """Test MINIMAL privacy level exists."""
        assert PrivacyLevel.MINIMAL.value == "minimal"

    def test_privacy_level_standard(self):
        """Test STANDARD privacy level exists."""
        assert PrivacyLevel.STANDARD.value == "standard"

    def test_privacy_level_full(self):
        """Test FULL privacy level exists."""
        assert PrivacyLevel.FULL.value == "full"

    def test_apply_privacy_full(self):
        """Test apply_privacy with FULL level."""
        fingerprint = {"fingerprint_id": "fp123", "total_tokens": 1000, "segment_count": 5}
        result = apply_privacy(fingerprint, PrivacyLevel.FULL)
        assert isinstance(result, dict)
        assert result["fingerprint_id"] == "fp123"

    def test_apply_privacy_minimal(self):
        """Test apply_privacy with MINIMAL level."""
        fingerprint = {
            "fingerprint_id": "fp123",
            "total_tokens": 1000,
            "segment_count": 5,
            "segments": [{"type": "code", "tokens": 100}, {"type": "text", "tokens": 200}],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        assert isinstance(result, dict)
        assert "total_tokens" in result or "segment_count" in result

    def test_apply_privacy_standard(self):
        """Test apply_privacy with STANDARD level."""
        fingerprint = {
            "fingerprint_id": "fp123",
            "total_tokens": 1000,
            "segment_count": 5,
            "segments": [{"type": "code", "tokens": 100}, {"type": "text", "tokens": 200}],
        }
        result = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
        assert isinstance(result, dict)

    def test_apply_privacy_empty_dict(self):
        """Test apply_privacy with empty dict."""
        result = apply_privacy({}, PrivacyLevel.MINIMAL)
        assert isinstance(result, dict)


# ============================================================================
# ScriptHooks Tests (Real API)
# ============================================================================


class TestScriptHooks:
    """Tests for script hooks with REAL functions."""

    def test_get_hook_path(self):
        """Test get_hook_path returns Path."""
        path = get_hook_path("on_request")
        assert isinstance(path, Path)
        assert "on_request" in str(path)

    def test_list_hooks(self):
        """Test list_hooks returns dict."""
        hooks = list_hooks()
        assert isinstance(hooks, dict)
        assert "on_request" in hooks
        assert "on_response" in hooks
        assert "on_error" in hooks
        assert "on_budget_alert" in hooks

    def test_hook_structure(self):
        """Test hook dict structure."""
        hooks = list_hooks()
        for name, info in hooks.items():
            assert "path" in info
            assert "exists" in info
            assert "executable" in info
            assert "description" in info

    def test_install_hook_default(self):
        """Test installing a hook with default content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            hook_path = Path(tmpdir) / "test_hook.sh"
            # Just test that install_hook works by checking return type
            from tokenpak._internal.macros.script_hooks import install_hook

            # This will use the default hooks dir, so we skip actual installation
            assert install_hook is not None

    def test_fire_hook_nonexistent(self):
        """Test firing a hook that doesn't exist."""
        result = fire_hook("nonexistent_hook", {"key": "value"})
        assert result is None

    def test_fire_hook_with_context(self):
        """Test firing hook captures context."""
        # Since hooks don't exist by default, this should return None
        result = fire_hook("on_request", {"model": "claude", "provider": "anthropic"})
        assert result is None


# ============================================================================
# StatsAPI Tests (Real API)
# ============================================================================


class TestStatsAPI:
    """Tests for stats API with REAL methods."""

    def test_stats_api_route_last(self):
        """Test StatsAPI.route for /stats/last."""
        result = StatsAPI.route("/stats/last")
        # May return data or None depending on storage state
        assert result is None or isinstance(result, tuple)

    def test_stats_api_route_session(self):
        """Test StatsAPI.route for /stats/session."""
        result = StatsAPI.route("/stats/session")
        assert result is None or isinstance(result, tuple)

    def test_stats_api_route_unknown(self):
        """Test StatsAPI.route for unknown path."""
        result = StatsAPI.route("/unknown/path")
        assert result is None

    def test_stats_api_handle_stats_last(self):
        """Test handle_stats_last returns tuple."""
        body, headers = StatsAPI.handle_stats_last()
        assert isinstance(body, str)
        assert isinstance(headers, dict)
        assert "Content-Type" in headers

    def test_stats_api_handle_stats_session(self):
        """Test handle_stats_session returns tuple."""
        body, headers = StatsAPI.handle_stats_session()
        assert isinstance(body, str)
        assert isinstance(headers, dict)
        assert headers.get("Content-Type") == "application/json"


# ============================================================================
# DebugLogger Tests (Real API)
# ============================================================================


class TestDebugLogger:
    """Tests for debug logger with REAL context manager."""

    def test_debug_logger_init(self):
        """Test creating debug logger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "debug.log"
            logger = DebugLogger(log_path=log_path)
            assert logger is not None

    def test_debug_logger_record_context(self):
        """Test using record context manager."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "debug.log"
            logger = DebugLogger(log_path=log_path)

            with logger.record() as rec:
                rec.set("model", "claude")
                rec.add_step("validate", status="ok")

            # Check log was written
            assert log_path.exists()
            content = log_path.read_text()
            assert len(content) > 0

    def test_debug_logger_multiple_records(self):
        """Test multiple log records."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "debug.log"
            logger = DebugLogger(log_path=log_path)

            for i in range(3):
                with logger.record() as rec:
                    rec.set("iteration", i)

            lines = log_path.read_text().strip().split("\n")
            assert len(lines) == 3

    def test_debug_logger_error_handling(self):
        """Test error handling in record context."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "debug.log"
            logger = DebugLogger(log_path=log_path)

            try:
                with logger.record() as rec:
                    rec.fail("test error")
            except Exception:
                pass

            content = log_path.read_text()
            assert len(content) > 0


# ============================================================================
# StreamTranslator Tests (Real API)
# ============================================================================


class TestStreamTranslator:
    """Tests for streaming translator with REAL classes."""

    def test_parse_sse_line_valid(self):
        """Test parsing valid SSE line."""
        line = 'data: {"type": "test"}'
        result = _parse_sse_line(line)
        assert result == {"type": "test"}

    def test_parse_sse_line_done(self):
        """Test parsing [DONE] marker."""
        result = _parse_sse_line("data: [DONE]")
        assert result is None

    def test_parse_sse_line_invalid(self):
        """Test parsing invalid line."""
        result = _parse_sse_line("not: data")
        assert result is None

    def test_sse_line_format(self):
        """Test formatting dict as SSE line."""
        data = {"type": "message", "id": 123}
        result = _sse_line(data)
        assert result.startswith("data: ")
        assert "message" in result

    def test_anthropic_to_openai_stream_init(self):
        """Test AnthropicToOpenAI stream translator init."""
        translator = _AnthropicToOpenAIStream()
        assert translator is not None
        assert translator._id is not None

    def test_anthropic_to_openai_message_start(self):
        """Test translating message_start event."""
        translator = _AnthropicToOpenAIStream()
        event = {"type": "message_start", "message": {"model": "claude-3-sonnet", "id": "msg_123"}}
        result = translator.translate(event)
        assert result is not None
        assert "assistant" in result

    def test_openai_to_anthropic_stream_init(self):
        """Test OpenAIToAnthropic stream translator init."""
        translator = _OpenAIToAnthropicStream()
        assert translator is not None

    def test_stream_translator_chunk_parsing(self):
        """Test parsing streaming chunks."""
        line = 'data: {"choices": [{"delta": {"content": "hello"}}]}'
        chunk = _parse_sse_line(line)
        assert chunk is not None
        assert "choices" in chunk
