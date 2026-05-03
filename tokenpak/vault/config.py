"""Vault config (v1 schema) — `~/.tokenpak/vault.yaml` loader/writer helpers.

Per VDS-01 (initiative 2026-04-28-tokenpak-vault-directory-scheduling).
This is the OSS foundation for vault-directory scheduling: a YAML
config that lists registered vault directories with optional schedule
hints, plus index health metadata persistence used by `tokenpak doctor`.

Schema v1
---------
::

    version: 1
    paths:
      - path: ~/projects/myapp
        schedule: "every 4h"   # OPTIONAL; parsed by VDS-02 scheduler
        last_indexed_ts: 1714400000  # written by index runs
        last_index_health: ok        # ok | stale | error
      - path: ~/notes
        # no schedule = manual-only

Defaults
--------
- Config path: ``~/.tokenpak/vault.yaml`` (override via ``TOKENPAK_VAULT_CONFIG``)
- Index health path: ``~/.tokenpak/vault-index-health.json``
- Index output path: ``~/vault/.tokenpak`` if ``~/vault/`` exists,
  else ``~/.tokenpak/vault_index/`` (override via ``TOKENPAK_VAULT_INDEX_PATH``)
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1
DEFAULT_CONFIG_REL = ".tokenpak/vault.yaml"
DEFAULT_HEALTH_REL = ".tokenpak/vault-index-health.json"


@dataclass
class VaultPathEntry:
    """Single registered vault directory."""

    path: str
    schedule: Optional[str] = None
    last_indexed_ts: Optional[int] = None
    last_index_health: Optional[str] = None  # ok | stale | error

    def expanded_path(self) -> str:
        return os.path.expanduser(self.path)


@dataclass
class VaultConfig:
    """v1 vault.yaml schema."""

    version: int = SCHEMA_VERSION
    paths: List[VaultPathEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "paths": [asdict(p) for p in self.paths],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VaultConfig":
        version = int(data.get("version", SCHEMA_VERSION))
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"vault.yaml version {version} unsupported "
                f"(this build supports v{SCHEMA_VERSION})"
            )
        paths_raw = data.get("paths") or []
        paths: List[VaultPathEntry] = []
        for p in paths_raw:
            if not isinstance(p, dict):
                continue
            if "path" not in p or not isinstance(p["path"], str):
                continue
            paths.append(
                VaultPathEntry(
                    path=p["path"],
                    schedule=p.get("schedule"),
                    last_indexed_ts=p.get("last_indexed_ts"),
                    last_index_health=p.get("last_index_health"),
                )
            )
        return cls(version=version, paths=paths)

    def find_path(self, path: str) -> Optional[VaultPathEntry]:
        """Find an entry by path (supports both raw + expanded form)."""
        target = os.path.realpath(os.path.expanduser(path))
        for entry in self.paths:
            if os.path.realpath(entry.expanded_path()) == target:
                return entry
            # Also match by raw path string in case ~ expansion differs
            if entry.path == path:
                return entry
        return None


def get_config_path() -> str:
    """Return the active vault.yaml path (env-overridable)."""
    override = os.environ.get("TOKENPAK_VAULT_CONFIG")
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser("~"), DEFAULT_CONFIG_REL)


def get_health_path() -> str:
    """Return the index health JSON path."""
    override = os.environ.get("TOKENPAK_VAULT_HEALTH_PATH")
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser("~"), DEFAULT_HEALTH_REL)


def get_default_index_path() -> str:
    """Return the index output dir.

    Order:
    1. ``TOKENPAK_VAULT_INDEX_PATH`` env override
    2. ``~/vault/.tokenpak`` if ``~/vault/`` exists (proxy-compatible default)
    3. ``~/.tokenpak/vault_index/``
    """
    override = os.environ.get("TOKENPAK_VAULT_INDEX_PATH")
    if override:
        return os.path.expanduser(override)
    home = os.path.expanduser("~")
    vault_dir = os.path.join(home, "vault")
    if os.path.isdir(vault_dir):
        return os.path.join(vault_dir, ".tokenpak")
    return os.path.join(home, ".tokenpak", "vault_index")


def load_config(path: Optional[str] = None) -> VaultConfig:
    """Load vault.yaml. Returns empty default config if file absent."""
    cfg_path = path or get_config_path()
    if not os.path.exists(cfg_path):
        return VaultConfig()
    try:
        import yaml  # PyYAML; in tokenpak's existing deps
    except ImportError as e:
        raise RuntimeError(
            "PyYAML required to load vault.yaml; install with "
            "`pip install pyyaml`"
        ) from e
    with open(cfg_path, "r") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"vault.yaml at {cfg_path} is not a mapping")
    return VaultConfig.from_dict(data)


def save_config(config: VaultConfig, path: Optional[str] = None) -> str:
    """Atomic write: tmp + rename."""
    cfg_path = path or get_config_path()
    os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
    try:
        import yaml
    except ImportError as e:
        raise RuntimeError("PyYAML required to write vault.yaml") from e
    data = config.to_dict()
    tmp = f"{cfg_path}.tmp.{os.getpid()}.{int(time.time())}"
    with open(tmp, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False)
    os.replace(tmp, cfg_path)
    return cfg_path


def load_health() -> Dict[str, Any]:
    """Load index health metadata. Returns empty dict if absent."""
    p = get_health_path()
    if not os.path.exists(p):
        return {}
    try:
        with open(p, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def update_health(path: str, status: str, ts: Optional[int] = None,
                  notes: Optional[str] = None) -> None:
    """Atomic update of health metadata for a single registered path."""
    if status not in ("ok", "stale", "error"):
        raise ValueError(f"invalid health status: {status!r}")
    health = load_health()
    health[os.path.realpath(os.path.expanduser(path))] = {
        "status": status,
        "ts": ts if ts is not None else int(time.time()),
        "notes": notes,
    }
    p = get_health_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = f"{p}.tmp.{os.getpid()}.{int(time.time())}"
    with open(tmp, "w") as f:
        json.dump(health, f, indent=2)
    os.replace(tmp, p)


__all__ = [
    "SCHEMA_VERSION",
    "VaultConfig",
    "VaultPathEntry",
    "get_config_path",
    "get_health_path",
    "get_default_index_path",
    "load_config",
    "save_config",
    "load_health",
    "update_health",
]
