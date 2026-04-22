"""RouteClassifier — 1.3.0-α acceptance.

Confirms the classifier is the single source of truth for RouteClass
across every input channel: HTTP headers, OAuth marker pair, environment
markers, body fingerprint.
"""

from __future__ import annotations

import pytest

from tokenpak.core.routing.route_class import RouteClass
from tokenpak.services.request import Request
from tokenpak.services.routing_service.classifier import (
    RouteClassifier,
    get_classifier,
)


@pytest.fixture
def clf() -> RouteClassifier:
    return RouteClassifier()


def _req(headers=None, body=b"") -> Request:
    return Request(body=body, headers=headers or {})


def test_claude_code_session_id_header_is_definitive(clf):
    r = _req(headers={"x-claude-code-session-id": "abc123"})
    rc = clf.classify(r)
    assert rc.is_claude_code


def test_user_agent_claude_cli_classifies_claude_code(clf):
    r = _req(headers={"User-Agent": "claude-cli/2.1.117"})
    rc = clf.classify(r)
    assert rc.is_claude_code


def test_anthropic_sdk_classifies_anthropic_sdk(clf):
    r = _req(headers={"User-Agent": "anthropic-python/0.40.0"})
    assert clf.classify(r) is RouteClass.ANTHROPIC_SDK


def test_openai_sdk_classifies_openai_sdk(clf):
    r = _req(headers={"User-Agent": "openai-python/1.45.0"})
    assert clf.classify(r) is RouteClass.OPENAI_SDK


def test_oauth_marker_pair_classifies_claude_code(clf):
    r = _req(headers={
        "authorization": "Bearer sk-ant-oat01-...",
        "anthropic-beta": "oauth-2025-04-20",
    })
    rc = clf.classify(r)
    assert rc.is_claude_code


def test_explicit_entrypoint_header_picks_cli(clf):
    r = _req(headers={
        "x-claude-code-session-id": "xyz",
        "x-claude-code-entrypoint": "cli",
    })
    assert clf.classify(r) is RouteClass.CLAUDE_CODE_CLI


def test_explicit_entrypoint_header_picks_cron(clf):
    r = _req(headers={
        "x-claude-code-session-id": "xyz",
        "x-claude-code-entrypoint": "cron",
    })
    assert clf.classify(r) is RouteClass.CLAUDE_CODE_CRON


def test_streaming_body_biases_to_tui(clf):
    r = _req(
        headers={"x-claude-code-session-id": "xyz"},
        body=b'{"model":"claude-opus-4-7","stream":true}',
    )
    assert clf.classify(r) is RouteClass.CLAUDE_CODE_TUI


def test_non_streaming_body_biases_to_cli(clf):
    r = _req(
        headers={"x-claude-code-session-id": "xyz"},
        body=b'{"model":"claude-opus-4-7","stream":false}',
    )
    assert clf.classify(r) is RouteClass.CLAUDE_CODE_CLI


def test_generic_fallback(clf):
    r = _req(headers={"User-Agent": "some-random-client/0.1"})
    assert clf.classify(r) is RouteClass.GENERIC


def test_body_fingerprint_detects_anthropic_shape(clf):
    r = _req(body=b'{"anthropic_version":"2023-06-01","messages":[]}')
    assert clf.classify(r) is RouteClass.ANTHROPIC_SDK


def test_body_fingerprint_detects_openai_shape(clf):
    r = _req(body=b'{"model":"gpt-4o","messages":[]}')
    assert clf.classify(r) is RouteClass.OPENAI_SDK


def test_classify_from_env_claude_code(monkeypatch, clf):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    rc = clf.classify_from_env()
    assert rc is RouteClass.CLAUDE_CODE_TUI  # default mode


def test_classify_from_env_cron(monkeypatch, clf):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cron")
    assert clf.classify_from_env() is RouteClass.CLAUDE_CODE_CRON


def test_classify_from_env_unknown(monkeypatch, clf):
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert clf.classify_from_env() is RouteClass.GENERIC


def test_module_level_classifier_is_shared(clf):
    # get_classifier() should return a shared, stateless instance.
    a = get_classifier()
    b = get_classifier()
    assert a is b
