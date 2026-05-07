"""
TokenPak Route Selector
~~~~~~~~~~~~~~~~~~~~~~~
Selects the best route from a pool of candidates using pluggable strategies.

Supported strategies
--------------------
``"priority"``
    Returns the enabled route with the **lowest** :attr:`Route.priority` number.
``"cost"``
    Returns the enabled route with the **lowest** :attr:`Route.cost_per_token`.
``"random"``
    Returns a uniformly random enabled route.

All operations are thread-safe.

Usage::

    from tokenpak.routing.route_selector import RouteSelector

    sel = RouteSelector()
    sel.add_route("primary",   provider="anthropic", priority=1,   cost_per_token=0.000003)
    sel.add_route("secondary", provider="openai",    priority=2,   cost_per_token=0.000001)
    sel.add_route("budget",    provider="google",    priority=100, cost_per_token=0.0000001)

    best_by_priority = sel.select("priority")    # → "primary"
    best_by_cost     = sel.select("cost")        # → "budget"
    random_pick      = sel.select("random")      # → any enabled route
"""

from __future__ import annotations

import logging
import random as _random
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SelectorError(Exception):
    """Raised when an invalid strategy is requested."""


# ---------------------------------------------------------------------------
# Route dataclass
# ---------------------------------------------------------------------------


@dataclass
class Route:
    """A candidate route entry managed by :class:`RouteSelector`.

    Attributes:
        name:           Unique route identifier (e.g. ``"primary"``).
        provider:       Target provider string (e.g. ``"anthropic"``).
        priority:       Lower number = higher priority in the ``"priority"``
                        strategy.  Defaults to ``100``.
        cost_per_token: Estimated cost per token in USD.  Used by the
                        ``"cost"`` strategy.  Defaults to ``0.0``.
        enabled:        Whether the route is eligible for selection.
    """

    name: str
    provider: str
    priority: int = 100
    cost_per_token: float = 0.0
    enabled: bool = True

    def to_dict(self) -> Dict:
        """Serialise to a plain dictionary."""
        return {
            "name": self.name,
            "provider": self.provider,
            "priority": self.priority,
            "cost_per_token": self.cost_per_token,
            "enabled": self.enabled,
        }


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

_VALID_STRATEGIES = frozenset({"priority", "cost", "random"})


# ---------------------------------------------------------------------------
# RouteSelector
# ---------------------------------------------------------------------------


class RouteSelector:
    """Thread-safe pool of candidate routes with pluggable selection strategies.

    Parameters
    ----------
    rng_seed:
        Optional seed for the random-number generator used by the ``"random"``
        strategy.  Useful for reproducible tests.

    Examples
    --------
    >>> sel = RouteSelector()
    >>> sel.add_route("a", "openai",    priority=2, cost_per_token=0.00001)
    >>> sel.add_route("b", "anthropic", priority=1, cost_per_token=0.00005)
    >>> sel.select("priority").name
    'b'
    >>> sel.select("cost").name
    'a'
    """

    def __init__(self, *, rng_seed: Optional[int] = None) -> None:
        self._lock = threading.Lock()
        self._routes: Dict[str, Route] = {}
        self._rng = _random.Random(rng_seed)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_route(
        self,
        name: str,
        provider: str,
        *,
        priority: int = 100,
        cost_per_token: float = 0.0,
        enabled: bool = True,
    ) -> Route:
        """Add (or replace) a route in the pool.

        Parameters
        ----------
        name:
            Unique route name.  If a route with the same name already exists
            it is **replaced**.
        provider:
            Target provider string.
        priority:
            Lower number = higher priority.
        cost_per_token:
            Estimated cost per token in USD.
        enabled:
            Whether the route starts as active.

        Returns
        -------
        Route
            The newly created :class:`Route` instance.
        """
        if not name:
            raise ValueError("Route name must not be empty")
        if not provider:
            raise ValueError("Route provider must not be empty")

        route = Route(
            name=name,
            provider=provider,
            priority=priority,
            cost_per_token=cost_per_token,
            enabled=enabled,
        )
        with self._lock:
            self._routes[name] = route
            logger.debug("Added route %r → %s (priority=%d)", name, provider, priority)
        return route

    def disable(self, name: str) -> bool:
        """Disable the named route so it is excluded from selection.

        Parameters
        ----------
        name:
            Route name to disable.

        Returns
        -------
        bool
            *True* if found and disabled, *False* if not registered.
        """
        with self._lock:
            route = self._routes.get(name)
            if route is None:
                return False
            route.enabled = False
            logger.debug("Disabled route %r", name)
            return True

    def enable(self, name: str) -> bool:
        """Re-enable a previously disabled route.

        Parameters
        ----------
        name:
            Route name to enable.

        Returns
        -------
        bool
            *True* if found and enabled, *False* if not registered.
        """
        with self._lock:
            route = self._routes.get(name)
            if route is None:
                return False
            route.enabled = True
            logger.debug("Enabled route %r", name)
            return True

    def remove(self, name: str) -> bool:
        """Remove a route from the pool entirely.

        Parameters
        ----------
        name:
            Route name to remove.

        Returns
        -------
        bool
            *True* if found and removed, *False* if not registered.
        """
        with self._lock:
            if name not in self._routes:
                return False
            del self._routes[name]
            logger.debug("Removed route %r", name)
            return True

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def list_available(self) -> List[Route]:
        """Return all **enabled** routes, sorted by priority then name.

        Returns
        -------
        List[Route]
            Snapshot of enabled routes.
        """
        with self._lock:
            available = [r for r in self._routes.values() if r.enabled]
        available.sort(key=lambda r: (r.priority, r.name))
        return available

    def list_all(self) -> List[Route]:
        """Return all routes (enabled and disabled), sorted by priority.

        Returns
        -------
        List[Route]
        """
        with self._lock:
            all_routes = list(self._routes.values())
        all_routes.sort(key=lambda r: (r.priority, r.name))
        return all_routes

    def get(self, name: str) -> Optional[Route]:
        """Return the named route, or *None* if not found."""
        with self._lock:
            return self._routes.get(name)

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def select(self, strategy: str = "priority") -> Optional[Route]:
        """Select the best route according to *strategy*.

        Parameters
        ----------
        strategy:
            One of ``"priority"``, ``"cost"``, or ``"random"``.

        Returns
        -------
        Optional[Route]
            Best matching :class:`Route`, or *None* if no routes are enabled.

        Raises
        ------
        SelectorError
            If an unknown strategy is requested.
        """
        if strategy not in _VALID_STRATEGIES:
            raise SelectorError(
                f"Unknown selection strategy {strategy!r}. "
                f"Valid strategies: {sorted(_VALID_STRATEGIES)}"
            )

        available = self.list_available()
        if not available:
            logger.debug("select(%r): no enabled routes available", strategy)
            return None

        if strategy == "priority":
            return self._select_priority(available)
        if strategy == "cost":
            return self._select_cost(available)
        # strategy == "random"
        return self._select_random(available)

    # ------------------------------------------------------------------
    # Strategy implementations
    # ------------------------------------------------------------------

    @staticmethod
    def _select_priority(routes: List[Route]) -> Route:
        """Return the route with the lowest priority number.

        Already sorted by (priority, name) from :meth:`list_available`, so
        just take the first element.
        """
        return routes[0]

    @staticmethod
    def _select_cost(routes: List[Route]) -> Route:
        """Return the route with the lowest ``cost_per_token``."""
        return min(routes, key=lambda r: r.cost_per_token)

    def _select_random(self, routes: List[Route]) -> Route:
        """Return a uniformly random enabled route."""
        with self._lock:
            return self._rng.choice(routes)

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """Total number of routes (enabled + disabled)."""
        with self._lock:
            return len(self._routes)

    def __repr__(self) -> str:  # pragma: no cover
        with self._lock:
            total = len(self._routes)
            enabled = sum(1 for r in self._routes.values() if r.enabled)
        return f"RouteSelector(total={total}, enabled={enabled})"
