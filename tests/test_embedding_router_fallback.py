"""Integration tests for EmbeddingRouter fallback chain.

Covers:
- Priority-based provider selection when all keys are present
- Fallback when primary provider keys are absent (discovery-time fallback)
- Rediscovery after a provider key is revoked (models post-401 behaviour)
- Rate-limit cooldown simulation via available_providers mutation
- No-providers RuntimeError
- get_providers_status() structure and cooldown_until contract
- handle_request() end-to-end routing
- resolve_model() with explicit model names
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from tokenpak.proxy.adapters.embedding_router import EmbeddingRouter

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# Base environment: all providers absent (empty string keeps is_available() False
# without triggering Ollama's network probe).
_NO_PROVIDERS: dict = {
    "VOYAGE_API_KEY": "",
    "OPENAI_API_KEY": "",
    "GEMINI_API_KEY": "",
    "JINA_API_KEY": "",
    "TOKENPAK_OLLAMA_URL": "",
}

# All five providers present.
_ALL_PROVIDERS: dict = {
    "VOYAGE_API_KEY": "vk-test",
    "OPENAI_API_KEY": "sk-test",
    "GEMINI_API_KEY": "gk-test",
    "JINA_API_KEY": "jk-test",
    "TOKENPAK_OLLAMA_URL": "http://localhost:11434",
}


def _env(**overrides: str) -> dict:
    """Return _NO_PROVIDERS with selected keys overridden."""
    e = dict(_NO_PROVIDERS)
    e.update(overrides)
    return e


def _router(**env_overrides: str) -> EmbeddingRouter:
    """Construct a freshly-discovered EmbeddingRouter under a controlled env."""
    with patch.dict(os.environ, _env(**env_overrides), clear=False):
        return EmbeddingRouter()


# ---------------------------------------------------------------------------
# 1. Priority — all keys set, Voyage wins
# ---------------------------------------------------------------------------


class TestProviderPriorityAllKeysSet(unittest.TestCase):
    """With all five API keys present, Voyage must be the top-priority provider."""

    def setUp(self):
        with patch.dict(os.environ, _ALL_PROVIDERS, clear=False):
            self.router = EmbeddingRouter()

    def test_voyage_is_first_in_available_providers(self):
        self.assertEqual(self.router.available_providers[0].source_format, "voyage-embeddings")

    def test_all_five_providers_discovered(self):
        names = [a.source_format for a in self.router.available_providers]
        self.assertEqual(len(names), 5)
        self.assertIn("voyage-embeddings", names)
        self.assertIn("openai-embeddings", names)
        self.assertIn("gemini-embeddings", names)
        self.assertIn("jina-embeddings", names)
        self.assertIn("ollama-embeddings", names)

    def test_auto_model_selects_voyage_default(self):
        with patch.dict(os.environ, _ALL_PROVIDERS, clear=False):
            model, adapter = self.router.resolve_model("auto")
        self.assertEqual(adapter.source_format, "voyage-embeddings")
        self.assertEqual(model, "voyage-3.5")

    def test_available_providers_priority_order(self):
        formats = [a.source_format for a in self.router.available_providers]
        expected_order = [
            "voyage-embeddings",
            "openai-embeddings",
            "gemini-embeddings",
            "jina-embeddings",
            "ollama-embeddings",
        ]
        self.assertEqual(formats, expected_order)


# ---------------------------------------------------------------------------
# 2. Fallback chain — discovery-time fallback when keys are absent
# ---------------------------------------------------------------------------


class TestFallbackChainDiscovery(unittest.TestCase):
    """When higher-priority keys are absent, the next provider becomes primary."""

    def test_voyage_absent_selects_openai(self):
        router = _router(OPENAI_API_KEY="sk-test")
        self.assertEqual(router.available_providers[0].source_format, "openai-embeddings")
        self.assertEqual(len(router.available_providers), 1)

    def test_voyage_openai_absent_selects_gemini(self):
        router = _router(GEMINI_API_KEY="gk-test")
        self.assertEqual(router.available_providers[0].source_format, "gemini-embeddings")

    def test_voyage_openai_gemini_absent_selects_jina(self):
        router = _router(JINA_API_KEY="jk-test")
        self.assertEqual(router.available_providers[0].source_format, "jina-embeddings")

    def test_only_ollama_available(self):
        router = _router(TOKENPAK_OLLAMA_URL="http://localhost:11434")
        self.assertEqual(len(router.available_providers), 1)
        self.assertEqual(router.available_providers[0].source_format, "ollama-embeddings")

    def test_only_jina_available(self):
        router = _router(JINA_API_KEY="jk-test")
        names = [a.source_format for a in router.available_providers]
        self.assertEqual(names, ["jina-embeddings"])

    def test_fallback_cascade_voyage_openai_gemini_absent_selects_jina(self):
        router = _router(JINA_API_KEY="jk-test", TOKENPAK_OLLAMA_URL="http://localhost:11434")
        self.assertEqual(router.available_providers[0].source_format, "jina-embeddings")


# ---------------------------------------------------------------------------
# 3. No providers available
# ---------------------------------------------------------------------------


class TestNoProvidersError(unittest.TestCase):
    """When no provider keys are set, the router must raise clear errors."""

    def setUp(self):
        self.router = _router()  # all keys absent / empty

    def test_no_providers_discover_returns_empty_list(self):
        self.assertEqual(self.router.available_providers, [])

    def test_no_providers_resolve_model_raises_runtime_error(self):
        with self.assertRaises(RuntimeError):
            self.router.resolve_model("auto")

    def test_no_providers_error_message_mentions_env_vars(self):
        try:
            self.router.resolve_model("auto")
            self.fail("Expected RuntimeError")
        except RuntimeError as exc:
            msg = str(exc)
            self.assertIn("VOYAGE_API_KEY", msg)
            self.assertIn("OPENAI_API_KEY", msg)

    def test_no_providers_handle_request_raises(self):
        body = json.dumps({"model": "auto", "input": ["hello"]}).encode()
        with self.assertRaises(RuntimeError):
            self.router.handle_request("/v1/embeddings", {}, body)


# ---------------------------------------------------------------------------
# 4. Rediscovery after auth failure (post-401 proxy pattern)
# ---------------------------------------------------------------------------


class TestRediscoveryAfterAuthFailure(unittest.TestCase):
    """After a 401, the proxy can revoke the env key and call discover_providers()
    to drop the failing provider from the rotation.
    """

    def test_rediscovery_drops_revoked_voyage_key(self):
        with patch.dict(os.environ, _ALL_PROVIDERS, clear=False):
            router = EmbeddingRouter()
            self.assertEqual(router.available_providers[0].source_format, "voyage-embeddings")
            # Simulate key revocation after 401
            os.environ["VOYAGE_API_KEY"] = ""
            router.discover_providers()
            names = [a.source_format for a in router.available_providers]
        self.assertNotIn("voyage-embeddings", names)

    def test_rediscovery_falls_to_openai_after_voyage_revoked(self):
        with patch.dict(os.environ, _ALL_PROVIDERS, clear=False):
            router = EmbeddingRouter()
            os.environ["VOYAGE_API_KEY"] = ""
            router.discover_providers()
            top = router.available_providers[0].source_format
        self.assertEqual(top, "openai-embeddings")

    def test_rediscovery_restores_voyage_when_key_added_back(self):
        env = dict(_ALL_PROVIDERS)
        env["VOYAGE_API_KEY"] = ""
        with patch.dict(os.environ, env, clear=False):
            router = EmbeddingRouter()
            self.assertNotEqual(router.available_providers[0].source_format, "voyage-embeddings")
            # Simulate key re-addition (e.g., operator corrects key)
            os.environ["VOYAGE_API_KEY"] = "vk-restored"
            router.discover_providers()
            self.assertEqual(router.available_providers[0].source_format, "voyage-embeddings")

    def test_rediscovery_to_empty_raises_on_resolve(self):
        with patch.dict(os.environ, _env(VOYAGE_API_KEY="vk-test"), clear=False):
            router = EmbeddingRouter()
            os.environ["VOYAGE_API_KEY"] = ""
            router.discover_providers()
            with self.assertRaises(RuntimeError):
                router.resolve_model("auto")


# ---------------------------------------------------------------------------
# 5. Rate-limit fallback boundary (429 cooldown simulation)
# ---------------------------------------------------------------------------


class TestRateLimitFallbackBoundary(unittest.TestCase):
    """The proxy injects cooldown by removing a provider from available_providers
    after a 429. EmbeddingRouter.resolve_model() must honour that modified list.
    """

    def test_manual_provider_removal_simulates_cooldown_injection(self):
        with patch.dict(os.environ, _ALL_PROVIDERS, clear=False):
            router = EmbeddingRouter()
            # Simulate: proxy received 429 from Voyage, injects cooldown by removal
            router.available_providers = [
                a for a in router.available_providers if a.source_format != "voyage-embeddings"
            ]
            model, adapter = router.resolve_model("auto")
        self.assertEqual(adapter.source_format, "openai-embeddings")

    def test_resolve_uses_available_providers_not_all_adapters(self):
        with patch.dict(os.environ, _ALL_PROVIDERS, clear=False):
            router = EmbeddingRouter()
            # Keep only Jina to simulate all others on cooldown
            router.available_providers = [
                a for a in router.available_providers if a.source_format == "jina-embeddings"
            ]
            model, adapter = router.resolve_model("auto")
        self.assertEqual(adapter.source_format, "jina-embeddings")


# ---------------------------------------------------------------------------
# 6. get_providers_status — structure and cooldown contract
# ---------------------------------------------------------------------------


class TestGetProvidersStatus(unittest.TestCase):
    """get_providers_status() must return the expected dict shape for all providers."""

    def setUp(self):
        with patch.dict(os.environ, _env(VOYAGE_API_KEY="vk-test"), clear=False):
            self.router = EmbeddingRouter()
            self.status = self.router.get_providers_status()

    def test_status_returns_five_entries(self):
        self.assertEqual(len(self.status), 5)

    def test_status_entry_has_required_keys(self):
        required = {"name", "available", "healthy", "default_model", "key_set", "cooldown_until"}
        for entry in self.status:
            self.assertEqual(set(entry.keys()), required)

    def test_status_cooldown_until_is_always_none(self):
        for entry in self.status:
            self.assertIsNone(entry["cooldown_until"])

    def test_status_voyage_available_when_key_set(self):
        voyage = next(e for e in self.status if e["name"] == "voyage-embeddings")
        self.assertTrue(voyage["available"])
        self.assertTrue(voyage["key_set"])

    def test_status_voyage_unavailable_when_key_absent(self):
        with patch.dict(os.environ, _NO_PROVIDERS, clear=False):
            router = EmbeddingRouter()
        status = router.get_providers_status()
        voyage = next(e for e in status if e["name"] == "voyage-embeddings")
        self.assertFalse(voyage["available"])

    def test_status_healthy_mirrors_available(self):
        for entry in self.status:
            self.assertEqual(entry["healthy"], entry["available"])


# ---------------------------------------------------------------------------
# 7. handle_request — end-to-end routing
# ---------------------------------------------------------------------------


class TestHandleRequestIntegration(unittest.TestCase):
    """handle_request() must produce correct (url, headers, body) for each provider."""

    def _call(self, body_dict: dict, env: dict) -> tuple:
        with patch.dict(os.environ, env, clear=False):
            router = EmbeddingRouter()
            body = json.dumps(body_dict).encode()
            return router.handle_request("/v1/embeddings", {}, body)

    def test_returns_tuple_of_three(self):
        result = self._call(
            {"model": "voyage-3.5", "input": ["hi"]},
            _env(VOYAGE_API_KEY="vk-test"),
        )
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)

    def test_auto_model_routes_to_voyage_url(self):
        url, _, _ = self._call(
            {"model": "auto", "input": ["hello"]},
            _env(VOYAGE_API_KEY="vk-test"),
        )
        self.assertIn("voyageai.com", url)

    def test_explicit_voyage_model_routes_to_voyage_url(self):
        url, _, _ = self._call(
            {"model": "voyage-3.5", "input": ["hello"]},
            _env(VOYAGE_API_KEY="vk-test"),
        )
        self.assertIn("voyageai.com", url)

    def test_explicit_openai_model_routes_to_openai_url(self):
        url, _, _ = self._call(
            {"model": "text-embedding-3-small", "input": ["hello"]},
            _env(OPENAI_API_KEY="sk-test"),
        )
        self.assertIn("openai.com", url)

    def test_explicit_jina_model_routes_to_jina_url(self):
        url, _, _ = self._call(
            {"model": "jina-embeddings-v3", "input": ["hello"]},
            _env(JINA_API_KEY="jk-test"),
        )
        self.assertIn("jina.ai", url)

    def test_output_headers_contain_authorization(self):
        _, headers, _ = self._call(
            {"model": "voyage-3.5", "input": ["hello"]},
            _env(VOYAGE_API_KEY="vk-test"),
        )
        self.assertIn("Authorization", headers)

    def test_authorization_header_starts_with_bearer(self):
        _, headers, _ = self._call(
            {"model": "voyage-3.5", "input": ["hello"]},
            _env(VOYAGE_API_KEY="vk-test"),
        )
        self.assertTrue(headers["Authorization"].startswith("Bearer "))

    def test_authorization_header_uses_api_key(self):
        _, headers, _ = self._call(
            {"model": "voyage-3.5", "input": ["hello"]},
            _env(VOYAGE_API_KEY="vk-secret-123"),
        )
        self.assertIn("vk-secret-123", headers["Authorization"])

    def test_output_body_is_valid_json(self):
        _, _, body = self._call(
            {"model": "voyage-3.5", "input": ["hello"]},
            _env(VOYAGE_API_KEY="vk-test"),
        )
        parsed = json.loads(body)
        self.assertIsInstance(parsed, dict)

    def test_output_body_contains_model_field(self):
        _, _, body = self._call(
            {"model": "voyage-3.5", "input": ["hello"]},
            _env(VOYAGE_API_KEY="vk-test"),
        )
        payload = json.loads(body)
        self.assertIn("model", payload)

    def test_input_field_preserved_in_output_body(self):
        _, _, body = self._call(
            {"model": "voyage-3.5", "input": ["embed this text"]},
            _env(VOYAGE_API_KEY="vk-test"),
        )
        payload = json.loads(body)
        self.assertIn("embed this text", payload.get("input", []))

    def test_invalid_json_body_raises_value_error(self):
        with patch.dict(os.environ, _env(VOYAGE_API_KEY="vk-test"), clear=False):
            router = EmbeddingRouter()
            with self.assertRaises(ValueError):
                router.handle_request("/v1/embeddings", {}, b"not-json{{{")

    def test_unknown_model_raises_value_error(self):
        with patch.dict(os.environ, _env(VOYAGE_API_KEY="vk-test"), clear=False):
            router = EmbeddingRouter()
            body = json.dumps({"model": "totally-unknown-xyz-999", "input": ["hi"]}).encode()
            with self.assertRaises(ValueError):
                router.handle_request("/v1/embeddings", {}, body)

    def test_unconfigured_model_raises_value_error(self):
        # OpenAI key absent — requesting an OpenAI model should raise ValueError
        with patch.dict(os.environ, _env(VOYAGE_API_KEY="vk-test"), clear=False):
            router = EmbeddingRouter()
            body = json.dumps({"model": "text-embedding-3-small", "input": ["hi"]}).encode()
            with self.assertRaises(ValueError):
                router.handle_request("/v1/embeddings", {}, body)


# ---------------------------------------------------------------------------
# 8. resolve_model — explicit model name routing
# ---------------------------------------------------------------------------


class TestResolveModelExplicit(unittest.TestCase):
    """Explicit model names must route to the correct adapter."""

    def _resolve(self, model: str, env: dict) -> tuple:
        with patch.dict(os.environ, env, clear=False):
            router = EmbeddingRouter()
            return router.resolve_model(model)

    def test_voyage_model_resolves_to_voyage_adapter(self):
        _, adapter = self._resolve("voyage-3.5", _env(VOYAGE_API_KEY="vk-test"))
        self.assertEqual(adapter.source_format, "voyage-embeddings")

    def test_voyage_code_model_resolves_to_voyage(self):
        _, adapter = self._resolve("voyage-code-3", _env(VOYAGE_API_KEY="vk-test"))
        self.assertEqual(adapter.source_format, "voyage-embeddings")

    def test_openai_large_resolves_to_openai(self):
        _, adapter = self._resolve("text-embedding-3-large", _env(OPENAI_API_KEY="sk-test"))
        self.assertEqual(adapter.source_format, "openai-embeddings")

    def test_openai_small_resolves_to_openai(self):
        _, adapter = self._resolve("text-embedding-3-small", _env(OPENAI_API_KEY="sk-test"))
        self.assertEqual(adapter.source_format, "openai-embeddings")

    def test_jina_model_resolves_to_jina(self):
        _, adapter = self._resolve("jina-embeddings-v3", _env(JINA_API_KEY="jk-test"))
        self.assertEqual(adapter.source_format, "jina-embeddings")

    def test_unconfigured_voyage_model_raises_value_error(self):
        # Voyage key absent but OpenAI present: voyage-3.5 should raise ValueError
        # (router finds Voyage adapter via detect() but its key is not configured)
        with patch.dict(os.environ, _env(OPENAI_API_KEY="sk-test"), clear=False):
            router = EmbeddingRouter()
        with self.assertRaises(ValueError):
            router.resolve_model("voyage-3.5")

    def test_unknown_model_raises_value_error(self):
        with patch.dict(os.environ, _env(VOYAGE_API_KEY="vk-test"), clear=False):
            router = EmbeddingRouter()
        with self.assertRaises(ValueError):
            router.resolve_model("totally-unknown-model-xyz")


if __name__ == "__main__":
    unittest.main()
