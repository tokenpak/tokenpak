# SPDX-License-Identifier: Apache-2.0
"""Bounded vault retrieval must degrade without changing request bytes."""

from __future__ import annotations

import json
import threading

import pytest

from tokenpak.proxy import vault_bridge
from tokenpak.proxy.adapters.anthropic_adapter import AnthropicAdapter
from tokenpak.proxy.pipeline import process_request, stage_vault_injection
from tokenpak.proxy.request import ROUTE_CLAUDE_CODE, ProxyRequest
from tokenpak.proxy.route_policy import get_policy


def _body() -> bytes:
    return json.dumps(
        {
            "model": "claude-test",
            "system": [{"type": "text", "text": "stable"}],
            "messages": [
                {
                    "role": "user",
                    "content": "Find vault retrieval timeout and byte-preservation notes.",
                }
            ],
            "max_tokens": 32,
        },
        separators=(",", ":"),
    ).encode()


def _request(body: bytes | None = None) -> ProxyRequest:
    return ProxyRequest(
        method="POST",
        url="https://api.anthropic.com/v1/messages",
        headers={"anthropic-version": "2023-06-01"},
        body=body or _body(),
    )


class _BlockingIndex:
    available = True

    def __init__(self) -> None:
        self.release = threading.Event()
        self._condition = threading.Condition()
        self.started = 0

    def compile_injection(self, *_args, **_kwargs):
        with self._condition:
            self.started += 1
            self._condition.notify_all()
        self.release.wait(timeout=2)
        return "context", 1, ["notes.md"]

    def wait_for_starts(self, count: int) -> bool:
        with self._condition:
            return self._condition.wait_for(lambda: self.started >= count, timeout=1)


class _ImmediateIndex:
    available = True

    def compile_injection(self, *_args, **_kwargs):
        return "bounded context", 3, ["notes.md"]


@pytest.fixture
def isolated_pool(monkeypatch):
    pool = vault_bridge._BoundedDaemonExecutor(max_workers=2)
    monkeypatch.setattr(vault_bridge, "_RETRIEVAL_POOL", pool)
    monkeypatch.setattr(vault_bridge, "RETRIEVAL_TIMEOUT_MS", 20)
    monkeypatch.setattr(vault_bridge, "get_term_resolver", lambda: None)
    monkeypatch.setattr(vault_bridge, "SKELETON_ENABLED", False)

    from tokenpak.proxy import config as proxy_config

    monkeypatch.setattr(proxy_config, "VAULT_INJECTION_ENABLED", True)
    return pool


def _use_index(monkeypatch, index) -> None:
    monkeypatch.setattr(vault_bridge, "get_vault_index", lambda: index)


def test_stalled_retrieval_times_out_with_unchanged_body_and_receipt(monkeypatch, isolated_pool):
    index = _BlockingIndex()
    _use_index(monkeypatch, index)
    original = _body()

    request, stage = stage_vault_injection(
        _request(original),
        {"vault_injection": "json_inject"},
        adapter=AnthropicAdapter(),
    )

    assert index.wait_for_starts(1)
    assert request.body == original
    assert stage.skipped
    assert stage.skip_reason == "retrieval_timeout"
    assert stage.details["timeout_ms"] == 20

    index.release.set()
    isolated_pool._queue.join()


def test_saturated_workers_skip_with_distinct_backlog_reason(monkeypatch, isolated_pool):
    index = _BlockingIndex()
    _use_index(monkeypatch, index)
    original = _body()

    first_request, first = stage_vault_injection(
        _request(original),
        {"vault_injection": "json_inject"},
        adapter=AnthropicAdapter(),
    )
    second_request, second = stage_vault_injection(
        _request(original),
        {"vault_injection": "json_inject"},
        adapter=AnthropicAdapter(),
    )
    assert index.wait_for_starts(2)

    third_request, third = stage_vault_injection(
        _request(original),
        {"vault_injection": "json_inject"},
        adapter=AnthropicAdapter(),
    )

    assert first.skip_reason == second.skip_reason == "retrieval_timeout"
    assert third.skip_reason == "retrieval_backlog"
    assert first.skipped and second.skipped and third.skipped
    assert first_request.body == second_request.body == third_request.body == original

    index.release.set()
    isolated_pool._queue.join()


def test_normal_retrieval_matches_the_unbounded_result(monkeypatch, isolated_pool):
    _use_index(monkeypatch, _ImmediateIndex())
    original = _body()
    adapter = AnthropicAdapter()

    expected = vault_bridge._retrieve_vault_context_with_text(original, adapter=adapter)
    actual = vault_bridge._inject_vault_context_with_text(original, adapter=adapter)

    assert actual == expected
    assert actual[0] != original


def test_public_wrapper_returns_original_body_on_timeout(monkeypatch, isolated_pool):
    index = _BlockingIndex()
    _use_index(monkeypatch, index)
    original = _body()

    body, tokens, sources = vault_bridge.inject_vault_context(
        original,
        adapter=AnthropicAdapter(),
    )

    assert body == original
    assert tokens == 0
    assert sources == []

    index.release.set()
    isolated_pool._queue.join()


def test_byte_preserved_route_forwards_exact_original_on_timeout(monkeypatch, isolated_pool):
    index = _BlockingIndex()
    _use_index(monkeypatch, index)
    original = _body()

    result = process_request(
        _request(original),
        get_policy(ROUTE_CLAUDE_CODE),
        route=ROUTE_CLAUDE_CODE,
        client_has_auth=True,
        adapter=AnthropicAdapter(),
    )

    stage = next(item for item in result.stages if item.name == "vault_injection")
    assert result.request.body == original
    assert stage.skipped
    assert stage.skip_reason == "retrieval_timeout"

    index.release.set()
    isolated_pool._queue.join()


def test_retrieval_workers_are_daemon_threads():
    pool = vault_bridge._BoundedDaemonExecutor(max_workers=1)
    release = threading.Event()
    started = threading.Event()

    def block():
        started.set()
        release.wait(timeout=1)
        return _body(), 0, [], ""

    future = pool.submit(block)
    assert started.wait(timeout=1)
    worker = next(
        thread for thread in threading.enumerate() if thread.name == "tokenpak-vault-retrieval-1"
    )
    assert worker.daemon

    release.set()
    assert future.result(timeout=1)[0] == _body()
