# SPDX-License-Identifier: Apache-2.0
"""Unit tests for tokenpak.core.runtime.providers — Provider enum and detect_provider()."""

from tokenpak.core.runtime.providers import Provider, detect_provider

# ---------------------------------------------------------------------------
# Provider enum
# ---------------------------------------------------------------------------


class TestProviderEnum:
    def test_enum_values(self):
        assert Provider.ANTHROPIC.value == "anthropic"
        assert Provider.OPENAI.value == "openai"
        assert Provider.GEMINI.value == "gemini"
        assert Provider.GROQ.value == "groq"
        assert Provider.XAI.value == "xai"
        assert Provider.FIREWORKS.value == "fireworks"
        assert Provider.TOGETHER.value == "together"
        assert Provider.AZURE_OPENAI.value == "azure_openai"
        assert Provider.BEDROCK.value == "bedrock"
        assert Provider.CODEX.value == "codex"
        assert Provider.VOYAGE.value == "voyage"
        assert Provider.JINA.value == "jina"
        assert Provider.UNKNOWN.value == "unknown"

    def test_enum_count(self):
        # Ensure no accidental members were added/removed
        assert len(Provider) == 13


# ---------------------------------------------------------------------------
# detect_provider — exact hostname matches
# ---------------------------------------------------------------------------


class TestDetectProviderExactHosts:
    def test_anthropic_exact(self):
        assert detect_provider("https://api.anthropic.com/v1/messages") == Provider.ANTHROPIC

    def test_openai_exact(self):
        assert detect_provider("https://api.openai.com/v1/chat/completions") == Provider.OPENAI

    def test_gemini_exact(self):
        assert (
            detect_provider("https://generativelanguage.googleapis.com/v1beta") == Provider.GEMINI
        )

    def test_groq_exact(self):
        assert detect_provider("https://api.groq.com/openai/v1/chat/completions") == Provider.GROQ

    def test_xai_exact(self):
        assert detect_provider("https://api.x.ai/v1/chat/completions") == Provider.XAI

    def test_fireworks_exact(self):
        assert (
            detect_provider("https://api.fireworks.ai/inference/v1/chat/completions")
            == Provider.FIREWORKS
        )

    def test_together_xyz_exact(self):
        assert detect_provider("https://api.together.xyz/v1/chat/completions") == Provider.TOGETHER

    def test_together_ai_exact(self):
        assert detect_provider("https://api.together.ai/v1/chat/completions") == Provider.TOGETHER

    def test_codex_chatgpt_exact(self):
        assert detect_provider("https://chatgpt.com/backend-api/conversation") == Provider.CODEX

    def test_voyage_exact(self):
        assert detect_provider("https://api.voyageai.com/v1/embeddings") == Provider.VOYAGE

    def test_jina_exact(self):
        assert detect_provider("https://api.jina.ai/v1/embeddings") == Provider.JINA


# ---------------------------------------------------------------------------
# detect_provider — suffix / subdomain matches
# ---------------------------------------------------------------------------


class TestDetectProviderSuffixRules:
    def test_anthropic_subdomain(self):
        assert detect_provider("https://regional.anthropic.com/v1/messages") == Provider.ANTHROPIC

    def test_azure_openai_openai_azure_com(self):
        result = detect_provider("https://mydeployment.openai.azure.com/openai/deployments")
        assert result == Provider.AZURE_OPENAI

    def test_azure_openai_azure_api_net(self):
        result = detect_provider("https://mygateway.azure-api.net/openai/v1")
        assert result == Provider.AZURE_OPENAI

    def test_openai_subdomain(self):
        # subdomain of openai.com (not azure) → OPENAI
        assert detect_provider("https://foo.openai.com/v1/chat/completions") == Provider.OPENAI

    def test_gemini_googleapis_subdomain(self):
        assert (
            detect_provider("https://us-central1-aiplatform.googleapis.com/v1/projects")
            == Provider.GEMINI
        )

    def test_groq_subdomain(self):
        assert (
            detect_provider("https://api-eu.groq.com/openai/v1/chat/completions") == Provider.GROQ
        )

    def test_xai_subdomain(self):
        assert detect_provider("https://eu.x.ai/v1/chat/completions") == Provider.XAI

    def test_fireworks_subdomain(self):
        assert detect_provider("https://eu.fireworks.ai/inference/v1") == Provider.FIREWORKS

    def test_together_xyz_subdomain(self):
        assert detect_provider("https://eu.together.xyz/v1/chat") == Provider.TOGETHER

    def test_together_ai_subdomain(self):
        assert detect_provider("https://eu.together.ai/v1/chat") == Provider.TOGETHER


# ---------------------------------------------------------------------------
# detect_provider — Bedrock hostname detection
# ---------------------------------------------------------------------------


class TestDetectProviderBedrock:
    def test_bedrock_us_east(self):
        result = detect_provider("https://bedrock-runtime.us-east-1.amazonaws.com/model/invoke")
        assert result == Provider.BEDROCK

    def test_bedrock_us_west(self):
        result = detect_provider("https://bedrock-runtime.us-west-2.amazonaws.com/model/invoke")
        assert result == Provider.BEDROCK

    def test_amazonaws_without_bedrock_is_unknown(self):
        # A generic amazonaws.com hostname with no bedrock keyword
        result = detect_provider("https://s3.us-east-1.amazonaws.com/bucket/key")
        assert result == Provider.UNKNOWN


# ---------------------------------------------------------------------------
# detect_provider — UNKNOWN / edge cases
# ---------------------------------------------------------------------------


class TestDetectProviderEdgeCases:
    def test_empty_string(self):
        assert detect_provider("") == Provider.UNKNOWN

    def test_none_like_empty(self):
        # None is not valid but let's ensure it returns UNKNOWN via falsy check
        assert detect_provider("") == Provider.UNKNOWN

    def test_unrecognized_host(self):
        assert detect_provider("https://somerandomprovider.io/v1/chat") == Provider.UNKNOWN

    def test_malformed_url_no_scheme(self):
        # urlparse("not-a-url") returns an empty hostname — should return UNKNOWN
        result = detect_provider("not a url at all")
        assert result == Provider.UNKNOWN

    def test_localhost_is_unknown(self):
        assert detect_provider("http://localhost:8080/v1/chat") == Provider.UNKNOWN

    def test_ip_address_is_unknown(self):
        assert detect_provider("http://192.168.1.1:11434/v1/chat") == Provider.UNKNOWN

    def test_url_with_path_and_query(self):
        # Path/query params should not affect detection
        result = detect_provider("https://api.anthropic.com/v1/messages?foo=bar&baz=qux")
        assert result == Provider.ANTHROPIC

    def test_url_with_port(self):
        # Port should not affect detection
        assert detect_provider("https://api.openai.com:443/v1/chat") == Provider.OPENAI
