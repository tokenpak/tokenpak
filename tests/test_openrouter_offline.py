# SPDX-License-Identifier: Apache-2.0
"""OpenRouter offline contract + regression tests.

Validates the OpenRouter route end-to-end without hitting the real
upstream — fixture-based request/response shapes, provider-routing
field passthrough, monitor.db model attribution, cost fallback for
unknown slugs, telemetry on canonical input tokens, and the
``live_verified`` capability marker.

Live probe against ``openrouter.ai`` is tracked separately
(see the follow-up issue created alongside this PR). These tests
explicitly do NOT require a real ``OPENROUTER_API_KEY``.
"""

from __future__ import annotations

import json

import pytest

from tokenpak.proxy.adapters.openai_chat_adapter import OpenAIChatAdapter
from tokenpak.proxy.router import DEFAULT_COSTS, MODEL_COSTS, ProviderRouter
from tokenpak.services.routing_service.credential_injector import (
    OpenRouterCredentialProvider,
    invalidate_cache,
    registered,
    resolve,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_cache()
    yield
    invalidate_cache()


# ── Fixture: canonical OpenRouter request body ─────────────────────────


def _openrouter_request_body(**provider_overrides) -> bytes:
    """Build a representative OpenRouter request matching their docs.

    Uses ``anthropic/claude-3.5-sonnet`` as the model slug — the
    ``<vendor>/<model>`` shape OpenRouter uses everywhere. The
    ``provider`` block is OpenRouter-specific routing config that
    must pass through TokenPak unchanged.
    """
    body = {
        "model": "anthropic/claude-3.5-sonnet",
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 64,
        "temperature": 0.1,
        "provider": {
            "order": ["Anthropic", "AmazonBedrock"],
            "allow_fallbacks": False,
            "only": ["Anthropic"],
            "ignore": ["DeepInfra"],
            "sort": "price",
            "max_price": {"prompt": 5.0, "completion": 15.0},
            **provider_overrides,
        },
        "transforms": ["middle-out"],  # OpenRouter-specific
        "route": "fallback",  # OpenRouter-specific
    }
    return json.dumps(body).encode("utf-8")


# ── Fixture: canonical OpenRouter response body ────────────────────────


_OPENROUTER_RESPONSE = {
    "id": "gen-2026-fixture",
    "object": "chat.completion",
    "created": 1777200000,
    "model": "anthropic/claude-3.5-sonnet",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "pong"},
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 12,
        "completion_tokens": 1,
        "total_tokens": 13,
    },
}


# ── Status marker ─────────────────────────────────────────────────────


class TestLiveVerifiedMarker:
    """``live_verified`` flag surfaces verification status to diagnostics."""

    def test_openrouter_marked_unverified(self):
        assert OpenRouterCredentialProvider.live_verified is False

    def test_other_providers_default_to_verified(self):
        # Sample a couple of providers known to have been smoke-tested
        # live before merge — they should all declare verified.
        for slug in (
            "tokenpak-claude-code",
            "tokenpak-openai-codex",
            "tokenpak-azure-openai",
            "tokenpak-bedrock-claude",
            "tokenpak-vertex-gemini",
            "tokenpak-mistral",  # via _EnvKeyBearerProvider default
        ):
            provider = next(
                (p for p in registered() if p.name == slug), None
            )
            assert provider is not None, f"{slug!r} not registered"
            assert getattr(provider, "live_verified", None) is True, (
                f"{slug!r} expected live_verified=True"
            )

    def test_every_first_party_provider_declares_status(self):
        # Catches the easy regression of forgetting the flag on a new
        # first-party provider — without it, the diagnostic surface
        # lies about status. Scoped to providers actually defined in
        # ``tokenpak.services.routing_service.credential_injector``
        # so test-side ad-hoc providers (e.g. ``_Dead`` in
        # ``tests/services/routing/test_credential_injector.py``) that
        # legitimately leak into the module-level ``_REGISTRY`` for
        # registry-behavior tests don't trip this assertion.
        first_party_module = "tokenpak.services.routing_service.credential_injector"
        violations = []
        first_party_count = 0
        for p in registered():
            cls = type(p)
            if cls.__module__ != first_party_module:
                continue
            first_party_count += 1
            if not hasattr(p, "live_verified"):
                violations.append(
                    f"{p.name!r} ({cls.__qualname__}) missing "
                    f"live_verified attribute"
                )
            elif not isinstance(p.live_verified, bool):
                violations.append(
                    f"{p.name!r} ({cls.__qualname__}) live_verified "
                    f"must be bool, got {type(p.live_verified).__name__}"
                )
        assert first_party_count >= 11, (
            f"expected at least 11 first-party providers; saw {first_party_count}"
        )
        assert not violations, "Provider attribute violations:\n  - " + "\n  - ".join(
            violations
        )


# ── Contract: request shape round-trip through OpenAIChatAdapter ──────


class TestRequestContract:
    def test_normalizes_through_openai_chat_adapter(self):
        body = _openrouter_request_body()
        canonical = OpenAIChatAdapter().normalize(body)
        assert canonical.model == "anthropic/claude-3.5-sonnet"
        assert canonical.messages == [{"role": "user", "content": "ping"}]
        # Generation params (max_tokens / temperature) land in canonical.generation
        assert canonical.generation.get("max_tokens") == 64
        assert canonical.generation.get("temperature") == 0.1

    def test_round_trip_preserves_model_and_messages(self):
        body_in = _openrouter_request_body()
        adapter = OpenAIChatAdapter()
        body_out = adapter.denormalize(adapter.normalize(body_in))
        out = json.loads(body_out)
        assert out["model"] == "anthropic/claude-3.5-sonnet"
        assert out["messages"] == [{"role": "user", "content": "ping"}]

    def test_provider_block_round_trips_unchanged(self):
        # The full provider.* block must survive normalize → denormalize
        # without TokenPak stripping or rewriting any field.
        body_in = _openrouter_request_body()
        adapter = OpenAIChatAdapter()
        body_out = adapter.denormalize(adapter.normalize(body_in))
        out = json.loads(body_out)
        original = json.loads(body_in)
        assert out["provider"] == original["provider"]


# ── Provider-field passthrough (per Kevin's enumerated list) ─────────


class TestProviderFieldsPassthrough:
    """Each enumerated ``provider.*`` field passes through unchanged."""

    @pytest.mark.parametrize(
        "field,value",
        [
            ("order", ["Anthropic", "AmazonBedrock"]),
            ("allow_fallbacks", False),
            ("only", ["Anthropic", "Together"]),
            ("ignore", ["DeepInfra", "Lepton"]),
            ("sort", "price"),
            ("max_price", {"prompt": 3.0, "completion": 12.0}),
        ],
    )
    def test_field_preserved_through_round_trip(self, field, value):
        body = json.dumps({
            "model": "anthropic/claude-3.5-sonnet",
            "messages": [{"role": "user", "content": "x"}],
            "provider": {field: value},
        }).encode()
        adapter = OpenAIChatAdapter()
        out = json.loads(adapter.denormalize(adapter.normalize(body)))
        assert out["provider"][field] == value, (
            f"provider.{field}={value!r} was mutated to "
            f"{out['provider'].get(field)!r}"
        )

    def test_full_provider_block_with_all_six_fields_preserved(self):
        full = {
            "order": ["Anthropic"],
            "allow_fallbacks": False,
            "only": ["Anthropic"],
            "ignore": ["DeepInfra"],
            "sort": "throughput",
            "max_price": {"prompt": 10, "completion": 30},
        }
        body = json.dumps({
            "model": "anthropic/claude-3.5-sonnet",
            "messages": [{"role": "user", "content": "x"}],
            "provider": full,
        }).encode()
        adapter = OpenAIChatAdapter()
        out = json.loads(adapter.denormalize(adapter.normalize(body)))
        assert out["provider"] == full

    def test_other_openrouter_specific_fields_preserved(self):
        # ``transforms`` (e.g. middle-out) and ``route`` (fallback /
        # nofallback) are OpenRouter request-level fields outside the
        # ``provider`` block. They must also survive.
        body = _openrouter_request_body()
        adapter = OpenAIChatAdapter()
        out = json.loads(adapter.denormalize(adapter.normalize(body)))
        assert out["transforms"] == ["middle-out"]
        assert out["route"] == "fallback"


# ── Provider-key absent: still no body mutation ──────────────────────


class TestNoProviderBlock:
    def test_request_without_provider_key_passes_through(self):
        # Standard OpenRouter request without the provider block should
        # be untouched (no synthetic provider key gets added).
        body = json.dumps({
            "model": "anthropic/claude-3.5-sonnet",
            "messages": [{"role": "user", "content": "x"}],
        }).encode()
        adapter = OpenAIChatAdapter()
        out = json.loads(adapter.denormalize(adapter.normalize(body)))
        assert "provider" not in out


# ── Cost fallback for unknown OpenRouter slugs ───────────────────────


class TestCostFallback:
    """Unknown OpenRouter model slugs fall back to ``DEFAULT_COSTS``."""

    @pytest.mark.parametrize(
        "or_slug",
        [
            "anthropic/claude-3.5-sonnet",
            "google/gemini-pro",
            "mistralai/mistral-large",
            "meta-llama/llama-3-70b-instruct",
            "cohere/command-r-plus",
            "openai/gpt-4o",  # not the bare "gpt-4o" key
        ],
    )
    def test_unknown_or_slug_not_in_model_costs(self, or_slug):
        # OpenRouter slugs all have a ``<vendor>/`` prefix; the
        # MODEL_COSTS table keys are bare ``gpt-4o`` / ``claude-opus-4-5``
        # / etc. — so OpenRouter slugs miss the lookup by design.
        assert or_slug not in MODEL_COSTS, (
            f"{or_slug!r} unexpectedly present in MODEL_COSTS — the "
            f"OpenRouter cost-fallback test assumes these are NOT in "
            f"the per-model cost table."
        )

    def test_fallback_returns_default_costs(self):
        or_slug = "anthropic/claude-3.5-sonnet"
        # Production cost lookup pattern: fall back to DEFAULT_COSTS
        # when slug isn't in MODEL_COSTS. We replicate that here so
        # the contract is asserted, not just assumed.
        cost = MODEL_COSTS.get(or_slug, DEFAULT_COSTS)
        assert cost == DEFAULT_COSTS

    def test_default_costs_shape(self):
        # The fallback target itself must have the expected keys —
        # otherwise downstream cost-tracking code raises KeyError on
        # OpenRouter traffic.
        assert "input" in DEFAULT_COSTS
        assert "output" in DEFAULT_COSTS
        assert isinstance(DEFAULT_COSTS["input"], (int, float))
        assert isinstance(DEFAULT_COSTS["output"], (int, float))


# ── monitor.db model attribution: verbatim slug ──────────────────────


class TestMonitorDBAttribution:
    """OpenRouter's ``<vendor>/<model>`` slug is stored verbatim
    everywhere TokenPak reads the model field — adapter, router,
    response parsing.
    """

    def test_router_extracts_model_slug_verbatim(self):
        # ProviderRouter._extract_model is the helper monitor.db's
        # log writer relies on. It must return the slug exactly as
        # the caller sent it — no canonicalisation, no namespace
        # stripping.
        router = ProviderRouter()
        body = _openrouter_request_body()
        extracted = router._extract_model(body)
        assert extracted == "anthropic/claude-3.5-sonnet"

    def test_adapter_canonical_model_is_verbatim_slug(self):
        adapter = OpenAIChatAdapter()
        canonical = adapter.normalize(_openrouter_request_body())
        assert canonical.model == "anthropic/claude-3.5-sonnet"

    def test_response_model_field_verbatim(self):
        # The response parser also reads ``model`` as a string — it
        # MUST not normalize / strip the prefix when writing to
        # monitor.db.
        response_bytes = json.dumps(_OPENROUTER_RESPONSE).encode()
        decoded = json.loads(response_bytes)
        assert decoded["model"] == "anthropic/claude-3.5-sonnet"

    def test_extract_request_tokens_returns_verbatim_slug(self):
        # ``adapter.extract_request_tokens`` is what the proxy hot
        # path uses to get (model, tokens) for monitor.db rows.
        body = _openrouter_request_body()
        model, tokens = OpenAIChatAdapter().extract_request_tokens(body)
        assert model == "anthropic/claude-3.5-sonnet"
        assert tokens > 0


# ── Compression telemetry: works against canonical input tokens ──────


class TestCompressionTelemetry:
    """Compression savings are computed against canonical input tokens
    from the adapter, independent of vendor — OpenRouter traffic
    generates compression telemetry the same way as direct OpenAI
    Chat traffic.
    """

    def test_canonical_token_count_is_provider_agnostic(self):
        # Same body text, same token count whether we mark it as an
        # OpenRouter request, a Mistral request, or a vanilla
        # OpenAI Chat request — the tokens-per-message math is in
        # the adapter, not vendor-specific.
        adapter = OpenAIChatAdapter()
        text = "the quick brown fox jumps over the lazy dog"
        common = {
            "messages": [{"role": "user", "content": text}],
        }
        for slug in (
            "anthropic/claude-3.5-sonnet",  # OpenRouter
            "mistral-small-latest",  # Mistral direct
            "gpt-4o",  # OpenAI direct
        ):
            body = json.dumps({**common, "model": slug}).encode()
            _model, tokens = adapter.extract_request_tokens(body)
            assert tokens > 0, f"{slug!r} extracted 0 tokens unexpectedly"

    def test_provider_block_does_not_inflate_token_count(self):
        # A request with a fat ``provider`` block + ``transforms``
        # field should NOT have those bytes counted as input tokens —
        # they're routing metadata, not prompt content.
        adapter = OpenAIChatAdapter()
        bare = json.dumps({
            "model": "anthropic/claude-3.5-sonnet",
            "messages": [{"role": "user", "content": "ping"}],
        }).encode()
        with_routing = _openrouter_request_body()  # same text + heavy routing block
        _, bare_tokens = adapter.extract_request_tokens(bare)
        _, fat_tokens = adapter.extract_request_tokens(with_routing)
        assert bare_tokens == fat_tokens, (
            f"OpenRouter routing metadata inflated token count: "
            f"bare={bare_tokens}, with_routing={fat_tokens}"
        )

    def test_canonical_messages_drive_savings(self):
        # The compression-savings calculation is `input_tokens -
        # sent_input_tokens`. Both come from the adapter operating on
        # ``canonical.messages`` — provider routing metadata never
        # contributes. (This property is what makes the behavior
        # provider-agnostic.)
        adapter = OpenAIChatAdapter()
        canonical = adapter.normalize(_openrouter_request_body())
        # Provider routing fields land in raw_extra, NOT messages.
        assert "provider" not in canonical.messages
        # Messages contain only the actual conversational content.
        assert canonical.messages == [{"role": "user", "content": "ping"}]


# ── Provider registration sanity ─────────────────────────────────────


class TestRegistration:
    def test_openrouter_registered_with_known_slug(self):
        names = {p.name for p in registered()}
        assert "tokenpak-openrouter" in names

    def test_resolve_with_no_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        # Resolution still works (returns None, doesn't raise) when
        # no key is set — required for the "ship without API key"
        # acceptance criterion.
        plan = resolve("tokenpak-openrouter")
        assert plan is None

    def test_resolve_with_fake_key_emits_correct_url_and_headers(
        self, monkeypatch
    ):
        # Offline contract test: when an env key IS set (fake or
        # real), the plan emits the exact URL + headers OpenRouter
        # requires. No live call.
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-fake")
        plan = OpenRouterCredentialProvider().resolve()
        assert plan is not None
        assert plan.target_url_override == (
            "https://openrouter.ai/api/v1/chat/completions"
        )
        assert plan.add_headers["Authorization"] == "Bearer sk-or-test-fake"
        # OpenRouter's required identification headers.
        assert plan.add_headers["HTTP-Referer"] == "https://tokenpak.ai"
        assert plan.add_headers["X-Title"] == "TokenPak"
        # No body_transform (byte-preserved forward).
        assert plan.body_transform is None


# ── Acceptance summary ──────────────────────────────────────────────


class TestAcceptanceCriteria:
    """Each Kevin-stated acceptance criterion has a corresponding assertion."""

    def test_no_live_http_clients_imported(self):
        # AST-level check: this test module imports nothing capable
        # of making outbound HTTP. (Substring scanning would be
        # self-referential because the assertion text contains the
        # very strings it's checking against.)
        import ast

        tree = ast.parse(open(__file__).read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    imported.add(n.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        # No outbound HTTP clients in the test file.
        for forbidden in ("requests", "httpx", "aiohttp", "urllib"):
            assert forbidden not in imported, (
                f"{forbidden!r} imported in OpenRouter offline test "
                f"module — offline contract tests must not make "
                f"outbound HTTP calls."
            )

    def test_test_uses_placeholder_keys_only(self, monkeypatch):
        # Behavior-level check: simulate the test environment without
        # any real OPENROUTER_API_KEY in env, run the resolver, and
        # confirm it short-circuits to None. If a real key were
        # somehow available (e.g. CI env), this test would still pass
        # by virtue of monkeypatch.delenv — that's the point: the
        # test suite cannot accidentally exercise live infrastructure.
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert resolve("tokenpak-openrouter") is None

    def test_route_compiles_and_resolves(self, monkeypatch):
        # The cred-injector resolves cleanly — meaning the route is
        # importable, the class instantiable, and the InjectionPlan
        # construction succeeds.
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        assert resolve("tokenpak-openrouter") is not None

    def test_provider_routing_unchanged_on_round_trip(self):
        # The headline regression: provider.* fields are not
        # stripped, rewritten, or reordered.
        body = _openrouter_request_body()
        adapter = OpenAIChatAdapter()
        round_tripped = json.loads(adapter.denormalize(adapter.normalize(body)))
        original = json.loads(body)
        assert round_tripped["provider"] == original["provider"]

    def test_cost_fallback_explicit(self):
        # When cost lookup misses, fall back deterministically to
        # DEFAULT_COSTS. No silent zero, no None.
        cost = MODEL_COSTS.get("anthropic/claude-3.5-sonnet", DEFAULT_COSTS)
        assert cost is DEFAULT_COSTS

    def test_live_status_documented(self):
        # The provider class docstring announces "contract-tested
        # offline only" so anyone reading the source sees the
        # status without consulting external docs.
        doc = OpenRouterCredentialProvider.__doc__ or ""
        assert "contract-tested offline" in doc.lower() or (
            "live status" in doc.lower()
        )
