# SPDX-License-Identifier: Apache-2.0
"""Emit a flat TSV snapshot of model input-rates for fast shell-hook lookup.

Why TSV, not JSON or SQLite: the pre-send hook must stay under ~30ms, which
rules out Python invocation and jq parsing. A two-column TSV is sub-ms via
awk and requires no tokenpak runtime on the critical path.

The snapshot is regenerated every time the launcher runs, so registry edits
propagate to hooks automatically.  Rate is rounded to whole dollars per
1M input tokens — matches existing hook precision.
"""

from __future__ import annotations

from pathlib import Path

from tokenpak.models import get_rates, known_models

DEFAULT_SNAPSHOT_PATH = Path.home() / ".tokenpak" / "companion" / "run" / "model_rates.tsv"


def refresh(path: Path | None = None) -> Path:
    """Write ``<model>\\t<input_rate_usd_per_mtok>`` lines to ``path``.

    Rate is an integer dollar value — sufficient precision for the
    hook's budget gate (sub-dollar differences don't shift decisions).
    """
    out_path = path or DEFAULT_SNAPSHOT_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for model in known_models():
        rates = get_rates(model)
        if not rates:
            continue
        rate = round(float(rates.get("input", 0)))
        lines.append(f"{model}\t{rate}")

    # Sort by model id for determinism — stable snapshots play better with
    # git diffs if a user ever checks this file in.
    lines.sort()
    out_path.write_text("\n".join(lines) + "\n")
    return out_path


def count(path: Path | None = None) -> int:
    """Return the number of entries in the snapshot (0 if missing)."""
    out_path = path or DEFAULT_SNAPSHOT_PATH
    if not out_path.exists():
        return 0
    return sum(1 for line in out_path.read_text().splitlines() if line.strip())
