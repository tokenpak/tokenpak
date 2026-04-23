"""DLPScanner — 1.3.0-β acceptance."""

from __future__ import annotations

import pytest

from tokenpak.security.dlp import DLPScanner
from tokenpak.security.dlp.rules import DEFAULT_RULES


@pytest.fixture
def scanner() -> DLPScanner:
    return DLPScanner()


def test_empty_body_no_findings(scanner):
    assert scanner.scan("") == []
    assert scanner.scan_bytes(b"") == []


def test_detects_aws_access_key(scanner):
    text = 'const key = "AKIAIOSFODNN7EXAMPLE";'
    findings = scanner.scan(text)
    assert any(f.rule_id == "aws_access_key" for f in findings)


def test_detects_stripe_live_key(scanner):
    text = "stripe_key = 'sk_live_abc123defghijk4567890mnop'"
    findings = scanner.scan(text)
    assert any(f.rule_id == "stripe_live_key" for f in findings)


def test_detects_github_pat(scanner):
    text = "GH token: ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    findings = scanner.scan(text)
    assert any(f.rule_id == "github_pat" for f in findings)


def test_detects_anthropic_key(scanner):
    text = "ANTHROPIC_API_KEY=sk-ant-api03-abcdefghijklmnopqrst"
    findings = scanner.scan(text)
    assert any(f.rule_id == "anthropic_api_key" for f in findings)


def test_detects_openai_key(scanner):
    text = "OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrstuvwxyz1234567890ABCD"
    findings = scanner.scan(text)
    assert any(f.rule_id == "openai_api_key" for f in findings)


def test_detects_private_key_pem(scanner):
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEvQI..."
    findings = scanner.scan(text)
    assert any(f.rule_id == "private_key_pem" for f in findings)


def test_finding_redacted_does_not_leak_secret(scanner):
    text = "sk_live_abc123defghijk4567890mnop"
    findings = scanner.scan(text)
    assert findings
    red = findings[0].redacted()
    # Redacted form must NOT include the matched string.
    assert findings[0].matched not in red
    assert "stripe_live_key" in red


def test_clean_text_no_false_positives(scanner):
    text = "Just a normal user prompt about refactoring a file."
    assert scanner.scan(text) == []


def test_scan_bytes_handles_non_utf8(scanner):
    # Should not raise on bad bytes.
    findings = scanner.scan_bytes(b"\xff\xfe\x00hello AKIAIOSFODNN7EXAMPLE")
    assert any(f.rule_id == "aws_access_key" for f in findings)


def test_default_rules_have_unique_ids():
    ids = [r.id for r in DEFAULT_RULES]
    assert len(ids) == len(set(ids)), "DEFAULT_RULES ids must be unique"
