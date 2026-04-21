"""tokenpak trigger — list, add, remove, test, log CLI commands.

Events supported:
  git:commit                  Fires on git post-commit hook
  git:push                    Fires on git post-push hook
  file:changed:<glob>         Fires when a matching file changes  (e.g. file:changed:*.py)
  file:created:<glob>         Fires when a matching file appears
  cost:daily><amount>         Fires when daily cost exceeds threshold (e.g. cost:daily>5.00)
  cost:threshold              Alias — use cost:daily>N instead
  agent:register              Fires when an agent registers itself
  agent:finished[:<name>]     Fires when an agent/task finishes
  agent:failed[:<name>]       Fires when an agent/task fails
  schedule:cron:<expr>        Timer-style — fires on cron-like intervals (stored; daemon polls)
  timer:<interval>            Short-hand timer  (e.g. timer:5m, timer:30s, timer:1h)
"""

from __future__ import annotations

import json
import subprocess
import sys

import click

from tokenpak.agent.triggers.matcher import match_event
from tokenpak.agent.triggers.store import DEFAULT_CONFIG, TriggerStore

SEP = "─" * 64


def _store() -> TriggerStore:
    return TriggerStore()


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


@click.group("trigger")
def trigger_group():
    """Manage event triggers: list, add, remove, test, log."""


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@trigger_group.command("list")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output raw JSON")
def list_cmd(output_json: bool) -> None:
    """List all configured triggers."""
    store = _store()
    triggers = store.list()

    if output_json:
        click.echo(
            json.dumps(
                [
                    dict(
                        id=t.id,
                        event=t.event,
                        action=t.action,
                        enabled=t.enabled,
                        created_at=t.created_at,
                    )
                    for t in triggers
                ],
                indent=2,
            )
        )
        return

    if not triggers:
        click.echo("No triggers configured.")
        click.echo("Tip: tokenpak trigger add --event <event> --action <action>")
        return

    click.echo(SEP)
    click.echo(f"  {'ID':<10} {'EN':>2}  {'EVENT':<36}  ACTION")
    click.echo(SEP)
    for t in triggers:
        enabled = "●" if t.enabled else "○"
        click.echo(f"  {t.id:<10} {enabled:>2}  {t.event:<36}  {t.action}")
    click.echo(SEP)
    click.echo(f"  {len(triggers)} trigger(s)  |  config: {DEFAULT_CONFIG}")


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------


@trigger_group.command("add")
@click.option(
    "--event",
    required=True,
    help="Event pattern (e.g. file:changed:*.py, git:commit, cost:daily>5)",
)
@click.option("--action", required=True, help="Shell command or tokenpak sub-command to run")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output raw JSON")
def add_cmd(event: str, action: str, output_json: bool) -> None:
    """Add a new event trigger."""
    store = _store()
    t = store.add(event=event, action=action)

    if output_json:
        click.echo(
            json.dumps(
                dict(
                    id=t.id,
                    event=t.event,
                    action=t.action,
                    enabled=t.enabled,
                    created_at=t.created_at,
                ),
                indent=2,
            )
        )
        return

    click.echo(f"✓ Trigger added  [{t.id}]")
    click.echo(f"  Event:  {t.event}")
    click.echo(f"  Action: {t.action}")
    click.echo(f"  Config: {DEFAULT_CONFIG}")


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------


@trigger_group.command("remove")
@click.argument("trigger_id")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output raw JSON")
def remove_cmd(trigger_id: str, output_json: bool) -> None:
    """Remove a trigger by ID."""
    store = _store()
    removed = store.remove(trigger_id)

    if output_json:
        click.echo(json.dumps({"removed": removed, "id": trigger_id}, indent=2))
        return

    if removed:
        click.echo(f"✓ Trigger [{trigger_id}] removed")
    else:
        click.echo(f"✖ Trigger [{trigger_id}] not found", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------

# Known tokenpak sub-commands (for action prefixing in execute mode)
_TOKENPAK_SUBCMDS = frozenset(
    [
        "status",
        "version",
        "config",
        "reset",
        "logs",
        "proxy",
        "debug",
        "learn",
        "trigger",
        "workflow",
        "index",
        "doctor",
        "metrics",
        "cost",
        "budget",
        "last",
        "help",
        "stats",
        "serve",
        "benchmark",
        "calibrate",
        "lock",
        "agent",
        "replay",
        "recipe",
        "demo",
        "run",
        "macro",
        "search",
    ]
)


def _build_cmd(action: str) -> str:
    """Prefix tokenpak sub-commands; leave shell commands as-is."""
    first_word = action.split()[0] if action.split() else action
    if not action.startswith(("/", "./", "~")) and first_word in _TOKENPAK_SUBCMDS:
        return f"tokenpak {action}"
    return action


@trigger_group.command("test")
@click.option(
    "--event",
    required=True,
    help="Simulate this event string (e.g. file:changed:/home/user/foo.py)",
)
@click.option(
    "--dry-run/--execute",
    "dry_run",
    default=True,
    help="--dry-run (default): simulate only. --execute: run matching actions.",
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Output raw JSON")
def test_cmd(event: str, dry_run: bool, output_json: bool) -> None:
    """Test which triggers would fire for a given event.

    By default this is a dry-run (no actions executed). Pass --execute to run them.
    """
    store = _store()
    triggers = store.list()

    matches = [t for t in triggers if t.enabled and match_event(t.event, event)]

    if output_json:
        results = []
        for t in matches:
            entry: dict = dict(
                id=t.id,
                event=t.event,
                action=t.action,
                would_fire=True,
                dry_run=dry_run,
            )
            if not dry_run:
                cmd = _build_cmd(t.action)
                try:
                    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                    entry["exit_code"] = r.returncode
                    entry["output"] = (r.stdout + r.stderr).strip()
                    store.log_fire(t, r.returncode, entry["output"])
                except Exception as exc:
                    entry["exit_code"] = -1
                    entry["output"] = str(exc)
            results.append(entry)
        click.echo(json.dumps(results, indent=2))
        return

    click.echo(f"Event: {event}")
    click.echo(f"Mode:  {'dry-run' if dry_run else 'execute'}")
    click.echo(SEP)

    if not matches:
        click.echo(f"  No triggers match — 0 of {len(triggers)} would fire")
        return

    click.echo(f"  {len(matches)} trigger(s) would fire:")
    for t in matches:
        click.echo(f"\n  [{t.id}] {t.event}")
        click.echo(f"    Action: {t.action}")
        if not dry_run:
            cmd = _build_cmd(t.action)
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                out = (r.stdout + r.stderr).strip()
                store.log_fire(t, r.returncode, out)
                click.echo(f"    Exit:   {r.returncode}")
                if out:
                    click.echo(f"    Output: {out[:200]}")
            except Exception as exc:
                click.echo(f"    Error:  {exc}")


# ---------------------------------------------------------------------------
# log
# ---------------------------------------------------------------------------


@trigger_group.command("log")
@click.option(
    "--limit", default=20, show_default=True, type=int, help="Number of recent log entries to show"
)
@click.option("--trigger-id", default=None, help="Filter log to a specific trigger ID")
@click.option("--json", "output_json", is_flag=True, default=False, help="Output raw JSON")
def log_cmd(limit: int, trigger_id: str | None, output_json: bool) -> None:
    """Show recent trigger fire log."""
    store = _store()
    logs = store.list_logs(limit=limit)

    if trigger_id:
        logs = [lg for lg in logs if lg.trigger_id == trigger_id]

    if output_json:
        click.echo(
            json.dumps(
                [
                    dict(
                        trigger_id=lg.trigger_id,
                        event=lg.event,
                        action=lg.action,
                        fired_at=lg.fired_at,
                        exit_code=lg.exit_code,
                        output=lg.output,
                    )
                    for lg in logs
                ],
                indent=2,
            )
        )
        return

    if not logs:
        click.echo("No trigger log entries found.")
        return

    click.echo(SEP)
    click.echo(f"  {'FIRED AT':<22} {'TRIGGER':<10} {'EXIT':>4}  {'EVENT':<30}  ACTION")
    click.echo(SEP)
    for lg in logs:
        fired = lg.fired_at[:19].replace("T", " ")
        status = "✓" if lg.exit_code == 0 else "✖"
        click.echo(
            f"  {fired:<22} {lg.trigger_id:<10} {status} {lg.exit_code:>3}  "
            f"{lg.event:<30}  {lg.action}"
        )
        if lg.output:
            # Show first line of output indented
            first_line = lg.output.splitlines()[0][:60]
            click.echo(f"  {'':22} {'':10}       {'':>3}  {first_line}")
    click.echo(SEP)
    click.echo(f"  {len(logs)} log entr{'y' if len(logs) == 1 else 'ies'}")
