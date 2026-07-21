# SPDX-License-Identifier: Apache-2.0
"""`tokenpak permissions` — permission tiers for Claude Code + Codex.

Two deliberately separate concepts (do NOT merge them back together):

1. **Persistent trust level** — the tier (``strict`` / ``standard`` /
   ``auto``) written into the client's own persistent config
   (``~/.claude/settings.json`` ``permissions.defaultMode`` for Claude
   Code; top-level ``approval_policy`` + ``sandbox_mode`` in
   ``~/.codex/config.toml`` for Codex). This changes how the client
   behaves on EVERY launch, however it is started.

2. **Launcher permission default** — a per-client TokenPak-owned mode
   stored in ``~/.config/tokenpak/permissions.toml`` and consumed ONLY by
   ``tokenpak claude`` / ``tokenpak codex``. Non-inherit modes inject
   session-only permission arguments at exec time and print a mandatory
   stderr warning. Client config files are never changed by launcher
   modes; launching the client directly is unaffected.

Tier mapping (canonical):

    ==========  ============================  =======================================
    Tier        Claude Code                   Codex CLI
    ==========  ============================  =======================================
    strict      defaultMode = default         approval_policy=on-request, read-only
    standard    defaultMode = acceptEdits     approval_policy=on-request, workspace-write
    auto        defaultMode = bypassPermissions  approval_policy=never, workspace-write
    fleet       (persistent tier unchanged)   (persistent tier unchanged)
    ==========  ============================  =======================================

``fleet`` is NOT a persistent tier value. It remains a compatibility
alias for setting both launcher defaults to ``full-bypass``. The legacy
``fleet_mode = true|false`` field remains in the state file so older
TokenPak versions fail closed for client-specific modes. No display
surface may ever label a persistent tier as "fleet".

Write discipline (additive-only):
    - Backup before any client-config write (``*.bak`` next to the file).
    - Only the managed keys above are ever touched. ``permissions.allow``
      / ``deny`` / ``ask`` arrays, env blocks, MCP config, profiles and
      every other key are preserved verbatim.
    - ``reset`` is scoped: it removes only the managed keys (and resets
      launcher defaults). Full-file restore stays available via the printed
      ``.bak`` rollback path, but reset itself never restores from
      backup (that would clobber unrelated user edits made since apply).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Canonical tier tables
# ---------------------------------------------------------------------------

#: Tiers that persist into client config. "fleet" is intentionally absent.
PERSISTENT_TIERS: tuple[str, ...] = ("strict", "standard", "auto")

#: Tiers accepted by `permissions set` / `integrate --tier`.
ALL_TIERS: tuple[str, ...] = ("strict", "standard", "auto", "fleet")

DEFAULT_TIER = "standard"

CLIENTS: tuple[str, ...] = ("claude-code", "codex")

#: tier -> Claude Code permissions.defaultMode value
CLAUDE_TIER_TO_MODE: dict[str, str] = {
    "strict": "default",
    "standard": "acceptEdits",
    "auto": "bypassPermissions",
}
CLAUDE_MODE_TO_TIER: dict[str, str] = {v: k for k, v in CLAUDE_TIER_TO_MODE.items()}

#: tier -> Codex top-level (approval_policy, sandbox_mode) values
CODEX_TIER_TO_SETTINGS: dict[str, dict[str, str]] = {
    "strict": {"approval_policy": "on-request", "sandbox_mode": "read-only"},
    "standard": {"approval_policy": "on-request", "sandbox_mode": "workspace-write"},
    "auto": {"approval_policy": "never", "sandbox_mode": "workspace-write"},
}
CODEX_SETTINGS_TO_TIER: dict[tuple[str, str], str] = {
    (v["approval_policy"], v["sandbox_mode"]): k
    for k, v in CODEX_TIER_TO_SETTINGS.items()
}

TIER_DESCRIPTIONS: dict[str, str] = {
    "strict": "prompts for everything; read-only sandbox (exploring, untrusted code)",
    "standard": "accept file edits; workspace-write sandbox (day-to-day, default)",
    "auto": (
        "no prompts; Claude needs external isolation, Codex keeps workspace sandbox"
    ),
    "fleet": "legacy alias: full-bypass for both TokenPak launchers",
}

#: Launcher-scoped defaults. These never persist into client config files.
_LAUNCHER_MODES: tuple[str, ...] = (
    "inherit",
    "approval-bypass",
    "sandbox-bypass",
    "full-bypass",
)
_DEFAULT_LAUNCHER_MODE = "inherit"

# Claude Code exposes a combined permission bypass, but no launcher argument
# that disables only approvals or only its sandbox.
_LAUNCHER_MODE_SUPPORT: dict[str, frozenset[str]] = {
    "claude-code": frozenset({"inherit", "full-bypass"}),
    "codex": frozenset(_LAUNCHER_MODES),
}

_CODEX_MANAGED_KEYS: tuple[str, ...] = ("approval_policy", "sandbox_mode")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class TierApplyResult:
    """Outcome of one tier apply/reset operation against one client."""

    ok: bool
    summary: str
    changes: list[str] = field(default_factory=list)
    backup_path: Optional[str] = None
    error: Optional[str] = None
    rollback_cmd: Optional[str] = None


# ---------------------------------------------------------------------------
# TokenPak launcher state (~/.config/tokenpak/permissions.toml)
# ---------------------------------------------------------------------------
# User-owned path only (never /etc/ or any system path). Schema is a
# launcher knob plus a per-client record of the tier TokenPak last applied
# (used purely for external-modification detection in `doctor`):
#
#     schema_version = 2
#
#     [launcher]
#     fleet_mode = false
#     set_at = "2026-06-10T00:00:00+00:00"
#     set_by = "tokenpak permissions launcher approval-bypass --client codex"
#
#     [launcher.modes]
#     "claude-code" = "inherit"
#     codex = "approval-bypass"
#
#     [tiers]
#     claude-code = "standard"
#     codex = "standard"
#
# The file must never contain a `tier = "fleet"` key. A legacy file with
# only ``fleet_mode = true`` resolves to full-bypass for both launchers.
# New writers keep that boolean true only when both clients use full-bypass,
# so older readers never broaden a client-specific mode.


def _state_path() -> Path:
    return Path.home() / ".config" / "tokenpak" / "permissions.toml"


def _read_state_with_error() -> tuple[dict, Optional[str]]:
    """Parse launcher state and retain a safe, user-visible error note."""
    p = _state_path()
    if not p.exists():
        return {}, None
    try:
        state = tomllib.loads(p.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return {}, "state root is not a TOML table"
        schema_version = state.get("schema_version")
        if schema_version is not None and (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version != 2
        ):
            return {}, f"unsupported launcher state schema_version {schema_version!r}"
        return state, None
    except Exception as exc:
        return {}, f"state file is invalid TOML ({type(exc).__name__})"


def _read_state() -> dict:
    """Parse launcher state. Returns an empty mapping on any read error."""
    state, _error = _read_state_with_error()
    return state


def _resolved_launcher_mode(state: dict, client: str) -> tuple[str, Optional[str]]:
    """Resolve one client mode from v2 state or the legacy compatibility boolean."""
    launcher = state.get("launcher", {})
    if not isinstance(launcher, dict):
        return _DEFAULT_LAUNCHER_MODE, "[launcher] is not a TOML table; using inherit"

    legacy_fleet = launcher.get("fleet_mode", False)
    if not isinstance(legacy_fleet, bool):
        return (
            _DEFAULT_LAUNCHER_MODE,
            "launcher.fleet_mode must be true or false; using inherit",
        )

    modes = launcher.get("modes")
    raw_mode: object = None
    if modes is not None:
        if not isinstance(modes, dict):
            return (
                _DEFAULT_LAUNCHER_MODE,
                "[launcher.modes] is not a TOML table; using inherit",
            )
        raw_mode = modes.get(client)

    if raw_mode is None:
        if legacy_fleet is True:
            return "full-bypass", None
        return _DEFAULT_LAUNCHER_MODE, None
    if not isinstance(raw_mode, str) or raw_mode not in _LAUNCHER_MODES:
        return (
            _DEFAULT_LAUNCHER_MODE,
            f"invalid stored mode {raw_mode!r}; using inherit",
        )
    if raw_mode not in _LAUNCHER_MODE_SUPPORT[client]:
        return (
            _DEFAULT_LAUNCHER_MODE,
            f"unsupported stored mode {raw_mode!r}; using inherit",
        )
    return raw_mode, None


def _get_launcher_mode_status(client: str) -> tuple[str, Optional[str]]:
    """Return ``(mode, warning)``; invalid state always fails closed."""
    if client not in CLIENTS:
        return _DEFAULT_LAUNCHER_MODE, f"unknown client {client!r}; using inherit"
    state, error = _read_state_with_error()
    if error:
        return _DEFAULT_LAUNCHER_MODE, f"{error}; using inherit"
    return _resolved_launcher_mode(state, client)


def _get_launcher_mode(client: str) -> str:
    """Return the safe launcher default for ``client``. Never raises."""
    try:
        return _get_launcher_mode_status(client)[0]
    except Exception:
        return _DEFAULT_LAUNCHER_MODE


def _write_state(state: dict) -> None:
    """Serialize the known v2 launcher state schema atomically (0644)."""
    launcher = state.get("launcher", {})
    if not isinstance(launcher, dict):
        launcher = {}
    tiers = state.get("tiers", {})
    resolved_modes = {client: _resolved_launcher_mode(state, client)[0] for client in CLIENTS}
    legacy_fleet = all(mode == "full-bypass" for mode in resolved_modes.values())

    lines = ["schema_version = 2", "", "[launcher]"]
    lines.append(f"fleet_mode = {'true' if legacy_fleet else 'false'}")
    if launcher.get("set_at"):
        lines.append(f"set_at = {json.dumps(str(launcher['set_at']))}")
    if launcher.get("set_by"):
        lines.append(f"set_by = {json.dumps(str(launcher['set_by']))}")
    lines.extend(
        [
            "",
            "# WARNING: non-inherit values affect only `tokenpak claude` /",
            "# `tokenpak codex` launches. approval-bypass runs without prompts inside",
            "# the remaining Codex sandbox. sandbox-bypass removes Codex sandboxing",
            "# and can combine with approval_policy=never. Claude Code supports only",
            "# inherit/full-bypass because bypassPermissions is itself a full bypass.",
            "# Use bypass modes only with trusted code and external isolation.",
            "[launcher.modes]",
        ]
    )
    for client in CLIENTS:
        lines.append(f"{json.dumps(client)} = {json.dumps(resolved_modes[client])}")
    if isinstance(tiers, dict) and tiers:
        lines.append("")
        lines.append("[tiers]")
        for client in sorted(tiers):
            tier = tiers[client]
            # Hard guard: the state file records persistent tiers only.
            if tier not in PERSISTENT_TIERS:
                continue
            lines.append(f'"{client}" = "{tier}"')
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=p.parent, delete=False, suffix=".tmp", encoding="utf-8"
    ) as f:
        f.write("\n".join(lines) + "\n")
        tmp = f.name
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o644)
    except Exception:
        pass


def fleet_mode_enabled() -> bool:
    """Compatibility API: true only when both clients use full-bypass."""
    try:
        return all(_get_launcher_mode(client) == "full-bypass" for client in CLIENTS)
    except Exception:
        return False


def set_fleet_mode(enabled: bool, source: str) -> None:
    """Compatibility API: set both clients to full-bypass or inherit."""
    mode = "full-bypass" if enabled else _DEFAULT_LAUNCHER_MODE
    _set_launcher_modes({client: mode for client in CLIENTS}, source)


def _set_launcher_modes(updates: dict[str, str], source: str) -> None:
    """Atomically persist validated per-client launcher defaults."""
    for client, mode in updates.items():
        if client not in CLIENTS:
            raise ValueError(f"unknown client {client!r}")
        if mode not in _LAUNCHER_MODES:
            raise ValueError(f"unknown launcher mode {mode!r}")
        if mode not in _LAUNCHER_MODE_SUPPORT[client]:
            raise ValueError(f"launcher mode {mode!r} is unsupported for {client}")

    state = _read_state()
    launcher = state.setdefault("launcher", {})
    if not isinstance(launcher, dict):
        launcher = {}
        state["launcher"] = launcher
    current = {client: _resolved_launcher_mode(state, client)[0] for client in CLIENTS}
    current.update(updates)
    launcher["modes"] = current
    launcher["fleet_mode"] = all(mode == "full-bypass" for mode in current.values())
    launcher["set_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    launcher["set_by"] = source
    _write_state(state)


def get_last_set_tier(client: str) -> Optional[str]:
    """Return the persistent tier TokenPak last applied for client, if any."""
    tier = _read_state().get("tiers", {}).get(client)
    return tier if tier in PERSISTENT_TIERS else None


def _record_last_set_tier(client: str, tier: str) -> None:
    if tier not in PERSISTENT_TIERS:  # never record "fleet" as a tier
        return
    state = _read_state()
    state.setdefault("tiers", {})[client] = tier
    _write_state(state)


def _clear_last_set_tier(client: str) -> None:
    state = _read_state()
    tiers = state.get("tiers", {})
    if client in tiers:
        tiers.pop(client, None)
        _write_state(state)


# ---------------------------------------------------------------------------
# Claude Code (~/.claude/settings.json) — managed key: permissions.defaultMode
# ---------------------------------------------------------------------------


def read_claude_tier() -> tuple[str, str]:
    """Return (tier_label, note) for the Claude Code persistent tier.

    tier_label is restricted to {strict, standard, auto, custom}. It is
    NEVER "fleet" — fleet is a launcher boolean, not a persistent tier.
    """
    from tokenpak.cli.commands.install import _read_settings

    settings = _read_settings()
    perms = settings.get("permissions", {})
    mode = perms.get("defaultMode") if isinstance(perms, dict) else None
    last = get_last_set_tier("claude-code")

    if mode is None:
        if last is not None:
            # We applied a tier; the key has since been removed externally.
            return "custom", f"(last set: {last}, modified externally)"
        return DEFAULT_TIER, "(default; not yet applied)"
    if mode in CLAUDE_MODE_TO_TIER:
        return CLAUDE_MODE_TO_TIER[mode], ""
    if last is not None:
        return "custom", f"(last set: {last}, modified externally)"
    return "custom", "(modified externally)"


def apply_claude_tier(tier: str, backup: bool = True) -> TierApplyResult:
    """Write the tier's defaultMode into ~/.claude/settings.json (additive)."""
    if tier not in PERSISTENT_TIERS:
        return TierApplyResult(
            ok=False,
            summary=f"'{tier}' is not a persistent tier",
            error="not_persistent_tier",
        )
    try:
        from tokenpak.cli.commands.install import (
            _atomic_write_settings,
            _backup_settings,
            _read_settings,
            _settings_path,
        )
    except Exception as exc:  # pragma: no cover — import failure
        return TierApplyResult(
            ok=False, summary="Claude Code install helpers unavailable", error=str(exc)
        )

    bak: Optional[Path] = None
    try:
        if backup:
            bak = _backup_settings()
        settings = _read_settings()
        perms = settings.setdefault("permissions", {})
        if not isinstance(perms, dict):
            return TierApplyResult(
                ok=False,
                summary="settings.json 'permissions' is not an object — refusing to touch.",
                error="permissions_not_dict",
                backup_path=str(bak) if bak else None,
            )
        prev = perms.get("defaultMode")
        new = CLAUDE_TIER_TO_MODE[tier]
        changes: list[str] = []
        if prev != new:
            perms["defaultMode"] = new
            _atomic_write_settings(settings)
            changes.append(
                f"permissions.defaultMode: {prev or '(unset)'} → {new} (tier: {tier})"
            )
        _record_last_set_tier("claude-code", tier)
        settings_p = _settings_path()
        rollback = f"cp {bak} {settings_p}" if bak else None
        summary = (
            f"Claude Code persistent tier set to {tier}."
            if changes
            else f"Claude Code already at tier {tier} — no changes."
        )
        return TierApplyResult(
            ok=True,
            summary=summary,
            changes=changes,
            backup_path=str(bak) if bak else None,
            rollback_cmd=rollback,
        )
    except Exception as exc:
        return TierApplyResult(
            ok=False,
            summary="Claude Code tier apply failed.",
            error=str(exc),
            backup_path=str(bak) if bak else None,
        )


def reset_claude_tier() -> TierApplyResult:
    """Scoped reset: remove ONLY permissions.defaultMode from settings.json.

    Never touches allow/deny/ask arrays, env, mcpServers, hooks or any
    other key, and never restores from a full ``.bak``.
    """
    try:
        from tokenpak.cli.commands.install import (
            _atomic_write_settings,
            _backup_settings,
            _read_settings,
            _settings_path,
        )
    except Exception as exc:  # pragma: no cover
        return TierApplyResult(
            ok=False, summary="Claude Code install helpers unavailable", error=str(exc)
        )

    bak: Optional[Path] = None
    try:
        settings_p = _settings_path()
        if not settings_p.exists():
            _clear_last_set_tier("claude-code")
            return TierApplyResult(
                ok=True, summary="Claude Code settings.json absent — nothing to reset."
            )
        bak = _backup_settings()
        settings = _read_settings()
        perms = settings.get("permissions")
        changes: list[str] = []
        if isinstance(perms, dict) and "defaultMode" in perms:
            prev = perms.pop("defaultMode", None)
            _atomic_write_settings(settings)
            changes.append(f"removed permissions.defaultMode (was: {prev})")
        _clear_last_set_tier("claude-code")
        return TierApplyResult(
            ok=True,
            summary=(
                "Claude Code tier reset (removed permissions.defaultMode)."
                if changes
                else "Claude Code had no TokenPak-managed tier key — nothing removed."
            ),
            changes=changes,
            backup_path=str(bak) if bak else None,
        )
    except Exception as exc:
        return TierApplyResult(
            ok=False,
            summary="Claude Code tier reset failed.",
            error=str(exc),
            backup_path=str(bak) if bak else None,
        )


# ---------------------------------------------------------------------------
# Codex (~/.codex/config.toml) — managed keys: top-level approval_policy +
# sandbox_mode. Edits are line-scoped so comments, [profiles.*], MCP blocks
# and every unrelated key survive byte-for-byte. (tomli_w is not a
# dependency; round-tripping through a TOML writer would also destroy
# comments, which violates the additive-only contract.)
# ---------------------------------------------------------------------------


def _codex_config_path() -> Path:
    return Path.home() / ".codex" / "config.toml"


def _backup_codex_config() -> Optional[Path]:
    """Backup ~/.codex/config.toml → config.toml.bak (mirrors the Claude
    settings backup helper in install.py). Returns None when absent."""
    p = _codex_config_path()
    if not p.exists():
        return None
    bak = p.with_suffix(".toml.bak")
    shutil.copy2(p, bak)
    return bak


def _read_codex_config() -> dict:
    p = _codex_config_path()
    if not p.exists():
        return {}
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _top_level_extent(lines: list[str]) -> int:
    """Index of the first TOML table header (= end of top-level scope)."""
    for i, ln in enumerate(lines):
        if re.match(r"^\s*\[", ln):
            return i
    return len(lines)


def _set_codex_top_level_keys(text: str, updates: dict[str, str]) -> str:
    """Return text with top-level keys set, preserving everything else."""
    lines = text.splitlines()
    extent = _top_level_extent(lines)
    seen: set[str] = set()
    for i in range(extent):
        for key, val in updates.items():
            if re.match(rf"^\s*{re.escape(key)}\s*=", lines[i]):
                lines[i] = f'{key} = "{val}"'
                seen.add(key)
    missing = [k for k in updates if k not in seen]
    if missing:
        inserts = [f'{k} = "{updates[k]}"' for k in missing]
        head, tail = lines[:extent], lines[extent:]
        if head and head[-1].strip():
            head.append("")
        head.extend(inserts)
        if tail:
            head.append("")
        lines = head + tail
    out = "\n".join(lines)
    return out + "\n" if out and not out.endswith("\n") else (out or "")


def _remove_codex_top_level_keys(text: str, keys: tuple[str, ...]) -> str:
    """Return text with the given top-level keys removed (line-scoped)."""
    lines = text.splitlines()
    extent = _top_level_extent(lines)
    kept: list[str] = []
    for i, ln in enumerate(lines):
        if i < extent and any(
            re.match(rf"^\s*{re.escape(k)}\s*=", ln) for k in keys
        ):
            continue
        kept.append(ln)
    out = "\n".join(kept)
    return out + "\n" if out and not out.endswith("\n") else (out or "")


def _atomic_write_codex_config(text: str) -> None:
    p = _codex_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=p.parent, delete=False, suffix=".tmp", encoding="utf-8"
    ) as f:
        f.write(text)
        tmp = f.name
    os.replace(tmp, p)


def read_codex_tier() -> tuple[str, str]:
    """Return (tier_label, note) for the Codex persistent tier.

    tier_label is restricted to {strict, standard, auto, custom}; never
    "fleet" (see module docstring).
    """
    cfg = _read_codex_config()
    approval = cfg.get("approval_policy")
    sandbox = cfg.get("sandbox_mode")
    last = get_last_set_tier("codex")

    if approval is None and sandbox is None:
        if last is not None:
            return "custom", f"(last set: {last}, modified externally)"
        return DEFAULT_TIER, "(default; not yet applied)"
    if (approval, sandbox) in CODEX_SETTINGS_TO_TIER:
        return CODEX_SETTINGS_TO_TIER[(approval, sandbox)], ""
    if last is not None:
        return "custom", f"(last set: {last}, modified externally)"
    return "custom", "(modified externally)"


def apply_codex_tier(tier: str, backup: bool = True) -> TierApplyResult:
    """Write the tier's approval_policy + sandbox_mode into config.toml."""
    if tier not in PERSISTENT_TIERS:
        return TierApplyResult(
            ok=False,
            summary=f"'{tier}' is not a persistent tier",
            error="not_persistent_tier",
        )
    p = _codex_config_path()
    bak: Optional[Path] = None
    try:
        if backup:
            bak = _backup_codex_config()
        old_text = p.read_text(encoding="utf-8") if p.exists() else ""
        new_text = _set_codex_top_level_keys(old_text, CODEX_TIER_TO_SETTINGS[tier])

        # Parse-validate before committing; a malformed result must never
        # land on disk.
        try:
            tomllib.loads(new_text)
        except Exception as exc:
            return TierApplyResult(
                ok=False,
                summary="Codex config edit failed TOML validation — file left untouched.",
                error=str(exc),
                backup_path=str(bak) if bak else None,
            )

        changes: list[str] = []
        if new_text != old_text:
            _atomic_write_codex_config(new_text)
            wanted = CODEX_TIER_TO_SETTINGS[tier]
            changes.append(
                f"approval_policy = \"{wanted['approval_policy']}\", "
                f"sandbox_mode = \"{wanted['sandbox_mode']}\" (tier: {tier})"
            )
        _record_last_set_tier("codex", tier)
        rollback = f"cp {bak} {p}" if bak else None
        summary = (
            f"Codex persistent tier set to {tier}."
            if changes
            else f"Codex already at tier {tier} — no changes."
        )
        return TierApplyResult(
            ok=True,
            summary=summary,
            changes=changes,
            backup_path=str(bak) if bak else None,
            rollback_cmd=rollback,
        )
    except Exception as exc:
        return TierApplyResult(
            ok=False,
            summary="Codex tier apply failed.",
            error=str(exc),
            backup_path=str(bak) if bak else None,
        )


def reset_codex_tier() -> TierApplyResult:
    """Scoped reset: remove ONLY the managed top-level keys from config.toml."""
    p = _codex_config_path()
    bak: Optional[Path] = None
    try:
        if not p.exists():
            _clear_last_set_tier("codex")
            return TierApplyResult(
                ok=True, summary="Codex config.toml absent — nothing to reset."
            )
        bak = _backup_codex_config()
        old_text = p.read_text(encoding="utf-8")
        new_text = _remove_codex_top_level_keys(old_text, _CODEX_MANAGED_KEYS)
        changes: list[str] = []
        if new_text != old_text:
            _atomic_write_codex_config(new_text)
            changes.append("removed top-level approval_policy + sandbox_mode")
        _clear_last_set_tier("codex")
        return TierApplyResult(
            ok=True,
            summary=(
                "Codex tier reset (removed managed keys)."
                if changes
                else "Codex had no TokenPak-managed tier keys — nothing removed."
            ),
            changes=changes,
            backup_path=str(bak) if bak else None,
        )
    except Exception as exc:
        return TierApplyResult(
            ok=False,
            summary="Codex tier reset failed.",
            error=str(exc),
            backup_path=str(bak) if bak else None,
        )


# ---------------------------------------------------------------------------
# Shared display helpers (used by show / doctor / menu)
# ---------------------------------------------------------------------------


def applied_tier(client: str) -> Optional[str]:
    """Tier actually present in the client config right now.

    Returns the tier name only when the managed keys exactly match a
    known mapping; None when the keys are absent or set to unmanaged
    values. Never returns "fleet".
    """
    if client == "claude-code":
        from tokenpak.cli.commands.install import _read_settings

        perms = _read_settings().get("permissions", {})
        mode = perms.get("defaultMode") if isinstance(perms, dict) else None
        return CLAUDE_MODE_TO_TIER.get(mode) if mode else None
    if client == "codex":
        cfg = _read_codex_config()
        pair = (cfg.get("approval_policy"), cfg.get("sandbox_mode"))
        if pair == (None, None):
            return None
        return CODEX_SETTINGS_TO_TIER.get(pair)  # type: ignore[arg-type]
    return None


def apply_tier(client: str, tier: str, backup: bool = True) -> TierApplyResult:
    """Apply a persistent tier to one client."""
    if client == "claude-code":
        return apply_claude_tier(tier, backup=backup)
    if client == "codex":
        return apply_codex_tier(tier, backup=backup)
    return TierApplyResult(ok=False, summary=f"unknown client '{client}'", error="unknown_client")


def reset_tier(client: str) -> TierApplyResult:
    """Scoped tier reset for one client."""
    if client == "claude-code":
        return reset_claude_tier()
    if client == "codex":
        return reset_codex_tier()
    return TierApplyResult(ok=False, summary=f"unknown client '{client}'", error="unknown_client")


def resolved_settings_line(client: str, tier: str) -> str:
    """Human-readable resolved per-client settings for a tier."""
    if tier not in PERSISTENT_TIERS:
        return "(custom — not a TokenPak tier mapping)"
    if client == "claude-code":
        return f'permissions.defaultMode = "{CLAUDE_TIER_TO_MODE[tier]}"'
    wanted = CODEX_TIER_TO_SETTINGS[tier]
    return (
        f'approval_policy = "{wanted["approval_policy"]}", '
        f'sandbox_mode = "{wanted["sandbox_mode"]}"'
    )


def _resolved_launcher_line(client: str, mode: str) -> str:
    """Human-readable launcher effect without exposing client-config writes."""
    if mode == "inherit":
        return "no injected permission arguments"
    if client == "claude-code":
        if mode == "full-bypass":
            return "combined permission-check bypass"
    if client == "codex":
        if mode == "approval-bypass":
            return "approval policy never; configured sandbox remains"
        if mode == "sandbox-bypass":
            return "danger-full-access sandbox; approval policy remains"
        if mode == "full-bypass":
            return "combined approval and sandbox bypass"
    return "unsupported mode; ignored"


def doctor_rows() -> tuple[list[str], bool]:
    """Build persistent-tier and per-client launcher-default rows.

    Returns ``(rows, drift)``. ``drift`` also covers malformed launcher
    state so diagnostics can fail closed and point the operator at reset.

        Claude Code persistent tier:  standard
        Codex persistent tier:        standard
        Claude Code launcher default: inherit
        Codex launcher default:       inherit
        Legacy full-bypass alias:       disabled

    The persistent-tier rows can only ever read strict / standard / auto
    / custom — never "fleet". ``drift`` is True when either client config
    was modified away from a known tier mapping after TokenPak set it.
    """
    claude_tier, claude_note = read_claude_tier()
    codex_tier, codex_note = read_codex_tier()
    claude_mode, claude_mode_note = _get_launcher_mode_status("claude-code")
    codex_mode, codex_mode_note = _get_launcher_mode_status("codex")
    legacy_alias = "enabled (both full-bypass)" if fleet_mode_enabled() else "disabled"

    def _row(label: str, value: str, note: str) -> str:
        cell = f"{value} {note}".strip() if note else value
        return f"{label + ':':<30}{cell}"

    rows = [
        _row(
            "Claude Code persistent tier",
            claude_tier,
            claude_note if claude_tier == "custom" else "",
        ),
        _row(
            "Codex persistent tier",
            codex_tier,
            codex_note if codex_tier == "custom" else "",
        ),
        _row(
            "Claude Code launcher default",
            claude_mode,
            f"({claude_mode_note})" if claude_mode_note else "",
        ),
        _row(
            "Codex launcher default",
            codex_mode,
            f"({codex_mode_note})" if codex_mode_note else "",
        ),
        _row("Legacy full-bypass alias", legacy_alias, ""),
    ]
    drift = (
        claude_tier == "custom"
        or codex_tier == "custom"
        or bool(claude_mode_note)
        or bool(codex_mode_note)
    )
    return rows, drift


# ---------------------------------------------------------------------------
# CLI handler — `tokenpak permissions show|set|reset|launcher`
# ---------------------------------------------------------------------------

_FLEET_WARNING = (
    "fleet mode is a compatibility alias for full-bypass on both TokenPak launchers.\n"
    "  Approval prompts and local sandboxing will be disabled. Client config\n"
    "  files are NOT modified; every affected launch prints a stderr warning.\n"
    "  Use only inside an external isolation boundary with trusted code."
)

_MANAGED_POLICY_WARNING = (
    "TokenPak cannot override administrator policy, a managed wrapper, a container, "
    "or host security controls; those layers can still constrain or reject a launch."
)

_LAUNCHER_WARNINGS: dict[str, dict[str, str]] = {
    "claude-code": {
        "full-bypass": (
            "CRITICAL: `tokenpak claude` will bypass all Claude Code permission "
            "checks. Use only with trusted code inside an external isolation boundary."
        ),
    },
    "codex": {
        "approval-bypass": (
            "Codex approval prompts will be disabled for `tokenpak codex` launches. "
            "The configured sandbox still applies; if it is danger-full-access, this "
            "is effectively full bypass."
        ),
        "sandbox-bypass": (
            "Codex sandboxing will be disabled for `tokenpak codex` launches. The "
            "approval policy still applies; if it is never, this is effectively full "
            "bypass. Approved commands may access host files, credentials, and network."
        ),
        "full-bypass": (
            "CRITICAL: `tokenpak codex` will run commands without approval prompts or "
            "local sandboxing. Use only with trusted code inside an external isolation "
            "boundary."
        ),
    },
}


def _launcher_warning_messages(client: str, mode: str) -> list[str]:
    """Return base and effective-composition warnings for a launcher mode."""
    if mode == _DEFAULT_LAUNCHER_MODE:
        return []
    messages = [_LAUNCHER_WARNINGS[client][mode]]
    if client != "codex":
        return messages

    cfg = _read_codex_config()
    approval = cfg.get("approval_policy")
    sandbox = cfg.get("sandbox_mode")
    if mode == "sandbox-bypass" and approval == "never":
        messages.append(
            "CRITICAL EFFECTIVE CONFIG: persistent approval_policy=never plus "
            "sandbox-bypass means no approval prompts and no local sandbox."
        )
    elif mode == "approval-bypass" and sandbox == "danger-full-access":
        messages.append(
            "CRITICAL EFFECTIVE CONFIG: persistent sandbox_mode=danger-full-access "
            "plus approval-bypass means no approval prompts and no local sandbox."
        )
    return messages


def _resolve_clients(arg: Optional[str]) -> list[str]:
    if not arg or arg == "both":
        return list(CLIENTS)
    return [arg]


def _print_result(client: str, result: TierApplyResult) -> None:
    badge = "✅" if result.ok else "✖"
    print(f"  {badge} {client}: {result.summary}")
    for c in result.changes:
        print(f"       • {c}")
    if result.backup_path:
        print(f"       backup:   {result.backup_path}")
    if result.rollback_cmd:
        print(f"       rollback: {result.rollback_cmd}")
    if result.error:
        print(f"       error:    {result.error}")


_PERMISSIONS_JSON_SCHEMA = "tokenpak.permissions.v1"


def _permission_snapshot() -> dict:
    """Build a stable machine-readable view of tiers and launcher defaults."""
    persistent: dict[str, dict[str, object]] = {}
    launcher: dict[str, dict[str, object]] = {}
    warnings: list[str] = []
    launcher_invalid = False
    for client, reader in (
        ("claude-code", read_claude_tier),
        ("codex", read_codex_tier),
    ):
        tier, note = reader()
        persistent[client] = {
            "tier": tier,
            "note": note or None,
            "resolved": resolved_settings_line(client, tier),
        }
        mode, mode_note = _get_launcher_mode_status(client)
        mode_warnings = _launcher_warning_messages(client, mode)
        launcher[client] = {
            "mode": mode,
            "note": mode_note,
            "resolved": _resolved_launcher_line(client, mode),
            "warnings": mode_warnings,
        }
        if mode_note:
            launcher_invalid = True
            warnings.append(f"{client}: {mode_note}")
        warnings.extend(f"{client}: {message}" for message in mode_warnings)

    if any(item["mode"] != _DEFAULT_LAUNCHER_MODE for item in launcher.values()):
        warnings.append(_MANAGED_POLICY_WARNING)
    if launcher_invalid:
        warnings.append(
            "Reset invalid launcher state with `tokenpak permissions launcher "
            "inherit --client both`."
        )
    return {
        "schema": _PERMISSIONS_JSON_SCHEMA,
        "persistent_tiers": persistent,
        "launcher_defaults": launcher,
        "legacy_fleet_alias": {
            "enabled": fleet_mode_enabled(),
            "meaning": "both launcher defaults are full-bypass",
        },
        "state_file": str(_state_path()),
        "warnings": warnings,
    }


def _cmd_show(args: argparse.Namespace) -> int:
    snapshot = _permission_snapshot()
    if bool(getattr(args, "as_json", False)):
        print(json.dumps(snapshot, sort_keys=True))
        return 0
    if bool(getattr(args, "quiet", False)):
        for warning in snapshot["warnings"]:
            print(f"tokenpak WARNING: {warning}", file=sys.stderr)
        return 0

    rows, _drift = doctor_rows()
    print()
    print("  TOKENPAK permissions")
    print("  " + "─" * 40)
    for row in rows:
        print(f"  {row}")
    print()
    print("  Resolved client settings:")
    claude_tier, _ = read_claude_tier()
    codex_tier, _ = read_codex_tier()
    print(f"    claude-code  {resolved_settings_line('claude-code', claude_tier)}")
    print(f"    codex        {resolved_settings_line('codex', codex_tier)}")
    print()
    print("  Resolved launcher defaults (TokenPak launchers only):")
    active_modes: list[tuple[str, str]] = []
    for client in CLIENTS:
        mode, note = _get_launcher_mode_status(client)
        print(f"    {client:<12} {mode:<17} {_resolved_launcher_line(client, mode)}")
        if mode != _DEFAULT_LAUNCHER_MODE:
            active_modes.append((client, mode))
        if note:
            print(f"                 WARNING: {note}")
    if active_modes:
        print()
        for client, mode in active_modes:
            for warning in _launcher_warning_messages(client, mode):
                print(f"  WARNING [{client} / {mode}]: {warning}")
        print(f"  {_MANAGED_POLICY_WARNING}")
    print()
    print(f"  Launcher state file: {_state_path()}")
    tier_drift = claude_tier == "custom" or codex_tier == "custom"
    launcher_drift = any(_get_launcher_mode_status(client)[1] for client in CLIENTS)
    if tier_drift:
        print()
        print(
            "  ⚠  A client config was modified outside TokenPak. Run "
            "`tokenpak permissions set <tier>` to re-apply, or "
            "`tokenpak permissions reset` to clear the managed keys."
        )
    if launcher_drift:
        print()
        print(
            "  ⚠  Invalid launcher state was ignored safely. Run `tokenpak "
            "permissions launcher inherit --client both` to restore inherit defaults."
        )
    print()
    return 0


def _validate_launcher_selection(mode: str, clients: list[str]) -> Optional[str]:
    """Return an actionable error when a mode/client combination is invalid."""
    if mode not in _LAUNCHER_MODES:
        return f"unknown launcher mode {mode!r}. Choose one of: " + ", ".join(_LAUNCHER_MODES)
    unsupported = [client for client in clients if mode not in _LAUNCHER_MODE_SUPPORT[client]]
    if not unsupported:
        return None
    names = ", ".join(unsupported)
    return (
        f"launcher mode {mode!r} is unavailable for {names}. Claude Code exposes "
        "only inherit/full-bypass at launch because its bypassPermissions mode is "
        "itself a full bypass. Use `--client codex`, choose full-bypass, or leave "
        "Claude Code at inherit."
    )


def _launcher_warning_lines(mode: str, clients: list[str]) -> list[str]:
    """Return mandatory configuration-time warning and reset lines."""
    if mode == _DEFAULT_LAUNCHER_MODE:
        return []
    lines: list[str] = []
    for client in clients:
        lines.extend(
            f"[{client} / {mode}] {message}"
            for message in _launcher_warning_messages(client, mode)
        )
    lines.append(_MANAGED_POLICY_WARNING)
    selected = "both" if set(clients) == set(CLIENTS) else clients[0]
    lines.append(
        "Reset with: `tokenpak permissions launcher inherit "
        f"--client {selected}`."
    )
    return lines


def _print_launcher_warnings(lines: list[str]) -> None:
    for line in lines:
        print(f"tokenpak WARNING: {line}", file=sys.stderr)


def _interactive_confirmation_allowed() -> bool:
    """Reuse the CLI-wide TTY/noninteractive/no-TUI policy."""
    try:
        from tokenpak._cli_core import _interactive_menu_allowed

        return _interactive_menu_allowed()
    except Exception:
        return False


def _confirm_launcher_change(
    mode: str,
    clients: list[str],
    assume_yes: bool,
    interactive: bool,
) -> bool:
    """Require explicit opt-in for every non-inherit launcher mode."""
    if mode == _DEFAULT_LAUNCHER_MODE:
        return True
    _print_launcher_warnings(_launcher_warning_lines(mode, clients))
    if assume_yes:
        return True
    if interactive:
        target = "both clients" if len(clients) == 2 else clients[0]
        sys.stdout.write(f"\n  Set {mode} for {target}? [y/N]: ")
        sys.stdout.flush()
        try:
            line = sys.stdin.readline().strip().lower()
        except (EOFError, KeyboardInterrupt):
            line = ""
        if line in ("y", "yes"):
            return True
        print("  Cancelled — launcher defaults unchanged.")
        return False
    print(
        "permissions: refusing bypass configuration without --yes in "
        "non-interactive/JSON/quiet mode; rerun with --yes after reviewing warnings.",
        file=sys.stderr,
    )
    return False


def _cmd_launcher(args: argparse.Namespace) -> int:
    """Set one flat, launcher-only mode (``permissions launcher MODE``)."""
    mode = getattr(args, "launcher_mode", None)
    client_arg = getattr(args, "client", None)
    as_json = bool(getattr(args, "as_json", False))
    quiet = bool(getattr(args, "quiet", False))
    if not client_arg:
        message = "launcher requires --client codex|claude-code|both"
        if as_json:
            print(json.dumps({"schema": _PERMISSIONS_JSON_SCHEMA, "ok": False, "error": message}))
        else:
            print(f"permissions: {message}", file=sys.stderr)
        return 2
    clients = _resolve_clients(client_arg)
    error = _validate_launcher_selection(mode, clients)
    if error:
        if as_json:
            print(json.dumps({"schema": _PERMISSIONS_JSON_SCHEMA, "ok": False, "error": error}))
        else:
            print(f"permissions: {error}", file=sys.stderr)
        return 2
    warnings = _launcher_warning_lines(mode, clients)
    interactive = (
        not as_json
        and not quiet
        and _interactive_confirmation_allowed()
    )
    if not _confirm_launcher_change(
        mode,
        clients,
        bool(getattr(args, "yes", False)),
        interactive,
    ):
        if as_json:
            print(
                json.dumps(
                    {
                        "schema": _PERMISSIONS_JSON_SCHEMA,
                        "ok": False,
                        "error": "explicit --yes required",
                        "warnings": warnings,
                    },
                    sort_keys=True,
                )
            )
        return 0 if interactive else 1

    source = f"tokenpak permissions launcher {mode} --client {client_arg}"
    _set_launcher_modes({client: mode for client in clients}, source)
    result = {
        "schema": _PERMISSIONS_JSON_SCHEMA,
        "ok": True,
        "action": "launcher_default_set",
        "clients": clients,
        "mode": mode,
        "state_file": str(_state_path()),
        "warnings": warnings,
    }
    if as_json:
        print(json.dumps(result, sort_keys=True))
    elif not quiet:
        print()
        for client in clients:
            print(f"  ✅ {client} launcher default: {mode}")
        print("     Client config files were not modified.")
        print()
    return 0


def _cmd_set(args: argparse.Namespace) -> int:
    tier = args.tier
    client_arg = getattr(args, "client", None) or "both"
    clients = _resolve_clients(client_arg)

    if tier == "fleet":
        if client_arg != "both":
            print(
                "permissions: legacy fleet mode always targets both launchers; "
                f"--client {client_arg} would broaden scope unexpectedly. Use "
                f"`tokenpak permissions launcher full-bypass --client {client_arg}`.",
                file=sys.stderr,
            )
            return 2
        print(f"tokenpak WARNING: {_FLEET_WARNING}", file=sys.stderr)
        _print_launcher_warnings(_launcher_warning_lines("full-bypass", clients))
        assume_yes = bool(getattr(args, "yes", False))
        if not assume_yes:
            if _interactive_confirmation_allowed():
                sys.stdout.write("\n  Enable fleet mode? [y/N]: ")
                sys.stdout.flush()
                try:
                    line = sys.stdin.readline().strip().lower()
                except (EOFError, KeyboardInterrupt):
                    line = ""
                if line not in ("y", "yes"):
                    print("  Cancelled — fleet mode unchanged.")
                    return 0
            else:
                print(
                    "permissions: refusing legacy fleet mode non-interactively "
                    "without --yes (explicit opt-in required).",
                    file=sys.stderr,
                )
                return 1
        set_fleet_mode(True, "tokenpak permissions set fleet")
        print()
        print("  ✅ Launcher full-bypass compatibility alias: enabled for both clients.")
        print("     Client config files were not modified.")
        print()
        return 0

    rc = 0
    print()
    for client in clients:
        result = apply_tier(client, tier, backup=True)
        _print_result(client, result)
        if not result.ok:
            rc = 1
    print()
    return rc


def _cmd_reset(args: argparse.Namespace) -> int:
    clients = _resolve_clients(getattr(args, "client", None))
    rc = 0
    print()
    for client in clients:
        result = reset_tier(client)
        _print_result(client, result)
        if not result.ok:
            rc = 1
    # Legacy reset always clears all launcher modes, regardless of --client scope.
    launcher_state_present = _state_path().exists()
    launcher_active = any(
        _get_launcher_mode(client) != _DEFAULT_LAUNCHER_MODE for client in CLIENTS
    )
    if launcher_state_present or launcher_active:
        set_fleet_mode(False, "tokenpak permissions reset")
        print("  ✅ Launcher defaults reset to inherit (legacy full-bypass alias disabled).")
    print()
    return rc


def run_permissions(args: argparse.Namespace) -> int:
    """CLI handler for `tokenpak permissions`."""
    verb = getattr(args, "permissions_cmd", None) or "show"
    if verb == "show":
        return _cmd_show(args)
    if verb == "set":
        return _cmd_set(args)
    if verb == "reset":
        return _cmd_reset(args)
    if verb == "launcher":
        return _cmd_launcher(args)
    print(f"permissions: unknown subcommand '{verb}' (expected show|set|reset|launcher)")
    return 2
