# SPDX-License-Identifier: Apache-2.0
"""Contract tests for provider detection used by ``tokenpak test``."""

from __future__ import annotations

from unittest.mock import Mock, patch

from tokenpak.cli.commands import test as test_command


def test_detect_proxy_reads_provider_map_not_envelope_metadata() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {
        "circuit_breakers": {
            "enabled": True,
            "any_open": False,
            "providers": {
                "anthropic": {"state": "closed"},
                "openai": {"state": "open"},
            },
        }
    }

    test_command._proxy_detection_cache = None
    with patch("tokenpak.cli.commands.test.httpx.get", return_value=response):
        running, providers = test_command._detect_proxy()

    assert running is True
    assert providers == ["anthropic", "openai"]


def test_detect_proxy_keeps_missing_provider_observations_empty() -> None:
    response = Mock(status_code=200)
    response.json.return_value = {
        "circuit_breakers": {"enabled": False, "any_open": False, "providers": {}}
    }

    test_command._proxy_detection_cache = None
    with patch("tokenpak.cli.commands.test.httpx.get", return_value=response):
        running, providers = test_command._detect_proxy()

    assert running is True
    assert providers == []
