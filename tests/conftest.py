"""
Pytest configuration for regression tests
"""

def pytest_addoption(parser):
    """Add custom pytest options"""
    parser.addoption(
        "--update-baselines",
        action="store_true",
        default=False,
        help="Update baseline compression ratios (use after intentional changes)"
    )
