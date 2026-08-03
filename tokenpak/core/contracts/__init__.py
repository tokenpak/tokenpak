# SPDX-License-Identifier: Apache-2.0
"""Shared output contracts for user-facing TokenPak surfaces.

These contracts exist so that independent commands cannot disagree about
how an absent measurement is rendered. The rule they encode is simple and
non-negotiable: **a value that was not measured is never a number.**
"""

from tokenpak.core.contracts.measured import (
    DataState,
    Measured,
    error,
    measured,
    no_data,
    unavailable,
)

__all__ = [
    "DataState",
    "Measured",
    "error",
    "measured",
    "no_data",
    "unavailable",
]
