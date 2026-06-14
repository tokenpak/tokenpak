# SPDX-License-Identifier: Apache-2.0
"""Environment-variable resolution order — specification + pure helper.

This module DEFINES the canonical precedence by which a ``TOKENPAK_*`` (or
provider) key's effective value is chosen across CLI flags, the process
environment, ``.env`` files, config files, and built-in defaults.

It is a **specification / pure helper only**. It is deliberately not imported
by — and not wired into — the live proxy/daemon startup path. The runtime
loaders (``tokenpak.core.config_loader`` and ``tokenpak.core.config``) keep
their existing behavior; adopting this order in the runtime is a separately
gated change. This module exists so the precedence is documented, importable,
and unit-testable in isolation.

Canonical precedence (highest wins)::

    1. CLI flag            (--config <path>, and per-key flags where defined)
    2. Process environment (os.environ — already-exported TOKENPAK_*/provider keys)
    3. Project .env        (./.env in the current working directory)
    4. User env file       (<tpk-home>/.env — mode 0600, gitignored)
    5. [legacy fallback]   (<legacy-home>/.env, ONLY behind a fallback flag)   -- HELD
    6. Project config      (a config file named by TOKENPAK_CONFIG, if set)
    7. User config         (<tpk-home>/config.yaml, then config.json toggles)
    8. Built-in defaults   (per-key defaults)

Process env above ``.env`` (layers 2 > 3 > 4) is deliberate: a value the
operator already exported for this process must win over a dotenv file, so
CI / sandbox / ``$TOKENPAK_HOME``-override invocations are never silently
overridden by an on-disk ``.env``. This matches the path resolver's own
"operator override is highest" philosophy and standard dotenv semantics
(a ``.env`` file does not clobber an already-set env var).

The legacy fallback (layer 5) reads a foreign/legacy ``.env`` and is therefore
credential-handling-adjacent. Its *position* in the order is specified here
(strictly below all first-class ``.env`` files, gated behind an opt-in flag,
off by default), but reading it is HELD: this module's resolver never consults
layer 5 unless the opt-in flag is explicitly set, and the default helper API
leaves the legacy reader disabled.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Optional

from tokenpak import _paths

# Opt-in flag that gates the layer-5 legacy ``.env`` fallback. Off by default.
# Defining the flag name here does NOT enable the fallback in the runtime — the
# runtime loaders do not import this module. It bounds the spec so the build
# that eventually adopts it has a single canonical flag name.
OPENCLAW_FALLBACK_FLAG = "TOKENPAK_OPENCLAW_FALLBACK"


class Layer(Enum):
    """The canonical resolution layers, ordered highest-precedence first.

    The integer value is the precedence rank (lower wins). Iterating the enum
    yields layers in precedence order.
    """

    CLI_FLAG = 1
    PROCESS_ENV = 2
    PROJECT_DOTENV = 3
    USER_DOTENV = 4
    LEGACY_DOTENV = 5  # HELD — only consulted behind the opt-in flag
    PROJECT_CONFIG = 6
    USER_CONFIG = 7
    DEFAULT = 8


# Human-facing description of each layer (for `config doctor`/`config env`).
LAYER_DESCRIPTIONS: dict[Layer, str] = {
    Layer.CLI_FLAG: "CLI flag (e.g. --config)",
    Layer.PROCESS_ENV: "process environment (os.environ)",
    Layer.PROJECT_DOTENV: "project .env (./.env)",
    Layer.USER_DOTENV: "user env file (<tpk-home>/.env)",
    Layer.LEGACY_DOTENV: "legacy .env fallback (opt-in, off by default)",
    Layer.PROJECT_CONFIG: "project config (TOKENPAK_CONFIG)",
    Layer.USER_CONFIG: "user config (<tpk-home>/config.yaml)",
    Layer.DEFAULT: "built-in default",
}


@dataclass(frozen=True)
class Resolution:
    """The outcome of resolving a single key.

    ``found`` is False (and ``value`` is the supplied default) when no layer
    supplied a value. ``layer`` records which layer won.
    """

    key: str
    value: Optional[str]
    layer: Layer
    found: bool


def precedence() -> list[Layer]:
    """Return the canonical layers in precedence order (highest first)."""
    return sorted(Layer, key=lambda layer: layer.value)


def describe() -> list[tuple[int, str, str]]:
    """Return ``(rank, layer_name, description)`` rows in precedence order.

    Pure, side-effect-free; suitable for rendering the load-order in
    ``config doctor`` / ``config env`` output and in generated docs.
    """
    return [
        (layer.value, layer.name, LAYER_DESCRIPTIONS[layer])
        for layer in precedence()
    ]


def parse_dotenv(text: str) -> dict[str, str]:
    """Parse ``.env`` file text into a mapping.

    Minimal, dependency-free dotenv parser: ``KEY=VALUE`` lines, ``#`` comments,
    blank lines ignored, optional ``export `` prefix stripped, surrounding single
    or double quotes stripped from the value. Whitespace around the key and the
    ``=`` is trimmed. Malformed lines (no ``=``) are skipped rather than raising
    — an unparseable hint should never crash resolution.
    """
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key] = value
    return result


def _read_dotenv(path: Path) -> dict[str, str]:
    """Read + parse a ``.env`` file, returning ``{}`` if absent/unreadable."""
    try:
        if not path.is_file():
            return {}
        return parse_dotenv(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


@dataclass
class LoadOrderResolver:
    """Resolve env keys through the canonical precedence (pure helper).

    Every input is injectable so the resolver is fully testable without
    touching the real environment, real home, or the network:

    - ``environ``      — process-env mapping (defaults to ``os.environ``).
    - ``cwd``          — directory whose ``./.env`` is the project dotenv.
    - ``home``         — ``<tpk-home>`` (defaults to ``_paths.home()``).
    - ``legacy_home``  — ``<legacy-home>`` (defaults to ``_paths.legacy_home()``).
    - ``config_lookup``— callable ``key -> Optional[str]`` for config-file
                         layers (6/7). Defaults to "no config layer" so this
                         module never imports the runtime loaders.
    - ``cli_flags``    — mapping for layer 1 (CLI-bound values).
    - ``openclaw_fallback`` — when True, layer 5 (legacy ``.env``) is consulted.
                         Defaults to honoring ``$TOKENPAK_OPENCLAW_FALLBACK`` in
                         ``environ`` (off unless explicitly truthy). Layer 5 is
                         HELD: it is never consulted with the default-off flag.

    The resolver does not write anything and creates no directories.
    """

    environ: Optional[dict] = None
    cwd: Optional[Path] = None
    home: Optional[Path] = None
    legacy_home: Optional[Path] = None
    config_lookup: Optional[Callable[[str], Optional[str]]] = None
    cli_flags: dict = field(default_factory=dict)
    openclaw_fallback: Optional[bool] = None

    def __post_init__(self) -> None:
        if self.environ is None:
            self.environ = dict(os.environ)
        if self.cwd is None:
            self.cwd = Path.cwd()
        if self.home is None:
            self.home = _paths.home()
        if self.legacy_home is None:
            self.legacy_home = _paths.legacy_home()
        if self.openclaw_fallback is None:
            self.openclaw_fallback = _truthy(
                self.environ.get(OPENCLAW_FALLBACK_FLAG, "")
            )

    # -- dotenv layer caches -------------------------------------------------

    def _project_dotenv(self) -> dict[str, str]:
        return _read_dotenv(Path(self.cwd) / ".env")

    def _user_dotenv(self) -> dict[str, str]:
        return _read_dotenv(Path(self.home) / ".env")

    def _legacy_dotenv(self) -> dict[str, str]:
        # HELD: never read unless the opt-in fallback is explicitly enabled.
        if not self.openclaw_fallback:
            return {}
        return _read_dotenv(Path(self.legacy_home) / ".env")

    # -- resolution ----------------------------------------------------------

    def resolve(self, key: str, default: Optional[str] = None) -> Resolution:
        """Resolve ``key`` to a :class:`Resolution` (first layer that hits)."""
        # Layer 1 — CLI flag.
        if key in self.cli_flags and self.cli_flags[key] is not None:
            return Resolution(key, self.cli_flags[key], Layer.CLI_FLAG, True)

        # Layer 2 — process env.
        if key in self.environ:
            return Resolution(key, self.environ[key], Layer.PROCESS_ENV, True)

        # Layers 3/4/5 — .env files (project, user, then HELD legacy).
        for layer, source in (
            (Layer.PROJECT_DOTENV, self._project_dotenv()),
            (Layer.USER_DOTENV, self._user_dotenv()),
            (Layer.LEGACY_DOTENV, self._legacy_dotenv()),
        ):
            if key in source:
                return Resolution(key, source[key], layer, True)

        # Layers 6/7 — config files (project, then user). The config_lookup
        # callable abstracts both; resolution never imports the runtime loader.
        if self.config_lookup is not None:
            cfg_val = self.config_lookup(key)
            if cfg_val is not None:
                return Resolution(key, cfg_val, Layer.USER_CONFIG, True)

        # Layer 8 — built-in default.
        return Resolution(key, default, Layer.DEFAULT, False)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Convenience: return only the resolved value (or *default*)."""
        return self.resolve(key, default).value

    def provenance(self, keys: Iterable[str]) -> dict[str, Resolution]:
        """Resolve many keys, returning ``{key: Resolution}`` (for `config env`)."""
        return {key: self.resolve(key) for key in keys}


# Type coercion mirrors the runtime loaders' ``_bool_env`` / cast semantics, so
# a later build can reuse it when wiring this order into the runtime.
_TRUTHY = {"1", "true", "yes", "on"}
_FALSY = {"0", "false", "no", "off", ""}


def _truthy(raw: str) -> bool:
    """Parse a flag-shaped env string as a boolean (dotenv/runtime-compatible)."""
    return str(raw).strip().lower() in _TRUTHY


def coerce(value: Optional[str], fmt: str) -> object:
    """Coerce a resolved string ``value`` to a schema ``Format``.

    Supported formats: ``int``, ``float``, ``bool``, ``csv``, ``string``/``path``/
    ``url`` (pass-through). Applied *after* layer selection (the precedence picks
    the raw string; coercion shapes it). ``None`` passes through unchanged so an
    unset key keeps its default.
    """
    if value is None:
        return None
    fmt = (fmt or "string").lower()
    if fmt == "int":
        return int(value)
    if fmt == "float":
        return float(value)
    if fmt == "bool":
        return _truthy(value)
    if fmt == "csv":
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


__all__ = [
    "OPENCLAW_FALLBACK_FLAG",
    "Layer",
    "LAYER_DESCRIPTIONS",
    "Resolution",
    "LoadOrderResolver",
    "precedence",
    "describe",
    "parse_dotenv",
    "coerce",
]
