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

from .suite import run_quick

__all__ = ["run_quick"]
