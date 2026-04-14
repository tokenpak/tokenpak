"""tokenpak.sdk.openclaw — OpenClaw gateway adapter."""
from __future__ import annotations

import os
from tokenpak.sdk.base import TokenPakAdapter


class OpenClawAdapter(TokenPakAdapter):
    """Adapter for OpenClaw gateway environments."""

    provider_name = "openclaw"

    def __init__(self, base_url: str = "") -> None:
        url = base_url or os.environ.get("OPENCLAW_GATEWAY_URL", "http://localhost:18789")
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


__all__ = ["OpenClawAdapter"]
