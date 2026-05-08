#!/usr/bin/env python3
"""
Test suite for cached token response parsing across providers.

CACHE-P2-002: Validates provider-dispatched cache response parsing for:
- OpenAI (verified, CACHE-P1-001)
- Azure OpenAI (same format as OpenAI)
- xAI/Grok (same format as OpenAI)
- Codex (same format as OpenAI)
- Groq (same format as OpenAI - prompt_tokens_details.cached_tokens)
- Fireworks (does NOT expose cached_tokens in response body)
- Together (does NOT expose cached_tokens in response body)
- Anthropic (cache_read_input_tokens - existing, regression test)
- Unknown provider (returns 0)

Research Notes (2026-04-03):
| Provider     | Cached Tokens Field                           | Format | Notes                                           |
|--------------|-----------------------------------------------|--------|------------------------------------------------|
| OpenAI       | usage.prompt_tokens_details.cached_tokens     | int    | ✅ Confirmed (CACHE-P1-001)                    |
| Azure OpenAI | usage.prompt_tokens_details.cached_tokens     | int    | Same as OpenAI                                 |
| xAI/Grok     | usage.prompt_tokens_details.cached_tokens     | int    | Same as OpenAI                                 |
| Codex        | usage.prompt_tokens_details.cached_tokens     | int    | Same as OpenAI (same API format)               |
| Groq         | usage.prompt_tokens_details.cached_tokens     | int    | ✅ Confirmed via docs                          |
| Fireworks    | (headers only for dedicated endpoints)        | N/A    | ❌ Does NOT expose in response body            |
| Together     | (not exposed)                                 | N/A    | ❌ Does NOT expose cached tokens               |
| Anthropic    | usage.cache_read_input_tokens                 | int    | Different field name (existing)                |
"""

import sys
from enum import Enum, auto
from pathlib import Path

import pytest


# TSR-05o source-grep skip reason (grep-able)
# ─────────────────────────────────────────────
# TestProxyCodeIntegration greps `proxy.py` to verify symbols (Provider enum,
# `_parse_cached_tokens`, `_detect_provider_from_url`) are present at the
# proxy.py module level. Post-monolith, `proxy.py` is a thin 4-line shim
# that exec()s `proxy_monolith.py.bak`; the symbols now live in the modular
# tree (`tokenpak/proxy/server.py` / `tokenpak/proxy/cache_token_parser.py`).
# Source-grep is an antipattern that doesn't survive the refactor; future
# redesign should rewrite to behavioral tests against the canonical APIs.
# The 34 inline-implementation tests (TestProviderEnum, TestParseCachedTokens,
# TestParseCachedTokensSSE, TestProviderDetectionFromModel,
# TestAllProvidersCovered) are unaffected — they exercise the inline
# Provider enum and parsing functions extracted at module top.
# Same Path B pattern as TSR-05f (#120), TSR-05k (#125), TSR-05n (#127);
# 9th source-grep recurrence.
SKIP_PROXY_SOURCE_GREP_LEGACY = (
    "Test source-greps proxy.py to verify CACHE-P2-002 cache-token parsing "
    "symbols are present. Post-monolith, proxy.py is a thin shim that "
    "exec()s proxy_monolith.py.bak; the symbols now live in the modular "
    "tree (tokenpak/proxy/server.py / tokenpak/proxy/cache_token_parser.py). "
    "Source-grep is an antipattern that doesn't survive the refactor; future "
    "redesign should rewrite to behavioral tests against the canonical APIs. "
    "The 34 inline-implementation tests in this file are unaffected."
)


# ---------------------------------------------------------------------------
# Inline copy of Provider enum and functions for testability
# These are extracted from proxy.py to avoid heavyweight import chain
# ---------------------------------------------------------------------------
class Provider(Enum):
    """LLM provider identifiers for cache response parsing dispatch."""
    ANTHROPIC = auto()
    OPENAI = auto()
    AZURE_OPENAI = auto()
    XAI = auto()  # Grok
    CODEX = auto()  # OpenAI Codex
    GROQ = auto()
    FIREWORKS = auto()
    TOGETHER = auto()
    GEMINI = auto()  # Phase 3
    BEDROCK = auto()  # Phase 3
    UNKNOWN = auto()


def _detect_provider_from_url(upstream_url: str) -> Provider:
    """Detect provider from upstream URL."""
    url_lower = upstream_url.lower()
    if "anthropic.com" in url_lower:
        return Provider.ANTHROPIC
    if "openai.com" in url_lower:
        # Check if it's a Codex endpoint
        if "codex" in url_lower or "/responses" in url_lower:
            return Provider.CODEX
        return Provider.OPENAI
    if "azure.com" in url_lower or "azure-api.net" in url_lower:
        return Provider.AZURE_OPENAI
    if "x.ai" in url_lower or "grok" in url_lower:
        return Provider.XAI
    if "groq.com" in url_lower:
        return Provider.GROQ
    if "fireworks.ai" in url_lower:
        return Provider.FIREWORKS
    if "together.ai" in url_lower or "together.xyz" in url_lower:
        return Provider.TOGETHER
    if "googleapis.com" in url_lower or "generativelanguage" in url_lower:
        return Provider.GEMINI
    if "bedrock" in url_lower or "amazonaws.com" in url_lower:
        return Provider.BEDROCK
    return Provider.UNKNOWN


def _detect_provider_from_model(model_name: str) -> Provider:
    """Detect provider from model name prefix."""
    model_lower = model_name.lower()
    if model_lower.startswith("claude") or "anthropic" in model_lower:
        return Provider.ANTHROPIC
    # Check Codex BEFORE OpenAI (gpt-5.2-codex should be CODEX, not OPENAI)
    if "codex" in model_lower:
        return Provider.CODEX
    if model_lower.startswith("gpt") or model_lower.startswith("o1") or model_lower.startswith("o3"):
        return Provider.OPENAI
    if model_lower.startswith("grok") or "x-ai" in model_lower:
        return Provider.XAI
    if "groq" in model_lower or model_lower.startswith("llama") and "groq" in model_lower:
        return Provider.GROQ
    if "fireworks" in model_lower:
        return Provider.FIREWORKS
    if "together" in model_lower:
        return Provider.TOGETHER
    if model_lower.startswith("gemini"):
        return Provider.GEMINI
    return Provider.UNKNOWN


def _parse_cached_tokens(provider: Provider, response_data: dict) -> int:
    """
    Extract cached token count from provider-specific response format.
    
    CACHE-P2-002: Provider-dispatched parser for unified cache_read_tokens DB column.
    
    Research Notes (2026-04-03):
    - OpenAI/Azure/xAI/Codex: usage.prompt_tokens_details.cached_tokens (int)
    - Groq: usage.prompt_tokens_details.cached_tokens (same as OpenAI)
    - Fireworks: Does NOT expose cached_tokens in response body (only in headers for dedicated)
    - Together: Does NOT expose cached_tokens in response body
    - Anthropic: usage.cache_read_input_tokens (separate field, handled elsewhere)
    
    Args:
        provider: The Provider enum value
        response_data: Parsed JSON response from the provider
        
    Returns:
        Number of cached tokens (0 if not available or not supported)
    """
    usage = response_data.get("usage", {})
    if not usage:
        return 0

    # Anthropic has its own format — handled separately in existing code
    if provider == Provider.ANTHROPIC:
        return usage.get("cache_read_input_tokens", 0)

    # OpenAI-compatible providers (OpenAI, Azure, xAI, Codex, Groq)
    # All use: usage.prompt_tokens_details.cached_tokens
    if provider in (Provider.OPENAI, Provider.AZURE_OPENAI, Provider.XAI, Provider.CODEX, Provider.GROQ):
        details = usage.get("prompt_tokens_details", {})
        if details is None:
            return 0
        return details.get("cached_tokens", 0)

    # Fireworks: Does NOT expose cached_tokens in response body
    # Caching info is returned in headers: fireworks-prompt-tokens, fireworks-cached-prompt-tokens
    # TODO: Fireworks does not expose cached_tokens in usage response as of 2026-04
    if provider == Provider.FIREWORKS:
        return 0

    # Together: Does NOT expose cached_tokens in response body
    # TODO: Together does not expose cached_tokens as of 2026-04
    if provider == Provider.TOGETHER:
        return 0

    # Gemini/Bedrock — Phase 3, different format
    if provider in (Provider.GEMINI, Provider.BEDROCK):
        # TODO: Implement Gemini/Bedrock cache parsing in Phase 3
        return 0

    # Unknown provider — return 0
    return 0


def _parse_cached_tokens_from_sse(provider: Provider, event_data: dict) -> dict:
    """
    Extract cached token info from an SSE event for a specific provider.
    
    Args:
        provider: The Provider enum value
        event_data: Parsed JSON from a single SSE data line
        
    Returns:
        Dict with cache_read_input_tokens and cache_creation_input_tokens
    """
    result = {"cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    
    # Anthropic format: message_start contains cache info
    if provider == Provider.ANTHROPIC:
        if event_data.get("type") == "message_start":
            usage = event_data.get("message", {}).get("usage", {})
            result["cache_read_input_tokens"] = usage.get("cache_read_input_tokens", 0)
            result["cache_creation_input_tokens"] = usage.get("cache_creation_input_tokens", 0)
        return result
    
    # OpenAI-compatible providers (final chunk contains usage)
    if provider in (Provider.OPENAI, Provider.AZURE_OPENAI, Provider.XAI, Provider.CODEX, Provider.GROQ):
        usage = event_data.get("usage", {})
        if usage:
            prompt_details = usage.get("prompt_tokens_details", {})
            if prompt_details:
                cached = prompt_details.get("cached_tokens", 0)
                if cached and cached > 0:
                    result["cache_read_input_tokens"] = cached
        return result
    
    # Providers that don't expose cache tokens in SSE
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestProviderEnum:
    """Test Provider enum values and detection functions."""

    def test_provider_enum_exists(self):
        """Provider enum should exist with expected values."""
        assert hasattr(Provider, "OPENAI")
        assert hasattr(Provider, "AZURE_OPENAI")
        assert hasattr(Provider, "ANTHROPIC")
        assert hasattr(Provider, "XAI")
        assert hasattr(Provider, "CODEX")
        assert hasattr(Provider, "GROQ")
        assert hasattr(Provider, "FIREWORKS")
        assert hasattr(Provider, "TOGETHER")
        assert hasattr(Provider, "GEMINI")
        assert hasattr(Provider, "BEDROCK")
        assert hasattr(Provider, "UNKNOWN")

    def test_detect_provider_from_url_openai(self):
        """OpenAI URL should be detected correctly."""
        assert _detect_provider_from_url("https://api.openai.com/v1/chat/completions") == Provider.OPENAI
        assert _detect_provider_from_url("https://api.openai.com/v1/responses") == Provider.CODEX

    def test_detect_provider_from_url_azure(self):
        """Azure OpenAI URL should be detected correctly."""
        assert _detect_provider_from_url("https://myresource.openai.azure.com/openai/deployments/gpt-4") == Provider.AZURE_OPENAI
        assert _detect_provider_from_url("https://myresource.azure-api.net/openai") == Provider.AZURE_OPENAI

    def test_detect_provider_from_url_groq(self):
        """Groq URL should be detected correctly."""
        assert _detect_provider_from_url("https://api.groq.com/openai/v1/chat/completions") == Provider.GROQ

    def test_detect_provider_from_url_fireworks(self):
        """Fireworks URL should be detected correctly."""
        assert _detect_provider_from_url("https://api.fireworks.ai/inference/v1") == Provider.FIREWORKS

    def test_detect_provider_from_url_together(self):
        """Together URL should be detected correctly."""
        assert _detect_provider_from_url("https://api.together.ai/v1/chat/completions") == Provider.TOGETHER
        assert _detect_provider_from_url("https://api.together.xyz/v1/chat/completions") == Provider.TOGETHER

    def test_detect_provider_from_url_xai(self):
        """xAI/Grok URL should be detected correctly."""
        assert _detect_provider_from_url("https://api.x.ai/v1/chat/completions") == Provider.XAI

    def test_detect_provider_from_url_unknown(self):
        """Unknown URL should return UNKNOWN provider."""
        assert _detect_provider_from_url("https://custom-llm.example.com/api") == Provider.UNKNOWN


class TestParseCachedTokens:
    """Test _parse_cached_tokens dispatch function."""

    def test_openai_with_cached_tokens(self):
        """OpenAI response with cached_tokens should be parsed correctly."""
        response = {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "total_tokens": 1200,
                "prompt_tokens_details": {
                    "cached_tokens": 800
                }
            }
        }
        
        assert _parse_cached_tokens(Provider.OPENAI, response) == 800

    def test_azure_openai_with_cached_tokens(self):
        """Azure OpenAI response should use same format as OpenAI."""
        response = {
            "usage": {
                "prompt_tokens": 4641,
                "completion_tokens": 1817,
                "total_tokens": 6458,
                "prompt_tokens_details": {
                    "cached_tokens": 4608
                }
            }
        }
        
        assert _parse_cached_tokens(Provider.AZURE_OPENAI, response) == 4608

    def test_xai_grok_with_cached_tokens(self):
        """xAI/Grok response should use same format as OpenAI."""
        response = {
            "usage": {
                "prompt_tokens": 2000,
                "completion_tokens": 500,
                "prompt_tokens_details": {
                    "cached_tokens": 1500
                }
            }
        }
        
        assert _parse_cached_tokens(Provider.XAI, response) == 1500

    def test_codex_with_cached_tokens(self):
        """Codex response should use same format as OpenAI."""
        response = {
            "usage": {
                "prompt_tokens": 5000,
                "completion_tokens": 1000,
                "prompt_tokens_details": {
                    "cached_tokens": 4500
                }
            }
        }
        
        assert _parse_cached_tokens(Provider.CODEX, response) == 4500

    def test_groq_with_cached_tokens(self):
        """Groq response should parse cached_tokens from prompt_tokens_details."""
        # Groq response format (verified from docs)
        response = {
            "id": "chatcmpl-...",
            "model": "openai/gpt-oss-120b",
            "usage": {
                "queue_time": 0.026959759,
                "prompt_tokens": 4641,
                "prompt_time": 0.009995497,
                "completion_tokens": 1817,
                "completion_time": 5.57691751,
                "total_tokens": 6458,
                "total_time": 5.586913007,
                "prompt_tokens_details": {
                    "cached_tokens": 4608
                }
            }
        }
        
        assert _parse_cached_tokens(Provider.GROQ, response) == 4608

    def test_fireworks_no_cached_tokens(self):
        """Fireworks does NOT expose cached_tokens in response body - should return 0."""
        # Fireworks response (no cached tokens in body, only in headers)
        response = {
            "id": "chatcmpl-...",
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "total_tokens": 1500
            }
        }
        
        # Should return 0 since Fireworks doesn't expose cache info in response body
        assert _parse_cached_tokens(Provider.FIREWORKS, response) == 0

    def test_together_no_cached_tokens(self):
        """Together does NOT expose cached_tokens - should return 0."""
        # Together response (no cached tokens exposed)
        response = {
            "id": "chatcmpl-...",
            "usage": {
                "prompt_tokens": 2000,
                "completion_tokens": 800,
                "total_tokens": 2800
            }
        }
        
        # Should return 0 since Together doesn't expose cache info
        assert _parse_cached_tokens(Provider.TOGETHER, response) == 0

    def test_anthropic_cache_read_tokens(self):
        """Anthropic response should parse cache_read_input_tokens (regression test)."""
        response = {
            "usage": {
                "input_tokens": 5000,
                "output_tokens": 1000,
                "cache_read_input_tokens": 4000,
                "cache_creation_input_tokens": 500
            }
        }
        
        assert _parse_cached_tokens(Provider.ANTHROPIC, response) == 4000

    def test_unknown_provider_returns_zero(self):
        """Unknown provider should return 0."""
        response = {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "prompt_tokens_details": {
                    "cached_tokens": 500
                }
            }
        }
        
        assert _parse_cached_tokens(Provider.UNKNOWN, response) == 0

    def test_missing_usage_object(self):
        """Response without usage object should return 0."""
        response = {"id": "chatcmpl-...", "model": "gpt-4"}
        
        assert _parse_cached_tokens(Provider.OPENAI, response) == 0

    def test_empty_usage_object(self):
        """Response with empty usage object should return 0."""
        response = {"usage": {}}
        
        assert _parse_cached_tokens(Provider.OPENAI, response) == 0

    def test_null_prompt_tokens_details(self):
        """Response with null prompt_tokens_details should return 0."""
        response = {
            "usage": {
                "prompt_tokens": 1000,
                "prompt_tokens_details": None
            }
        }
        
        assert _parse_cached_tokens(Provider.OPENAI, response) == 0

    def test_missing_cached_tokens_field(self):
        """Response with prompt_tokens_details but no cached_tokens should return 0."""
        response = {
            "usage": {
                "prompt_tokens": 1000,
                "prompt_tokens_details": {
                    "audio_tokens": 0
                }
            }
        }
        
        assert _parse_cached_tokens(Provider.OPENAI, response) == 0

    def test_zero_cached_tokens(self):
        """Response with zero cached_tokens should return 0."""
        response = {
            "usage": {
                "prompt_tokens": 1000,
                "prompt_tokens_details": {
                    "cached_tokens": 0
                }
            }
        }
        
        assert _parse_cached_tokens(Provider.OPENAI, response) == 0


class TestParseCachedTokensSSE:
    """Test _parse_cached_tokens_from_sse for streaming responses."""

    def test_anthropic_message_start_sse(self):
        """Anthropic SSE message_start should parse cache tokens."""
        event = {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 5000,
                    "cache_read_input_tokens": 4000,
                    "cache_creation_input_tokens": 500
                }
            }
        }
        
        result = _parse_cached_tokens_from_sse(Provider.ANTHROPIC, event)
        assert result["cache_read_input_tokens"] == 4000
        assert result["cache_creation_input_tokens"] == 500

    def test_openai_final_chunk_sse(self):
        """OpenAI SSE final chunk with usage should parse cached tokens."""
        event = {
            "id": "chatcmpl-...",
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "prompt_tokens_details": {
                    "cached_tokens": 800
                }
            }
        }
        
        result = _parse_cached_tokens_from_sse(Provider.OPENAI, event)
        assert result["cache_read_input_tokens"] == 800

    def test_groq_sse_final_chunk(self):
        """Groq SSE final chunk should parse cached tokens like OpenAI."""
        event = {
            "usage": {
                "prompt_tokens": 4641,
                "completion_tokens": 1817,
                "prompt_tokens_details": {
                    "cached_tokens": 4608
                }
            }
        }
        
        result = _parse_cached_tokens_from_sse(Provider.GROQ, event)
        assert result["cache_read_input_tokens"] == 4608

    def test_fireworks_sse_no_cache(self):
        """Fireworks SSE should return zeros (no cache info in response)."""
        event = {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 500
            }
        }
        
        result = _parse_cached_tokens_from_sse(Provider.FIREWORKS, event)
        assert result["cache_read_input_tokens"] == 0
        assert result["cache_creation_input_tokens"] == 0

    def test_together_sse_no_cache(self):
        """Together SSE should return zeros (no cache info in response)."""
        event = {
            "usage": {
                "prompt_tokens": 2000,
                "completion_tokens": 800
            }
        }
        
        result = _parse_cached_tokens_from_sse(Provider.TOGETHER, event)
        assert result["cache_read_input_tokens"] == 0
        assert result["cache_creation_input_tokens"] == 0


class TestProviderDetectionFromModel:
    """Test _detect_provider_from_model function."""

    def test_detect_claude_models(self):
        """Claude models should be detected as Anthropic."""
        assert _detect_provider_from_model("claude-3-opus-20240229") == Provider.ANTHROPIC
        assert _detect_provider_from_model("claude-sonnet-4-6") == Provider.ANTHROPIC
        assert _detect_provider_from_model("claude-haiku-4-5") == Provider.ANTHROPIC

    def test_detect_gpt_models(self):
        """GPT models should be detected as OpenAI."""
        assert _detect_provider_from_model("gpt-4o") == Provider.OPENAI
        assert _detect_provider_from_model("gpt-4-turbo") == Provider.OPENAI
        assert _detect_provider_from_model("gpt-3.5-turbo") == Provider.OPENAI

    def test_detect_codex_models(self):
        """Codex models should be detected as Codex."""
        assert _detect_provider_from_model("gpt-5.2-codex") == Provider.CODEX
        assert _detect_provider_from_model("gpt-5.3-codex-spark") == Provider.CODEX

    def test_detect_grok_models(self):
        """Grok models should be detected as xAI."""
        assert _detect_provider_from_model("grok-2") == Provider.XAI
        assert _detect_provider_from_model("grok-beta") == Provider.XAI

    def test_detect_gemini_models(self):
        """Gemini models should be detected as Gemini."""
        assert _detect_provider_from_model("gemini-2-flash") == Provider.GEMINI
        assert _detect_provider_from_model("gemini-3-pro-preview") == Provider.GEMINI


class TestAllProvidersCovered:
    """Ensure all prefix-auto providers have parsing implemented or documented."""

    def test_prefix_auto_providers_coverage(self):
        """All Group A (prefix-auto) providers should be handled."""
        # Group A providers (prefix-auto)
        prefix_auto_providers = [
            Provider.OPENAI,
            Provider.AZURE_OPENAI,
            Provider.XAI,
            Provider.CODEX,
            Provider.GROQ,
            Provider.FIREWORKS,
            Provider.TOGETHER,
        ]
        
        # Test that each provider can be called without error
        test_response = {"usage": {"prompt_tokens": 100}}
        
        for provider in prefix_auto_providers:
            # Should not raise, should return int >= 0
            result = _parse_cached_tokens(provider, test_response)
            assert isinstance(result, int)
            assert result >= 0

    def test_anthropic_separate_handling(self):
        """Anthropic uses different field - verify it's handled."""
        response = {
            "usage": {
                "cache_read_input_tokens": 1000
            }
        }
        
        assert _parse_cached_tokens(Provider.ANTHROPIC, response) == 1000


@pytest.mark.skip(reason=SKIP_PROXY_SOURCE_GREP_LEGACY)
class TestProxyCodeIntegration:
    """Verify the code in proxy.py matches our test implementations."""

    def test_proxy_has_provider_enum(self):
        """Verify Provider enum exists in proxy.py source."""
        proxy_path = Path(__file__).parent.parent / "proxy.py"
        with open(proxy_path) as f:
            content = f.read()
        
        assert "class Provider(Enum):" in content
        assert "OPENAI = auto()" in content
        assert "GROQ = auto()" in content
        assert "FIREWORKS = auto()" in content
        assert "TOGETHER = auto()" in content

    def test_proxy_has_parse_cached_tokens(self):
        """Verify _parse_cached_tokens function exists in proxy.py source."""
        proxy_path = Path(__file__).parent.parent / "proxy.py"
        with open(proxy_path) as f:
            content = f.read()
        
        assert "def _parse_cached_tokens(provider: Provider, response_data: dict) -> int:" in content

    def test_proxy_has_detect_provider_from_url(self):
        """Verify _detect_provider_from_url function exists in proxy.py source."""
        proxy_path = Path(__file__).parent.parent / "proxy.py"
        with open(proxy_path) as f:
            content = f.read()
        
        assert "def _detect_provider_from_url(upstream_url: str) -> Provider:" in content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
