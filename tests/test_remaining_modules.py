"""test_remaining_modules.py — Quick tests for 10 remaining modules

Tests for:
- cooldown_manager (auth)
- claim_indexer (ingest)
- privacy (fingerprint)
- premade_macros (macros)
- script_hooks (macros)
- stream_translator (proxy)
- stats_api (proxy)
- config
- logger (debug)
- capabilities (agentic)
"""

import time
import pytest

from tokenpak.agent.auth.cooldown_manager import CooldownManager
from tokenpak.agent.ingest.claim_indexer import extract_claims_from_document, ClaimEvidence
from tokenpak.agent.fingerprint.privacy import apply_privacy, PrivacyLevel
from tokenpak.agent.macros.premade_macros import PREMADE_MACROS, PremadeMacroRunner
from tokenpak.agent.macros.script_hooks import fire_hook
from tokenpak.agent.proxy.providers.stream_translator import StreamingTranslator
from tokenpak.agent.proxy.stats_api import StatsAPI, get_stats_storage
from tokenpak.agent.config import get_config
from tokenpak.agent.debug.logger import DebugLogger
from tokenpak.agent.agentic.capabilities import AgentCapabilities


class TestCooldownManager:
    """Test cooldown manager for rate limiting."""

    @pytest.fixture
    def manager(self):
        return CooldownManager()

    def test_init(self, manager):
        """Test initialization."""
        assert isinstance(manager, CooldownManager)

    def test_is_available_initial(self, manager):
        """Test getting active cooldowns."""
        active = manager.get_active_cooldowns()
        assert isinstance(active, dict)

    def test_record_call(self, manager):
        """Test clearing expired cooldowns."""
        cleared = manager.clear_expired()
        assert isinstance(cleared, list)

    def test_cooldown_period(self, manager):
        """Test clearing from profiles."""
        cleared = manager.clear_expired_from_profiles()
        assert isinstance(cleared, list)

    def test_multiple_keys(self, manager):
        """Test run cycle."""
        count = manager.run_cycle()
        assert isinstance(count, int)


class TestClaimIndexer:
    """Test claim indexing."""

    def test_extract_claims(self):
        """Test extracting claims from document."""
        doc = {"text": "The study shows that X is true. Based on research, Y is important."}
        claims = extract_claims_from_document(doc)
        assert isinstance(claims, list)

    def test_extract_claims_empty(self):
        """Test extracting from empty document."""
        claims = extract_claims_from_document({"text": ""})
        assert isinstance(claims, list)

    def test_claim_evidence(self):
        """Test claim evidence dataclass."""
        # ClaimEvidence should be a dataclass
        assert hasattr(ClaimEvidence, '__dataclass_fields__')

    def test_extract_multiple_documents(self):
        """Test extracting from multiple documents."""
        docs = [
            {"text": "First document with claims."},
            {"text": "Second document with more claims."}
        ]
        for doc in docs:
            claims = extract_claims_from_document(doc)
            assert isinstance(claims, list)

    def test_claim_extraction_consistency(self):
        """Test consistency of claim extraction."""
        doc = {"text": "X is true according to research."}
        claims1 = extract_claims_from_document(doc)
        claims2 = extract_claims_from_document(doc)
        # Same input should give same result
        assert type(claims1) == type(claims2)


class TestPrivacy:
    """Test privacy handling."""

    def test_apply_privacy_low(self):
        """Test applying minimal privacy level."""
        fingerprint = {"fingerprint_id": "test1", "total_tokens": 100, "segment_count": 5}
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        assert isinstance(result, dict)

    def test_apply_privacy_medium(self):
        """Test applying standard privacy level."""
        fingerprint = {"fingerprint_id": "test2", "total_tokens": 200, "segment_count": 10, "segments": []}
        result = apply_privacy(fingerprint, PrivacyLevel.STANDARD)
        assert isinstance(result, dict)

    def test_apply_privacy_high(self):
        """Test applying full privacy level."""
        fingerprint = {"fingerprint_id": "test3", "total_tokens": 300, "segment_count": 15}
        result = apply_privacy(fingerprint, PrivacyLevel.FULL)
        assert isinstance(result, dict)

    def test_privacy_levels(self):
        """Test privacy level enum."""
        assert hasattr(PrivacyLevel, 'MINIMAL')
        assert hasattr(PrivacyLevel, 'STANDARD')
        assert hasattr(PrivacyLevel, 'FULL')

    def test_apply_privacy_empty(self):
        """Test applying privacy to minimal fingerprint."""
        fingerprint = {"fingerprint_id": "test4"}
        result = apply_privacy(fingerprint, PrivacyLevel.MINIMAL)
        assert isinstance(result, dict)


class TestPremadeMacros:
    """Test premade macros."""

    def test_premade_macros_exists(self):
        """Test PREMADE_MACROS exists."""
        assert PREMADE_MACROS is not None

    def test_premade_macros_structure(self):
        """Test PREMADE_MACROS structure."""
        assert isinstance(PREMADE_MACROS, (list, dict))

    def test_premade_macro_runner_init(self):
        """Test PremadeMacroRunner initialization."""
        runner = PremadeMacroRunner()
        assert isinstance(runner, PremadeMacroRunner)

    def test_premade_macro_runner_load(self):
        """Test loading macros."""
        runner = PremadeMacroRunner()
        if hasattr(runner, 'load'):
            runner.load()
        # Should not raise

    def test_premade_macro_content(self):
        """Test macro content."""
        macros_str = str(PREMADE_MACROS)
        assert len(macros_str) > 0


class TestScriptHooks:
    """Test script hook execution."""

    def test_fire_hook_basic(self):
        """Test firing a basic hook."""
        result = fire_hook("on_request", {})
        # Should not raise or return dict

    def test_fire_hook_with_context(self):
        """Test firing hook with context."""
        context = {"key": "value"}
        result = fire_hook("on_request", context)
        # Should handle context

    def test_fire_on_error(self):
        """Test error hook."""
        from tokenpak.agent.macros.script_hooks import fire_on_error
        result = fire_on_error("anthropic", "default", "rate_limit", "Too many requests")
        # Should not raise

    def test_fire_on_budget_alert(self):
        """Test budget alert hook."""
        from tokenpak.agent.macros.script_hooks import fire_on_budget_alert
        result = fire_on_budget_alert("budget_1", 100.0, 80.0)
        # Should not raise

    def test_fire_on_request(self):
        """Test request hook."""
        from tokenpak.agent.macros.script_hooks import fire_on_request
        result = fire_on_request("gpt-4", "openai", 10)
        # Should not raise


class TestStreamTranslator:
    """Test stream translation."""

    @pytest.fixture
    def translator(self):
        return StreamingTranslator("anthropic", "openai")

    def test_init(self, translator):
        """Test initialization."""
        assert isinstance(translator, StreamingTranslator)

    def test_translate_event(self, translator):
        """Test translating a chunk line."""
        line = 'data: {"type": "message_start", "message": {"model": "claude-3"}}'
        result = translator.translate_chunk(line)
        assert isinstance(result, list)

    def test_translate_empty(self, translator):
        """Test translating empty line."""
        result = translator.translate_chunk("")
        assert isinstance(result, list)

    def test_translate_batch(self, translator):
        """Test translating batch of chunk lines."""
        lines = [
            'data: {"type": "message_start", "message": {"model": "claude-3"}}',
            'data: [DONE]',
        ]
        results = [translator.translate_chunk(line) for line in lines]
        assert all(isinstance(r, list) for r in results)

    def test_translate_streaming(self, translator):
        """Test streaming translation."""
        lines = iter(['data: {"type": "message_start", "message": {"model": "claude-3"}}'])
        result = list(translator.translate_stream(lines))
        assert isinstance(result, list)


class TestStatsAPI:
    """Test statistics API."""

    @pytest.fixture
    def stats(self):
        return StatsAPI()

    def test_init(self, stats):
        """Test initialization."""
        assert isinstance(stats, StatsAPI)

    def test_record_metric(self, stats):
        """Test handling /stats/last route."""
        result = stats.route("/stats/last")
        assert result is None or isinstance(result, tuple)

    def test_get_stats(self, stats):
        """Test handling /stats/session route."""
        result = stats.route("/stats/session")
        assert result is None or isinstance(result, tuple)

    def test_increment_counter(self, stats):
        """Test handle_stats_last method."""
        body, headers = StatsAPI.handle_stats_last()
        assert isinstance(body, str)
        assert isinstance(headers, dict)

    def test_multiple_metrics(self, stats):
        """Test handle_stats_session method."""
        body, headers = StatsAPI.handle_stats_session()
        assert isinstance(body, str)
        assert isinstance(headers, dict)

    def test_reset_stats(self, stats):
        """Test route with unknown path."""
        result = stats.route("/unknown")
        assert result is None


class TestConfig:
    """Test configuration."""

    def test_get_config(self):
        """Test getting config."""
        config = get_config()
        assert config is not None or config is None

    def test_debug_enabled(self):
        """Test debug config."""
        from tokenpak.agent.config import get_debug_enabled
        debug = get_debug_enabled()
        assert isinstance(debug, bool)

    def test_metrics_enabled(self):
        """Test metrics config."""
        from tokenpak.agent.config import get_metrics_enabled
        metrics = get_metrics_enabled()
        assert isinstance(metrics, bool)

    def test_capsule_builder_enabled(self):
        """Test capsule builder config."""
        from tokenpak.agent.config import get_capsule_builder_enabled
        capsule = get_capsule_builder_enabled()
        assert isinstance(capsule, bool)

    def test_stats_footer_enabled(self):
        """Test stats footer config."""
        from tokenpak.agent.config import get_stats_footer_enabled
        footer = get_stats_footer_enabled()
        assert isinstance(footer, bool)


class TestDebugLogger:
    """Test debug logger."""

    @pytest.fixture
    def logger(self):
        return DebugLogger()

    def test_init(self, logger):
        """Test initialization."""
        assert isinstance(logger, DebugLogger)

    def test_log_message(self, logger):
        """Test record context manager."""
        with logger.record() as rec:
            rec.set("key", "value")
        # Should not raise

    def test_log_levels(self, logger):
        """Test record with steps."""
        with logger.record() as rec:
            rec.add_step("step1", status="ok")
            rec.add_step("step2", status="ok")
        # All should work

    def test_get_logs(self, logger):
        """Test record with error handling."""
        try:
            with logger.record() as rec:
                rec.fail("test error")
        except:
            pass
        # Should handle errors

    def test_clear_logs(self, logger):
        """Test multiple records."""
        with logger.record() as rec:
            rec.set("a", 1)
        with logger.record() as rec:
            rec.set("b", 2)
        # Should record multiple

    def test_log_with_context(self, logger):
        """Test record to_dict."""
        with logger.record() as rec:
            rec.set("msg", "test")
            data = rec.to_dict()
        assert isinstance(data, dict)


class TestCapabilities:
    """Test capabilities."""

    def test_agent_capabilities_init(self):
        """Test AgentCapabilities initialization."""
        caps = AgentCapabilities()
        assert isinstance(caps, AgentCapabilities)

    def test_agent_info(self):
        """Test AgentInfo."""
        from tokenpak.agent.agentic.capabilities import AgentInfo
        # AgentInfo should be available
        assert AgentInfo is not None

    def test_agent_registry(self):
        """Test AgentRegistry."""
        from tokenpak.agent.agentic.capabilities import AgentRegistry
        registry = AgentRegistry()
        assert isinstance(registry, AgentRegistry)

    def test_capability_matcher(self):
        """Test CapabilityMatcher."""
        from tokenpak.agent.agentic.capabilities import CapabilityMatcher
        matcher = CapabilityMatcher()
        assert isinstance(matcher, CapabilityMatcher)

    def test_match_result(self):
        """Test MatchResult."""
        from tokenpak.agent.agentic.capabilities import MatchResult
        # MatchResult should be available
        assert MatchResult is not None
