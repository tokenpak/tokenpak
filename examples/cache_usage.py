"""Cache usage example with hit/miss reporting.

Run:
    python examples/cache_usage.py
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenPakClient:
    cache: dict[str, str] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0

    def compress(self, prompt: str) -> str:
        if prompt in self.cache:
            self.hits += 1
            return self.cache[prompt]

        self.misses += 1
        result = " ".join(prompt.split()[:20])
        self.cache[prompt] = result
        return result

    def cache_stats(self) -> dict[str, float]:
        total = self.hits + self.misses
        hit_rate = (self.hits / total) * 100 if total else 0.0
        return {
            "hits": float(self.hits),
            "misses": float(self.misses),
            "hit_rate": hit_rate,
        }


def main() -> None:
    client = TokenPakClient()

    prompts = [
        "summarize release notes for tokenpak",
        "generate migration checklist for proxy v4",
        "summarize release notes for tokenpak",
        "summarize release notes for tokenpak",
        "generate migration checklist for proxy v4",
    ]

    for i, prompt in enumerate(prompts, start=1):
        output = client.compress(prompt)
        print(f"{i:02d}. {prompt} -> {output}")

    stats = client.cache_stats()
    print("\nCache stats")
    print(f"Hits: {int(stats['hits'])}")
    print(f"Misses: {int(stats['misses'])}")
    print(f"Hit rate: {stats['hit_rate']:.1f}%")


if __name__ == "__main__":
    main()
