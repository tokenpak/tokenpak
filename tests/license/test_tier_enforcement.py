"""
tests/license/test_tier_enforcement.py — License tier enforcement unit tests.

Covers:
  - OSS tier: Pro features blocked with TierRequiredError
  - Pro tier: Pro features allowed
  - Expired license: falls back to OSS
  - Missing license file: falls back to OSS
  - Gate decorator behaviour: message, feature name, required/current tier
  - load_license() integration: fixture loaded as enterprise
"""
from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.license", reason="module not available in current build")
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tokenpak.license.tier import LicenseTier, TIER_FEATURES
from tokenpak.license.gates import TierRequiredError, requires_tier
from tokenpak.license.loader import (
    get_active_tier,
    load_license,
    reset_for_testing,
    get_active_features,
)

# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
_FIXTURE_LICENSE = _FIXTURES_DIR / "test_license.json"
_FIXTURE_PUBKEY = _FIXTURES_DIR / "test_license_pub.pem"


@pytest.fixture(autouse=True)
def reset_loader():
    """Reset the process-global license state to OSS for each test, then restore."""
    saved = get_active_tier()
    reset_for_testing(LicenseTier.OSS)
    yield
    reset_for_testing(saved)


# ─────────────────────────────────────────────
# LicenseTier ordering
# ─────────────────────────────────────────────


def test_tier_ordering_oss_lt_pro():
    assert LicenseTier.OSS < LicenseTier.PRO


def test_tier_ordering_pro_lt_team():
    assert LicenseTier.PRO < LicenseTier.TEAM


def test_tier_ordering_team_lt_enterprise():
    assert LicenseTier.TEAM < LicenseTier.ENTERPRISE


def test_tier_from_str_enterprise():
    assert LicenseTier.from_str("enterprise") == LicenseTier.ENTERPRISE


def test_tier_from_str_unknown_returns_oss():
    assert LicenseTier.from_str("unknown_tier") == LicenseTier.OSS


# ─────────────────────────────────────────────
# requires_tier decorator — blocked
# ─────────────────────────────────────────────


def test_oss_blocked_from_pro_feature():
    """OSS tier must not access Pro features."""
    reset_for_testing(LicenseTier.OSS)

    @requires_tier(LicenseTier.PRO, message="Test CTA")
    def pro_feature():
        return "ok"

    with pytest.raises(TierRequiredError) as exc_info:
        pro_feature()

    err = exc_info.value
    assert err.required == LicenseTier.PRO
    assert err.current == LicenseTier.OSS
    assert "Test CTA" in err.cta


def test_tier_error_message_in_exception_str():
    """TierRequiredError str representation includes the CTA."""
    reset_for_testing(LicenseTier.OSS)

    @requires_tier(LicenseTier.PRO, message="Upgrade now!")
    def gated():
        pass

    with pytest.raises(TierRequiredError) as exc_info:
        gated()

    assert "Upgrade now!" in str(exc_info.value)


# ─────────────────────────────────────────────
# requires_tier decorator — allowed
# ─────────────────────────────────────────────


def test_pro_tier_allows_pro_feature():
    """Pro tier should pass through Pro-gated functions."""
    reset_for_testing(LicenseTier.PRO)

    @requires_tier(LicenseTier.PRO)
    def pro_feature():
        return "success"

    assert pro_feature() == "success"


def test_enterprise_tier_allows_pro_feature():
    """Enterprise tier inherits Pro access."""
    reset_for_testing(LicenseTier.ENTERPRISE)

    @requires_tier(LicenseTier.PRO)
    def pro_feature():
        return "enterprise_ok"

    assert pro_feature() == "enterprise_ok"


def test_team_tier_allows_pro_feature():
    """Team tier inherits Pro access."""
    reset_for_testing(LicenseTier.TEAM)

    @requires_tier(LicenseTier.PRO)
    def pro_feature():
        return "team_ok"

    assert pro_feature() == "team_ok"


# ─────────────────────────────────────────────
# Loader: missing license file → OSS
# ─────────────────────────────────────────────


def test_missing_license_file_falls_back_to_oss(tmp_path, monkeypatch):
    """When the license file does not exist, loader defaults to OSS."""
    monkeypatch.delenv("TOKENPAK_TEST_LICENSE", raising=False)
    monkeypatch.setenv("TOKENPAK_LICENSE_DIR", str(tmp_path))  # empty dir

    tier = load_license()
    assert tier == LicenseTier.OSS
    assert get_active_tier() == LicenseTier.OSS


# ─────────────────────────────────────────────
# Loader: corrupt license file → OSS
# ─────────────────────────────────────────────


def test_corrupt_license_file_falls_back_to_oss(tmp_path, monkeypatch):
    """Corrupt JSON in the license file must not crash the proxy — OSS fallback."""
    corrupt = tmp_path / "license.json"
    corrupt.write_text("NOT VALID JSON!@#")

    monkeypatch.delenv("TOKENPAK_TEST_LICENSE", raising=False)
    monkeypatch.setenv("TOKENPAK_LICENSE_DIR", str(tmp_path))

    tier = load_license()
    assert tier == LicenseTier.OSS


# ─────────────────────────────────────────────
# Loader: expired license → OSS
# ─────────────────────────────────────────────


def test_expired_license_falls_back_to_oss(tmp_path, monkeypatch):
    """A license that is past expiry + grace period must fall back to OSS."""
    from tokenpak._internal.license.keys import (
        generate_keypair, sign_license, LicensePayload, format_license_key
    )

    private_pem, public_pem = generate_keypair()

    payload = LicensePayload(
        key_id=format_license_key(),
        tier="pro",
        seats=0,
        issued_at="2020-01-01T00:00:00+00:00",
        expires_at="2020-06-01T00:00:00+00:00",  # expired long ago + past grace
        features=[],
        customer_id=None,
    )
    token = sign_license(payload, private_pem)

    license_file = tmp_path / "license.json"
    license_file.write_text(json.dumps({"token": token}))

    monkeypatch.delenv("TOKENPAK_TEST_LICENSE", raising=False)
    monkeypatch.setenv("TOKENPAK_LICENSE_DIR", str(tmp_path))
    monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", public_pem.decode())

    tier = load_license()
    assert tier == LicenseTier.OSS


# ─────────────────────────────────────────────
# Loader: test fixture → enterprise
# ─────────────────────────────────────────────


def test_fixture_loads_as_enterprise(monkeypatch):
    """The synthetic test fixture must validate as enterprise tier."""
    assert _FIXTURE_LICENSE.exists(), f"Missing fixture: {_FIXTURE_LICENSE}"
    assert _FIXTURE_PUBKEY.exists(), f"Missing public key: {_FIXTURE_PUBKEY}"

    monkeypatch.setenv("TOKENPAK_TEST_LICENSE", str(_FIXTURE_LICENSE))
    monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", _FIXTURE_PUBKEY.read_text())

    tier = load_license()
    assert tier == LicenseTier.ENTERPRISE, f"Expected ENTERPRISE, got {tier!r}"


def test_fixture_enterprise_features(monkeypatch):
    """Enterprise fixture must have replay_store, ab_testing, budget_alerts features."""
    monkeypatch.setenv("TOKENPAK_TEST_LICENSE", str(_FIXTURE_LICENSE))
    monkeypatch.setenv("TOKENPAK_PUBLIC_KEY", _FIXTURE_PUBKEY.read_text())

    load_license()
    features = get_active_features()
    assert "replay_store" in features
    assert "ab_testing" in features
    assert "budget_alerts" in features
    assert "compression_advanced" in features


# ─────────────────────────────────────────────
# Gating points: replay
# ─────────────────────────────────────────────


def test_replay_blocked_on_oss():
    """tokenpak replay commands must raise TierRequiredError on OSS."""
    from tokenpak.cli.commands.replay import cmd_replay_list

    reset_for_testing(LicenseTier.OSS)
    import argparse
    args = argparse.Namespace(limit=5, provider=None, json=False)

    with pytest.raises(TierRequiredError) as exc_info:
        cmd_replay_list(args)

    assert "Pro feature" in exc_info.value.cta or "trial" in exc_info.value.cta


def test_replay_cta_contains_portal_url():
    """The CTA message must contain the portal trial URL."""
    from tokenpak.cli.commands.replay import cmd_replay_list

    reset_for_testing(LicenseTier.OSS)
    import argparse
    args = argparse.Namespace(limit=5, provider=None, json=False)

    with pytest.raises(TierRequiredError) as exc_info:
        cmd_replay_list(args)

    assert "portal.tokenpak.io/trial" in exc_info.value.cta


# ─────────────────────────────────────────────
# Gating points: budget alerts
# ─────────────────────────────────────────────


def test_budget_alert_slack_blocked_on_oss():
    """SlackChannel.send() must raise TierRequiredError on OSS."""
    from tokenpak.alerts.channels.slack import SlackChannel

    reset_for_testing(LicenseTier.OSS)
    ch = SlackChannel(webhook="https://hooks.example.com/test")

    with pytest.raises(TierRequiredError):
        ch.send("budget_exceeded", "warning", "You are over budget!")


def test_budget_alert_webhook_blocked_on_oss():
    """WebhookChannel.send() must raise TierRequiredError on OSS."""
    from tokenpak.alerts.channels.webhook import WebhookChannel

    reset_for_testing(LicenseTier.OSS)
    ch = WebhookChannel(url="https://webhook.example.com/alert")

    with pytest.raises(TierRequiredError):
        ch.send("budget_exceeded", "warning", "You are over budget!")


def test_budget_alert_allowed_on_pro():
    """SlackChannel.send() must pass gate on Pro (actual HTTP call mocked)."""
    from tokenpak.alerts.channels.slack import SlackChannel
    from unittest.mock import MagicMock, patch

    reset_for_testing(LicenseTier.PRO)

    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        ch = SlackChannel(webhook="https://hooks.example.com/test")
        result = ch.send("budget_exceeded", "warning", "You are over budget!")
        assert result is True


# ─────────────────────────────────────────────
# Gating points: advanced recipes
# ─────────────────────────────────────────────


def test_advanced_recipes_blocked_on_oss():
    """CompressionRecipeEngine.advanced_recipes_for_file() must raise TierRequiredError on OSS."""
    from tokenpak.compression.recipes import CompressionRecipeEngine

    reset_for_testing(LicenseTier.OSS)
    engine = CompressionRecipeEngine()

    with pytest.raises(TierRequiredError) as exc_info:
        engine.advanced_recipes_for_file("test.py")

    assert "Pro feature" in exc_info.value.cta or "trial" in exc_info.value.cta


def test_advanced_recipes_allowed_on_pro():
    """CompressionRecipeEngine.advanced_recipes_for_file() must return a list on Pro."""
    from tokenpak.compression.recipes import CompressionRecipeEngine

    reset_for_testing(LicenseTier.PRO)
    engine = CompressionRecipeEngine()

    result = engine.advanced_recipes_for_file("test.py")
    assert isinstance(result, list)  # empty list is fine — no pro recipes yet


# ─────────────────────────────────────────────
# TIER_FEATURES catalogue
# ─────────────────────────────────────────────


def test_oss_features_subset_of_pro():
    """Every OSS feature must also be in Pro."""
    oss = set(TIER_FEATURES[LicenseTier.OSS])
    pro = set(TIER_FEATURES[LicenseTier.PRO])
    assert oss.issubset(pro), f"OSS features not in Pro: {oss - pro}"


def test_pro_features_subset_of_enterprise():
    """Every Pro feature must also be in Enterprise."""
    pro = set(TIER_FEATURES[LicenseTier.PRO])
    ent = set(TIER_FEATURES[LicenseTier.ENTERPRISE])
    assert pro.issubset(ent), f"Pro features not in Enterprise: {pro - ent}"
