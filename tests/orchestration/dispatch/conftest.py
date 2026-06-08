"""Dispatch integration-fixture pytest wiring — the ``--live`` opt-in (P-FIXTURES-01).

The dispatch integration fixtures (``test_fixtures.py``) run **mocked and
deterministic by default** so the suite is CI-safe and never calls a provider.
A ``--live`` opt-in flag enables a real-LLM smoke variant for the golden-path
fixtures; default CI never passes it, so the live path is never triggered.

Standards Delta v0 §15 acceptance element 5 ("Mock TIP responses (deterministic)
OR ``live`` flag for live-execution variant") + kickoff §13 item 7 ("a ``--live``
opt-in flag enables a real-LLM smoke variant; **default mode is mocked**").

Wiring lives in this *local* conftest (scoped to ``tests/orchestration/dispatch/``)
so the option is added without touching the repo-root ``tests/conftest.py`` —
pytest aggregates ``pytest_addoption`` hooks across every conftest, so a local
addoption is the correct, in-scope place for a directory-local flag.
"""

from __future__ import annotations

import pytest


def pytest_addoption(parser):
    """Register the ``--live`` opt-in for the dispatch integration fixtures.

    Default ``False`` → the fixtures run mocked + deterministic (no network).
    Passing ``--live`` flips ``run_live`` so the ``live`` smoke variant collects;
    the default CI invocation omits it, so the live path is never reached.
    """

    group = parser.getgroup("dispatch fixtures")
    group.addoption(
        "--live",
        action="store_true",
        dest="dispatch_live",
        default=False,
        help=(
            "Run the dispatch integration fixtures against a REAL LLM (smoke "
            "variant). Default: mocked + deterministic (CI-safe, no network)."
        ),
    )


@pytest.fixture(scope="session")
def dispatch_live(request) -> bool:
    """True only when ``--live`` was passed (default False → mocked mode)."""

    return bool(request.config.getoption("dispatch_live"))
