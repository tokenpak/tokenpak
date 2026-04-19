"""
Generic fallback adapter.

Always detects as a match — used when no other adapter claims the request.
Strategy: conservative compression (0.3 ratio target), no special routing.
"""

from __future__ import annotations

from typing import Any, Dict

from .base import BaseAdapter


class GenericAdapter(BaseAdapter):
    """Catch-all adapter for unrecognised or unknown platforms."""

    @property
    def platform_name(self) -> str:
        return "generic"

    @classmethod
    def detect(
        cls,
        request_headers: Dict[str, str],
        env: Dict[str, str],
    ) -> bool:
        # Always matches — must be last in priority order.
        return True

    def get_config(self) -> Dict[str, Any]:
        return {
            "compression_ratio_target": 0.3,
            "vault_aware": False,
            "preserve_code_blocks": False,
            "prefer_fast_models": False,
            "routing_hints": {
                "context_recipe": "default",
                "platform": self.platform_name,
            },
        }
