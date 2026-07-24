"""DispatchRoute registry + loader implementation.

This module turns the packaged route YAML profiles into validated,
registry-bound :class:`~tokenpak.orchestration.dispatch.models.route.DispatchRoute`
records and provides the dynamic capability-binding contract the dispatch
runtime consumes when it resolves a route's worker stations against the worker
registry.

Two contracts live here, mirroring :mod:`tokenpak.orchestration.dispatch.registry.workers`:

* **Route registry** — :class:`DispatchRouteRegistry` discovers route YAML
  profiles (``route.*.v<n>.yaml``) and parses each into a ``DispatchRoute``.
  Because every station ``required_capabilities`` string is validated against
  the capability registry by the model's field validator, a route profile
  that declares an unknown capability is rejected **fail-loud at load time**,
  not skipped.

* **Dynamic worker binding** — :func:`resolve_station_workers` resolves a
  worker station (``required_role`` + ``required_capabilities``) against a
  :class:`~tokenpak.orchestration.dispatch.registry.workers.DispatchWorkerRegistry`
  by **capability intersection**, NOT by hardcoded worker id. A station
  binds to exactly those registry workers that declare the station's role and
  possess every required capability; :func:`bind_route` walks all worker
  stations of a route and fails loud (:class:`RouteResolutionError`) if any
  worker station has no eligible worker.

"Always dynamic" (the standing convention): routes and worker bindings are
discovered from files / the registry at runtime; there is no hardcoded
enumeration of route ids or worker ids. The file system (packaged defaults +
user overrides) plus the live worker registry are the single sources of truth.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from tokenpak import _paths

from ..models.route import DispatchRoute, RouteStation
from ..models.worker import DispatchWorker
from .workers import DispatchWorkerRegistry

# Packaged route defaults ship in the ``routes/`` subdirectory beside this
# module, mirroring the ``overlays/`` layout (worker profiles live directly in
# the registry package; routes and overlays live in their own subdirectories).
_REGISTRY_DIR = Path(__file__).resolve().parent
_PACKAGED_ROUTES_DIR = _REGISTRY_DIR / "routes"

# Discovery glob — the file naming convention is the single source of truth, so
# dropping a ``route.*.yaml`` file into the directory registers it (no code edit).
_ROUTE_GLOB = "route.*.yaml"

# On-disk root for user-supplied routes. Resolved through the canonical path
# helper so the location is never hardcoded (it follows ``TOKENPAK_HOME`` /
# ``~/.tpk`` / legacy resolution), mirroring ``workers.user_overlay_dir``.
_USER_ROUTES_PARTS: tuple[str, ...] = ("dispatch", "routes")


def user_routes_dir() -> Path:
    """Return the user routes directory (``<tokenpak-home>/dispatch/routes/``).

    Pure path resolution via :func:`tokenpak._paths.under`; the directory is not
    required to exist (the registry falls back to packaged defaults when absent).
    """

    return _paths.under(*_USER_ROUTES_PARTS)


class RouteProfileError(ValueError):
    """Raised when a route YAML profile cannot be loaded or validated.

    Subclasses :class:`ValueError`; the underlying cause (an unknown-capability
    rejection, a malformed YAML mapping, a duplicate id) is chained via
    ``__cause__``.
    """


class RouteResolutionError(ValueError):
    """Raised when a route's worker station cannot bind to any worker.

    Carries the offending route + station ids and the reason (no worker with the
    required role, or none with the full required-capability set) so the
    dispatch runtime can report exactly why the route did not resolve.
    """

    def __init__(self, route_id: str, station_id: str, reason: str) -> None:
        self.route_id = route_id
        self.station_id = station_id
        self.reason = reason
        super().__init__(
            f"route {route_id!r} station {station_id!r} cannot bind to any worker: {reason}"
        )


def _read_yaml_mapping(path: Path, error_cls: type[ValueError]) -> dict[str, Any]:
    """Load ``path`` as a YAML mapping, raising ``error_cls`` on any problem."""

    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:  # pragma: no cover - exercised via tests
        raise error_cls(f"failed to read route YAML {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise error_cls(f"expected a YAML mapping in {path}, got {type(raw).__name__}")
    return raw


def is_worker_station(station: RouteStation) -> bool:
    """Return ``True`` iff ``station`` is a worker station (has a ``required_role``).

    A station is either a worker station (``required_role`` set) or a
    system-component station (``system_component`` set, e.g. ``delivery_dock``);
    only worker stations bind to the worker registry.
    """

    return station.required_role is not None


class DispatchRouteRegistry:
    """Loads and indexes route profiles from one or more directories.

    Routes are discovered by glob (``route.*.yaml``), so dropping a new profile
    file into the routes directory registers it with no code change. Each profile
    is parsed into a :class:`DispatchRoute`; the model's station field validator
    rejects unknown capability strings at load time (fail-loud), and the
    registry re-raises that as :class:`RouteProfileError` with the offending file
    path attached.
    """

    def __init__(self, routes: dict[str, DispatchRoute]) -> None:
        self._routes = routes

    @classmethod
    def from_dir(cls, directory: Path | None = None) -> "DispatchRouteRegistry":
        """Build a registry from every ``route.*.yaml`` in ``directory``.

        Defaults to the packaged routes directory shipped beside this module.
        Fail-loud on a duplicate route id across files.
        """

        source = directory if directory is not None else _PACKAGED_ROUTES_DIR
        routes: dict[str, DispatchRoute] = {}
        if source.is_dir():
            for path in sorted(source.glob(_ROUTE_GLOB)):
                route = cls._load_route(path)
                if route.id in routes:
                    raise RouteProfileError(
                        f"duplicate route id {route.id!r} (second definition in {path})"
                    )
                routes[route.id] = route
        return cls(routes)

    @staticmethod
    def _load_route(path: Path) -> DispatchRoute:
        data = _read_yaml_mapping(path, RouteProfileError)
        try:
            return DispatchRoute.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - re-wrapped with file context
            raise RouteProfileError(f"invalid route profile {path.name}: {exc}") from exc

    def ids(self) -> list[str]:
        """Return the registered route ids (sorted)."""

        return sorted(self._routes)

    def all(self) -> list[DispatchRoute]:
        """Return all registered routes (ordered by id)."""

        return [self._routes[rid] for rid in self.ids()]

    def get(self, route_id: str) -> DispatchRoute:
        """Return the route with ``route_id``; raise ``KeyError`` if absent."""

        try:
            return self._routes[route_id]
        except KeyError as exc:
            raise KeyError(f"no route {route_id!r} in registry; known: {self.ids()}") from exc

    def has(self, route_id: str) -> bool:
        """Return ``True`` iff ``route_id`` is registered."""

        return route_id in self._routes

    def for_intent(self, intent: str) -> list[DispatchRoute]:
        """Return every route declaring ``intent`` in its triggers (sorted by id).

        This is the exact-route_trigger lookup used by the dispatch precedence
        layer.
        """

        return [r for r in self.all() if intent in r.triggers.intents]


def resolve_station_workers(
    station: RouteStation,
    worker_registry: DispatchWorkerRegistry,
) -> list[DispatchWorker]:
    """Resolve the workers eligible for a worker ``station``.

    Dynamic capability binding: a worker is eligible iff it declares the
    station's ``required_role`` AND possesses **every** capability in the
    station's ``required_capabilities`` (capability intersection). Workers
    are resolved from the live ``worker_registry`` by capability intersection,
    never by a hardcoded worker id.

    Returns the eligible workers ordered by id (stable, deterministic). A
    system-component station (no ``required_role``) returns ``[]`` — those
    stations are not worker-bound. The empty-list outcome for a *worker* station
    is the signal the dispatcher uses to fail the route binding.
    """

    if not is_worker_station(station):
        return []

    role = station.required_role
    if role is None:
        return []
    required = set(station.required_capabilities)
    eligible: list[DispatchWorker] = []
    for worker in worker_registry.for_role(role):
        if required.issubset(set(worker.capabilities)):
            eligible.append(worker)
    # ``for_role`` already returns workers ordered by id; preserve that order.
    return eligible


def bind_route(
    route: DispatchRoute,
    worker_registry: DispatchWorkerRegistry,
) -> dict[str, list[DispatchWorker]]:
    """Bind every worker station of ``route`` to its eligible workers.

    Walks the route's worker stations (skipping system-component stations) and
    resolves each against ``worker_registry`` by capability intersection. Returns
    ``{station_id: [eligible workers]}`` for every worker station. Fail-loud
    (:class:`RouteResolutionError`) if any worker station resolves to zero
    eligible workers — a route that cannot staff a station is not dispatchable.
    """

    bindings: dict[str, list[DispatchWorker]] = {}
    for station in route.stations:
        if not is_worker_station(station):
            continue
        workers = resolve_station_workers(station, worker_registry)
        if not workers:
            role = station.required_role
            if role is None:
                continue
            have_role = [w.id for w in worker_registry.for_role(role)]
            if not have_role:
                reason = f"no worker declares role {role!r}"
            else:
                reason = (
                    f"no worker with role {role!r} has all required capabilities "
                    f"{sorted(station.required_capabilities)!r} "
                    f"(candidates with the role: {have_role})"
                )
            raise RouteResolutionError(route.id, station.id, reason)
        bindings[station.id] = workers
    return bindings


def route_is_bindable(
    route: DispatchRoute,
    worker_registry: DispatchWorkerRegistry,
) -> bool:
    """Return ``True`` iff every worker station of ``route`` has an eligible worker.

    Non-raising counterpart to :func:`bind_route` — used by the scorer to apply
    the ``forbidden_action_required`` / capability-mismatch penalty without
    aborting the precedence walk.
    """

    try:
        bind_route(route, worker_registry)
    except RouteResolutionError:
        return False
    return True


def default_route_registry() -> DispatchRouteRegistry:
    """Return a registry loaded from the packaged route profiles."""

    return DispatchRouteRegistry.from_dir()


def merged_route_registry(
    user_dir: Path | None = None,
) -> DispatchRouteRegistry:
    """Return a registry merging packaged defaults with user routes (user wins).

    Packaged routes load first; a user route file of the same id shadows the
    packaged default (mirrors the overlay user-dir override idiom). ``user_dir``
    defaults to :func:`user_routes_dir` (``<tokenpak-home>/dispatch/routes/``);
    a missing directory contributes nothing.
    """

    merged: dict[str, DispatchRoute] = {}
    user = user_dir if user_dir is not None else user_routes_dir()
    for source in (_PACKAGED_ROUTES_DIR, user):
        if not source.is_dir():
            continue
        for path in sorted(source.glob(_ROUTE_GLOB)):
            route = DispatchRouteRegistry._load_route(path)
            merged[route.id] = route  # later source (user) shadows earlier
    return DispatchRouteRegistry(merged)


__all__ = [
    "RouteProfileError",
    "RouteResolutionError",
    "DispatchRouteRegistry",
    "is_worker_station",
    "resolve_station_workers",
    "bind_route",
    "route_is_bindable",
    "user_routes_dir",
    "default_route_registry",
    "merged_route_registry",
]
