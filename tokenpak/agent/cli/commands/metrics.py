"""metrics command — preview pending and view history of anonymous metrics.

Usage:
    tokenpak metrics preview          # show records queued for next upload
    tokenpak metrics history [--days N]   # show aggregated daily history
    tokenpak metrics sync             # trigger an immediate sync (dry-run safe)
    tokenpak metrics status           # show enabled/disabled + pending count
"""

from __future__ import annotations

import json

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _metrics_enabled() -> bool:
    from tokenpak.agent.config import get_metrics_enabled

    return get_metrics_enabled()


def _fmt_ratio(r: float) -> str:
    return f"{r * 100:.1f}%" if r else "—"


def _fmt_tokens(n: int) -> str:
    return f"{n:,}"


SEP = "────────────────────────"


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def cmd_status(_args=None) -> None:
    """Show metrics opt-in status and pending record count."""
    from tokenpak.telemetry.anon_metrics import get_store

    enabled = _metrics_enabled()
    store = get_store()
    pending = store.get_pending()

    print(f"TOKENPAK  |  Anonymous Metrics\n{SEP}\n")
    print(f"  Status:      {'● enabled' if enabled else '○ disabled (opt-in)'}")
    print(f"  Pending:     {len(pending)} record(s) awaiting upload")

    if not enabled:
        print()
        print("  Enable with: tokenpak config set metrics.enabled true")
        print("  Data collected: token counts, model, compression ratio, latency only.")
        print("  No prompt or response content is ever sent.")

    if enabled and pending:
        print()
        print("  Run 'tokenpak metrics sync' to upload now, or wait for daily batch.")
    print()


def cmd_preview(_args=None) -> None:
    """Show records queued for the next upload."""
    from tokenpak.telemetry.anon_metrics import get_store

    if not _metrics_enabled():
        print("⚠ Anonymous metrics are disabled. Enable with:")
        print("    tokenpak config set metrics.enabled true")
        return

    store = get_store()
    records = store.get_pending()

    print(f"TOKENPAK  |  Metrics Preview ({len(records)} pending)\n{SEP}\n")

    if not records:
        print("  No pending records. All caught up!")
        return

    print(
        f"  {'DATE':<12} {'MODEL':<30} {'INPUT':>8} {'OUTPUT':>8} {'SAVED':>8} {'RATIO':>7} {'LATENCY':>9}"
    )
    print(f"  {'─'*12} {'─'*30} {'─'*8} {'─'*8} {'─'*8} {'─'*7} {'─'*9}")

    for r in records:
        print(
            f"  {r.date_utc:<12} {r.model[:30]:<30} "
            f"{_fmt_tokens(r.input_tokens):>8} "
            f"{_fmt_tokens(r.output_tokens):>8} "
            f"{_fmt_tokens(r.tokens_saved):>8} "
            f"{_fmt_ratio(r.compression_ratio):>7} "
            f"{r.latency_ms:>8.0f}ms"
        )
    print()


def cmd_history(args=None) -> None:
    """Show aggregated daily metrics history."""
    from tokenpak.telemetry.anon_metrics import get_store

    days = getattr(args, "days", 30) or 30
    raw = getattr(args, "raw", False)

    store = get_store()
    summary = store.daily_summary(days=days)

    if raw:
        print(json.dumps(summary, indent=2))
        return

    print(f"TOKENPAK  |  Metrics History (last {days} days)\n{SEP}\n")

    if not summary:
        print("  No metrics data recorded yet.")
        if not _metrics_enabled():
            print()
            print("  Metrics are disabled. Enable with:")
            print("    tokenpak config set metrics.enabled true")
        return

    print(
        f"  {'DATE':<12} {'REQS':>6} {'INPUT':>10} {'SAVED':>10} {'AVG RATIO':>10} {'AVG LATENCY':>12} {'SYNCED':>8}"
    )
    print(f"  {'─'*12} {'─'*6} {'─'*10} {'─'*10} {'─'*10} {'─'*12} {'─'*8}")

    for row in summary:
        synced_str = f"{row['synced_count']}/{row['requests']}"
        print(
            f"  {row['date_utc']:<12} "
            f"{row['requests']:>6} "
            f"{_fmt_tokens(row['input_tokens'] or 0):>10} "
            f"{_fmt_tokens(row['tokens_saved'] or 0):>10} "
            f"{_fmt_ratio(row['avg_compression'] or 0):>10} "
            f"{(row['avg_latency_ms'] or 0):>10.0f}ms "
            f"{synced_str:>8}"
        )
    print()

    # Totals
    total_reqs = sum(r["requests"] for r in summary)
    total_saved = sum(r["tokens_saved"] or 0 for r in summary)
    total_input = sum(r["input_tokens"] or 0 for r in summary)
    overall_ratio = total_saved / total_input if total_input else 0.0
    print(
        f"  Total: {total_reqs} requests, {_fmt_tokens(total_saved)} tokens saved ({_fmt_ratio(overall_ratio)} avg compression)"
    )
    print()


def cmd_sync(args=None) -> None:
    """Immediately sync pending records to the ingest endpoint."""
    from tokenpak.telemetry.anon_metrics import get_store
    from tokenpak.telemetry.reporter import sync_batch

    if not _metrics_enabled():
        print("⚠ Anonymous metrics are disabled. Enable with:")
        print("    tokenpak config set metrics.enabled true")
        return

    dry_run = getattr(args, "dry_run", False)

    store = get_store()
    pending = store.get_pending()

    if not pending:
        print("✔ No pending metrics records to sync.")
        return

    print(f"Syncing {len(pending)} record(s)…", end=" ", flush=True)
    result = sync_batch(dry_run=dry_run)

    if result["errors"]:
        print(f"⚠ {result['uploaded']} uploaded, {result['skipped']} skipped")
        for e in result["errors"]:
            print(f"  Error: {e}")
    else:
        label = " (dry-run)" if dry_run else ""
        print(f"✔ {result['uploaded']} record(s) synced{label}")


# ---------------------------------------------------------------------------
# Click interface
# ---------------------------------------------------------------------------

try:
    import click

    @click.group("metrics")
    def metrics_cmd():
        """Anonymous metrics: preview, history, sync."""
        pass

    @metrics_cmd.command("status")
    def metrics_status_cmd():
        """Show opt-in status and pending count."""
        cmd_status()

    @metrics_cmd.command("preview")
    def metrics_preview_cmd():
        """Show records queued for next upload."""
        cmd_preview()

    @metrics_cmd.command("history")
    @click.option("--days", default=30, show_default=True, help="Number of days to show")
    @click.option("--raw", is_flag=True, help="Output raw JSON")
    def metrics_history_cmd(days, raw):
        """Show aggregated daily metrics history."""

        class _Args:
            pass

        a = _Args()
        a.days = days
        a.raw = raw
        cmd_history(a)

    @metrics_cmd.command("sync")
    @click.option("--dry-run", is_flag=True, help="Build payload but don't POST")
    def metrics_sync_cmd(dry_run):
        """Immediately sync pending records to ingest endpoint."""

        class _Args:
            pass

        a = _Args()
        a.dry_run = dry_run
        cmd_sync(a)

except ImportError:
    pass
