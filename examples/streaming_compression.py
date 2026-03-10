"""Streaming compression for large inputs.

Run:
    python examples/streaming_compression.py
"""

from __future__ import annotations

from collections.abc import Iterator


def count_tokens(text: str) -> int:
    return len(text.split())


class TokenPakClient:
    def compress(self, text: str) -> str:
        words = text.split()
        return " ".join(words[: max(1, int(len(words) * 0.6))])


def chunk_text(text: str, chunk_words: int = 60) -> Iterator[str]:
    words = text.split()
    for i in range(0, len(words), chunk_words):
        yield " ".join(words[i : i + chunk_words])


def main() -> None:
    client = TokenPakClient()
    large_prompt = " ".join(["workflow context and constraints"] * 1200)

    peak_in_memory_tokens = 0
    streamed_output: list[str] = []

    for chunk in chunk_text(large_prompt):
        chunk_tokens = count_tokens(chunk)
        peak_in_memory_tokens = max(peak_in_memory_tokens, chunk_tokens)
        streamed_output.append(client.compress(chunk))

    compressed = "\n".join(streamed_output)

    print(f"Input tokens:      {count_tokens(large_prompt)}")
    print(f"Output tokens:     {count_tokens(compressed)}")
    print(f"Peak chunk tokens: {peak_in_memory_tokens}")
    print("Memory efficiency: only one chunk processed at a time")


if __name__ == "__main__":
    main()
