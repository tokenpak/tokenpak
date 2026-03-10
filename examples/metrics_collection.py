"""Collect per-model compression metrics.

Run:
    python examples/metrics_collection.py
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Sample:
    model: str
    original_tokens: int
    compressed_tokens: int

    @property
    def savings_pct(self) -> float:
        return (1 - self.compressed_tokens / max(1, self.original_tokens)) * 100


def main() -> None:
    samples = [
        Sample("gpt-5.3-codex", 4200, 1550),
        Sample("claude-opus-4-6", 3900, 1480),
        Sample("gemini-3-pro", 3700, 1410),
    ]

    print("Model metrics")
    print("-------------")

    total_in = 0
    total_out = 0
    for row in samples:
        total_in += row.original_tokens
        total_out += row.compressed_tokens
        print(
            f"{row.model:<18} in={row.original_tokens:<5} out={row.compressed_tokens:<5} "
            f"saved={row.savings_pct:>5.1f}%"
        )

    overall = (1 - total_out / max(1, total_in)) * 100
    print("-------------")
    print(f"Overall savings: {overall:.1f}%")


if __name__ == "__main__":
    main()
