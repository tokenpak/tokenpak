"""
TokenPak Model Router — Proxy-native model routing & fallback.

STATUS: PLACEHOLDER (Pro feature — not included in OSS)

When implemented, this module will:
- Read routing config from ~/.tokenpak/fleet-models.yaml (or config.yaml routing section)
- Remap incoming model requests to configured primary/fallback chain
- Handle health-aware fallback (if primary upstream is down, try next)
- Support per-client or per-profile overrides
- Work transparently with any client (OpenClaw, Claude Code, Cursor, IDE, etc.)

Architecture:
    Client (any) → TokenPak proxy → ModelRouter middleware → upstream

Config sketch (fleet-models.yaml):
    defaults:
      primary: claude-haiku-4-6
      fallbacks:
        - claude-haiku-4-5
        - claude-sonnet-4-6
    profiles:
      heavy-reasoning:
        primary: claude-opus-4-6
        fallbacks:
          - claude-sonnet-4-6

This file is a no-op stub. The pipeline skips it when not enabled.
"""

# Phase: Pro v1
# Spec: TBD
# Assigned: TBD


class ModelRouter:
    """Proxy-native model routing with fallback (Pro feature)."""

    def __init__(self, config_path=None):
        self.enabled = False
        self._config_path = config_path

    def is_enabled(self) -> bool:
        return self.enabled

    def resolve_model(self, requested_model: str, client_hint: str = "") -> str:
        """
        Resolve the actual model to use based on routing config.

        Args:
            requested_model: Model ID from the client request
            client_hint: Optional client identifier for per-profile routing

        Returns:
            The model ID to forward upstream (unchanged in OSS)
        """
        # OSS: passthrough — no remapping
        return requested_model

    def on_upstream_error(self, model: str, error: Exception) -> str | None:
        """
        Called when upstream returns an error. Returns fallback model or None.

        Args:
            model: The model that failed
            error: The upstream error

        Returns:
            Next fallback model ID, or None if exhausted
        """
        # OSS: no fallback handling at proxy layer
        return None
