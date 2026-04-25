# SPDX-License-Identifier: Apache-2.0
"""Phase 2b: AWS Bedrock for Claude — SigV4 + Bedrock envelope.

Bedrock takes the Anthropic Messages payload but routes the model via
URL path (not body) and authenticates with AWS SigV4 over the request
bytes. The provider:

  - Strips ``model`` from the body and adds ``anthropic_version`` per
    Bedrock's contract.
  - Builds the URL from the body's ``model`` field — picks
    ``/invoke`` or ``/invoke-with-response-stream`` based on the
    body's ``stream`` flag.
  - Signs with boto3's ``SigV4Auth`` after body transform + URL
    resolution, so the signature reflects the exact bytes being sent.

Tests use placeholder AWS creds via env vars; SigV4 produces a valid
signature shape (we verify the headers it emits without hitting AWS).
"""

from __future__ import annotations

import json

import pytest

boto3 = pytest.importorskip("boto3")

from tokenpak.services.routing_service.credential_injector import (
    BedrockClaudeCredentialProvider,
    invalidate_cache,
    registered,
)


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch):
    """Set placeholder AWS credentials so boto3's session can resolve."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAIOSFODNN7EXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("AWS_SESSION_TOKEN", raising=False)
    invalidate_cache()
    yield
    invalidate_cache()


# ── boto3 gating ─────────────────────────────────────────────────────


class TestBoto3Gating:
    def test_returns_none_when_boto3_missing(self, monkeypatch):
        # Simulate boto3 being unimportable by replacing the cached
        # module entry. The provider catches ImportError and logs.
        import sys
        monkeypatch.setitem(sys.modules, "boto3", None)
        # Force cache reload of the credential_injector module so
        # ``import boto3`` re-runs inside _load.
        invalidate_cache()
        plan = BedrockClaudeCredentialProvider().resolve()
        assert plan is None


# ── URL resolution from body ─────────────────────────────────────────


class TestUrlResolution:
    def _resolver(self):
        return BedrockClaudeCredentialProvider().resolve().target_url_resolver

    def test_non_streaming_picks_invoke_suffix(self):
        body = json.dumps({
            "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
            "messages": [{"role": "user", "content": "hi"}],
        }).encode()
        url = self._resolver()(body, {})
        assert url == (
            "https://bedrock-runtime.us-east-1.amazonaws.com/model/"
            "anthropic.claude-3-5-sonnet-20241022-v2:0/invoke"
        )

    def test_streaming_picks_response_stream_suffix(self):
        body = json.dumps({
            "model": "anthropic.claude-3-haiku-20240307-v1:0",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        }).encode()
        url = self._resolver()(body, {})
        assert url.endswith(
            "/model/anthropic.claude-3-haiku-20240307-v1:0/invoke-with-response-stream"
        )

    def test_inference_profile_arn_preserved(self):
        # Inference profile IDs contain dots/colons; the resolver must
        # not mangle them.
        body = json.dumps({
            "model": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
            "messages": [],
        }).encode()
        url = self._resolver()(body, {})
        assert "us.anthropic.claude-3-5-sonnet-20241022-v2:0" in url

    def test_region_env_override(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "eu-west-2")
        invalidate_cache()
        body = json.dumps({"model": "anthropic.claude-x", "messages": []}).encode()
        url = BedrockClaudeCredentialProvider().resolve().target_url_resolver(
            body, {}
        )
        assert "bedrock-runtime.eu-west-2.amazonaws.com" in url

    def test_default_region_when_neither_env_var_set(self, monkeypatch):
        monkeypatch.delenv("AWS_REGION", raising=False)
        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        invalidate_cache()
        body = json.dumps({"model": "anthropic.claude-x", "messages": []}).encode()
        url = BedrockClaudeCredentialProvider().resolve().target_url_resolver(
            body, {}
        )
        assert "bedrock-runtime.us-east-1.amazonaws.com" in url

    def test_missing_model_returns_none(self):
        body = json.dumps({"messages": []}).encode()
        assert self._resolver()(body, {}) is None

    def test_empty_body_returns_none(self):
        assert self._resolver()(b"", {}) is None

    def test_malformed_body_returns_none(self):
        assert self._resolver()(b"not json", {}) is None


# ── Body transform: strip model + add anthropic_version ──────────────


class TestBodyTransform:
    def _xform(self):
        return BedrockClaudeCredentialProvider().resolve().body_transform

    def test_strips_model_field(self):
        body = json.dumps({
            "model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 100,
        }).encode()
        out = self._xform()(body)
        decoded = json.loads(out)
        assert "model" not in decoded
        assert decoded["messages"] == [{"role": "user", "content": "hi"}]
        assert decoded["max_tokens"] == 100

    def test_adds_anthropic_version(self):
        body = json.dumps({"model": "x", "messages": []}).encode()
        out = self._xform()(body)
        decoded = json.loads(out)
        assert decoded["anthropic_version"] == "bedrock-2023-05-31"

    def test_preserves_callers_anthropic_version(self):
        # If the caller already set their own version, don't clobber.
        body = json.dumps({
            "model": "x",
            "messages": [],
            "anthropic_version": "bedrock-2023-05-31-custom",
        }).encode()
        out = self._xform()(body)
        decoded = json.loads(out)
        assert decoded["anthropic_version"] == "bedrock-2023-05-31-custom"

    def test_empty_body_passes_through(self):
        assert self._xform()(b"") == b""

    def test_malformed_body_passes_through(self):
        assert self._xform()(b"not json") == b"not json"


# ── SigV4 header_resolver ────────────────────────────────────────────


class TestSigV4Headers:
    def _signer(self):
        return BedrockClaudeCredentialProvider().resolve().header_resolver

    def test_emits_canonical_sigv4_headers(self):
        url = (
            "https://bedrock-runtime.us-east-1.amazonaws.com/model/"
            "anthropic.claude-x/invoke"
        )
        body = json.dumps({"messages": [], "anthropic_version": "x"}).encode()
        headers = self._signer()(body, url, "POST", {})
        # SigV4 always emits these four.
        assert "Authorization" in headers
        assert headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=")
        assert "X-Amz-Date" in headers or "x-amz-date" in {
            k.lower() for k in headers
        }
        # Host derived from URL.
        host_key = next(k for k in headers if k.lower() == "host")
        assert headers[host_key] == "bedrock-runtime.us-east-1.amazonaws.com"

    def test_signature_changes_when_body_changes(self):
        signer = self._signer()
        url = "https://bedrock-runtime.us-east-1.amazonaws.com/model/x/invoke"
        body_a = json.dumps({"a": 1}).encode()
        body_b = json.dumps({"a": 2}).encode()
        # SigV4 includes a date timestamp, so two consecutive calls
        # may produce the same Authorization for the same body if
        # they land in the same second. We just check the two bodies
        # produce DIFFERENT Authorization headers — the signature
        # must reflect body bytes (it includes the SHA256 of body
        # in the canonical request).
        headers_a = signer(body_a, url, "POST", {})
        headers_b = signer(body_b, url, "POST", {})
        assert headers_a["Authorization"] != headers_b["Authorization"]


# ── End-to-end plan composition ──────────────────────────────────────


class TestPlanComposition:
    def test_plan_has_all_dynamic_fields(self):
        plan = BedrockClaudeCredentialProvider().resolve()
        assert plan is not None
        assert plan.target_url_resolver is not None
        assert plan.body_transform is not None
        assert plan.header_resolver is not None
        # Static fields:
        assert "authorization" in plan.strip_headers
        assert "x-api-key" in plan.strip_headers
        assert plan.add_headers["Content-Type"] == "application/json"
        # Dynamic SigV4 means no static Authorization to inject.
        assert "Authorization" not in plan.add_headers


class TestRegistration:
    def test_registered_with_known_slug(self):
        names = {p.name for p in registered()}
        assert "tokenpak-bedrock-claude" in names

    def test_does_not_displace_other_providers(self):
        names = {p.name for p in registered()}
        for slug in (
            "tokenpak-claude-code",
            "tokenpak-mistral",
            "tokenpak-azure-openai",
        ):
            assert slug in names
