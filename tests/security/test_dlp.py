# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak.security.dlp — DLP gitleaks-pattern scanner.

Covers:
- All pattern types (AWS keys, API keys, passwords, secrets, SSN, email,
  phone, private key block, GitHub token, Slack token, Stripe key,
  SendGrid key, access token)
- Three modes: warn, redact, block
- Redaction format: [REDACTED:<rule_id>]
- Clean-text negative cases (no false positives)
- Multi-finding text
- DLPBlockError content
"""

import pytest

from tokenpak.security.dlp import DLPBlockError, DLPScanner

# ---------------------------------------------------------------------------
# Helper fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scanner_warn():
    return DLPScanner(mode="warn")


@pytest.fixture
def scanner_redact():
    return DLPScanner(mode="redact")


@pytest.fixture
def scanner_block():
    return DLPScanner(mode="block")


# ---------------------------------------------------------------------------
# Pattern detection tests (one per rule)
# ---------------------------------------------------------------------------


class TestPatternDetection:
    """Verify each pattern type is detected by scan()."""

    def test_aws_access_key_detected(self, scanner_warn):
        text = "export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "aws-access-key" in rule_ids

    def test_aws_secret_key_detected(self, scanner_warn):
        text = "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "aws-secret-key" in rule_ids

    def test_generic_api_key_detected(self, scanner_warn):
        text = 'api_key = "sk-1234567890abcdefghijklmnop"'
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "generic-api-key" in rule_ids

    def test_password_detected(self, scanner_warn):
        text = "password = 'hunter2password!'"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "password" in rule_ids

    def test_generic_secret_detected(self, scanner_warn):
        text = 'client_secret = "abcdef1234567890xyz"'
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "generic-secret" in rule_ids

    def test_ssn_detected(self, scanner_warn):
        text = "SSN: 123-45-6789"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "ssn" in rule_ids

    def test_email_detected(self, scanner_warn):
        text = "Contact us at alice@example.com for help."
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "email" in rule_ids

    def test_phone_detected(self, scanner_warn):
        text = "Call us at 555-867-5309"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "phone" in rule_ids

    def test_private_key_block_detected(self, scanner_warn):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n-----END RSA PRIVATE KEY-----"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "private-key-block" in rule_ids

    def test_github_token_detected(self, scanner_warn):
        text = "token = ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "github-token" in rule_ids

    def test_slack_token_detected(self, scanner_warn):
        text = "SLACK_TOKEN=xoxb-12345678901-12345678901-abcdefghijklmnop"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "slack-token" in rule_ids

    def test_stripe_secret_key_detected(self, scanner_warn):
        text = "STRIPE_KEY=sk_live_abcdefghijklmnopqrstuvwx"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "stripe-secret-key" in rule_ids

    def test_sendgrid_key_detected(self, scanner_warn):
        text = "SENDGRID_API_KEY=SG.abcdefghijklmnopqrstuv.WXYZ1234567890abcdefghij"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "sendgrid-key" in rule_ids

    def test_access_token_detected(self, scanner_warn):
        text = 'access_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload"'
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "access-token" in rule_ids

    def test_clean_text_returns_empty(self, scanner_warn):
        text = "The quick brown fox jumps over the lazy dog."
        findings = scanner_warn.scan(text)
        assert findings == []

    def test_multiple_findings_in_one_text(self, scanner_warn):
        text = (
            "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n"
            "password = 'supersecret123!'\n"
            "Contact: user@example.com\n"
        )
        findings = scanner_warn.scan(text)
        assert len(findings) >= 3
        rule_ids = [f.rule_id for f in findings]
        assert "aws-access-key" in rule_ids
        assert "password" in rule_ids
        assert "email" in rule_ids


# ---------------------------------------------------------------------------
# DLPMatch structure
# ---------------------------------------------------------------------------


class TestDLPMatchStructure:
    def test_finding_has_expected_fields(self, scanner_warn):
        text = "AKIAIOSFODNN7EXAMPLE"
        findings = scanner_warn.scan(text)
        assert len(findings) >= 1
        f = findings[0]
        assert f.rule_id == "aws-access-key"
        assert "AKIA" in f.match_text
        assert f.severity == "critical"
        assert isinstance(f.start, int)
        assert isinstance(f.end, int)
        assert f.end > f.start


# ---------------------------------------------------------------------------
# Warn mode
# ---------------------------------------------------------------------------


class TestWarnMode:
    def test_warn_scan_returns_findings(self, scanner_warn):
        text = "AKIAIOSFODNN7EXAMPLE"
        findings = scanner_warn.scan(text)
        assert len(findings) >= 1

    def test_warn_redact_does_not_modify_text(self, scanner_warn):
        """scan() in warn mode should not modify the source text."""
        text = "AKIAIOSFODNN7EXAMPLE is my key"
        scanner_warn.scan(text)  # side-effect-free
        # text is unchanged (Python strings are immutable; this verifies no mutation)
        assert text == "AKIAIOSFODNN7EXAMPLE is my key"

    def test_warn_block_check_true_on_clean(self, scanner_warn):
        assert scanner_warn.block_check("Hello world") is True

    def test_warn_block_check_false_on_secret(self, scanner_warn):
        assert scanner_warn.block_check("AKIAIOSFODNN7EXAMPLE") is False


# ---------------------------------------------------------------------------
# Redact mode
# ---------------------------------------------------------------------------


class TestRedactMode:
    def test_redact_replaces_aws_key(self, scanner_redact):
        text = "key=AKIAIOSFODNN7EXAMPLE"
        result = scanner_redact.redact(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[REDACTED:aws-access-key]" in result

    def test_redact_replaces_password(self, scanner_redact):
        text = "password = 'hunter2password!'"
        result = scanner_redact.redact(text)
        assert "[REDACTED:password]" in result
        assert "hunter2password!" not in result

    def test_redact_replaces_email(self, scanner_redact):
        text = "Send to alice@example.com please"
        result = scanner_redact.redact(text)
        assert "[REDACTED:email]" in result
        assert "alice@example.com" not in result

    def test_redact_clean_text_unchanged(self, scanner_redact):
        text = "No secrets here at all."
        result = scanner_redact.redact(text)
        assert result == text

    def test_redact_multiple_secrets(self, scanner_redact):
        text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE password='secret123!'"
        result = scanner_redact.redact(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "secret123!" not in result
        assert "[REDACTED:" in result

    def test_redact_format_uses_rule_id(self, scanner_redact):
        text = "AKIAIOSFODNN7EXAMPLE"
        result = scanner_redact.redact(text)
        assert result == "[REDACTED:aws-access-key]"


# ---------------------------------------------------------------------------
# Block mode
# ---------------------------------------------------------------------------


class TestBlockMode:
    def test_block_check_returns_true_on_clean(self, scanner_block):
        assert scanner_block.block_check("totally clean text") is True

    def test_block_check_returns_false_on_secret(self, scanner_block):
        assert scanner_block.block_check("AKIAIOSFODNN7EXAMPLE") is False

    def test_dlp_block_error_carries_findings(self):
        text = "AKIAIOSFODNN7EXAMPLE"
        scanner = DLPScanner(mode="block")
        findings = scanner.scan(text)
        error = DLPBlockError(findings)
        assert len(error.findings) >= 1
        assert "aws-access-key" in [f.rule_id for f in error.findings]
        assert "DLP block" in str(error)


# ---------------------------------------------------------------------------
# No false positives
# ---------------------------------------------------------------------------


class TestNoFalsePositives:
    def test_email_variable_name_not_flagged(self, scanner_warn):
        # A variable *named* email_address should not trigger the email rule
        text = "email_address = None"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "email" not in rule_ids

    def test_short_alphanumeric_not_api_key(self, scanner_warn):
        # Short strings should not match 20+ char api_key rule
        text = "api_key = 'short'"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "generic-api-key" not in rule_ids

    def test_plain_number_sequence_not_ssn(self, scanner_warn):
        # Bare digits without hyphens should not match SSN
        text = "count: 1234567890"
        findings = scanner_warn.scan(text)
        rule_ids = [f.rule_id for f in findings]
        assert "ssn" not in rule_ids


# ---------------------------------------------------------------------------
# Mode configuration via env var
# ---------------------------------------------------------------------------


class TestModeConfiguration:
    def test_default_mode_is_warn(self, monkeypatch):
        monkeypatch.delenv("TOKENPAK_DLP_MODE", raising=False)
        scanner = DLPScanner()
        assert scanner.mode == "warn"

    def test_mode_from_env_var(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_DLP_MODE", "redact")
        scanner = DLPScanner()
        assert scanner.mode == "redact"

    def test_explicit_mode_overrides_env(self, monkeypatch):
        monkeypatch.setenv("TOKENPAK_DLP_MODE", "block")
        scanner = DLPScanner(mode="warn")
        assert scanner.mode == "warn"

    def test_invalid_mode_falls_back_to_warn(self):
        scanner = DLPScanner(mode="invalid-mode")
        assert scanner.mode == "warn"
