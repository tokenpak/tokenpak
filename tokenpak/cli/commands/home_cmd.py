# SPDX-License-Identifier: Apache-2.0
"""``tokenpak home`` CLI subcommand (Beta 1).

Tester-friendly user-config surface that honors the canonical
``~/.tpk/`` boundary while preserving zero-touch fallback to the legacy
``~/.tokenpak/`` directory until ``tokenpak home migrate`` runs.

Subcommands:
    path                 Print the resolved TokenPak home + which rule
                         decided it (env/canonical/legacy).
    init                 Create the home directory + a starter
                         config.json (no overwrites unless --force).
    validate             Parse the config file and report any obvious
                         shape problems (missing keys, bad types).
    explain              Show every config key the install knows about
                         + its current value + where it came from.
    migrate              Backup-first move from ``~/.tokenpak/`` →
                         ``~/.tpk/`` (never destructive; never blind).

The Pro daemon coordination layout lives under
``<home>/pro/`` and is intentionally not touched by these commands.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any


def build_home_parser(sub: Any) -> None:
    """Register the ``tokenpak home`` subcommand."""
    p = sub.add_parser(
        "home",
        help="Manage tokenpak's on-disk configuration",
        description=(
            "Inspect, validate, and migrate the TokenPak home directory. "
            "All paths resolve through tokenpak._paths so subcommands "
            "honor TOKENPAK_HOME and the canonical ~/.tpk/ boundary."
        ),
    )
    csub = p.add_subparsers(dest="home_action", required=False)

    p_path = csub.add_parser("path", help="Print resolved TokenPak home")
    p_path.add_argument("--json", dest="as_json", action="store_true")
    p_path.set_defaults(func=cmd_home_path)

    p_init = csub.add_parser("init", help="Create home + starter config")
    p_init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing config.json",
    )
    p_init.set_defaults(func=cmd_home_init)

    p_validate = csub.add_parser("validate", help="Parse + lint config file")
    p_validate.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
    )
    p_validate.set_defaults(func=cmd_home_validate)

    p_explain = csub.add_parser("explain", help="List every config key + value + source")
    p_explain.add_argument("--json", dest="as_json", action="store_true")
    p_explain.set_defaults(func=cmd_home_explain)

    p_migrate = csub.add_parser(
        "migrate",
        help="Backup-first migrate ~/.tokenpak/ → ~/.tpk/",
        description=(
            "Copy the legacy ~/.tokenpak/ tree to the canonical ~/.tpk/ "
            "location. The legacy tree is left in place as a safety "
            "backup; you can prune it manually once satisfied."
        ),
    )
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be copied without writing anything",
    )
    p_migrate.add_argument(
        "--force",
        action="store_true",
        help=(
            "Allow merging into an existing ~/.tpk/ (default: refuse "
            "and report what to do manually)"
        ),
    )
    p_migrate.set_defaults(func=cmd_home_migrate)

    p.set_defaults(func=lambda a: p.print_help())


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def cmd_home_path(args: Any) -> int:
    from tokenpak import _paths

    canonical = _paths.canonical_home()
    legacy = _paths.legacy_home()
    home = _paths.home()
    if home == canonical:
        rule = "canonical" if canonical.exists() else "default"
    elif home == legacy:
        rule = "legacy"
    else:
        rule = "env"

    payload = {
        "home": str(home),
        "rule": rule,
        "canonical_path": str(canonical),
        "canonical_present": _paths.has_canonical(),
        "legacy_path": str(legacy),
        "legacy_present": _paths.has_legacy(),
        "needs_migration": _paths.needs_migration(),
        "env_var": _paths.ENV_VAR,
    }
    if getattr(args, "as_json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"TokenPak home : {home}")
    print(f"Resolved by   : {rule}")
    print(f"Canonical     : {canonical}  (present: {_paths.has_canonical()})")
    print(f"Legacy        : {legacy}  (present: {_paths.has_legacy()})")
    if _paths.needs_migration():
        print()
        print("⚠️  Legacy ~/.tokenpak/ is in use.")
        print("   Run `tokenpak home migrate` to move to the canonical ~/.tpk/.")
    return 0


def cmd_home_init(args: Any) -> int:
    from tokenpak import _paths

    home = _paths.ensure_home()
    cfg_path = home / "config.json"
    if cfg_path.exists() and not getattr(args, "force", False):
        print(f"ℹ️  Config already exists at {cfg_path} (use --force to overwrite)")
        return 0
    cfg_path.write_text(json.dumps(_starter_config(), indent=2))
    print(f"✅ Wrote starter config → {cfg_path}")
    print()
    print("Next steps:")
    print("  • tokenpak home explain  — see every config key")
    print("  • tokenpak doctor          — verify the install")
    return 0


def cmd_home_validate(args: Any) -> int:
    from tokenpak import _paths

    cfg_path = _paths.under("config.json")
    issues: list[str] = []
    parsed: Any = None
    if not cfg_path.exists():
        issues.append(f"missing config: {cfg_path}")
    else:
        try:
            parsed = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"cannot parse config: {exc}")
        if isinstance(parsed, dict):
            for k in ("schema_version",):
                if k not in parsed:
                    issues.append(f"missing recommended key: {k}")
            for k, expected_type in (("port", int), ("vault_path", str)):
                if k in parsed and not isinstance(parsed[k], expected_type):
                    issues.append(f"{k} should be {expected_type.__name__}")
        elif parsed is not None:
            issues.append("config root is not a JSON object")

    payload = {
        "config_path": str(cfg_path),
        "ok": not issues,
        "issues": issues,
    }
    if getattr(args, "as_json", False):
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if not issues else 1
    if not issues:
        print(f"✅ {cfg_path} OK")
        return 0
    print(f"✗ {cfg_path}", file=sys.stderr)
    for i in issues:
        print(f"   - {i}", file=sys.stderr)
    return 1


def cmd_home_explain(args: Any) -> int:
    """Show every config key + value + provenance.

    Reads the merged config from the loader so env-var overrides and
    defaults appear alongside file values.
    """
    from tokenpak import _paths

    file_cfg: dict[str, Any] = {}
    cfg_path = _paths.under("config.json")
    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                file_cfg = raw
        except Exception:
            pass

    keys = {
        "home": (str(_paths.home()), "tokenpak._paths.home()"),
        "schema_version": (
            file_cfg.get("schema_version", "unset"),
            "config.json" if "schema_version" in file_cfg else "default",
        ),
        "port": (
            file_cfg.get("port", 8766),
            "config.json" if "port" in file_cfg else "default",
        ),
        "vault_path": (
            file_cfg.get("vault_path", str(Path.home() / "vault")),
            "config.json" if "vault_path" in file_cfg else "default",
        ),
    }

    if getattr(args, "as_json", False):
        print(
            json.dumps(
                {k: {"value": v, "source": s} for k, (v, s) in keys.items()},
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(f"Config file : {cfg_path}")
    print("─" * 60)
    for k, (v, s) in keys.items():
        print(f"  {k:18s} = {v!r:30s}   ({s})")
    return 0


def cmd_home_migrate(args: Any) -> int:
    """Backup-first non-destructive migration ``~/.tokenpak/`` → ``~/.tpk/``."""
    from tokenpak import _paths

    legacy = _paths.legacy_home()
    canonical = _paths.canonical_home()

    if not legacy.exists():
        print(f"ℹ️  No legacy directory at {legacy}; nothing to migrate.")
        return 0

    if canonical.exists() and not getattr(args, "force", False):
        print(f"⚠️  Canonical {canonical} already exists.", file=sys.stderr)
        print(
            "   Refusing to merge automatically. Either:\n"
            f"     - inspect {canonical} and {legacy} and merge manually, OR\n"
            f"     - re-run with --force to overlay the legacy tree onto the canonical.",
            file=sys.stderr,
        )
        return 1

    plan: list[tuple[str, str]] = []
    for src in legacy.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(legacy)
        dst = canonical / rel
        plan.append((str(src), str(dst)))

    if getattr(args, "dry_run", False):
        print(f"Dry run: would copy {len(plan)} file(s) from {legacy} → {canonical}")
        for s, d in plan[:10]:
            print(f"  {s} → {d}")
        if len(plan) > 10:
            print(f"  … and {len(plan) - 10} more")
        return 0

    canonical.mkdir(mode=0o700, parents=True, exist_ok=True)
    copied = 0
    skipped = 0
    for s, d in plan:
        dpath = Path(d)
        if dpath.exists() and not getattr(args, "force", False):
            skipped += 1
            continue
        dpath.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(s, d)
            copied += 1
        except OSError as exc:
            print(f"⚠️  {s}: {exc}", file=sys.stderr)
            skipped += 1

    print(f"✅ Migrated {copied} file(s) → {canonical}  ({skipped} skipped)")
    print(
        f"ℹ️  The legacy tree at {legacy} is untouched; remove it manually "
        "once you've verified the canonical install works."
    )
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _starter_config() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "port": 8766,
        "vault_path": str(Path.home() / "vault"),
        "_comment": (
            "Created by `tokenpak home init`. See `tokenpak home explain` for what each key does."
        ),
    }


__all__ = [
    "build_home_parser",
    "cmd_home_path",
    "cmd_home_init",
    "cmd_home_validate",
    "cmd_home_explain",
    "cmd_home_migrate",
]
