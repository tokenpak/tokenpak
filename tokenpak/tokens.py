"""Token counting utilities."""

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")

    def count_tokens(text: str) -> int:
        """Count tokens using tiktoken (accurate)."""
        return len(_ENC.encode(text))
except ImportError:
    def count_tokens(text: str) -> int:
        """Estimate tokens at ~4 chars per token (fallback)."""
        return max(1, len(text) // 4)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text to approximately max_tokens."""
    estimated_chars = max_tokens * 4
    if len(text) <= estimated_chars:
        return text
    return text[:estimated_chars].rsplit(" ", 1)[0] + "…"
