"""
TokenPak CLI — fingerprint subcommand.

Commands:
    tokenpak fingerprint sync     — sync fingerprint to intelligence server
    tokenpak fingerprint cache    — show local directive cache status
    tokenpak fingerprint clear-cache [--id <fp-id>]  — clear cached directives
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click


@click.group("fingerprint", help="Fingerprint sync and cache management (Pro+).")
def fingerprint_cmd():
    pass


# ── sync ─────────────────────────────────────────────────────────────────────


@fingerprint_cmd.command("sync", help="Generate and sync a fingerprint, receive directives.")
@click.argument("text", required=False)
@click.option(
    "--file",
    "-f",
    "input_file",
    type=click.Path(exists=True),
    help="Read prompt from file instead of stdin/arg.",
)
@click.option(
    "--messages",
    "messages_file",
    type=click.Path(exists=True),
    help="Read OpenAI-style messages JSON from file.",
)
@click.option(
    "--dry-run", is_flag=True, default=False, help="Show what would be sent without transmitting."
)
@click.option(
    "--privacy",
    type=click.Choice(["minimal", "standard", "full"]),
    default="standard",
    show_default=True,
    help="Privacy level for the fingerprint payload.",
)
@click.option("--ttl", type=int, default=3600, show_default=True, help="Cache TTL in seconds.")
@click.option(
    "--skip-cache",
    is_flag=True,
    default=False,
    help="Bypass local cache and always contact server.",
)
@click.option("--json", "output_json", is_flag=True, default=False, help="Output result as JSON.")
def fingerprint_sync(
    text: Optional[str],
    input_file: Optional[str],
    messages_file: Optional[str],
    dry_run: bool,
    privacy: str,
    ttl: int,
    skip_cache: bool,
    output_json: bool,
) -> None:
    from tokenpak.compression.fingerprinting.generator import FingerprintGenerator
    from tokenpak.compression.fingerprinting.privacy import PrivacyLevel
    from tokenpak.compression.fingerprinting.sync import FingerprintSync

    gen = FingerprintGenerator()

    # Resolve input
    if messages_file:
        with open(messages_file) as f:
            messages = json.load(f)
        fingerprint = gen.generate_from_messages(messages)
    elif input_file:
        content = Path(input_file).read_text()
        fingerprint = gen.generate(content)
    elif text:
        fingerprint = gen.generate(text)
    elif not sys.stdin.isatty():
        content = sys.stdin.read()
        fingerprint = gen.generate(content)
    else:
        click.echo("Error: provide TEXT, --file, --messages, or pipe stdin.", err=True)
        sys.exit(1)

    privacy_level = PrivacyLevel(privacy)
    client = FingerprintSync(ttl=ttl, privacy_level=privacy_level)

    if dry_run:
        from tokenpak.compression.fingerprinting.privacy import apply_privacy

        payload = apply_privacy(fingerprint.to_dict(), privacy_level)
        if output_json:
            click.echo(
                json.dumps(
                    {
                        "dry_run": True,
                        "fingerprint_id": fingerprint.fingerprint_id,
                        "payload_preview": payload,
                    },
                    indent=2,
                )
            )
        else:
            click.echo("── Dry Run ─────────────────────────────────")
            click.echo(f"  Fingerprint ID : {fingerprint.fingerprint_id}")
            click.echo(f"  Total tokens   : {fingerprint.total_tokens:,}")
            click.echo(f"  Segments       : {fingerprint.segment_count}")
            click.echo(f"  Privacy level  : {privacy}")
            click.echo()
            click.echo("  Payload that would be sent:")
            click.echo(json.dumps(payload, indent=4))
        return

    try:
        result = client.sync(fingerprint, dry_run=False, skip_cache=skip_cache)
    except PermissionError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)

    if output_json:
        click.echo(
            json.dumps(
                {
                    "success": result.success,
                    "source": result.source,
                    "fingerprint_id": fingerprint.fingerprint_id,
                    "directives": [d.to_dict() for d in result.directives],
                    "cached_at": result.cached_at,
                    "expires_at": result.expires_at,
                    "error": result.error,
                },
                indent=2,
            )
        )
        return

    status_icon = "✓" if result.success else "⚠"
    source_label = {
        "server": "intelligence server",
        "cache": "local cache",
        "oss_fallback": "OSS fallback",
    }.get(result.source, result.source)

    click.echo(f"{status_icon} Fingerprint synced  [{source_label}]")
    click.echo(f"  ID         : {fingerprint.fingerprint_id}")
    click.echo(f"  Tokens     : {fingerprint.total_tokens:,}")
    click.echo(f"  Directives : {len(result.directives)}")

    if result.error:
        click.echo(f"  Warning    : {result.error}", err=True)

    if result.directives:
        click.echo()
        click.echo("  Directives received:")
        for d in result.directives:
            click.echo(f"    [{d.priority}] {d.action}  — {d.description or d.directive_id}")


# ── cache ─────────────────────────────────────────────────────────────────────


@fingerprint_cmd.command("cache", help="Show local directive cache status.")
@click.option("--json", "output_json", is_flag=True, default=False)
def fingerprint_cache(output_json: bool) -> None:
    from tokenpak.compression.fingerprinting.sync import FingerprintSync

    client = FingerprintSync()
    status = client.cache_status()

    if output_json:
        click.echo(json.dumps(status, indent=2))
        return

    click.echo("── Fingerprint Cache ────────────────────────")
    click.echo(f"  Cache dir  : {status['cache_dir']}")
    click.echo(f"  TTL        : {status['ttl_seconds']}s")
    click.echo(f"  Entries    : {status['entries']}")
    click.echo(f"  Valid      : {status.get('valid', 0)}")
    click.echo(f"  Expired    : {status.get('expired', 0)}")


# ── clear-cache ───────────────────────────────────────────────────────────────


@fingerprint_cmd.command("clear-cache", help="Clear cached directives.")
@click.option(
    "--id", "fp_id", default=None, help="Clear only this fingerprint ID (default: clear all)."
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Skip confirmation prompt.")
def fingerprint_clear_cache(fp_id: Optional[str], yes: bool) -> None:
    from tokenpak.compression.fingerprinting.sync import FingerprintSync

    client = FingerprintSync()

    scope = f"fingerprint {fp_id}" if fp_id else "ALL cached directives"
    if not yes:
        click.confirm(f"Clear {scope}?", abort=True)

    deleted = client.clear_cache(fingerprint_id=fp_id)
    click.echo(f"✓ Cleared {deleted} cache file(s).")
