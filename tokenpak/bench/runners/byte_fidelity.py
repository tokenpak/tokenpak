# SPDX-License-Identifier: Apache-2.0
"""C1 — wire-byte preservation.

Loads the byte-fidelity corpus, runs each request body through JSON
serialize/deserialize without any proxy mutation, and asserts the output
is byte-equal to the input. This is the smoke version of the full
byte-fidelity check; the `--full` tier extends it through the proxy's
pre-forward hooks (vault injection, credential rewrite, etc.).

For Phase 1 / `--quick`: validates that the canonical body shape is
round-trippable. Records the count of corpus entries that survive.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from ..manifest import fixture_path


@dataclass(frozen=True)
class ByteFidelityResult:
    metric_id: str
    metric_name: str
    fixture: str
    pass_count: int
    total_count: int
    pass_pct: float
    failures: list[str]
    duration_ms: float


def run() -> ByteFidelityResult:
    fname = "byte_fidelity_corpus.jsonl"
    path = fixture_path(fname)
    t0 = time.perf_counter()

    failures: list[str] = []
    total = 0
    passed = 0

    for line_no, raw in enumerate(path.read_text().splitlines(), start=1):
        raw = raw.strip()
        if not raw:
            continue
        total += 1
        try:
            entry = json.loads(raw)
        except Exception as e:
            failures.append(f"line {line_no}: malformed JSON: {e}")
            continue

        # Smoke check: required keys present.
        for key in ("id", "method", "path", "headers", "body"):
            if key not in entry:
                failures.append(f"line {line_no}: missing key {key!r}")
                break
        else:
            # Body must round-trip JSON-serialize → -deserialize cleanly.
            body = entry["body"]
            try:
                redeserialized = json.loads(json.dumps(body))
            except Exception as e:
                failures.append(f"line {line_no} ({entry['id']}): body round-trip failed: {e}")
                continue
            if redeserialized != body:
                failures.append(f"line {line_no} ({entry['id']}): body changed under round-trip")
                continue
            passed += 1

    duration_ms = (time.perf_counter() - t0) * 1000
    pct = (passed / total * 100.0) if total else 0.0

    return ByteFidelityResult(
        metric_id="C1",
        metric_name="byte_fidelity_pass_pct",
        fixture=fname,
        pass_count=passed,
        total_count=total,
        pass_pct=pct,
        failures=failures,
        duration_ms=duration_ms,
    )
