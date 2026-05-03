# SPDX-License-Identifier: Apache-2.0
"""TokenPak controlled benchmark suite.

Implements `tokenpak bench` per
~/vault/01_PROJECTS/tokenpak/standards/24-benchmarking-standard.md.

This package is intentionally separate from `tokenpak.benchmark` (legacy
ad-hoc compression/latency benchmarks). The two coexist: this package is the
controlled, version-tracked, frozen-fixture suite; the legacy package is for
exploratory measurement.
"""
from __future__ import annotations

__all__ = ["run_quick"]


def __getattr__(name: str):
    # Lazy re-export so `python -m tokenpak.bench.suite` doesn't double-import.
    if name == "run_quick":
        from .suite import run_quick
        return run_quick
    raise AttributeError(name)
