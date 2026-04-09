"""
BaseAdapter — abstract base class for all platform adapters.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseAdapter(ABC):
    """
    Abstract base class for TokenPak platform adapters.

    Each concrete adapter must implement:
      - ``platform_name`` property
      - ``detect(request_headers, env)`` classmethod
      - ``get_config()`` instance method
    """

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Human-readable platform identifier (e.g. "openclaw", "claude_cli")."""

    @classmethod
    @abstractmethod
    def detect(
        cls,
        request_headers: Dict[str, str],
        env: Dict[str, str],
    ) -> bool:
        """
        Return True if this adapter recognises the calling platform from the
        given HTTP request headers and/or environment variables.

        Parameters
        ----------
        request_headers:
            Case-insensitive mapping of HTTP request headers.
        env:
            Mapping of environment variables (e.g. ``os.environ``).
        """

    @abstractmethod
    def get_config(self) -> Dict[str, Any]:
        """
        Return a configuration dict consumed by the proxy pipeline.

        Expected keys (all optional — consume defensively):
          - ``compression_ratio_target``  float 0-1  (fraction of tokens to save)
          - ``vault_aware``               bool       (enable vault context integration)
          - ``preserve_code_blocks``      bool       (never compress fenced code)
          - ``prefer_fast_models``        bool       (route simple tasks to cheaper models)
          - ``routing_hints``             dict       (arbitrary hints for the router)
        """

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} platform={self.platform_name!r}>"
