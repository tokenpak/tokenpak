"""Guard: runner-sensitive perf/SLA tests stay classified as ``needs_fast_host``.

Two test modules assert wall-clock performance and therefore flake on shared CI
runners (noisy, no guaranteed capacity):

* ``tests/benchmarks/test_compression_benchmarks.py`` — median-latency thresholds
* ``tests/benchmarks/test_load_100rps.py`` — sustained-throughput SLA targets

They are intentionally excluded from the always-on correctness matrix
(``.github/workflows/ci.yml`` runs ``pytest -m "... and not needs_fast_host"``)
and instead run + report in the dedicated "Performance Benchmarks" workflow
(``.github/workflows/benchmarks.yml``: per-PR + nightly schedule).

If the ``needs_fast_host`` marker is dropped from either module, those tests
silently re-enter the blocking matrix and start flaking unrelated PRs again.
This guard fails loudly so the quarantine stays intentional and documented.

It is itself a fast, deterministic correctness check (no timing assertions), so
it runs in the normal matrix and enforces the policy on every PR.
"""

import importlib

import pytest

# Modules whose timing-sensitive perf/SLA tests must stay quarantined.
QUARANTINED_MODULES = [
    "tests.benchmarks.test_compression_benchmarks",
    "tests.benchmarks.test_load_100rps",
]


def _module_marker_names(module) -> set:
    """Return the set of marker names declared in a module-level ``pytestmark``.

    ``pytestmark`` may be a single mark or a list/tuple of marks; normalise both.
    """
    pm = getattr(module, "pytestmark", [])
    if not isinstance(pm, (list, tuple)):
        pm = [pm]
    return {getattr(m, "name", None) for m in pm}


@pytest.mark.parametrize("module_path", QUARANTINED_MODULES)
def test_perf_sla_tests_are_quarantined(module_path):
    module = importlib.import_module(module_path)
    assert "needs_fast_host" in _module_marker_names(module), (
        f"{module_path} must carry the module-level `needs_fast_host` marker so "
        f"the always-on CI matrix (.github/workflows/ci.yml) excludes its "
        f"timing-sensitive perf/SLA assertions. Removing it re-introduces "
        f"shared-runner flakiness into unrelated PRs. Drop it only via a "
        f"deliberate CI-policy change (see .github/workflows/benchmarks.yml and "
        f"tests/conftest.py)."
    )
