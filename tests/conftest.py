"""
Pytest configuration for the tokenpak test suite.

Env-dependency markers (exclude these for a hermetic developer run):
  needs_proxy           — starts or connects to a real tokenpak ProxyServer/subprocess
  needs_webhook         — requires a live external API key (e.g. ANTHROPIC_API_KEY)
  needs_internal_alerts — requires tokenpak._internal.alerts (internal-only module)
  needs_cali_env        — requires calibot-specific paths (/home/cali/tokenpak)
  needs_fast_host       — timing-sensitive benchmark assertions; fail on slow/shared hosts

Hermetic developer run (SueWu-friendly):
  pytest -m 'not needs_proxy and not needs_webhook and not needs_internal_alerts \
             and not needs_cali_env and not needs_fast_host' --tb=short -q

See tests/TEST-ENV-MATRIX.md for the full dependency matrix.
"""

def pytest_addoption(parser):
    """Add custom pytest options"""
    parser.addoption(
        "--update-baselines",
        action="store_true",
        default=False,
        help="Update baseline compression ratios (use after intentional changes)"
    )
