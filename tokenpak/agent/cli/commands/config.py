"""config command — show and set TOKENPAK_* environment variables and persistent config."""

from __future__ import annotations

import os
import sys

TOKENPAK_VARS = [
    ("TOKENPAK_PORT", "Proxy listen port"),
    ("TOKENPAK_MODE", "Compilation mode (strict|hybrid|aggressive)"),
    ("TOKENPAK_COMPACT", "Compaction on/off (0|1)"),
    ("TOKENPAK_COMPACT_THRESHOLD_TOKENS", "Compaction threshold (tokens)"),
    ("TOKENPAK_COMPACT_MAX_CHARS", "Max chars for compressed text"),
    ("TOKENPAK_COMPACT_CACHE_SIZE", "Compaction cache size"),
    ("TOKENPAK_DB", "Monitor DB path"),
    ("TOKENPAK_VAULT_INDEX", "Vault index path"),
    ("TOKENPAK_INJECT_BUDGET", "Max vault inject tokens"),
    ("TOKENPAK_INJECT_TOP_K", "Max vault blocks to inject"),
    ("TOKENPAK_PROXY_URL", "Proxy URL override"),
    ("TOKENPAK_STATS_FOOTER", "Stats footer after each request (0|1)"),
]

# Keys settable via `tokenpak config set <key> <value>`
# Maps friendly key → env var name and config.json key
_SETTABLE_KEYS: dict[str, tuple[str, str]] = {
    "stats_footer": ("TOKENPAK_STATS_FOOTER", "stats_footer"),
    "metrics.enabled": ("TOKENPAK_METRICS_ENABLED", "metrics.enabled"),
}

_TRUTHY = {"1", "true", "on", "yes"}
_FALSY = {"0", "false", "off", "no"}


def _parse_bool(raw: str) -> bool:
    if raw.lower() in _TRUTHY:
        return True
    if raw.lower() in _FALSY:
        return False
    raise ValueError(f"Cannot parse '{raw}' as bool. Use true/false.")


def run(verbose: bool = False) -> None:
    """Print TOKENPAK_* environment configuration."""
    from tokenpak.agent.config import get_config

    SEP = "────────────────────────"
    print(f"TOKENPAK  |  Configuration\n{SEP}\n")

    cfg = get_config()

    for var, label in TOKENPAK_VARS:
        val = os.environ.get(var, "○ not set")
        print(f"  {label:<40}  {var}={val}")

    print(f"\n{SEP}")
    print("  Persistent config (~/.tokenpak/config.json):")
    if cfg:
        for k, v in cfg.items():
            print(f"    {k} = {v}")
    else:
        print("    (empty)")


def run_set(key: str, value: str) -> None:
    """Set a persistent config value by friendly key name."""
    from tokenpak.agent.config import set_config

    if key not in _SETTABLE_KEYS:
        known = ", ".join(_SETTABLE_KEYS.keys())
        print(f"✖ Unknown config key '{key}'. Known keys: {known}", file=sys.stderr)
        sys.exit(1)

    _, json_key = _SETTABLE_KEYS[key]

    # Coerce value type
    try:
        parsed: bool | str | int = _parse_bool(value)
    except ValueError:
        # Fall back to storing as-is if not a bool token
        parsed = value

    set_config(json_key, parsed)

    flag = "enabled" if parsed else "disabled"
    print(f"✔ {key} → {flag}  (saved to ~/.tokenpak/config.json)")
    if key == "metrics.enabled":
        if parsed:
            print("  Anonymous metrics: token counts, model, compression ratio, latency only.")
            print("  No prompt/response content is ever collected.")
        else:
            print("  Anonymous metrics reporting disabled.")
    else:
        print("  Note: restart proxy for changes to take effect if it is already running.")


try:
    import click

    @click.group("config")
    def config_cmd():
        """Show or set TOKENPAK_* configuration."""
        pass

    @config_cmd.command("show")
    @click.option("--verbose", "-v", is_flag=True)
    def config_show_cmd(verbose):
        """Show TOKENPAK_* environment configuration."""
        run(verbose=verbose)

    @config_cmd.command("set")
    @click.argument("key")
    @click.argument("value")
    def config_set_cmd(key, value):
        """Set a persistent config value. Example: tokenpak config set stats_footer true"""
        run_set(key, value)

    # Keep bare `tokenpak config` (no subcommand) as an alias for show
    @config_cmd.result_callback()
    def _default(*args, **kwargs):
        pass

except ImportError:
    pass
