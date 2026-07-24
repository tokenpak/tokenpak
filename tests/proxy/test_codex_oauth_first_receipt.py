# SPDX-License-Identifier: Apache-2.0
"""Provider-neutral first-receipt contracts for native Codex OAuth traffic."""

from __future__ import annotations

import json
from types import SimpleNamespace

import zstandard

from tokenpak.companion.capsules.builder import CapsuleBuilder
from tokenpak.companion.codex import launcher
from tokenpak.proxy.capsule_integration import _estimate_tokens
from tokenpak.proxy.router import ProviderRouter
from tokenpak.proxy.server import _decode_request_entity, _ProxyHandler


def test_oauth_responses_routes_to_chatgpt_without_api_key_or_model_override() -> None:
    route = ProviderRouter().route(
        "/v1/responses?include=usage",
        {"Authorization": "Bearer oauth-session-token"},
    )

    assert route.provider == "openai-codex"
    assert route.auth_type == "oauth"
    assert route.full_url == "https://chatgpt.com/backend-api/codex/responses?include=usage"


def test_api_key_responses_remains_on_openai_api() -> None:
    route = ProviderRouter().route(
        "/v1/responses",
        {"Authorization": "Bearer sk-example"},
    )

    assert route.provider == "openai"
    assert route.auth_type == "apikey"
    assert route.full_url == "https://api.openai.com/v1/responses"


def test_native_codex_zstd_request_is_decoded_for_processing() -> None:
    payload = json.dumps(
        {
            "model": "client-selected-model",
            "input": [{"type": "message", "role": "user", "content": "hello"}],
        }
    ).encode()
    compressed = zstandard.ZstdCompressor().compress(payload)

    decoded, changed = _decode_request_entity(compressed, "zstd")

    assert changed is True
    assert decoded == payload


def _models_handler(monkeypatch, authorization: str):
    from tokenpak.proxy import app_endpoints

    monkeypatch.setattr(app_endpoints, "try_handle_get", lambda _handler: False)
    handler = object.__new__(_ProxyHandler)
    handler.server = SimpleNamespace(
        proxy_server=SimpleNamespace(
            router=ProviderRouter(),
            shutdown=SimpleNamespace(is_shutting_down=False),
        )
    )
    handler.path = "/v1/models?client_version=test"
    handler.headers = {"Authorization": authorization}
    handler._enforce_proxy_auth = lambda: True
    return handler


def test_oauth_model_refresh_uses_bundled_catalog_without_api_scope(monkeypatch) -> None:
    handler = _models_handler(monkeypatch, "Bearer oauth-session-token")
    replies: list[object] = []
    proxied: list[tuple[str, str]] = []
    handler._send_json = replies.append
    handler._proxy_to = lambda url, method: proxied.append((url, method))

    handler.do_GET()

    assert replies == [{"models": []}]
    assert proxied == []


def test_api_key_model_refresh_remains_provider_proxied(monkeypatch) -> None:
    handler = _models_handler(monkeypatch, "Bearer sk-example")
    replies: list[object] = []
    proxied: list[tuple[str, str]] = []
    handler._send_json = replies.append
    handler._proxy_to = lambda url, method: proxied.append((url, method))

    handler.do_GET()

    assert replies == []
    assert proxied == [("https://api.openai.com/v1/models?client_version=test", "GET")]


def test_responses_capsule_preserves_policy_and_compresses_only_history() -> None:
    policy_text = "You are the governed client.\n## Safety\nNever change this policy. " * 20
    history_text = "A long ordinary project update with background and rationale. " * 30
    payload = {
        "model": "client-selected-model",
        "stream": True,
        "input": [
            {
                "type": "message",
                "role": "developer",
                "content": [{"type": "input_text", "text": policy_text}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": history_text}],
            },
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Acknowledged."}],
            },
            {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "Continue."}],
            },
        ],
    }
    raw = json.dumps(payload).encode()

    sent, stats = CapsuleBuilder(enabled=True).process(raw)
    result = json.loads(sent)

    assert result["model"] == "client-selected-model"
    assert result["input"][0]["content"][0]["text"] == policy_text
    assert "[CAPSULE" in result["input"][1]["content"][0]["text"]
    assert result["input"][-1]["content"][0]["text"] == "Continue."
    assert stats["blocks_capsulized"] == 1
    assert _estimate_tokens(sent) < _estimate_tokens(raw)


def test_launcher_routes_healthy_proxy_without_credential_or_model_arguments(monkeypatch) -> None:
    monkeypatch.setattr(launcher, "_local_proxy_is_healthy", lambda: True)

    args, routed = launcher._with_tokenpak_proxy_route(["exec", "summarize this repository"])

    assert routed is True
    assert args[:12] == [
        "-c",
        'model_provider="tokenpak"',
        "-c",
        'model_providers.tokenpak.name="TokenPak local proxy"',
        "-c",
        'model_providers.tokenpak.base_url="http://127.0.0.1:8766/v1"',
        "-c",
        'model_providers.tokenpak.wire_api="responses"',
        "-c",
        "model_providers.tokenpak.requires_openai_auth=true",
        "-c",
        "model_providers.tokenpak.supports_websockets=false",
    ]
    assert "--model" not in args
    assert "-m" not in args
    assert all("api_key" not in arg.lower() for arg in args)


def test_launcher_preserves_explicit_base_override(monkeypatch) -> None:
    monkeypatch.setattr(launcher, "_local_proxy_is_healthy", lambda: True)
    explicit = ["-c", 'openai_base_url="http://example.invalid/v1"', "exec", "hello"]

    args, routed = launcher._with_tokenpak_proxy_route(explicit)

    assert routed is False
    assert args == explicit


def test_launcher_preserves_explicit_model_provider(monkeypatch) -> None:
    monkeypatch.setattr(launcher, "_local_proxy_is_healthy", lambda: True)
    explicit = ["-c", 'model_provider="company-proxy"', "exec", "hello"]

    args, routed = launcher._with_tokenpak_proxy_route(explicit)

    assert routed is False
    assert args == explicit
