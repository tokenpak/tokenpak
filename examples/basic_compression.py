"""Basic TokenPak compression example.

Run:
    python examples/basic_compression.py
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TokenPakClient:
    """Minimal local client used for examples (no network calls)."""

    compression_ratio: float = 0.58

    def compress(self, text: str) -> str:
        words = text.split()
        keep = max(1, int(len(words) * self.compression_ratio))
        return " ".join(words[:keep])


def count_tokens(text: str) -> int:
    """Tiny local token estimator for deterministic examples."""

    return len(text.split())


def main() -> None:
    client = TokenPakClient()
    original = (
        "TokenPak helps teams shrink prompt size while preserving intent, facts, and "
        "execution constraints for reliable multi-agent workflows. " * 4
    )
    compressed = client.compress(original)

    original_tokens = count_tokens(original)
    compressed_tokens = count_tokens(compressed)
    savings = (1 - (compressed_tokens / max(1, original_tokens))) * 100

    print(f"Original tokens:   {original_tokens}")
    print(f"Compressed tokens: {compressed_tokens}")
    print(f"Saved {savings:.1f}% tokens")


if __name__ == "__main__":
    main()
