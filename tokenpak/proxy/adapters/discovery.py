# SPDX-License-Identifier: Apache-2.0
"""Plugin discovery for FormatAdapter — entry points + filesystem drop-in.

Two ways third parties extend TokenPak with new format adapters:

1. **Python entry points** (the packaged path). A distribution declares::

       [project.entry-points."tokenpak.format_adapters"]
       my-provider = "my_pkg.adapters:MyProviderAdapter"

   ``pip install my_pkg`` then makes the adapter available without any
   edit to TokenPak.

2. **Filesystem drop-in** (the prototyping path). A user writes a
   ``.py`` file under ``~/.tokenpak/adapters/`` containing one or more
   :class:`FormatAdapter` subclasses. TokenPak imports the file and
   registers the subclasses on startup.

Both paths flow through this module's :func:`register_discovered`,
which:

  - Validates each candidate (subclass check, abstract-method check,
    TIP capability label pattern).
  - Skips bad candidates with a logged WARNING — startup never crashes
    from a misbehaving plugin.
  - Honors an explicit ``priority`` class attribute (default 100) so
    plugins slot between ``AnthropicAdapter`` (300) and
    ``PassthroughAdapter`` (0).
  - Skips duplicate ``source_format`` registrations — first wins, with
    a logged WARNING on collision.

Opt-out
-------

Discovery runs by default. Set ``TOKENPAK_DISABLE_ADAPTER_PLUGINS=1``
to skip both paths (e.g. in the conformance suite or CI environments
where third-party plugins must not affect the test surface).
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import os
import re
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import List, Optional, Tuple

from .base import FormatAdapter
from .registry import AdapterRegistry

logger = logging.getLogger(__name__)


_ENTRY_POINT_GROUP = "tokenpak.format_adapters"
_DISABLE_ENV = "TOKENPAK_DISABLE_ADAPTER_PLUGINS"
_DEFAULT_DROPIN_DIR = Path.home() / ".tokenpak" / "adapters"
_DEFAULT_PLUGIN_PRIORITY = 100

# TIP capability label pattern (matches the registry schema rule).
_TIP_LABEL_RE = re.compile(r"^(tip|ext)\.[a-z0-9._-]+$")


def discovery_enabled() -> bool:
    """``True`` when adapter-plugin discovery should run.

    Default ON (drop-in adapters work without ceremony). Set
    ``TOKENPAK_DISABLE_ADAPTER_PLUGINS=1`` to opt out.
    """
    return os.environ.get(_DISABLE_ENV, "").strip().lower() not in {
        "1", "true", "yes",
    }


def _ep_dist(ep: EntryPoint) -> str:
    """Best-effort distribution name for diagnostics."""
    dist = getattr(ep, "dist", None)
    if dist is not None:
        name = getattr(dist, "name", None) or getattr(dist, "metadata", {}).get("Name")
        if name:
            return str(name)
    return "<unknown>"


def _validate_adapter_class(cls, source: str) -> bool:
    """Return ``True`` if ``cls`` is a registrable FormatAdapter subclass.

    Logs a WARNING with the rejection reason on failure. Validation
    rules:

      - Must be a class, must subclass FormatAdapter (and not be it).
      - Must declare a non-empty ``source_format`` distinct from
        ``"unknown"``.
      - All declared capability labels must match the TIP vocabulary
        pattern (``tip.<...>`` or ``ext.<...>``).
    """
    if not inspect.isclass(cls):
        logger.warning(
            "tokenpak adapter-plugin %r (from %s) is not a class; skipped.",
            getattr(cls, "__name__", repr(cls)), source,
        )
        return False
    if not issubclass(cls, FormatAdapter) or cls is FormatAdapter:
        logger.warning(
            "tokenpak adapter-plugin %r (from %s) is not a FormatAdapter "
            "subclass; skipped.",
            cls.__name__, source,
        )
        return False
    fmt = getattr(cls, "source_format", None)
    if not isinstance(fmt, str) or not fmt or fmt == "unknown":
        logger.warning(
            "tokenpak adapter-plugin %r (from %s) has no source_format; "
            "skipped.",
            cls.__name__, source,
        )
        return False
    caps = getattr(cls, "capabilities", frozenset())
    bad_labels = [
        label for label in caps if not _TIP_LABEL_RE.match(str(label))
    ]
    if bad_labels:
        logger.warning(
            "tokenpak adapter-plugin %r (from %s) declares non-TIP capability "
            "labels %r; skipped. Use ``tip.<group>.<feature>`` or "
            "``ext.<vendor>.<feature>``.",
            cls.__name__, source, bad_labels,
        )
        return False
    return True


def _adapter_priority(cls) -> int:
    """Read the optional ``priority`` class attribute (default 100)."""
    pri = getattr(cls, "priority", None)
    if isinstance(pri, int):
        return pri
    return _DEFAULT_PLUGIN_PRIORITY


def discover_entry_point_adapters() -> List[Tuple[FormatAdapter, int, str]]:
    """Walk ``tokenpak.format_adapters`` entry points + return instances.

    Returns ``[(adapter_instance, priority, source_label), ...]``. The
    source label is human-readable (``"entry-point: <distribution>"``)
    and used for diagnostics + collision warnings.
    """
    out: List[Tuple[FormatAdapter, int, str]] = []
    try:
        eps = entry_points(group=_ENTRY_POINT_GROUP)
    except Exception as exc:
        logger.warning(
            "tokenpak adapter-plugin discovery failed at entry_points() "
            "call: %s: %s",
            type(exc).__name__, exc,
        )
        return out

    for ep in eps:
        try:
            target = ep.load()
        except Exception as exc:
            logger.warning(
                "tokenpak adapter-plugin %r (from %s) failed to load: %s: %s",
                ep.name, _ep_dist(ep), type(exc).__name__, exc,
            )
            continue
        if not _validate_adapter_class(target, f"entry-point: {_ep_dist(ep)}"):
            continue
        try:
            instance = target()
        except Exception as exc:
            logger.warning(
                "tokenpak adapter-plugin %r (from %s) failed to instantiate: "
                "%s: %s",
                ep.name, _ep_dist(ep), type(exc).__name__, exc,
            )
            continue
        out.append(
            (instance, _adapter_priority(target), f"entry-point: {_ep_dist(ep)}")
        )
    return out


def discover_filesystem_adapters(
    directory: Optional[Path] = None,
) -> List[Tuple[FormatAdapter, int, str]]:
    """Import ``*.py`` files in ``directory`` + return adapter instances.

    Each file is imported as a sandboxed module. Every top-level class
    that subclasses :class:`FormatAdapter` (and isn't FormatAdapter
    itself) is validated + instantiated.

    Defaults to ``~/.tokenpak/adapters``. Missing directory is a no-op.
    Files starting with ``_`` are skipped (Python convention for
    private / disabled).
    """
    out: List[Tuple[FormatAdapter, int, str]] = []
    if directory is None:
        directory = _DEFAULT_DROPIN_DIR
    if not directory.is_dir():
        return out

    for path in sorted(directory.glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = f"tokenpak._dropin_adapter_{path.stem}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            logger.warning(
                "tokenpak adapter-plugin: could not build import spec for %s; "
                "skipped.",
                path,
            )
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            logger.warning(
                "tokenpak adapter-plugin %s failed to import: %s: %s",
                path, type(exc).__name__, exc,
            )
            continue

        for name, obj in inspect.getmembers(module, inspect.isclass):
            # Only top-level classes defined in this module — skip
            # imports (e.g. FormatAdapter itself, or other adapters
            # imported for reference).
            if obj.__module__ != module_name:
                continue
            source = f"file: {path}"
            if not _validate_adapter_class(obj, source):
                continue
            try:
                instance = obj()
            except Exception as exc:
                logger.warning(
                    "tokenpak adapter-plugin %r (from %s) failed to "
                    "instantiate: %s: %s",
                    name, source, type(exc).__name__, exc,
                )
                continue
            out.append((instance, _adapter_priority(obj), source))

    return out


def register_discovered(
    registry: AdapterRegistry,
    *,
    include_entry_points: bool = True,
    include_filesystem: bool = True,
    filesystem_dir: Optional[Path] = None,
) -> int:
    """Discover + register plugin adapters into ``registry``.

    Returns the count of newly-registered adapters. Built-in adapters
    already registered take precedence on ``source_format`` collision
    (the registry's ``register`` is append-only; we add a separate
    pre-check here to log the collision before silently dropping).

    Honors :func:`discovery_enabled` — short-circuits to 0 when
    ``TOKENPAK_DISABLE_ADAPTER_PLUGINS=1``.
    """
    if not discovery_enabled():
        logger.debug("tokenpak adapter-plugin discovery disabled via env")
        return 0

    candidates: List[Tuple[FormatAdapter, int, str]] = []
    if include_entry_points:
        candidates.extend(discover_entry_point_adapters())
    if include_filesystem:
        candidates.extend(discover_filesystem_adapters(filesystem_dir))

    if not candidates:
        return 0

    existing_formats = {a.source_format for a in registry.adapters()}
    seen_now: dict = {}
    registered = 0

    for instance, priority, source in candidates:
        fmt = instance.source_format
        if fmt in existing_formats:
            logger.warning(
                "tokenpak adapter-plugin %r (from %s) collides with built-in "
                "format %r; built-in wins, plugin skipped.",
                type(instance).__name__, source, fmt,
            )
            continue
        prior = seen_now.get(fmt)
        if prior is not None:
            logger.warning(
                "tokenpak adapter-plugin %r (from %s) collides with already-"
                "discovered plugin (from %s) for format %r; first wins, "
                "this one skipped.",
                type(instance).__name__, source, prior, fmt,
            )
            continue
        registry.register(instance, priority=priority)
        seen_now[fmt] = source
        registered += 1
        logger.info(
            "tokenpak adapter-plugin: registered %r (format=%s, priority=%d, "
            "from %s)",
            type(instance).__name__, fmt, priority, source,
        )

    return registered


__all__ = [
    "discovery_enabled",
    "discover_entry_point_adapters",
    "discover_filesystem_adapters",
    "register_discovered",
]
