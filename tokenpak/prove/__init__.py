# SPDX-License-Identifier: Apache-2.0
"""tokenpak prove — live A/B value proof for tokenpak.

Runs the same multi-turn prompt scenario through two paths:
  - **Arm A (Direct)**: raw API call, no proxy, no optimizations
  - **Arm B (TokenPak)**: through the tokenpak proxy with all optimizations

Collects per-turn metrics (tokens, cost, latency, cache hits) and produces
a side-by-side comparison report proving tokenpak's value.

Quick start::

    tokenpak prove run                          # run the default scenario
    tokenpak prove run my-scenario              # run a custom .md scenario
    tokenpak prove list                         # list available scenarios
    tokenpak prove show prf_a1b2c3d4            # show a past proof result
"""
