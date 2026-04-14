"""
Legacy platform adapter.

Detection:
  - HTTP header ``X-TokenPak-Session`` is present, OR
  - Environment variable ``TOKENPAK_SESSION`` is set (non-empty).

Strategy: aggressive compression (0.7+ ratio target), vault-aware context,
prefer fast models for simple tasks.
"""

from __future__ import annotations

from typing import Any, Dict

from .base import BaseAdapter


class LegacyAdapter(BaseAdapter):
    """Adapter for requests originating from the legacy agent runtime."""

    @property
    def platform_name(self) -> str:
        return "legacy"

    @classmethod
    def detect(
        cls,
        request_headers: Dict[str, str],
        env: Dict[str, str],
    ) -> bool:
        # Normalise header keys to lowercase for case-insensitive lookup
        headers_lower = {k.lower(): v for k, v in request_headers.items()}
        if "x-tokenpak-session" in headers_lower:
            return True
        if env.get("TOKENPAK_SESSION", ""):
            return True
        return False

    def get_config(self) -> Dict[str, Any]:
        return {
            "compression_ratio_target": 0.7,
            "vault_aware": True,
            "preserve_code_blocks": False,
            "prefer_fast_models": True,
            "routing_hints": {
                "context_recipe": "vault",
                "platform": self.platform_name,
            },
        }
