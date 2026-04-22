#!/usr/bin/env python3
"""Run the TIP-1.0 self-conformance check against the local TokenPak.

This script is now a thin wrapper around ``tokenpak doctor --conformance``.
Kept for backward compatibility with pre-SC-07 call sites (Constitution
§13.3 reference-implementation gate, early CI wiring, doc snippets that
still point here). New callers should use the CLI:

    tokenpak doctor --conformance
    tokenpak doctor --conformance --json

Exit codes are passed through unchanged:
  0 — conformance PASS
  1 — conformance FAIL
  2 — tooling error (validator unimportable)

The canonical implementation lives at
``tokenpak.services.diagnostics.conformance.run_conformance_checks``.
"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        from tokenpak.services.diagnostics.conformance import (
            exit_code_for,
            run_conformance_checks,
            summarize,
        )
    except ImportError as exc:
        print(f"error: tokenpak conformance runner not importable: {exc}", file=sys.stderr)
        return 2

    results = run_conformance_checks()
    counts = summarize(results)
    code = exit_code_for(results)

    print("TIP-1.0 self-conformance check")
    print("-" * 60)
    for r in results:
        marker = {"ok": "PASS", "warn": "WARN", "fail": "FAIL"}[r.status.value]
        print(f"{marker}  {r.name:<28} {r.summary}")
        for d in r.details:
            print(f"      {d}")
    print("-" * 60)
    print(
        f"{counts['ok']} pass, "
        f"{counts['warn']} warn, "
        f"{counts['fail']} fail"
    )
    if code == 0:
        print("TokenPak conforms to every declared profile.")
    elif code == 1:
        print("Conformance failure — one or more checks did not pass.")
    else:
        print("Tooling error — validator or registry unavailable.")
    return code


if __name__ == "__main__":
    sys.exit(main())
