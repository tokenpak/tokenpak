# SPDX-License-Identifier: Apache-2.0
"""RouteClassifier — platform-bridge integration (2026-04-24).

Pins the classifier's new precedence rule: when the platform bridge
resolves a provider to ``tokenpak-claude-code``, the request is
classified as the Claude Code family regardless of what User-Agent /
auth headers the caller shipped. This closes the OpenClaw 401 loop
where bearer-token traffic was previously classified as ANTHROPIC_SDK
and routed to the api-key backend.
"""

from __future__ import annotations

import pytest

from tokenpak.core.routing.route_class import RouteClass
from tokenpak.services.request import Request
from tokenpak.services.routing_service.classifier import RouteClassifier


@pytest.fixture
def clf() -> RouteClassifier:
    return RouteClassifier()


def _req(headers=None, body=b"") -> Request:
    return Request(body=body, headers=headers or {})


def test_openclaw_session_header_classifies_as_claude_code(clf):
    rc = clf.classify(_req(headers={"X-OpenClaw-Session": "sess-1"}))
    assert rc.is_claude_code


def test_explicit_provider_claude_code_classifies_as_claude_code(clf):
    rc = clf.classify(_req(headers={"X-TokenPak-Provider": "tokenpak-claude-code"}))
    assert rc.is_claude_code


def test_openclaw_with_bearer_token_classifies_as_claude_code(clf):
    """Prior behavior: Authorization: Bearer + ``"messages"`` body → ANTHROPIC_SDK.
    New behavior: X-OpenClaw-Session wins over the body fingerprint.
    """
    rc = clf.classify(
        _req(
            headers={
                "X-OpenClaw-Session": "sess-1",
                "Authorization": "Bearer ocw_oauth_token",
                "Content-Type": "application/json",
            },
            body=b'{"model":"claude-sonnet-4-6","messages":[{"role":"user","content":"hi"}]}',
        )
    )
    assert rc.is_claude_code


def test_anthropic_provider_header_does_not_force_claude_code(clf):
    """Explicit tokenpak-anthropic provider should NOT reclassify as
    Claude Code; the classifier leaves it to the selector to choose
    api vs oauth based on auth shape."""
    rc = clf.classify(
        _req(
            headers={"X-TokenPak-Provider": "tokenpak-anthropic"},
            body=b'{"messages":[{"role":"user","content":"hi"}]}',
        )
    )
    # Body contains `"messages":` → should classify as ANTHROPIC_SDK.
    assert rc == RouteClass.ANTHROPIC_SDK


def test_no_platform_signal_preserves_prior_behavior(clf):
    """Regression: absent any platform markers, the classifier behaves
    exactly as before (body-fingerprint Anthropic SDK)."""
    rc = clf.classify(
        _req(body=b'{"anthropic_version":"bedrock-2023-05-31","messages":[]}')
    )
    assert rc == RouteClass.ANTHROPIC_SDK
