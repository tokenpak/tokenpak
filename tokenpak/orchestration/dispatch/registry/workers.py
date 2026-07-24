"""DispatchWorker registry + prompt-overlay loader.

This module turns the packaged worker/overlay YAML profiles into validated,
registry-bound runtime records and provides the additive prompt-composition and
capability-intersection contracts the station runner consumes.

Three contracts live here:

* **Worker registry** — :class:`DispatchWorkerRegistry` discovers worker YAML
  profiles (``worker.*.v<n>.yaml``) and parses each into a
  :class:`~tokenpak.orchestration.dispatch.models.worker.DispatchWorker`. Because
  every capability string is validated against the capability registry by
  the model's field validator, a profile that declares an unknown capability is
  rejected **fail-loud at load time**, not skipped.

* **Overlay loader** — :class:`OverlayLoader` reads prompt overlays
  (``overlay.*.v<n>.yaml``) from the user overlay directory
  (``~/.tpk/dispatch/overlays/``, resolved via :func:`tokenpak._paths.under`)
  and falls back to the packaged defaults shipped beside this module. A
  user-supplied overlay shadows the packaged one of the same id.

* **Additive composition + capability intersection** — :func:`compose_prompt`
  concatenates ``worker.system_directives`` then ``overlay.instructions``; the
  base directives are always preserved in full (overlays are additive and can
  never remove a base directive). :func:`bind_overlay` /
  :func:`assert_route_binding` enforce the route-binding rule: an overlay's
  ``required_capabilities`` (and any station-required capabilities) must ALL be
  present on the worker, otherwise dispatch fails loud.

"Always dynamic": workers and overlays are discovered from files at runtime;
there is no hardcoded enumeration of worker ids or overlay ids. The file system
(packaged defaults + user overrides) is the single source of truth.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, field_validator

from tokenpak import _paths

from ..models.common import DispatchBaseModel, _validate_capability_list
from ..models.worker import DispatchWorker

# Packaged defaults ship beside this module. Worker profiles live directly in
# the registry package; overlays live in the ``overlays/`` subdirectory so they
# mirror the user install layout (``~/.tpk/dispatch/overlays/``).
_REGISTRY_DIR = Path(__file__).resolve().parent
_PACKAGED_OVERLAY_DIR = _REGISTRY_DIR / "overlays"

# Discovery globs — the file naming convention is the single source of truth, so
# adding a profile/overlay file is all it takes to register it (no code edit).
_WORKER_GLOB = "worker.*.yaml"
_OVERLAY_GLOB = "overlay.*.yaml"

# On-disk root for user-supplied overlays. Resolved through the canonical path
# helper so the location is never hardcoded (it follows ``TOKENPAK_HOME`` /
# ``~/.tpk`` / legacy resolution).
_USER_OVERLAY_PARTS: tuple[str, ...] = ("dispatch", "overlays")


def user_overlay_dir() -> Path:
    """Return the user overlay directory (``<tokenpak-home>/dispatch/overlays/``).

    Pure path resolution via :func:`tokenpak._paths.under`; the directory is not
    required to exist (the loader falls back to packaged defaults when absent).
    """

    return _paths.under(*_USER_OVERLAY_PARTS)


class WorkerProfileError(ValueError):
    """Raised when a worker YAML profile cannot be loaded or validated.

    Subclasses :class:`ValueError`; the underlying cause (an unknown-capability
    rejection, a malformed YAML mapping, a duplicate id) is chained via
    ``__cause__``.
    """


class OverlayError(ValueError):
    """Raised when an overlay YAML file cannot be loaded or validated."""


class RouteBindError(ValueError):
    """Raised when an overlay/station cannot bind to a worker.

    Carries the missing capabilities so the dispatcher can report exactly which
    required capability the worker lacks.
    """

    def __init__(self, worker_id: str, overlay_id: str | None, missing: Iterable[str]) -> None:
        self.worker_id = worker_id
        self.overlay_id = overlay_id
        self.missing = sorted(set(missing))
        target = f"overlay {overlay_id!r}" if overlay_id else "the route"
        super().__init__(
            f"cannot bind {target} to worker {worker_id!r}: worker is missing "
            f"required capabilities {self.missing!r}. Route binding requires every "
            "overlay/station required capability to be present on the worker "
            "(capability intersection)."
        )


class PromptOverlay(DispatchBaseModel):
    """A prompt overlay record.

    Overlays are *additive deltas* to a base worker prompt, never full
    replacements: ``mode`` is fixed to ``"additive"``. ``required_capabilities``
    is registry-bound (validated against the capability registry) and is
    intersected against the bound worker's capabilities at route-binding time.
    """

    id: str = Field(description='e.g. "overlay.code_builder.v1"')
    applies_to_role: str = Field(description='e.g. "builder"')
    mode: Literal["additive"] = "additive"

    instructions: list[str] = Field(
        default_factory=list,
        description="additive directives appended after the base worker prompt",
    )
    required_capabilities: list[str] = Field(default_factory=list, description="registry-bound")

    @field_validator("required_capabilities")
    @classmethod
    def _check_capabilities(cls, value: list[str]) -> list[str]:
        return _validate_capability_list(value)


def _read_yaml_mapping(path: Path, error_cls: type[ValueError]) -> dict[str, Any]:
    """Load ``path`` as a YAML mapping, raising ``error_cls`` on any problem."""

    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:  # pragma: no cover - exercised via tests
        raise error_cls(f"failed to read overlay/worker YAML {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise error_cls(f"expected a YAML mapping in {path}, got {type(raw).__name__}")
    return raw


class DispatchWorkerRegistry:
    """Loads and indexes worker profiles from one or more directories.

    Workers are discovered by glob (``worker.*.yaml``), so dropping a new
    profile file into the registry directory registers it with no code change.
    Each profile is parsed into a :class:`DispatchWorker`; the model's field
    validator rejects unknown capability strings at load time (fail-loud), and
    the registry re-raises that as :class:`WorkerProfileError` with the
    offending file path attached.
    """

    def __init__(self, workers: dict[str, DispatchWorker]) -> None:
        self._workers = workers

    @classmethod
    def from_dir(cls, directory: Path | None = None) -> "DispatchWorkerRegistry":
        """Build a registry from every ``worker.*.yaml`` in ``directory``.

        Defaults to the packaged registry directory shipped beside this module.
        Fail-loud on a duplicate worker id across files.
        """

        source = directory if directory is not None else _REGISTRY_DIR
        workers: dict[str, DispatchWorker] = {}
        for path in sorted(source.glob(_WORKER_GLOB)):
            worker = cls._load_worker(path)
            if worker.id in workers:
                raise WorkerProfileError(
                    f"duplicate worker id {worker.id!r} (second definition in {path})"
                )
            workers[worker.id] = worker
        return cls(workers)

    @staticmethod
    def _load_worker(path: Path) -> DispatchWorker:
        data = _read_yaml_mapping(path, WorkerProfileError)
        try:
            return DispatchWorker.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - re-wrapped with file context
            raise WorkerProfileError(f"invalid worker profile {path.name}: {exc}") from exc

    def ids(self) -> list[str]:
        """Return the registered worker ids (sorted)."""

        return sorted(self._workers)

    def all(self) -> list[DispatchWorker]:
        """Return all registered workers (ordered by id)."""

        return [self._workers[wid] for wid in self.ids()]

    def get(self, worker_id: str) -> DispatchWorker:
        """Return the worker with ``worker_id``; raise ``KeyError`` if absent."""

        try:
            return self._workers[worker_id]
        except KeyError as exc:
            raise KeyError(f"no worker {worker_id!r} in registry; known: {self.ids()}") from exc

    def for_role(self, role: str) -> list[DispatchWorker]:
        """Return every worker declaring ``role`` (dynamic role→worker lookup)."""

        return [w for w in self.all() if role in w.roles]


class OverlayLoader:
    """Loads prompt overlays from the user dir with packaged-default fallback.

    Resolution order per overlay id: a file in the **user** overlay directory
    (``~/.tpk/dispatch/overlays/``) shadows the **packaged** default of the same
    id. Both directories are discovered by glob (``overlay.*.yaml``); there is
    no hardcoded overlay enumeration.
    """

    def __init__(
        self,
        user_dir: Path | None = None,
        packaged_dir: Path | None = None,
    ) -> None:
        # ``user_dir is None`` => resolve the canonical path lazily so tests can
        # point ``TOKENPAK_HOME`` at a tmp dir. An explicit path (e.g. tmp_path)
        # is honoured verbatim.
        self._user_dir = user_dir
        self._packaged_dir = packaged_dir if packaged_dir is not None else _PACKAGED_OVERLAY_DIR

    def _resolve_user_dir(self) -> Path:
        return self._user_dir if self._user_dir is not None else user_overlay_dir()

    def _discover(self) -> dict[str, Path]:
        """Map overlay id → source path, user dir shadowing packaged defaults."""

        found: dict[str, Path] = {}
        # Packaged defaults first, then user overrides shadow them.
        for source in (self._packaged_dir, self._resolve_user_dir()):
            if not source.is_dir():
                continue
            for path in sorted(source.glob(_OVERLAY_GLOB)):
                overlay_id = path.name[: -len(".yaml")]
                found[overlay_id] = path
        return found

    def ids(self) -> list[str]:
        """Return the discoverable overlay ids (user + packaged, sorted)."""

        return sorted(self._discover())

    def load(self, overlay_id: str) -> PromptOverlay:
        """Load a single overlay by id (user dir shadows packaged default)."""

        sources = self._discover()
        path = sources.get(overlay_id)
        if path is None:
            raise OverlayError(f"no overlay {overlay_id!r}; known overlays: {sorted(sources)}")
        return self._load_overlay(path)

    def load_all(self) -> dict[str, PromptOverlay]:
        """Load every discoverable overlay into ``{id: PromptOverlay}``."""

        return {oid: self._load_overlay(p) for oid, p in self._discover().items()}

    @staticmethod
    def _load_overlay(path: Path) -> PromptOverlay:
        data = _read_yaml_mapping(path, OverlayError)
        try:
            return PromptOverlay.model_validate(data)
        except Exception as exc:  # noqa: BLE001 - re-wrapped with file context
            raise OverlayError(f"invalid overlay {path.name}: {exc}") from exc


def compose_prompt(worker: DispatchWorker, overlay: PromptOverlay | None = None) -> list[str]:
    """Concatenate the base worker prompt with an overlay's instructions.

    Returns ``worker.system_directives`` followed by ``overlay.instructions``.
    The base directives are returned in full and first: overlays are **additive**
    and can never remove or reorder a base directive. ``overlay=None`` yields the
    base prompt unchanged.
    """

    composed = list(worker.system_directives)
    if overlay is not None:
        composed.extend(overlay.instructions)
    return composed


def missing_capabilities(
    worker: DispatchWorker,
    required: Iterable[str],
) -> list[str]:
    """Return the required capabilities the worker does NOT have (sorted)."""

    have = set(worker.capabilities)
    return sorted({cap for cap in required if cap not in have})


def assert_route_binding(
    worker: DispatchWorker,
    overlay: PromptOverlay | None = None,
    station_required_capabilities: Iterable[str] | None = None,
) -> None:
    """Enforce the capability-intersection route-binding rule (fail-loud).

    Route binding requires that EVERY capability demanded by the overlay and by
    the station be present on the worker. If any are missing, dispatch is failed
    by raising :class:`RouteBindError` naming the missing capabilities.
    """

    required: set[str] = set()
    if overlay is not None:
        required.update(overlay.required_capabilities)
    if station_required_capabilities is not None:
        required.update(station_required_capabilities)

    missing = missing_capabilities(worker, required)
    if missing:
        overlay_id = overlay.id if overlay is not None else None
        raise RouteBindError(worker.id, overlay_id, missing)


def bind_overlay(
    worker: DispatchWorker,
    overlay: PromptOverlay,
    station_required_capabilities: Iterable[str] | None = None,
) -> list[str]:
    """Validate the binding then return the composed (base + overlay) prompt.

    Convenience wrapper: runs :func:`assert_route_binding` (raising on a
    capability gap) and, only if it passes, returns :func:`compose_prompt`.
    """

    assert_route_binding(worker, overlay, station_required_capabilities)
    return compose_prompt(worker, overlay)


def default_worker_registry() -> DispatchWorkerRegistry:
    """Return a registry loaded from the packaged worker profiles."""

    return DispatchWorkerRegistry.from_dir()


def default_overlay_loader() -> OverlayLoader:
    """Return an overlay loader using user-dir → packaged-default resolution."""

    return OverlayLoader()


__all__ = [
    "WorkerProfileError",
    "OverlayError",
    "RouteBindError",
    "PromptOverlay",
    "DispatchWorkerRegistry",
    "OverlayLoader",
    "user_overlay_dir",
    "compose_prompt",
    "missing_capabilities",
    "assert_route_binding",
    "bind_overlay",
    "default_worker_registry",
    "default_overlay_loader",
]
