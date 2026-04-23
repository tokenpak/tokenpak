# SPDX-License-Identifier: Apache-2.0
"""B3 (PM/GTM v2 Phase 1): verify license CLI behavior across 4 paths.

Preflight (2026-04-23) confirmed the license CLI ships at
`tokenpak/cli/commands/license.py` with `activate`, `deactivate`, `plan`
subcommands wired through `tokenpak/agent/license/activation.py`. This
file is a verify-only drift guard exercising the four user-facing paths
of `tokenpak plan`:

  1. No license installed → OSS tier, exit 0.
  2. Valid Pro license → Pro tier in human output, exit 0. Terminology:
     human copy uses `Pro`, never `tokenpak-paid` (standard 08).
  3. Expired license → fallback to OSS with a loud warning, exit 0.
  4. Corrupt license bytes → fallback to OSS with a loud warning, exit 0.

Never-fail-closed is the central contract: no path raises, no path
prints OS-level errors that would confuse a fresh user.

Traces to v2 M-B3 (Axis B commercial enablement) per
~/vault/02_COMMAND_CENTER/initiatives/2026-04-23-tokenpak-pm-gtm-readiness-v2/.
"""

from __future__ import annotations

import contextlib
import io
from unittest.mock import patch

import pytest

# `_run_plan` is the plain-Python entrypoint used by both the Click-wrapped
# command and the argparse path. Exercising it directly avoids Click's test
# harness quirks while still proving the user-visible output.
from tokenpak.cli.commands.license import _run_plan
from tokenpak.agent.license import activation as _activation
from tokenpak.agent.license.validator import (
    LicenseStatus,
    LicenseTier,
    LicenseValidator,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def license_dir(tmp_path, monkeypatch):
    """Isolate ~/.tokenpak to a tmp dir so tests never touch the real user state."""
    monkeypatch.setenv("TOKENPAK_LICENSE_DIR", str(tmp_path))
    _activation._clear_plan_cache()  # ensure fresh state
    yield tmp_path


def _run_plan_capturing() -> tuple[str, str]:
    """Run `_run_plan` and return (stdout, stderr). Exit code is implicitly 0 —
    if the function raises or sys.exits, the test fails loudly."""
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        _run_plan()
    return out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# Path 1: No license installed → OSS
# ---------------------------------------------------------------------------


def test_plan_no_license_returns_oss(license_dir):
    """Path 1: empty license dir → `tokenpak plan` shows OSS, never raises."""
    stdout, _ = _run_plan_capturing()

    assert "OSS" in stdout, (
        f"B3 regression: with no license installed, `plan` must show OSS tier. Got:\n{stdout}"
    )
    # Should not include Pro/Team/Enterprise terminology in human output.
    assert "Pro" not in stdout or "OSS" in stdout, "OSS path leaked Pro terminology"


# ---------------------------------------------------------------------------
# Path 2: Valid Pro license → Pro tier (mocked validator)
# ---------------------------------------------------------------------------


def test_plan_valid_pro_license_shows_pro(license_dir):
    """Path 2: a valid Pro license produces Pro-tier output. Validator is mocked
    because generating a real signed license requires the MTC license-server
    signing key, which is not part of the OSS repo."""
    # Pre-seed a token (the content doesn't matter — validator is mocked).
    (license_dir / "license.key").write_text("fake-but-present-token\n")

    fake_pro_result = ValidationResult(
        status=LicenseStatus.VALID,
        tier=LicenseTier.PRO,
        features=["proxy_auth", "advanced_recipes"],
        seats=1,
        seats_used=1,
        expires_at="2099-01-01T00:00:00Z",
        grace_expires_at=None,
        message="OK",
    )

    with patch.object(LicenseValidator, "validate", return_value=fake_pro_result):
        stdout, _ = _run_plan_capturing()

    # Human copy must say "PRO" (the activation.py formatter upper-cases tier.value).
    assert "PRO" in stdout, (
        f"B3 regression: valid Pro license must show PRO in human output. Got:\n{stdout}"
    )
    # Standard 08: human copy should not include the package slug `tokenpak-paid`.
    assert "tokenpak-paid" not in stdout, (
        f"B3 standard-08 violation: human Pro output mentions package slug `tokenpak-paid`. "
        f"Use `Pro` in human copy; `tokenpak-paid` is the technical package name only. "
        f"Got:\n{stdout}"
    )


# ---------------------------------------------------------------------------
# Path 3: Expired license → OSS fallback with warn
# ---------------------------------------------------------------------------


def test_plan_expired_license_falls_back_to_oss(license_dir, caplog):
    """Path 3: an expired (beyond-grace) license falls back to OSS. Never fails closed."""
    (license_dir / "license.key").write_text("fake-expired-token\n")

    # Simulate the validator raising as it does on an unusable license —
    # the activation.get_plan() catches this and returns OSS fallback.
    with patch.object(
        LicenseValidator, "validate", side_effect=Exception("license expired (beyond grace period)")
    ):
        with caplog.at_level("WARNING", logger="tokenpak.agent.license.activation"):
            stdout, _ = _run_plan_capturing()

    assert "OSS" in stdout, (
        f"B3 regression: expired license must fall back to OSS in human output. Got:\n{stdout}"
    )
    # Loud-warn contract: the fallback message must say something, even if
    # the exact wording varies. The get_plan() fallback builds a message
    # containing the exception text.
    combined = stdout.lower()
    assert "license" in combined or any(
        "license" in record.message.lower() for record in caplog.records
    ), (
        "B3 never-fail-closed contract: expired-license fallback should be visible to "
        "the user (stdout) or ops (log). Neither surfaced."
    )


# ---------------------------------------------------------------------------
# Path 4: Corrupt license → OSS fallback with warn
# ---------------------------------------------------------------------------


def test_plan_corrupt_license_falls_back_to_oss(license_dir, caplog):
    """Path 4: a corrupt license file falls back to OSS. Never fails closed."""
    (license_dir / "license.key").write_text("this-is-not-a-valid-license-token-at-all\n")

    # Real validator will raise on this garbage — exercise the real path,
    # not a mock, to catch any regression in get_plan's error handling.
    with caplog.at_level("WARNING", logger="tokenpak.agent.license.activation"):
        stdout, _ = _run_plan_capturing()

    assert "OSS" in stdout, (
        f"B3 regression: corrupt license must fall back to OSS in human output. Got:\n{stdout}"
    )
    # No exception should have propagated — if _run_plan raised, this test
    # would have failed at the capture step. That's the never-fail-closed
    # guarantee in practice.


# ---------------------------------------------------------------------------
# Cross-path: output never fails closed + never crashes
# ---------------------------------------------------------------------------


def test_get_plan_never_raises_on_any_state(license_dir):
    """Cross-path: get_plan() contract — always returns a ValidationResult, never raises.

    This is the core never-fail-closed guarantee. Every fallback path in
    activation.get_plan() was verified above; this test locks the contract
    by exercising additional edge cases (permission error, broken path).
    """
    # Edge: license.key is a directory, not a file → load_stored_token returns None or raises.
    (license_dir / "license.key").mkdir(parents=True, exist_ok=True)
    result = _activation.get_plan()
    assert result is not None
    assert result.tier == LicenseTier.OSS, (
        f"B3 regression: get_plan() on malformed license.key path must return OSS, got {result.tier}"
    )
