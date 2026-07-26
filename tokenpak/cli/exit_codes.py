# SPDX-License-Identifier: Apache-2.0
"""Documented process exit codes for the TokenPak CLI.

Every non-zero code a TokenPak verb returns must be defined here and
described in ``docs/errors.md``. An undocumented code is unusable: a script
that wraps TokenPak cannot distinguish "you are not set up yet" from "the
thing you asked for is not installed" if both surface as an opaque number.

The audit that produced this module found ``tokenpak codex --help`` exiting
**120** — a code TokenPak never chose, leaked from a child process, and
documented nowhere.

Ranges
------
``0``       success
``1``       generic failure (an error we have not classified more precisely)
``2``       usage error — reserved by argparse, never assigned here
``3``–``9`` TokenPak-classified conditions, defined below

Codes are append-only: once published, a code keeps its meaning.
"""

from __future__ import annotations

#: Command completed and its result is trustworthy.
EXIT_OK = 0

#: Unclassified failure. Prefer a specific code where one fits.
EXIT_FAILURE = 1

#: Argparse's usage-error code. Listed for documentation; do not assign it
#: manually — argparse owns it, and reusing it for semantic failures is what
#: made entitlement refusals indistinguishable from typos.
EXIT_USAGE = 2

#: TokenPak is not configured yet — the caller should run ``tokenpak setup``.
EXIT_NOT_CONFIGURED = 3

#: An external prerequisite is missing (a client binary that is not on PATH).
#: Distinct from a TokenPak failure: nothing is broken, something is absent.
EXIT_MISSING_PREREQUISITE = 4

#: The command ran correctly but no measured data exists to report. Not an
#: error — a caller may legitimately treat this as "nothing to do yet".
EXIT_NO_DATA = 5

#: The requested capability requires an entitlement this install does not
#: carry. Separated from ``EXIT_USAGE`` so automation can tell "you asked
#: wrong" apart from "you asked for something you are not entitled to".
EXIT_ENTITLEMENT_REQUIRED = 6

#: A required runtime component is not answering (the proxy is not running,
#: or is running but unhealthy).
EXIT_RUNTIME_UNAVAILABLE = 7

#: Persisted state exists but could not be parsed or trusted.
EXIT_CORRUPT_STATE = 8


#: Human-readable meaning per code, for ``docs/errors.md`` generation and for
#: tests that assert the table and the documentation have not drifted apart.
EXIT_CODE_MEANINGS: dict[int, str] = {
    EXIT_OK: "Success",
    EXIT_FAILURE: "Unclassified failure",
    EXIT_USAGE: "Usage error (argparse)",
    EXIT_NOT_CONFIGURED: "TokenPak is not configured — run `tokenpak setup`",
    EXIT_MISSING_PREREQUISITE: "A required external program is not installed",
    EXIT_NO_DATA: "No measured data available to report",
    EXIT_ENTITLEMENT_REQUIRED: "The requested capability requires an entitlement",
    EXIT_RUNTIME_UNAVAILABLE: "The proxy is not running or is unhealthy",
    EXIT_CORRUPT_STATE: "Stored state could not be parsed",
}

__all__ = [
    "EXIT_CODE_MEANINGS",
    "EXIT_CORRUPT_STATE",
    "EXIT_ENTITLEMENT_REQUIRED",
    "EXIT_FAILURE",
    "EXIT_MISSING_PREREQUISITE",
    "EXIT_NO_DATA",
    "EXIT_NOT_CONFIGURED",
    "EXIT_OK",
    "EXIT_RUNTIME_UNAVAILABLE",
    "EXIT_USAGE",
]
