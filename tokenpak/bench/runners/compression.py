# SPDX-License-Identifier: Apache-2.0
"""V1, V2, V3 — compression reduction across realistic agent corpora.

Each fixture is run through the heuristic engine (the default `tokenpak serve`
path) and the percent reduction in token count is recorded.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from ..manifest import fixture_path


@dataclass(frozen=True)
class CompressionResult:
    metric_id: str
    metric_name: str
    fixture: str
    reduction_pct: float
    tokens_in: int
    tokens_out: int
    duration_ms: float


_FIXTURES: list[tuple[str, str, str]] = [
    ("V1", "headline_reduction_pct", "headline_corpus.txt"),
    ("V2", "cursor_reduction_pct", "cursor_corpus.txt"),
    ("V3", "aider_reduction_pct", "aider_corpus.txt"),
]


def run() -> list[CompressionResult]:
    from tokenpak.compression.engines.heuristic import HeuristicEngine
    from tokenpak.telemetry.tokens import count_tokens

    out: list[CompressionResult] = []
    for metric_id, metric_name, fname in _FIXTURES:
        text = fixture_path(fname).read_text(encoding="utf-8")
        t0 = time.perf_counter()
        engine = HeuristicEngine()
        compressed = engine.compact(text)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        tokens_in = count_tokens(text)
        tokens_out = count_tokens(compressed)
        if tokens_in <= 0:
            raise RuntimeError(f"fixture {fname} tokenized to {tokens_in} tokens — tokenizer regression")
        reduction = (1.0 - tokens_out / tokens_in) * 100.0

        out.append(
            CompressionResult(
                metric_id=metric_id,
                metric_name=metric_name,
                fixture=fname,
                reduction_pct=reduction,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                duration_ms=elapsed_ms,
            )
        )
    return out
