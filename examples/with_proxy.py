"""Use TokenPak SDK with a local proxy endpoint.

Run:
    python examples/with_proxy.py
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TokenPakClient:
    base_url: str = "http://localhost:8766"

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return ((input_tokens / 1_000_000) * 2.50) + ((output_tokens / 1_000_000) * 10.00)

    def estimate_compressed_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        compression_ratio: float = 0.60,
    ) -> float:
        compressed_in = int(input_tokens * compression_ratio)
        return self.estimate_cost(compressed_in, output_tokens)


def main() -> None:
    client = TokenPakClient()

    raw_input_tokens = 18_000
    output_tokens = 2_400

    direct_cost = client.estimate_cost(raw_input_tokens, output_tokens)
    compressed_cost = client.estimate_compressed_cost(raw_input_tokens, output_tokens)
    savings = direct_cost - compressed_cost

    print(f"Proxy endpoint: {client.base_url}")
    print("Mode: local cost simulation (no network call)")
    print(f"Direct model cost:     ${direct_cost:.4f}")
    print(f"Proxy-compressed cost: ${compressed_cost:.4f}")
    print(f"Estimated savings:     ${savings:.4f}")


if __name__ == "__main__":
    main()
