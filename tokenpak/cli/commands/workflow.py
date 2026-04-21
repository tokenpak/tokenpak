"""tokenpak.agent.cli.commands.workflow — Workflow state machine CLI."""

from __future__ import annotations

import json
from datetime import datetime

import click

from tokenpak.agent.agentic.workflow import (
    WORKFLOW_TEMPLATES,
    WorkflowStatus,
    WorkflowStep,
    get_manager,
    list_templates,
    template_steps,
)

SEP = "─" * 64


def _status_icon(s):
    return {
        "pending": "⏳",
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
        "cancelled": "🚫",
        "paused": "⏸️ ",
    }.get(str(s), "❓")


def _step_icon(s):
    return {
        "pending": "⬜",
        "running": "🔄",
        "completed": "✅",
        "failed": "❌",
        "skipped": "⏭️ ",
    }.get(str(s), "❓")


def _fmt_ts(ts):
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _fmt_dur(sec):
    if sec is None:
        return "—"
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    return f"{m}m {s}s"


def _progress_bar(pct, width=30):
    """Render a text progress bar: [████████░░░░░░░░░░░░░░░░░░░░░░] 45%"""
    filled = int(pct / 100 * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {pct:.0f}%"


def _eta_str(wf):
    """Estimate remaining time based on average step duration so far."""
    done_steps = [s for s in wf.steps if s.duration_seconds() is not None]
    pending_count = sum(1 for s in wf.steps if s.status.value in ("pending", "running"))
    if not done_steps or not pending_count:
        return None
    avg = sum(s.duration_seconds() for s in done_steps) / len(done_steps)
    eta_s = int(avg * pending_count)
    if eta_s < 60:
        return f"~{eta_s}s remaining"
    m, s = divmod(eta_s, 60)
    return f"~{m}m {s}s remaining"


def _resolve(mgr, wf_id):
    wf = mgr.load(wf_id)
    if wf:
        return wf
    matches = [w for w in mgr.list_workflows() if w.id.startswith(wf_id)]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        click.echo(f"Ambiguous ID prefix '{wf_id}'.", err=True)
        raise SystemExit(1)
    click.echo(f"Workflow '{wf_id}' not found.", err=True)
    raise SystemExit(1)


# Map --filter aliases to WorkflowStatus values
_FILTER_MAP = {
    "active": [WorkflowStatus.RUNNING, WorkflowStatus.PENDING, WorkflowStatus.PAUSED],
    "completed": [WorkflowStatus.COMPLETED],
    "failed": [WorkflowStatus.FAILED, WorkflowStatus.CANCELLED],
}


@click.group("workflow")
def workflow_cmd():
    """Workflow state machine: create, run, resume, and track workflows."""


@workflow_cmd.command("list")
@click.option(
    "--status",
    "filter_status",
    default=None,
    type=click.Choice([s.value for s in WorkflowStatus], case_sensitive=False),
)
@click.option(
    "--filter",
    "filter_preset",
    default=None,
    type=click.Choice(["active", "completed", "failed"], case_sensitive=False),
    help="Shorthand filter: active (running/pending/paused), completed, failed",
)
@click.option("--tag", "tags", multiple=True)
@click.option(
    "--type", "filter_type", default=None, help="Filter by workflow template/type (e.g. 'proxy')"
)
@click.option("--limit", default=20, show_default=True, type=int)
@click.option("--json", "output_json", is_flag=True, default=False)
def list_cmd(filter_status, filter_preset, tags, filter_type, limit, output_json):
    """List workflows.

    Use --filter active|completed|failed for quick status groups.
    Use --type proxy to filter to proxy-type workflows.
    """
    mgr = get_manager()

    # Collect candidate statuses
    target_statuses = None
    if filter_preset:
        target_statuses = _FILTER_MAP[filter_preset]
    elif filter_status:
        target_statuses = [WorkflowStatus(filter_status)]

    all_tags = list(tags)
    if filter_type:
        all_tags.append(filter_type)

    if target_statuses:
        # Fetch multiple status groups and merge
        records = []
        seen = set()
        for st in target_statuses:
            for wf in mgr.list_workflows(status=st, tags=all_tags if all_tags else None):
                if wf.id not in seen:
                    seen.add(wf.id)
                    records.append(wf)
        # Sort by created_at desc, respect limit
        records.sort(key=lambda w: w.created_at, reverse=True)
        records = records[:limit]
    else:
        records = mgr.list_workflows(tags=all_tags if all_tags else None, limit=limit)

    if output_json:
        click.echo(json.dumps([r.to_dict() for r in records], indent=2))
        return
    if not records:
        type_msg = f" (type={filter_type})" if filter_type else ""
        preset_msg = f" (filter={filter_preset})" if filter_preset else ""
        click.echo(f"No workflows found{type_msg}{preset_msg}.")
        return
    click.echo(SEP)
    click.echo(f"  {'STATUS':<12} {'NAME':<28} {'PCT':>5}  CREATED")
    click.echo(SEP)
    for wf in records:
        pct = f"{wf.completion_pct():.0f}%"
        created = datetime.fromtimestamp(wf.created_at).strftime("%m-%d %H:%M")
        click.echo(
            f"  {_status_icon(wf.status.value)} {wf.status.value:<10} {wf.name:<28} {pct:>5}  {created}  [{wf.id[:8]}]"
        )
    click.echo(SEP)
    click.echo(f"  {len(records)} workflow(s)")


@workflow_cmd.command("status")
@click.argument("wf_id")
@click.option("--json", "output_json", is_flag=True, default=False)
def status_cmd(wf_id, output_json):
    """Show detailed status of a workflow."""
    mgr = get_manager()
    wf = _resolve(mgr, wf_id)
    if output_json:
        click.echo(json.dumps(wf.to_dict(), indent=2))
        return
    done_count = sum(1 for s in wf.steps if s.is_done())
    total = len(wf.steps)
    pct = wf.completion_pct()
    click.echo(SEP)
    click.echo(f"  {_status_icon(wf.status.value)} {wf.name}  [{wf.id[:8]}]")
    click.echo(SEP)
    click.echo(f"  Status     : {wf.status.value}")
    click.echo(f"  Template   : {wf.template or '—'}")
    click.echo(f"  Progress   : {_progress_bar(pct)}  ({done_count}/{total} steps)")
    eta = _eta_str(wf)
    if eta:
        click.echo(f"  ETA        : {eta}")
    click.echo(f"  Created    : {_fmt_ts(wf.created_at)}")
    click.echo(f"  Started    : {_fmt_ts(wf.started_at)}")
    click.echo(f"  Completed  : {_fmt_ts(wf.completed_at)}")
    click.echo(f"  Duration   : {_fmt_dur(wf.duration_seconds())}")
    if wf.tags:
        click.echo(f"  Tags       : {', '.join(wf.tags)}")
    click.echo(f"  Full ID    : {wf.id}")
    click.echo()
    click.echo("  Steps:")
    for step in wf.steps:
        dur = f"  ({_fmt_dur(step.duration_seconds())})" if step.duration_seconds() else ""
        deps = f"  ← {', '.join(step.depends_on)}" if step.depends_on else ""
        click.echo(
            f"    {_step_icon(step.status.value)} {step.name:<28} {step.status.value:<12}{dur}{deps}"
        )
        if step.error:
            click.echo(f"       ⚠️  {step.error}")
    click.echo(SEP)


@workflow_cmd.command("create")
@click.option("--name", required=True)
@click.option("--template", default=None, type=click.Choice(list_templates(), case_sensitive=False))
@click.option("--steps", "step_names", default="")
@click.option("--tag", "tags", multiple=True)
@click.option("--meta", "meta_pairs", multiple=True, help="key=value")
@click.option("--json", "output_json", is_flag=True, default=False)
def create_cmd(name, template, step_names, tags, meta_pairs, output_json):
    """Create a new workflow."""
    mgr = get_manager()
    metadata = {}
    for pair in meta_pairs:
        if "=" in pair:
            k, v = pair.split("=", 1)
            metadata[k.strip()] = v.strip()
    steps = None
    if not template:
        if not step_names:
            click.echo("Provide --template or --steps.", err=True)
            raise SystemExit(1)
        steps = [WorkflowStep(name=n.strip()) for n in step_names.split(",") if n.strip()]
    wf = mgr.create(name=name, template=template, steps=steps, metadata=metadata, tags=list(tags))
    if output_json:
        click.echo(json.dumps(wf.to_dict(), indent=2))
        return
    click.echo(f"✅ Created workflow '{wf.name}' [{wf.id[:8]}]")
    click.echo(f"   Template: {wf.template or '—'}  |  Steps: {len(wf.steps)}")
    click.echo(f"   Resume: tokenpak workflow resume {wf.id[:8]}")


@workflow_cmd.command("resume")
@click.argument("wf_id")
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation prompt")
def resume_cmd(wf_id, yes):
    """Resume a paused or incomplete workflow.

    Shows the execution plan (completed, pending, failed steps) before
    resuming, and asks for confirmation unless --yes is passed.
    """
    mgr = get_manager()
    wf = _resolve(mgr, wf_id)

    # Show plan before confirming
    done_count = sum(1 for s in wf.steps if s.is_done())
    total = len(wf.steps)
    [s for s in wf.steps if s.status.value == "failed"]
    [s for s in wf.steps if s.status.value in ("pending",)]
    next_step = wf.next_pending_step()

    click.echo(SEP)
    click.echo(f"  🔁 Resume plan: {wf.name}  [{wf.id[:8]}]")
    click.echo(SEP)
    click.echo(f"  Status   : {wf.status.value}")
    click.echo(f"  Progress : {_progress_bar(wf.completion_pct())}  ({done_count}/{total} steps)")
    click.echo()
    click.echo("  Step plan:")
    for step in wf.steps:
        icon = _step_icon(step.status.value)
        note = ""
        if step.status.value == "failed" and step.error:
            note = f"  ← ERROR: {step.error[:60]}"
        elif step.status.value == "skipped":
            note = "  ← skipped (dependency failed)"
        elif next_step and step.name == next_step.name:
            note = "  ← RESUME HERE"
        deps = f" (needs: {', '.join(step.depends_on)})" if step.depends_on else ""
        click.echo(f"    {icon} {step.name:<28} {step.status.value:<12}{deps}{note}")
    click.echo(SEP)

    if not yes:
        click.confirm("Proceed with resume?", abort=True)

    try:
        wf = mgr.resume(wf.id)
    except ValueError as e:
        click.echo(f"⚠️  {e}", err=True)
        raise SystemExit(1)

    nxt = wf.next_pending_step()
    click.echo(f"🔄 Resumed '{wf.name}' [{wf.id[:8]}]")
    if nxt:
        click.echo(f"   Next step: {nxt.name}")
    else:
        click.echo("   All pending steps blocked or none remain.")


@workflow_cmd.command("cancel")
@click.argument("wf_id")
@click.option("--yes", is_flag=True, default=False)
def cancel_cmd(wf_id, yes):
    """Cancel a workflow and clean up running steps."""
    mgr = get_manager()
    wf = _resolve(mgr, wf_id)
    running = [s.name for s in wf.steps if s.status.value == "running"]
    pending = [s.name for s in wf.steps if s.status.value == "pending"]
    if not yes:
        lines = [f"Cancel workflow '{wf.name}'?"]
        if running:
            lines.append(f"  Running steps that will be stopped: {', '.join(running)}")
        if pending:
            lines.append(f"  Pending steps that will be skipped: {len(pending)}")
        click.echo("\n".join(lines))
        click.confirm("Confirm cancel?", abort=True)
    wf = mgr.cancel(wf.id)
    click.echo(f"🚫 Cancelled '{wf.name}' [{wf.id[:8]}]")
    if running:
        click.echo(f"   Stopped {len(running)} running step(s): {', '.join(running)}")
    click.echo(f"   Skipped {len(pending)} pending step(s).")


@workflow_cmd.command("history")
@click.option("--limit", default=20, show_default=True, type=int)
@click.option("--name", "name_filter", default=None)
@click.option("--json", "output_json", is_flag=True, default=False)
def history_cmd(limit, name_filter, output_json):
    """Show workflow history (newest first)."""
    mgr = get_manager()
    records = mgr.history(limit=limit, name_filter=name_filter)
    if output_json:
        click.echo(json.dumps([r.to_dict() for r in records], indent=2))
        return
    if not records:
        click.echo("No workflow history.")
        return
    click.echo(SEP)
    click.echo(f"  {'STATUS':<12} {'NAME':<28} {'PCT':>5}  {'DUR':>7}  CREATED")
    click.echo(SEP)
    for wf in records:
        pct = f"{wf.completion_pct():.0f}%"
        click.echo(
            f"  {_status_icon(wf.status.value)} {wf.status.value:<10} {wf.name:<28} {pct:>5}  {_fmt_dur(wf.duration_seconds()):>7}  {_fmt_ts(wf.created_at)}  [{wf.id[:8]}]"
        )
    click.echo(SEP)


@workflow_cmd.command("templates")
@click.option("--show", "show_name", default=None)
def templates_cmd(show_name):
    """List available workflow templates."""
    names = list_templates()
    if show_name:
        if show_name not in names:
            click.echo(f"Unknown template '{show_name}'.", err=True)
            raise SystemExit(1)
        steps = template_steps(show_name)
        click.echo(f"\nTemplate: {show_name} ({len(steps)} steps)\n")
        for i, s in enumerate(steps, 1):
            deps = f" ← {', '.join(s.depends_on)}" if s.depends_on else ""
            click.echo(f"  {i}. {s.name}{deps}")
            if s.description:
                click.echo(f"       {s.description}")
        return
    click.echo("\nAvailable templates:\n")
    for name in names:
        click.echo(f"  • {name:<20} ({len(WORKFLOW_TEMPLATES[name])} steps)")
    click.echo()


@workflow_cmd.command("delete")
@click.argument("wf_id")
@click.option("--yes", is_flag=True, default=False)
def delete_cmd(wf_id, yes):
    """Delete a workflow record from disk."""
    mgr = get_manager()
    wf = _resolve(mgr, wf_id)
    if not yes:
        click.confirm(f"Permanently delete workflow '{wf.name}'?", abort=True)
    mgr.delete(wf.id)
    click.echo(f"🗑️  Deleted '{wf.name}' [{wf.id[:8]}]")


@workflow_cmd.command("recover")
@click.option(
    "--type",
    "filter_type",
    default=None,
    help="Filter to a specific workflow type/template (e.g. 'proxy')",
)
def recover_cmd(filter_type):
    """Detect and list interrupted workflows that can be resumed.

    Use --type proxy to surface only incomplete proxy request workflows.
    """
    mgr = get_manager()
    incomplete = mgr.incomplete_workflows()
    if filter_type:
        incomplete = [wf for wf in incomplete if filter_type in wf.tags]
    if not incomplete:
        type_msg = f" (type={filter_type})" if filter_type else ""
        click.echo(f"✅ No incomplete workflows found{type_msg}.")
        return
    type_msg = f" [{filter_type}]" if filter_type else ""
    click.echo(f"⚠️  Found {len(incomplete)} incomplete workflow(s){type_msg}:\n")
    for wf in incomplete:
        nxt = wf.next_pending_step()
        running_step = next((s.name for s in wf.steps if s.status.value == "running"), None)
        click.echo(
            f"  {_status_icon(wf.status.value)} {wf.name}  [{wf.id[:8]}]  — {wf.status.value}"
        )
        if running_step:
            click.echo(f"       Last running step: {running_step}")
        if nxt:
            click.echo(f"       Next step: {nxt.name}")
    click.echo()
    click.echo("Run: tokenpak workflow resume <ID>")
