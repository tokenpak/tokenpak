"""tokenpak.adapters.generic — Generic fallback adapter."""
from __future__ import annotations

import os
from tokenpak.adapters.base import TokenPakAdapter


class GenericAdapter(TokenPakAdapter):
    """Generic fallback adapter for unknown environments."""

    provider_name = "generic"

    def __init__(self, base_url: str = "") -> None:
        url = base_url or os.environ.get("ANTHROPIC_BASE_URL", "http://localhost:8766")
        super().__init__(base_url=url)

    def prepare_request(self, request: dict) -> dict:
        return request

    def parse_response(self, response: dict) -> dict:
        return response

    def extract_tokens(self, response: dict) -> dict:
        usage = response.get("usage", {})
        return {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        }


__all__ = ["GenericAdapter"]
