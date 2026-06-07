# SPDX-License-Identifier: Apache-2.0
"""TokenPak vault config — ``~/.tokenpak/vault.yaml`` schema v1.

The vault config registers directories that ``tokenpak index`` knows about. It
is consumed by:

* ``tokenpak index --reindex-all``                 (this module + cli)
* ``tokenpak index --reindex-path <path>``         (this module + cli)
* ``tokenpak doctor`` staleness check
* paid ``tokenpak vault add/list/remove/reindex``

Schema v1 (``~/.tokenpak/vault.yaml``)::

    version: 1
    paths:
      - path: /abs/path/to/dir
        schedule: "every 6 hours"          # optional; raw grammar string
        expected_interval_seconds: 21600   # optional; doctor staleness threshold input
        last_indexed: "2026-04-28T18:00:00Z"  # optional; updated by reindex flags
        last_index_status: "ok"            # optional; ok | failed | running
        last_index_duration_ms: 1234       # optional; updated by reindex flags
        last_index_files: 42               # optional; updated by reindex flags

Env overrides:

* ``TOKENPAK_VAULT_CONFIG``      — path to the YAML file itself.
* ``TOKENPAK_VAULT_INDEX_PATH``  — vault index output directory; falls back to
  ``~/vault/.tokenpak`` (proxy-compatible default per spec).

This module is OSS — no license check. It is the single source of truth for
*which* directories tokenpak indexes; the paid scheduler writes to
the same file.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def default_config_path() -> Path:
    """Return the canonical ``vault.yaml`` path, honoring TOKENPAK_VAULT_CONFIG."""
    override = os.environ.get("TOKENPAK_VAULT_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".tokenpak" / "vault.yaml"


def default_index_path() -> Path:
    """Return the vault index output directory.

    Resolution order:
      1. ``$TOKENPAK_VAULT_INDEX_PATH``
      2. proxy-compatible default ``~/vault/.tokenpak``
    """
    override = os.environ.get("TOKENPAK_VAULT_INDEX_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / "vault" / ".tokenpak"


# ---------------------------------------------------------------------------
# Schema dataclasses
# ---------------------------------------------------------------------------

@dataclass
class VaultPathEntry:
    """One registered directory in ``vault.yaml``."""

    path: str
    schedule: Optional[str] = None
    expected_interval_seconds: Optional[int] = None
    last_indexed: Optional[str] = None
    last_index_status: Optional[str] = None
    last_index_duration_ms: Optional[int] = None
    last_index_files: Optional[int] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # Drop None values so the YAML stays readable.
        return {k: v for k, v in d.items() if v is not None}


@dataclass
class VaultConfig:
    """Top-level ``vault.yaml`` document."""

    version: int = SCHEMA_VERSION
    paths: list[VaultPathEntry] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "paths": [p.to_dict() for p in self.paths],
        }

    def find(self, path: str) -> Optional[VaultPathEntry]:
        """Return the registered entry for ``path`` (normalized), or None."""
        target = _normalize(path)
        for entry in self.paths:
            if _normalize(entry.path) == target:
                return entry
        return None


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def _normalize(path: str) -> str:
    """Normalize a directory path for equality comparisons."""
    return str(Path(path).expanduser().resolve(strict=False))


def _load_yaml_text(text: str) -> dict:
    """Parse YAML text, falling back to JSON if PyYAML is missing."""
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text)
    except ImportError:
        import json

        data = json.loads(text) if text.strip() else {}
    return data or {}


def _dump_yaml_text(data: dict) -> str:
    """Serialize ``data`` as YAML; fall back to JSON if PyYAML is missing."""
    try:
        import yaml  # type: ignore

        return yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    except ImportError:
        import json

        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def load(path: Optional[Path] = None) -> VaultConfig:
    """Load the vault config. Returns an empty v1 config if the file is absent."""
    cfg_path = Path(path) if path else default_config_path()
    if not cfg_path.exists():
        return VaultConfig()

    raw = _load_yaml_text(cfg_path.read_text(encoding="utf-8"))
    version = int(raw.get("version", SCHEMA_VERSION))
    if version != SCHEMA_VERSION:
        raise ValueError(
            f"vault.yaml schema version {version} not supported "
            f"(this build expects v{SCHEMA_VERSION})"
        )

    entries: list[VaultPathEntry] = []
    for item in raw.get("paths", []) or []:
        if not isinstance(item, dict) or "path" not in item:
            raise ValueError(f"vault.yaml: invalid path entry: {item!r}")
        entries.append(
            VaultPathEntry(
                path=str(item["path"]),
                schedule=_opt_str(item.get("schedule")),
                expected_interval_seconds=_opt_int(item.get("expected_interval_seconds")),
                last_indexed=_opt_str(item.get("last_indexed")),
                last_index_status=_opt_str(item.get("last_index_status")),
                last_index_duration_ms=_opt_int(item.get("last_index_duration_ms")),
                last_index_files=_opt_int(item.get("last_index_files")),
            )
        )

    return VaultConfig(version=version, paths=entries)


def save(cfg: VaultConfig, path: Optional[Path] = None) -> Path:
    """Write the vault config to disk atomically. Returns the written path."""
    cfg_path = Path(path) if path else default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    text = _dump_yaml_text(cfg.to_dict())
    tmp = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, cfg_path)
    return cfg_path


# ---------------------------------------------------------------------------
# Mutators (used by paid vault add/remove and the OSS reindex flags)
# ---------------------------------------------------------------------------

def add_path(
    cfg: VaultConfig,
    path: str,
    schedule: Optional[str] = None,
    expected_interval_seconds: Optional[int] = None,
) -> VaultConfig:
    """Idempotently register ``path``. Updates schedule fields if entry exists."""
    existing = cfg.find(path)
    if existing is not None:
        if schedule is not None:
            existing.schedule = schedule
        if expected_interval_seconds is not None:
            existing.expected_interval_seconds = expected_interval_seconds
        return cfg

    cfg.paths.append(
        VaultPathEntry(
            path=_normalize(path),
            schedule=schedule,
            expected_interval_seconds=expected_interval_seconds,
        )
    )
    return cfg


def remove_path(cfg: VaultConfig, path: str) -> bool:
    """Remove ``path`` from the config. Returns True if anything was removed."""
    target = _normalize(path)
    before = len(cfg.paths)
    cfg.paths = [p for p in cfg.paths if _normalize(p.path) != target]
    return len(cfg.paths) != before


def update_index_health(
    cfg: VaultConfig,
    path: str,
    *,
    status: str,
    duration_ms: Optional[int] = None,
    files_indexed: Optional[int] = None,
    indexed_at: Optional[datetime] = None,
) -> Optional[VaultPathEntry]:
    """Stamp the per-path index health metadata. Returns the entry, or None.

    The doctor reads ``last_indexed`` + ``expected_interval_seconds``
    to decide whether an index is stale.
    """
    entry = cfg.find(path)
    if entry is None:
        return None
    when = indexed_at or datetime.now(timezone.utc)
    entry.last_indexed = when.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    entry.last_index_status = status
    if duration_ms is not None:
        entry.last_index_duration_ms = int(duration_ms)
    if files_indexed is not None:
        entry.last_index_files = int(files_indexed)
    return entry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _opt_str(v: Any) -> Optional[str]:
    if v is None:
        return None
    return str(v)


def _opt_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def iter_registered_paths(cfg: VaultConfig) -> Iterable[VaultPathEntry]:
    """Yield each registered VaultPathEntry. Trivial helper for callers."""
    return iter(cfg.paths)
