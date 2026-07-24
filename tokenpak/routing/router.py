"""
TokenPak Top-Level Router
~~~~~~~~~~~~~~~~~~~~~~~~~
Routes model name strings to provider/target strings.

This is a lightweight, thread-safe registry that maps model names to
provider strings (e.g. "gpt-4o" → "openai", "claude-3-haiku" → "anthropic").

Distinct from the low-level ProviderRouter in pro/routing/router.py, which
orchestrates adapter failover at request-time.  This module is purely about
the model→provider lookup layer.

Usage::

    from tokenpak.routing.router import Router, RoutingError

    r = Router(default_provider="anthropic")
    r.register("gpt-4o", "openai")
    r.register("claude-3-haiku-20240307", "anthropic")

    provider = r.route("gpt-4o")        # "openai"
    provider = r.route("unknown-model") # "anthropic"  (default fallback)
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RoutingError(Exception):
    """Raised when a model cannot be routed and no default is configured."""


# ---------------------------------------------------------------------------
# Built-in defaults
# ---------------------------------------------------------------------------


def _build_default_routes() -> Dict[str, str]:
    """Build default routes from the dynamic model registry."""
    try:
        from tokenpak.models import get_default_routes

        return get_default_routes()
    except ImportError:
        return {}


#: Sensible out-of-the-box model → provider mappings (loaded from registry).
DEFAULT_ROUTES: Dict[str, str] = _build_default_routes()


# ---------------------------------------------------------------------------
# RouteEntry
# ---------------------------------------------------------------------------


class RouteEntry:
    """Internal record for a registered route.

    Attributes:
        model_name: The model identifier string.
        provider:   Target provider string (e.g. ``"openai"``).
        enabled:    Whether this route is active.  Disabled routes are
                    invisible to :meth:`Router.route` and
                    :meth:`Router.get_provider`.
    """

    __slots__ = ("model_name", "provider", "enabled")

    def __init__(self, model_name: str, provider: str, *, enabled: bool = True) -> None:
        self.model_name = model_name
        self.provider = provider
        self.enabled = enabled

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"RouteEntry(model_name={self.model_name!r}, "
            f"provider={self.provider!r}, enabled={self.enabled})"
        )


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class Router:
    """Thread-safe model-name → provider router.

    Parameters
    ----------
    default_provider:
        Provider to fall back to when a model is not found in the registry.
        If *None* and no matching route exists, :meth:`route` raises
        :class:`RoutingError`.
    load_defaults:
        If *True* (default), pre-populate the registry with
        :data:`DEFAULT_ROUTES`.

    Examples
    --------
    >>> r = Router(default_provider="anthropic")
    >>> r.register("gpt-4o", "openai")
    >>> r.route("gpt-4o")
    'openai'
    >>> r.route("unknown-model")
    'anthropic'
    """

    def __init__(
        self,
        default_provider: Optional[str] = None,
        *,
        load_defaults: bool = True,
    ) -> None:
        self._lock = threading.Lock()
        self._routes: Dict[str, RouteEntry] = {}
        self._default_provider = default_provider

        if load_defaults:
            for model, provider in DEFAULT_ROUTES.items():
                self._routes[model] = RouteEntry(model, provider)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        model_name: str,
        provider: str,
        *,
        enabled: bool = True,
    ) -> None:
        """Register (or update) a model → provider mapping.

        Parameters
        ----------
        model_name:
            Exact model identifier string (e.g. ``"gpt-4o"``).
        provider:
            Provider target string (e.g. ``"openai"``).
        enabled:
            Whether to activate the route immediately.
        """
        if not model_name:
            raise ValueError("model_name must not be empty")
        if not provider:
            raise ValueError("provider must not be empty")

        with self._lock:
            self._routes[model_name] = RouteEntry(model_name, provider, enabled=enabled)
            logger.debug("Registered route: %s → %s (enabled=%s)", model_name, provider, enabled)

    def route(self, model_name: str) -> str:
        """Return the provider for *model_name*.

        Looks up the registry for an **enabled** entry.  Falls back to
        :attr:`default_provider` when no entry is found.

        Parameters
        ----------
        model_name:
            The model identifier to route.

        Returns
        -------
        str
            Provider string (e.g. ``"openai"``).

        Raises
        ------
        RoutingError
            If *model_name* is not registered (or disabled) **and** no
            default provider was configured.
        """
        with self._lock:
            entry = self._routes.get(model_name)
            if entry is not None and entry.enabled:
                return entry.provider

            if self._default_provider is not None:
                logger.debug(
                    "No enabled route for %r — using default provider %r",
                    model_name,
                    self._default_provider,
                )
                return self._default_provider

            raise RoutingError(
                f"No route found for model {model_name!r} and no default provider configured."
            )

    def get_provider(self, model_name: str) -> Optional[str]:
        """Return the provider for *model_name*, or *None* if not found/disabled.

        Unlike :meth:`route`, this never falls back to the default and never
        raises — it simply returns *None* when no enabled route exists.

        Parameters
        ----------
        model_name:
            The model identifier to look up.

        Returns
        -------
        Optional[str]
            Provider string, or *None*.
        """
        with self._lock:
            entry = self._routes.get(model_name)
            if entry is not None and entry.enabled:
                return entry.provider
            return None

    def list_routes(self) -> Dict[str, Dict]:
        """Return a snapshot of all registered routes.

        Returns
        -------
        dict
            Mapping of ``model_name → {"provider": str, "enabled": bool}``.
        """
        with self._lock:
            return {
                name: {"provider": entry.provider, "enabled": entry.enabled}
                for name, entry in self._routes.items()
            }

    def disable(self, model_name: str) -> bool:
        """Disable the route for *model_name*.

        Parameters
        ----------
        model_name:
            Model to disable.

        Returns
        -------
        bool
            *True* if found and disabled, *False* if the model was not registered.
        """
        with self._lock:
            entry = self._routes.get(model_name)
            if entry is None:
                return False
            entry.enabled = False
            logger.debug("Disabled route for %r", model_name)
            return True

    def enable(self, model_name: str) -> bool:
        """Re-enable a previously disabled route.

        Parameters
        ----------
        model_name:
            Model to enable.

        Returns
        -------
        bool
            *True* if found and enabled, *False* if not registered.
        """
        with self._lock:
            entry = self._routes.get(model_name)
            if entry is None:
                return False
            entry.enabled = True
            logger.debug("Enabled route for %r", model_name)
            return True

    def set_default(self, provider: Optional[str]) -> None:
        """Update the fallback default provider.

        Parameters
        ----------
        provider:
            New default provider, or *None* to remove it.
        """
        with self._lock:
            self._default_provider = provider

    @property
    def default_provider(self) -> Optional[str]:
        """The configured fallback provider (read-only view)."""
        return self._default_provider

    def __repr__(self) -> str:  # pragma: no cover
        with self._lock:
            n = len(self._routes)
        return f"Router(routes={n}, default_provider={self._default_provider!r})"
