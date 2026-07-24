"""Base class for TokenPak compressor plugins."""

from abc import ABC, abstractmethod
from typing import Any


class CompressorPlugin(ABC):
    """Abstract base class for custom compressor plugins.

    Subclass this and implement ``compress()`` to create a plugin.
    Register via ``TOKENPAK_PLUGINS`` env var or ``config.yaml`` ``plugins.enabled`` key.
    """

    name: str = ""

    @abstractmethod
    def compress(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        """Compress *text* and return a result dict.

        Args:
            text: The input text to compress.
            context: Flexible metadata dict (e.g. model, mode, request_id).

        Returns:
            dict with at minimum ``{"text": str, "metadata": dict}``.
        """

    def priority(self) -> int:
        """Execution priority.  Higher number runs first.  Default: 50."""
        return 50
