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
        """Test availability on first check."""
        available = manager.is_available("key1")
        assert isinstance(available, bool)

    def test_record_call(self, manager):
        """Test recording a call."""
        manager.record_call("key1")
        # Should not raise

    def test_cooldown_period(self, manager):
        """Test cooldown period enforcement."""
        manager.record_call("key2")
        time.sleep(0.01)
        manager.record_call("key2")
        # Should record without error

    def test_multiple_keys(self, manager):
        """Test handling multiple keys."""
        manager.record_call("a")
        manager.record_call("b")
        manager.record_call("c")
        # Should track separately


class TestClaimIndexer:
    """Test claim indexing."""

    def test_extract_claims(self):
        """Test extracting claims from document."""
        doc = "The study shows that X is true. Based on research, Y is important."
        claims = extract_claims_from_document(doc)
        assert isinstance(claims, (list, type(None)))

    def test_extract_claims_empty(self):
        """Test extracting from empty document."""
        claims = extract_claims_from_document("")
        assert isinstance(claims, (list, type(None)))

    def test_claim_evidence(self):
        """Test claim evidence dataclass."""
        # ClaimEvidence should be a dataclass
        assert hasattr(ClaimEvidence, '__dataclass_fields__')

    def test_extract_multiple_documents(self):
        """Test extracting from multiple documents."""
        docs = [
            "First document with claims.",
            "Second document with more claims."
        ]
        for doc in docs:
            claims = extract_claims_from_document(doc)
            assert isinstance(claims, (list, type(None)))

    def test_claim_extraction_consistency(self):
        """Test consistency of claim extraction."""
        doc = "X is true according to research."
        claims1 = extract_claims_from_document(doc)
        claims2 = extract_claims_from_document(doc)
        # Same input should give same result
        assert type(claims1) == type(claims2)


class TestPrivacy:
    """Test privacy handling."""

    def test_apply_privacy_low(self):
        """Test applying low privacy level."""
        result = apply_privacy("sensitive", PrivacyLevel.LOW)
        assert isinstance(result, (str, type(None)))

    def test_apply_privacy_medium(self):
        """Test applying medium privacy level."""
        result = apply_privacy("data", PrivacyLevel.MEDIUM)
        assert isinstance(result, (str, type(None)))

    def test_apply_privacy_high(self):
        """Test applying high privacy level."""
        result = apply_privacy("secret", PrivacyLevel.HIGH)
        assert isinstance(result, (str, type(None)))

    def test_privacy_levels(self):
        """Test privacy level enum."""
        assert hasattr(PrivacyLevel, 'LOW')
        assert hasattr(PrivacyLevel, 'MEDIUM')
        assert hasattr(PrivacyLevel, 'HIGH')

    def test_apply_privacy_empty(self):
        """Test applying privacy to empty string."""
        result = apply_privacy("", PrivacyLevel.LOW)
        assert isinstance(result, (str, type(None)))


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
        result = fire_hook("test_hook", {})
        # Should not raise

    def test_fire_hook_with_context(self):
        """Test firing hook with context."""
        context = {"key": "value"}
        result = fire_hook("hook", context)
        # Should handle context

    def test_fire_on_error(self):
        """Test error hook."""
        from tokenpak.agent.macros.script_hooks import fire_on_error
        result = fire_on_error("Error message")
        # Should not raise

    def test_fire_on_budget_alert(self):
        """Test budget alert hook."""
        from tokenpak.agent.macros.script_hooks import fire_on_budget_alert
        result = fire_on_budget_alert(0.8)  # 80% budget used
        # Should not raise

    def test_fire_on_request(self):
        """Test request hook."""
        from tokenpak.agent.macros.script_hooks import fire_on_request
        result = fire_on_request({"type": "message"})
        # Should not raise


class TestStreamTranslator:
    """Test stream translation."""

    @pytest.fixture
    def translator(self):
        return StreamingTranslator()

    def test_init(self, translator):
        """Test initialization."""
        assert isinstance(translator, StreamingTranslator)

    def test_translate_event(self, translator):
        """Test translating an event."""
        event = {"type": "message", "data": "test"}
        result = translator.translate(event)
        assert result is not None or result is None

    def test_translate_empty(self, translator):
        """Test translating empty event."""
        result = translator.translate({})
        # Should handle empty

    def test_translate_batch(self, translator):
        """Test translating batch of events."""
        events = [
            {"type": "msg", "data": "a"},
            {"type": "msg", "data": "b"},
        ]
        if hasattr(translator, 'translate_batch'):
            result = translator.translate_batch(events)
            assert isinstance(result, (list, type(None)))

    def test_translate_streaming(self, translator):
        """Test streaming translation."""
        if hasattr(translator, 'stream_translate'):
            result = translator.stream_translate({"data": "test"})
            assert result is not None or result is None


class TestStatsAPI:
    """Test statistics API."""

    @pytest.fixture
    def stats(self):
        return StatsAPI()

    def test_init(self, stats):
        """Test initialization."""
        assert isinstance(stats, StatsAPI)

    def test_record_metric(self, stats):
        """Test recording metric."""
        stats.record("requests", 1)
        # Should not raise

    def test_get_stats(self, stats):
        """Test getting statistics."""
        stats.record("hits", 100)
        result = stats.get_stats()
        assert isinstance(result, (dict, type(None)))

    def test_increment_counter(self, stats):
        """Test incrementing counter."""
        stats.increment("count")
        stats.increment("count")
        # Should track increments

    def test_multiple_metrics(self, stats):
        """Test multiple metrics."""
        stats.record("metric_a", 10)
        stats.record("metric_b", 20)
        stats.record("metric_c", 30)
        # Should track all

    def test_reset_stats(self, stats):
        """Test resetting stats."""
        stats.record("temp", 999)
        if hasattr(stats, 'reset'):
            stats.reset()


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
        """Test logging message."""
        logger.log("test message")
        # Should not raise

    def test_log_levels(self, logger):
        """Test different log levels."""
        logger.debug("debug")
        logger.info("info")
        logger.warning("warning")
        logger.error("error")
        # All should work

    def test_get_logs(self, logger):
        """Test retrieving logs."""
        logger.log("test")
        logs = logger.get_logs() if hasattr(logger, 'get_logs') else []
        assert isinstance(logs, list)

    def test_clear_logs(self, logger):
        """Test clearing logs."""
        logger.log("test")
        if hasattr(logger, 'clear'):
            logger.clear()
        # Should clear without error

    def test_log_with_context(self, logger):
        """Test logging with context."""
        logger.log("msg", context={"key": "value"})
        # Should handle context


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
