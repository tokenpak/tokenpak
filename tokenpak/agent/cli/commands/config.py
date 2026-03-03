"""config command — show and set TOKENPAK_* environment variables."""

from __future__ import annotations

import os


TOKENPAK_VARS = [
    ("TOKENPAK_PORT",                    "Proxy listen port"),
    ("TOKENPAK_MODE",                    "Compilation mode (strict|hybrid|aggressive)"),
    ("TOKENPAK_COMPACT",                 "Compaction on/off (0|1)"),
    ("TOKENPAK_COMPACT_THRESHOLD_TOKENS","Compaction threshold (tokens)"),
    ("TOKENPAK_COMPACT_MAX_CHARS",       "Max chars for compressed text"),
    ("TOKENPAK_COMPACT_CACHE_SIZE",      "Compaction cache size"),
    ("TOKENPAK_DB",                      "Monitor DB path"),
    ("TOKENPAK_VAULT_INDEX",             "Vault index path"),
    ("TOKENPAK_INJECT_BUDGET",           "Max vault inject tokens"),
    ("TOKENPAK_INJECT_TOP_K",            "Max vault blocks to inject"),
    ("TOKENPAK_PROXY_URL",               "Proxy URL override"),
]


def run(verbose: bool = False) -> None:
    """Print TOKENPAK_* environment configuration."""
    SEP = "────────────────────────"
    print(f"TOKENPAK  |  Configuration\n{SEP}\n")
    for var, label in TOKENPAK_VARS:
        val = os.environ.get(var, "○ not set")
        print(f"  {label:<40}  {var}={val}")


try:
    import click

    @click.command("config")
    @click.option("--verbose", "-v", is_flag=True)
    def config_cmd(verbose):
        """Show TOKENPAK_* environment configuration."""
        run(verbose=verbose)

except ImportError:
    pass
