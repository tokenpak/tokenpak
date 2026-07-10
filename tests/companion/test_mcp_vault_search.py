# SPDX-License-Identifier: Apache-2.0
"""Offline MCP vault_search adapter coverage."""

from __future__ import annotations

import json
from typing import Any

from tokenpak.companion.mcp import tools


def test_vault_search_calls_proxy_search_endpoint(monkeypatch) -> None:
    """Red under handler-route mutation: the proxy path and params are exact."""
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def fake_get(path: str, params: dict[str, Any] | None = None):
        calls.append((path, params))
        return 200, {
            "query": "cache policy",
            "count": 1,
            "results": [
                {
                    "block_id": "block-a",
                    "path": "docs/cache-policy.md",
                    "source_path": "docs/cache-policy.md",
                    "tokens": 17,
                    "score": 1.234,
                    "snippet": "cache policy details",
                }
            ],
        }

    monkeypatch.setattr(tools, "_proxy_get", fake_get)

    body = json.loads(tools._handle_vault_search(None, {"query": "cache policy", "limit": 3}))

    assert calls == [("/tpk/v1/vault/search", {"q": "cache policy", "limit": 3})]
    assert body["query"] == "cache policy"
    assert body["count"] == 1
    assert body["results"][0]["block_id"] == "block-a"
    assert body["results"][0]["tokens"] == 17


def test_vault_search_clamps_limit(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, Any] | None]] = []

    def fake_get(path: str, params: dict[str, Any] | None = None):
        calls.append((path, params))
        return 200, {"query": "cache policy", "count": 0, "results": []}

    monkeypatch.setattr(tools, "_proxy_get", fake_get)

    body = json.loads(tools._handle_vault_search(None, {"query": "cache policy", "limit": 999}))

    assert calls == [("/tpk/v1/vault/search", {"q": "cache policy", "limit": 20})]
    assert body["results"] == []


def test_vault_search_requires_query_without_proxy_call(monkeypatch) -> None:
    def fail_get(path: str, params: dict[str, Any] | None = None):
        raise AssertionError("vault_search should not call proxy without query")

    monkeypatch.setattr(tools, "_proxy_get", fail_get)

    body = json.loads(tools._handle_vault_search(None, {"query": "  "}))

    assert body == {"error": "query is required"}


def test_vault_search_proxy_error_passes_through(monkeypatch) -> None:
    def fake_get(path: str, params: dict[str, Any] | None = None):
        return 503, {"error": "vault_unavailable", "detail": "index not loaded"}

    monkeypatch.setattr(tools, "_proxy_get", fake_get)

    body = json.loads(tools._handle_vault_search(None, {"query": "cache policy"}))

    assert body == {"error": "vault_unavailable", "detail": "index not loaded"}
