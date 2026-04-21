"""exec command — run built-in local operations and saved macros (no LLM calls)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Callable

import click

OperationFn = Callable[[dict[str, Any], bool], int]


def _op_reindex(params: dict[str, Any], dry_run: bool) -> int:
    from tokenpak.cli.commands.index import run_index_path

    path = os.path.expanduser(str(params.get("path", os.getcwd())))
    verbose = bool(params.get("verbose", False))

    if dry_run:
        click.echo(f"[dry-run] reindex path={path} verbose={verbose}")
        return 0

    run_index_path(path, verbose=verbose)
    return 0


def _op_validate_config(params: dict[str, Any], dry_run: bool) -> int:
    cfg_path = Path(os.path.expanduser(str(params.get("path", "~/.tokenpak/config.json"))))

    if dry_run:
        click.echo(f"[dry-run] validate-config path={cfg_path}")
        return 0

    if not cfg_path.exists():
        click.echo(f"✖ Config not found: {cfg_path}")
        return 1

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        click.echo(f"✖ Invalid JSON in {cfg_path}: {e}")
        return 1

    if not isinstance(data, dict):
        click.echo("✖ Config must be a JSON object")
        return 1

    click.echo(f"✓ Config valid: {cfg_path}")
    return 0


def _op_cleanup_cache(params: dict[str, Any], dry_run: bool) -> int:
    cache_path = Path(os.path.expanduser(str(params.get("path", "~/.tokenpak/cache"))))

    if dry_run:
        click.echo(f"[dry-run] cleanup-cache path={cache_path}")
        return 0

    if not cache_path.exists():
        click.echo(f"✓ Cache path not found: {cache_path}")
        return 0

    if not cache_path.is_dir():
        click.echo(f"✖ Not a directory: {cache_path}")
        return 1

    removed = 0
    for item in cache_path.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink(missing_ok=True)
            removed += 1
        except Exception as e:
            click.echo(f"⚠ Could not remove {item}: {e}")

    click.echo(f"✓ Cache cleanup complete: removed {removed} item(s) from {cache_path}")
    return 0


def _op_health_check(params: dict[str, Any], dry_run: bool) -> int:
    from tokenpak.cli.commands.doctor import run_doctor

    if dry_run:
        click.echo("[dry-run] health-check")
        return 0

    return int(run_doctor(fix=False))


BUILTIN_OPERATIONS: dict[str, OperationFn] = {
    "reindex": _op_reindex,
    "validate-config": _op_validate_config,
    "cleanup-cache": _op_cleanup_cache,
    "health-check": _op_health_check,
}


def _load_macro(name: str, macros_dir: Path) -> dict[str, Any]:
    json_path = macros_dir / f"{name}.json"
    yaml_path = macros_dir / f"{name}.yaml"
    yml_path = macros_dir / f"{name}.yml"

    if json_path.exists():
        return json.loads(json_path.read_text(encoding="utf-8"))

    for p in (yaml_path, yml_path):
        if p.exists():
            try:
                import yaml  # type: ignore
            except Exception as e:
                raise click.ClickException(f"YAML macro requires PyYAML ({e})") from e
            return yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    raise click.ClickException(f"Macro not found: {name} (looked in {macros_dir})")


def _run_operation(op_name: str, params: dict[str, Any], dry_run: bool) -> int:
    op = BUILTIN_OPERATIONS.get(op_name)
    if op is None:
        raise click.ClickException(f"Operation '{op_name}' is not allowed")
    return op(params, dry_run)


def run_macro(name: str, macros_dir: Path, dry_run: bool) -> int:
    macro = _load_macro(name, macros_dir)
    steps = macro.get("steps", [])

    if not isinstance(steps, list) or not steps:
        raise click.ClickException(f"Macro '{name}' has no valid steps list")

    click.echo(f"Running macro: {name} ({len(steps)} step(s))")
    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            raise click.ClickException(f"Macro step {idx} must be an object")

        op_name = step.get("operation") or step.get("op")
        if not op_name:
            raise click.ClickException(f"Macro step {idx} missing operation/op")

        params = step.get("params", {})
        if not isinstance(params, dict):
            raise click.ClickException(f"Macro step {idx} params must be an object")

        click.echo(f"[{idx}/{len(steps)}] {op_name}")
        rc = _run_operation(str(op_name), params, dry_run=dry_run)
        if rc != 0:
            click.echo(f"✖ Step failed: {op_name} (exit {rc})")
            return rc

    click.echo(f"✓ Macro complete: {name}")
    return 0


@click.command("exec", help="Run safe local operations/macros without LLM calls.")
@click.argument("name")
@click.option(
    "--macros-dir",
    default="~/.tokenpak/macros",
    show_default=True,
    help="Directory containing macro definitions (.json/.yaml/.yml)",
)
@click.option(
    "--execute",
    is_flag=True,
    default=False,
    help="Execute destructive operations. Without this, destructive ops are dry-run.",
)
def exec_cmd(name: str, macros_dir: str, execute: bool) -> None:
    """Run a built-in operation or a saved macro.

    Built-ins:
      - reindex
      - validate-config
      - cleanup-cache (destructive; dry-run unless --execute)
      - health-check

    Macro schema (JSON/YAML):
      {"steps": [{"operation": "reindex", "params": {"path": "~/vault"}}]}
    """

    destructive = {"cleanup-cache"}
    dry_run = name in destructive and not execute

    if name in BUILTIN_OPERATIONS:
        if dry_run:
            click.echo("cleanup-cache is destructive; running dry-run. Use --execute to apply.")
        rc = _run_operation(name, {}, dry_run=dry_run)
    else:
        rc = run_macro(name, Path(os.path.expanduser(macros_dir)), dry_run=False)

    if rc != 0:
        raise SystemExit(rc)
