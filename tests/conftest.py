"""
Root test configuration for TokenPak.

Sets TOKENPAK_TEST_LICENSE and TOKENPAK_PUBLIC_KEY so that the license loader
uses the synthetic enterprise fixture during tests.  This ensures tests that
exercise Pro+ features do not regress due to missing license.

The enterprise fixture is loaded once at session start so that Pro-gated
features are accessible by default.  Individual tests that need to test
OSS-tier behaviour should call reset_for_testing(LicenseTier.OSS) explicitly.
"""
import os
from pathlib import Path

# Resolve paths relative to this file (works regardless of CWD)
_TESTS_DIR = Path(__file__).parent
_FIXTURE_LICENSE = str(_TESTS_DIR / "fixtures" / "test_license.json")
_FIXTURE_PUBKEY = _TESTS_DIR / "fixtures" / "test_license_pub.pem"

# Set env vars before any test imports tokenpak.license.loader
os.environ.setdefault("TOKENPAK_TEST_LICENSE", _FIXTURE_LICENSE)
if Path(_FIXTURE_PUBKEY).exists():
    os.environ.setdefault("TOKENPAK_PUBLIC_KEY", Path(_FIXTURE_PUBKEY).read_text())

# Load the license once at import time so the process-global tier is enterprise
# for the entire test session (Pro features are accessible by default).
try:
    from tokenpak.license.loader import load_license as _load_license
    _load_license()
except Exception:
    pass  # Never fail test collection due to license loading
