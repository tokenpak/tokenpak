# SPDX-License-Identifier: Apache-2.0
"""Authoritative custom-provider registration and routing regressions."""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpak.cli.cli_validate_config import ConfigValidator
from tokenpak.proxy.adapters import build_default_registry
from tokenpak.proxy.custom_providers import (
    CustomProvider,
    build_custom_adapters,
    get_provider_display_list,
    load_custom_providers,
)
from tokenpak.proxy.router import INTERCEPT_HOSTS, ProviderRouter, should_intercept
from tokenpak.proxy.server import (
    _format_proxy_startup_banner,
    _inject_custom_provider_credential,
)


def _deepseek() -> CustomProvider:
    return CustomProvider(
        name="deepseek",
        endpoint="https://api.deepseek.com/v1",
        format="openai-chat",
        api_key_env="DEEPSEEK_API_KEY",
        hostname="api.deepseek.com",
    )


def test_loader_calls_canonical_config_module() -> None:
    config = {
        "providers": {
            "deepseek": {
                "endpoint": "https://api.deepseek.com/v1",
                "format": "openai",
                "api_key_env": "DEEPSEEK_API_KEY",
            }
        }
    }
    with patch("tokenpak.core.config_loader.load_config", return_value=config) as load_config:
        providers = load_custom_providers()

    load_config.assert_called_once_with()
    assert [provider.name for provider in providers] == ["deepseek"]


def test_loader_rejects_unsafe_urls_and_true_endpoint_duplicates() -> None:
    config = {
        "providers": {
            "primary": {
                "endpoint": "https://api.example.test/v1",
                "format": "openai",
            },
            "duplicate": {
                "endpoint": "https://API.EXAMPLE.TEST:443/alternate",
                "format": "anthropic",
            },
            "other-port": {
                "endpoint": "https://api.example.test:8443/v1",
                "format": "openai",
            },
            "wrong-scheme": {
                "endpoint": "ftp://files.example.test/v1",
                "format": "openai",
            },
            "bad-port": {
                "endpoint": "https://port.example.test:not-a-port/v1",
                "format": "openai",
            },
            "userinfo": {
                "endpoint": "https://user:secret@userinfo.example.test/v1",
                "format": "openai",
            },
            "fragment": {
                "endpoint": "https://fragment.example.test/v1#credentials",
                "format": "openai",
            },
            "query-secret": {
                "endpoint": "https://query.example.test/v1?api_key=secret",
                "format": "openai",
            },
        }
    }

    with patch("tokenpak.core.config_loader.load_config", return_value=config):
        providers = load_custom_providers()

    assert [provider.name for provider in providers] == ["primary", "other-port"]


def test_loader_rejects_invalid_api_key_env_name() -> None:
    config = {
        "providers": {
            "unsafe": {
                "endpoint": "https://api.example.test/v1",
                "format": "openai",
                "api_key_env": "$(print-secret)",
            }
        }
    }

    with patch("tokenpak.core.config_loader.load_config", return_value=config):
        assert load_custom_providers() == []


def test_custom_hostname_wins_before_generic_wire_format() -> None:
    registry = build_default_registry()
    adapters = build_custom_adapters([_deepseek()], registry)

    detected = registry.detect(
        "https://api.deepseek.com/v1/chat/completions",
        {"Host": "api.deepseek.com:443"},
        b'{"model":"deepseek-chat","messages":[]}',
    )

    assert detected is adapters[0]
    assert detected.source_format == "custom-deepseek"


def test_custom_hostname_matching_is_exact_and_port_safe() -> None:
    registry = build_default_registry()
    adapter = build_custom_adapters([_deepseek()], registry)[0]

    assert adapter.detect("/v1/chat/completions", {"Host": "API.DEEPSEEK.COM:443"}, None)
    assert not adapter.detect(
        "https://notapi.deepseek.com/v1/chat/completions", {"Host": "notapi.deepseek.com"}, None
    )
    assert not adapter.detect(
        "https://api.deepseek.com.evil.test/v1/chat/completions",
        {"Host": "api.deepseek.com.evil.test"},
        None,
    )


def test_same_hostname_distinct_ports_route_independently() -> None:
    first = CustomProvider(
        name="first",
        endpoint="https://api.example.test:8443/v1",
        format="openai-chat",
        api_key_env="FIRST_KEY",
        hostname="api.example.test",
    )
    second = CustomProvider(
        name="second",
        endpoint="https://api.example.test:9443/v1",
        format="openai-chat",
        api_key_env="SECOND_KEY",
        hostname="api.example.test",
    )
    registry = build_default_registry()
    adapters = build_custom_adapters([first, second], registry)
    router = ProviderRouter(
        custom_urls={adapter.source_format: adapter.get_default_upstream() for adapter in adapters},
        custom_hosts={provider.endpoint: f"custom-{provider.name}" for provider in (first, second)},
    )

    assert not adapters[0].detect("/v1/chat/completions", {"Host": "api.example.test"}, None)
    assert adapters[0].detect("/v1/chat/completions", {"Host": "api.example.test:8443"}, None)
    assert (
        router.route("/v1/chat/completions", {"Host": "api.example.test:8443"}).provider
        == "custom-first"
    )
    assert (
        router.route("/v1/chat/completions", {"Host": "api.example.test:9443"}).provider
        == "custom-second"
    )


def test_host_header_cannot_guess_between_schemes_on_the_same_authority() -> None:
    http_provider = CustomProvider(
        name="http",
        endpoint="http://api.example.test:443/v1",
        format="openai-chat",
        api_key_env="HTTP_KEY",
        hostname="api.example.test",
    )
    https_provider = CustomProvider(
        name="https",
        endpoint="https://api.example.test/v1",
        format="openai-chat",
        api_key_env="HTTPS_KEY",
        hostname="api.example.test",
    )
    registry = build_default_registry()
    adapters = build_custom_adapters([http_provider, https_provider], registry)

    assert not any(
        adapter.detect("/v1/chat/completions", {"Host": "api.example.test:443"}, None)
        for adapter in adapters
    )
    assert adapters[0].detect("http://api.example.test:443/v1/chat/completions", {}, None)
    assert adapters[1].detect("https://api.example.test/v1/chat/completions", {}, None)


def test_registered_adapter_upstream_is_the_router_selection() -> None:
    registry = build_default_registry()
    adapter = build_custom_adapters([_deepseek()], registry)[0]
    router = ProviderRouter(
        custom_urls={adapter.source_format: adapter.get_default_upstream()},
        custom_hosts={"api.deepseek.com": adapter.source_format},
    )

    route = router.route(
        "/v1/chat/completions",
        {"Host": "api.deepseek.com:443"},
        b'{"model":"deepseek-chat","messages":[]}',
    )

    assert route.provider == "custom-deepseek"
    assert route.base_url == adapter.get_default_upstream()
    assert route.full_url == "https://api.deepseek.com/v1/chat/completions"


def test_base_query_is_preserved_and_wins_conflicts() -> None:
    router = ProviderRouter(
        custom_urls={
            "custom-gateway": "https://gateway.example.test/root/v1?api-version=2026-08-01"
        },
        custom_hosts={
            "https://gateway.example.test/root/v1?api-version=2026-08-01": "custom-gateway"
        },
    )

    route = router.route(
        "/v1/chat/completions?api-version=client&stream=true",
        {"Host": "gateway.example.test:443"},
    )

    assert route.full_url == (
        "https://gateway.example.test/root/v1/chat/completions?api-version=2026-08-01&stream=true"
    )


def test_request_fragment_is_rejected() -> None:
    router = ProviderRouter(
        custom_urls={"custom-gateway": "https://gateway.example.test/v1"},
        custom_hosts={"https://gateway.example.test/v1": "custom-gateway"},
    )

    with pytest.raises(ValueError, match="fragments"):
        router.route(
            "/v1/chat/completions#not-forwardable",
            {"Host": "gateway.example.test:443"},
        )


def test_advertised_custom_provider_shape_validates_without_models(monkeypatch) -> None:
    monkeypatch.setenv("MY_LLM_API_KEY", "configured-secret")
    validator = ConfigValidator()
    exit_code, errors, warnings = validator.validate_dict(
        {
            "server": {"port": _free_port()},
            "providers": {
                "my-local-llm": {
                    "endpoint": "http://localhost:8000/v1",
                    "format": "openai",
                    "api_key_env": "MY_LLM_API_KEY",
                }
            },
        }
    )

    assert exit_code == 0
    assert errors == []
    assert warnings == []


def test_config_validator_rejects_unsafe_and_duplicate_endpoints(monkeypatch) -> None:
    monkeypatch.setenv("PROVIDER_KEY", "configured-secret")
    validator = ConfigValidator()
    exit_code, errors, _warnings = validator.validate_dict(
        {
            "server": {"port": _free_port()},
            "providers": {
                "first": {
                    "endpoint": "https://api.example.test/v1",
                    "api_key_env": "PROVIDER_KEY",
                },
                "duplicate": {
                    "endpoint": "https://API.EXAMPLE.TEST:443/other",
                    "api_key_env": "PROVIDER_KEY",
                },
                "fragment": {
                    "endpoint": "https://fragment.example.test/v1#secret",
                    "api_key_env": "PROVIDER_KEY",
                },
            },
        }
    )

    assert exit_code == 1
    messages = [error.message for error in errors]
    assert any("duplicates endpoint identity" in message for message in messages)
    assert any("without userinfo, fragments" in message for message in messages)


def test_custom_credential_uses_wire_format_and_never_overwrites_client(monkeypatch) -> None:
    cases = [
        ("openai-chat", "Authorization", "Bearer configured-secret"),
        ("anthropic-messages", "x-api-key", "configured-secret"),
        ("google-generative-ai", "x-goog-api-key", "configured-secret"),
    ]
    monkeypatch.setenv("CUSTOM_PROVIDER_KEY", "configured-secret")
    for wire_format, expected_header, expected_value in cases:
        provider = CustomProvider(
            name="configured",
            endpoint="https://api.example.test/v1",
            format=wire_format,
            api_key_env="CUSTOM_PROVIDER_KEY",
            hostname="api.example.test",
        )
        headers: dict[str, str] = {}
        assert _inject_custom_provider_credential(
            headers, "https://api.example.test/v1/chat/completions", provider
        )
        assert headers == {expected_header: expected_value}

        client_headers = {"Authorization": "Bearer client-secret"}
        assert not _inject_custom_provider_credential(
            client_headers, "https://api.example.test/v1/chat/completions", provider
        )
        assert client_headers == {"Authorization": "Bearer client-secret"}
        assert "configured-secret" not in repr(provider)


def test_intercept_matching_is_exact() -> None:
    INTERCEPT_HOSTS.add("evil.com")
    try:
        assert should_intercept("https://evil.com/v1/chat/completions")
        assert not should_intercept("https://notevil.com/v1/chat/completions")
        assert not should_intercept("https://evil.com.attacker.test/v1/chat/completions")
    finally:
        INTERCEPT_HOSTS.discard("evil.com")


def test_intercept_matching_accepts_an_explicit_host_collection() -> None:
    hosts = {"127.0.0.1"}

    assert should_intercept("http://127.0.0.1:18971/v1/messages", hosts)
    assert not should_intercept("http://127.0.0.2:18971/v1/messages", hosts)


def test_display_lists_only_successfully_registered_custom_providers() -> None:
    configured = [
        _deepseek(),
        CustomProvider(
            name="unsupported",
            endpoint="https://unsupported.example/v1",
            format="missing-format",
            api_key_env="",
            hostname="unsupported.example",
        ),
    ]
    registry = build_default_registry()
    adapters = build_custom_adapters(configured, registry)
    registered_formats = {adapter.source_format for adapter in adapters}
    registered = [
        provider for provider in configured if f"custom-{provider.name}" in registered_formats
    ]

    display = get_provider_display_list(registry, registered)

    assert "deepseek (custom)" in display
    assert "unsupported (custom)" not in display


def test_proxy_config_exposes_configured_and_registered_counts(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
providers:
  deepseek:
    endpoint: https://api.deepseek.com/v1
    format: openai
    api_key_env: DEEPSEEK_API_KEY
  unsupported:
    endpoint: https://unsupported.example/v1
    format: missing-format
""".lstrip(),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(
        {
            "TOKENPAK_CONFIG": str(config_path),
            "TOKENPAK_HOME": str(tmp_path / "home"),
            "HOME": str(tmp_path),
        }
    )
    code = """
import json
from tokenpak.proxy import config
print(json.dumps({
    "configured": config.CUSTOM_PROVIDER_CONFIGURED_COUNT,
    "registered": config.CUSTOM_PROVIDER_REGISTERED_COUNT,
    "route": config.CUSTOM_PROVIDER_ROUTES.get("custom-deepseek"),
    "host": config.CUSTOM_PROVIDER_HOSTS.get("https://api.deepseek.com/v1"),
    "detected": config.ADAPTER_REGISTRY.detect(
        "https://api.deepseek.com/v1/chat/completions", {}, None
    ).source_format,
    "display": config.PROVIDER_DISPLAY,
}))
"""

    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(completed.stdout)

    assert payload == {
        "configured": 2,
        "registered": 1,
        "route": "https://api.deepseek.com/v1",
        "host": "custom-deepseek",
        "detected": "custom-deepseek",
        "display": payload["display"],
    }
    assert "deepseek (custom)" in payload["display"]


def test_startup_banner_reports_registered_over_configured(tmp_path: Path) -> None:
    banner = _format_proxy_startup_banner(
        host="127.0.0.1",
        port=8766,
        profile="balanced",
        mode="hybrid",
        mode_description="test mode",
        provider_display="openai-chat, deepseek (custom)",
        custom_configured=2,
        custom_registered=1,
        pid=1234,
        pid_path=tmp_path / "proxy.pid",
    )

    assert "Providers:  openai-chat, deepseek (custom)" in banner
    assert "Custom:     1/2 registered" in banner


def test_proxy_server_wires_registered_custom_routes(monkeypatch, tmp_path: Path) -> None:
    from tokenpak.proxy import config
    from tokenpak.proxy import server as server_module

    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    monkeypatch.setattr(
        config,
        "CUSTOM_PROVIDER_ROUTES",
        {"custom-deepseek": "https://api.deepseek.com/v1"},
    )
    monkeypatch.setattr(
        config,
        "CUSTOM_PROVIDER_HOSTS",
        {"https://api.deepseek.com/v1": "custom-deepseek"},
    )
    monkeypatch.setattr(config, "REGISTERED_CUSTOM_PROVIDERS", [_deepseek()])
    monkeypatch.setattr(config, "CUSTOM_PROVIDER_CONFIGURED_COUNT", 2)
    monkeypatch.setattr(config, "CUSTOM_PROVIDER_REGISTERED_COUNT", 1)
    monkeypatch.setattr(server_module, "_create_memory_guard", lambda: None)
    monkeypatch.setattr(server_module, "_memory_guard_configuration_status", lambda: {})
    monkeypatch.setattr(server_module, "_DbMonitor", lambda _path: None)

    proxy = server_module.ProxyServer()
    try:
        route = proxy.router.route(
            "/v1/chat/completions",
            {"Host": "api.deepseek.com:443"},
            b'{"model":"deepseek-chat","messages":[]}',
        )
        assert route.provider == "custom-deepseek"
        assert route.full_url == "https://api.deepseek.com/v1/chat/completions"
        assert proxy.custom_provider_configured_count == 2
        assert proxy.custom_provider_registered_count == 1
    finally:
        proxy._connection_pool.close()


class _RecordingCustomUpstream(BaseHTTPRequestHandler):
    path_seen = ""
    headers_seen: dict[str, str] = {}
    body_seen = b""

    def log_message(self, _format: str, *_args: object) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        type(self).path_seen = self.path
        type(self).headers_seen = dict(self.headers.items())
        length = int(self.headers.get("Content-Length", "0"))
        type(self).body_seen = self.rfile.read(length)
        response = json.dumps(
            {
                "id": "chatcmpl-custom",
                "object": "chat.completion",
                "model": "custom-model",
                "choices": [],
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_real_proxy_exchange_delivers_destination_query_body_and_credential(
    caplog,
    monkeypatch,
    tmp_path: Path,
) -> None:
    from tokenpak.proxy import config
    from tokenpak.proxy import server as server_module

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingCustomUpstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    upstream_port = int(upstream.server_address[1])
    provider = CustomProvider(
        name="local-gateway",
        endpoint=(f"http://127.0.0.1:{upstream_port}/gateway/v1?api-version=2026-08-01"),
        format="openai-chat",
        api_key_env="LOCAL_GATEWAY_KEY",
        hostname="127.0.0.1",
    )
    proxy_port = _free_port()
    proxy = None
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path))
    monkeypatch.setenv("LOCAL_GATEWAY_KEY", "network-boundary-secret")
    monkeypatch.setattr(
        config,
        "CUSTOM_PROVIDER_ROUTES",
        {"custom-local-gateway": provider.endpoint},
    )
    monkeypatch.setattr(
        config,
        "CUSTOM_PROVIDER_HOSTS",
        {provider.endpoint: "custom-local-gateway"},
    )
    monkeypatch.setattr(config, "REGISTERED_CUSTOM_PROVIDERS", [provider])
    monkeypatch.setattr(config, "CUSTOM_PROVIDER_CONFIGURED_COUNT", 1)
    monkeypatch.setattr(config, "CUSTOM_PROVIDER_REGISTERED_COUNT", 1)
    monkeypatch.setattr(server_module, "_create_memory_guard", lambda: None)
    monkeypatch.setattr(server_module, "_memory_guard_configuration_status", lambda: {})
    monkeypatch.setattr(server_module, "_DbMonitor", lambda _path: None)
    _RecordingCustomUpstream.path_seen = ""
    _RecordingCustomUpstream.headers_seen = {}
    _RecordingCustomUpstream.body_seen = b""

    body = b'{"model":"custom-model","messages":[{"role":"user","content":"hello"}]}'
    try:
        proxy = server_module.ProxyServer(host="127.0.0.1", port=proxy_port)
        proxy.monitor = None
        proxy.request_hook = None
        proxy.start(blocking=False)
        time.sleep(0.1)

        connection = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=5)
        connection.request(
            "POST",
            "/v1/chat/completions?api-version=client&stream=true",
            body=body,
            headers={
                "Host": f"127.0.0.1:{upstream_port}",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        response.read()
        connection.close()

        assert response.status == 200
        assert _RecordingCustomUpstream.path_seen == (
            "/gateway/v1/chat/completions?api-version=2026-08-01&stream=true"
        )
        assert _RecordingCustomUpstream.body_seen == body
        assert _RecordingCustomUpstream.headers_seen["Authorization"] == (
            "Bearer network-boundary-secret"
        )
        assert _RecordingCustomUpstream.headers_seen["Host"] == (f"127.0.0.1:{upstream_port}")

        _RecordingCustomUpstream.headers_seen = {}
        connection = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=5)
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=body,
            headers={
                "Host": f"127.0.0.1:{upstream_port}",
                "Authorization": "Bearer client-boundary-secret",
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = connection.getresponse()
        response.read()
        connection.close()

        assert response.status == 200
        assert _RecordingCustomUpstream.headers_seen["Authorization"] == (
            "Bearer client-boundary-secret"
        )
        assert "network-boundary-secret" not in caplog.text
        assert "client-boundary-secret" not in caplog.text
    finally:
        if proxy is not None:
            proxy.stop()
        upstream.shutdown()
        upstream.server_close()
