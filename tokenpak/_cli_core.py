# SPDX-License-Identifier: Apache-2.0
"""TokenPak CLI — canonical implementation.

This is the single source of truth for all CLI commands.  The ``cli/``
package re-exports everything from this module for backward compatibility.
Entry points (pyproject.toml) resolve through ``cli/__init__.py`` -> here.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import socket
import sys
import time
from collections.abc import Iterator, Sized
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    TypedDict,
    cast,
)

from tokenpak._formatting import OutputFormatter, OutputMode, resolve_mode
from tokenpak._formatting import symbols as FS

CommandArgs = argparse.Namespace
CommandHandler = Callable[[CommandArgs], object]
PluginCommand = Callable[[list[str]], int | None]
JsonObject = dict[str, object]

if TYPE_CHECKING:
    Subparsers = argparse._SubParsersAction[argparse.ArgumentParser]
    from tokenpak.cli.goals import GoalManager
    from tokenpak.compression.recipes import CompressionRecipeEngine
    from tokenpak.orchestration.triggers.store import TriggerStore
    from tokenpak.routing.rules import RouteRule, RouteStore
    from tokenpak.telemetry.budget import BudgetTracker
    from tokenpak.telemetry.replay import ReplayStore
else:
    Subparsers = argparse._SubParsersAction


class PreviewBlock(TypedDict):
    type: str
    tokens: int


class PreviewResult(TypedDict):
    input_tokens: int
    output_tokens: int
    saved_tokens: int
    compression_ratio: float
    retained_blocks: list[PreviewBlock]
    removed_blocks: list[PreviewBlock]
    flags: list[str]
    mode: str
    duration_ms: float


class MonitorModelRow(TypedDict):
    model: str
    requests: int
    input_tokens: int
    output_tokens: int
    cache_read: int
    cost: float
    compressed: int
    avg_latency: float


class MonitorSavingsData(TypedDict):
    actual_cost: float
    cache_read: int
    cache_hit_rate: float
    compressed_tokens: int
    raw_input_tokens: int
    total_input: int
    total_output: int


class ProxyCounters(TypedDict, total=False):
    start_time: float
    requests: int
    errors: int
    input_tokens: int
    sent_input_tokens: int
    saved_tokens: int
    protected_tokens: int
    cache_read_tokens: int
    cache_hits: int
    cache_misses: int
    compressed_tokens: int
    cost: float
    cost_saved: float


class CircuitStatus(TypedDict, total=False):
    state: str


class CircuitBreakersPayload(TypedDict, total=False):
    enabled: bool
    any_open: bool
    providers: dict[str, CircuitStatus]


class HealthPayload(TypedDict, total=False):
    admission: JsonObject
    agent_concurrency: JsonObject
    circuit_breakers: CircuitBreakersPayload
    compression_ratio_avg: float | None
    connection_pool: JsonObject
    in_flight_requests: int
    is_degraded: bool
    is_shutting_down: bool
    memory_guard: JsonObject
    requests_errors: int
    requests_total: int
    status: str
    timestamp: str
    uptime_seconds: float
    version: str


class StatsPayload(TypedDict, total=False):
    session: ProxyCounters
    today: ProxyCounters
    compilation_mode: str


class CachePayload(TypedDict, total=False):
    cache_hits: int
    cache_misses: int
    cache_read_tokens: int
    miss_reasons: dict[str, int]


class ProxyVersionPayload(TypedDict, total=False):
    error: str
    version: str
    uptime: int
    pythonVersion: str
    configHash: str


def _json_object(value: object) -> JsonObject:
    """Validate a dynamic JSON value as an object with string keys."""
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return cast(JsonObject, value)
    return {}


def _as_float(value: object, default: float = 0.0) -> float:
    """Convert a dynamic scalar to float without accepting containers."""
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        try:
            return float(value)
        except ValueError:
            pass
    return default


def _as_int(value: object, default: int = 0) -> int:
    """Convert a dynamic scalar to int without accepting containers."""
    if isinstance(value, (int, float, str)) and not isinstance(value, bool):
        try:
            return int(value)
        except ValueError:
            pass
    return default


# ── --no-tui global escape ────────────────────
# Set true when --no-tui is present anywhere on the command line. Stripped
# from sys.argv early in main() so per-subcommand parsers don't need to
# know about it. Honored at every TTY entry point: bare `tokenpak`, `tokenpak
# setup`, and `tokenpak integrate <X>` without `--apply`.
_NO_TUI_FLAG = False


def _no_tui() -> bool:
    return _NO_TUI_FLAG


def _interactive_menu_allowed() -> bool:
    """Whether bare ``tokenpak`` may launch the interactive menu (spec F1/F2).

    The menu runs ONLY when both streams are a TTY, ``--no-tui`` is absent,
    ``TOKENPAK_NONINTERACTIVE`` is unset, CI is not detected, and ``TERM`` is
    not ``dumb``. Every other case falls through to deterministic, exit-0
    non-interactive output.
    """
    if _no_tui():
        return False
    if os.environ.get("TOKENPAK_NONINTERACTIVE"):
        return False
    if os.environ.get("CI"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _emit_bare_json() -> None:
    """Emit deterministic, schema-versioned bare-invocation JSON (spec F3).

    Cheap and stable: cached/unknown status only (no slow probe), a sorted
    command catalog, and stable field names. Never blocks; never fabricates a
    savings figure (unknown -> null).
    """
    import json as _json

    try:
        from tokenpak.cli.commands import menu_status

        status = menu_status.json_snapshot()
    except Exception:
        status = {
            "schema_version": 1,
            "proxy": "unknown",
            "cost_today": None,
            "saved_today": None,
        }
    _ver: str | None
    try:
        from tokenpak import __version__ as package_version

        _ver = package_version
    except Exception:
        _ver = None
    try:
        commands = sorted(_core_command_names())
    except Exception:
        commands = []
    payload = {
        "schema_version": 1,
        "tokenpak_version": _ver,
        "status": status,
        "commands": commands,
    }
    print(_json.dumps(payload, indent=2, sort_keys=True))


# ── Monitor DB Access ────────────────────────────────────────────────────────


def _get_monitor_db_path() -> Optional[Path]:
    """Return the canonical proxy monitor.db path.

    D5 (feed normalization): delegate to ``tokenpak._paths.monitor_db()`` so
    this legacy-core reader, the CLI ``status`` reader, ``doctor``, and the
    proxy writer all resolve the SAME DB through one candidate chain
    (``$TOKENPAK_DB`` -> ``~/.tpk`` -> ``~/.tokenpak`` -> ``~/tokenpak``).
    The previous hand-rolled list omitted ``~/.tpk`` (the canonical TPK home),
    which is the latent split-brain this removes. Returns None when no valid
    monitor DB exists.
    """
    try:
        from tokenpak import _paths

        return _paths.monitor_db(mode="read")
    except Exception:
        return None


def _monitor_db_cost(period: str = "daily") -> Optional[float]:
    """Total estimated_cost from monitor.db for *period*, or ``None``.

    ``None`` means no request store exists or it could not be read. That is a
    different fact from "you spent nothing", and rendering both as $0.0000
    told a user with no data that we had measured their spend and it was zero.
    """
    import sqlite3
    from datetime import date, timedelta

    db = _get_monitor_db_path()
    if db is None:
        return None

    today = date.today()
    if period == "daily":
        since = today.isoformat()
    elif period == "weekly":
        since = (today - timedelta(days=6)).isoformat()
    else:  # monthly
        since = today.replace(day=1).isoformat()

    try:
        conn = sqlite3.connect(str(db), timeout=2)
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(estimated_cost), 0) FROM requests WHERE timestamp >= ?",
            (since,),
        )
        total = float(cur.fetchone()[0] or 0.0)
        conn.close()
        return total
    except Exception:
        return None


def _monitor_db_savings(days: int = 30) -> MonitorSavingsData | None:
    """Return savings summary from monitor.db."""
    import sqlite3
    from datetime import date, timedelta

    db = _get_monitor_db_path()
    if db is None:
        return None

    since = (date.today() - timedelta(days=days)).isoformat()
    try:
        conn = sqlite3.connect(str(db), timeout=2)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """SELECT
                COALESCE(SUM(estimated_cost), 0)      AS actual_cost,
                COALESCE(SUM(input_tokens), 0)         AS total_input,
                COALESCE(SUM(output_tokens), 0)        AS total_output,
                COALESCE(SUM(cache_read_tokens), 0)    AS cache_read,
                COALESCE(SUM(cache_creation_tokens), 0) AS cache_created,
                COALESCE(SUM(compressed_tokens), 0)    AS compressed,
                COALESCE(SUM(protected_tokens), 0)     AS protected
            FROM requests WHERE timestamp >= ? AND status_code < 400""",
            (since,),
        )
        row = cast(JsonObject, dict(cur.fetchone()))
        conn.close()

        actual = _as_float(row["actual_cost"])
        cache_read = _as_int(row["cache_read"])
        total_input = _as_int(row["total_input"])
        compressed = _as_int(row["compressed"])
        total_in = total_input + cache_read

        # Rough baseline: what it would cost without cache/compression
        raw_input = total_input + compressed
        return MonitorSavingsData(
            actual_cost=actual,
            cache_read=cache_read,
            cache_hit_rate=cache_read / total_in if total_in else 0,
            compressed_tokens=compressed,
            raw_input_tokens=raw_input,
            total_input=total_input,
            total_output=_as_int(row["total_output"]),
        )
    except Exception:
        return None


def _monitor_db_models(days: int = 30) -> list[MonitorModelRow]:
    """Return per-model usage from monitor.db."""
    import sqlite3
    from datetime import date, timedelta

    db = _get_monitor_db_path()
    if db is None:
        return []

    since = (date.today() - timedelta(days=days)).isoformat()
    try:
        conn = sqlite3.connect(str(db), timeout=2)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """SELECT
                model,
                COUNT(*) AS requests,
                COALESCE(SUM(input_tokens), 0)          AS input_tokens,
                COALESCE(SUM(output_tokens), 0)         AS output_tokens,
                COALESCE(SUM(cache_read_tokens), 0)     AS cache_read,
                COALESCE(SUM(estimated_cost), 0)        AS cost,
                COALESCE(SUM(compressed_tokens), 0)     AS compressed,
                COALESCE(AVG(latency_ms), 0)            AS avg_latency
            FROM requests WHERE timestamp >= ? AND status_code < 400
            GROUP BY model ORDER BY requests DESC""",
            (since,),
        )
        return [
            MonitorModelRow(
                model=str(r["model"] or "unknown"),
                requests=_as_int(r["requests"]),
                input_tokens=_as_int(r["input_tokens"]),
                output_tokens=_as_int(r["output_tokens"]),
                cache_read=_as_int(r["cache_read"]),
                cost=_as_float(r["cost"]),
                compressed=_as_int(r["compressed"]),
                avg_latency=_as_float(r["avg_latency"]),
            )
            for r in cur.fetchall()
        ]
    except Exception:
        return []


# ── Live Proxy Access ─────────────────────────────────────────────────────────


def _proxy_base_url() -> str:
    """Base URL of this install's proxy.

    Honours TOKENPAK_PROXY_URL, then TOKENPAK_PORT, then the default port.
    Two callers hardcoded ``http://127.0.0.1:8766`` and consulted only
    TOKENPAK_PROXY_URL, so on a host running a proxy on the default port,
    `tokenpak stats` under TOKENPAK_PORT=<other> reported *that* proxy's
    request count and uptime as the user's own — measured numbers, from
    somebody else's traffic.
    """
    explicit = os.environ.get("TOKENPAK_PROXY_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    port = os.environ.get("TOKENPAK_PORT", "8766").strip() or "8766"
    return f"http://127.0.0.1:{port}"


def _proxy_get(path: str, port: Optional[int] = None) -> JsonObject | None:
    """Fetch JSON from running proxy. Returns None if unreachable."""
    import urllib.request as _urlreq

    port = port or int(os.environ.get("TOKENPAK_PORT", "8766"))
    try:
        resp = _urlreq.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2)
        payload = json.loads(resp.read())
        return cast(JsonObject, payload) if isinstance(payload, dict) else None
    except Exception:
        return None


# ── Progressive Disclosure ────────────────────────────────────────────────────


#: Name of the first-run marker. Resolved lazily (see ``_first_run_flag``)
#: rather than bound at import time: a module-level
#: ``Path.home() / ".tokenpak" / ".seen_intro"`` meant that *any* verb — even
#: ``tokenpak version`` — created the legacy home before anything else ran,
#: which pinned path resolution to the legacy directory for the life of the
#: install and created it at the process umask (0775) instead of 0700.
def _paths_mod() -> Any:
    """The canonical path resolver, imported lazily.

    Imported on use so a caller that sets ``TOKENPAK_HOME`` programmatically
    is not racing a module-level binding.
    """
    from tokenpak import _paths

    return _paths


_FIRST_RUN_FLAG_NAME = ".seen_intro"

# Commands shown in quick --help (beginner view)
_QUICK_COMMANDS = ["setup", "start", "demo", "cost", "status"]

# All commands grouped for `tokenpak help`
_COMMAND_GROUPS = {
    "Getting Started": [
        ("setup", "Guided first-run setup"),
        ("start", "Start the proxy"),
        ("stop", "Stop the proxy"),
        ("restart", "Restart the proxy"),
        ("demo", "See compression in action"),
        ("cost", "View API spend"),
        ("status", "Check proxy health"),
        ("logs", "Show recent logs"),
    ],
    "Indexing": [
        ("index", "Index a directory"),
        ("search", "Search indexed content"),
    ],
    "Configuration": [
        ("route", "Manage routing rules"),
        ("recipe", "Manage compression recipes"),
        ("template", "Manage prompt templates"),
        ("budget", "Set budget limits"),
        ("alerts", "Manage alert channels"),
        ("goals", "Track savings goals"),
        ("config", "View and edit config"),
        ("explain", "Explain workflow profiles"),
        ("permissions", "Persistent tiers + per-client launcher permission defaults"),
    ],
    "Versioning": [
        ("version", "Show current version"),
        ("update", "Update tokenpak"),
        ("uninstall", "Un-route (--soft) or purge state + remove package (--hard)"),
    ],
    "Operations": [
        ("benchmark", "Run benchmarks"),
        ("calibrate", "Calibrate workers"),
        ("doctor", "Run diagnostics"),
        ("diagnose", "Full health check"),
        ("dashboard", "Live dashboard"),
        ("timeline", "View savings trend"),
        ("attribution", "Savings by source"),
        ("recommendations", "Ranked, telemetry-backed actions"),
        ("models", "Per-model breakdown"),
        ("forecast", "Cost projections"),
        ("debug", "Toggle debug logging"),
        ("learn", "View learned patterns"),
        ("vault-health", "Vault index health"),
        ("fleet", "Fleet status"),
        ("aggregate", "Aggregate ledger"),
        ("requests", "Live request explorer"),
        ("dispatch", "Workflow control (v0.1-alpha preview): run, decide, deliver"),
    ],
    "Companion": [
        ("claude", "Launch with Claude Code"),
        ("codex", "Launch with Codex"),
        ("creds", "Discover credentials + doctor"),
        ("pak", "Inspect/export/import Paks (MultiPak Pro Phase 1)"),
        ("test", "Interactive A/B test"),
        ("prove", "A/B value proof"),
    ],
    "Advanced": [
        ("trigger", "Manage event triggers"),
        ("macro", "Manage macros"),
        ("fingerprint", "Fingerprint management"),
        ("agent", "Agent coordination"),
        ("lock", "File lock management"),
        ("run", "Schedule macro runs"),
        ("replay", "Replay captured sessions"),
        ("audit", "Audit log surface (Pro)"),
        ("compliance", "Compliance report surface (Pro)"),
        ("validate", "Validate JSON files"),
        ("config-check", "Validate config"),
        ("diff", "Show context changes"),
        ("stats", "Registry stats"),
        ("serve", "Start proxy server"),
        ("retrieval", "Test search retrieval"),
    ],
}

# All known command names (for typo detection)
_ALL_COMMANDS = [cmd for group in _COMMAND_GROUPS.values() for cmd, _ in group]

# Argparse/stub commands advertised in help/registry but not grouped above.
# Kept in sync with the inline typo-detection set in main(); the union of the
# two is the authoritative built-in verb catalog (see _core_command_names).
_EXTRA_KNOWN_COMMANDS = {
    "help",
    "start",
    "stop",
    "restart",
    "logs",
    "version",
    "update",
    "config",
    "setup",
    "compare",
    "leaderboard",
    "report",
    "check-alerts",
    "alerts",
    "watch",
    "integrate",
    "openclaw",
    "savings",
    "recommendations",
    "usage",
    "preview",
    "aggregate",
    "requests",
    "validate-config",
    "vault",
    "vault-health",
    "compress",
    "optimize",
    "last",
    "prune",
    "retrieval",
    "menu",
    "license",
    "plan",
    "activate",
    "deactivate",
    "init",
    "monitor",
    "tip",
    "features",
    "pakplan",
    "home",
}


def _core_command_names() -> set[str]:
    """Return the authoritative set of all built-in CLI verb names.

    Derived from the **live parser**, unioned with the hand-maintained lists.

    It used to be the hand-maintained lists alone, and `main()` uses this set
    to decide whether a verb is real before argparse ever sees it. Two verbs
    the parser accepts — ``upgrade`` and ``telemetry`` — were missing from
    those lists, so typing them produced "❌ Unknown command: 'upgrade'  Did
    you mean: tokenpak update?" while the command sat registered and
    dispatchable a few frames away. For ``upgrade`` that silently voided the
    compatibility shim it exists to provide, and misdirected the user to an
    unrelated verb.

    Plugin discovery also excludes every name in this set, so widening it to
    the real parser strictly improves the guarantee that a plugin cannot
    shadow a built-in.
    """
    names = set(_ALL_COMMANDS) | set(_EXTRA_KNOWN_COMMANDS)
    try:
        names |= registered_command_names()
    except Exception:
        # Never let verb dispatch depend on parser construction succeeding.
        pass
    return names


def registered_command_names(
    parser: argparse.ArgumentParser | None = None,
) -> set[str]:
    """Enumerate every verb the built CLI parser will actually accept.

    This is the authoritative *invokable* command set, read live from the
    argparse sub-parsers rather than any hand-maintained list. The help
    catalog (and the count it advertises) is validated against this so it can
    never drift back into advertising commands the parser won't dispatch — see
    ``tests/test_help_parser_parity.py``.
    """
    import argparse

    if parser is None:
        parser = build_parser()
    names: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.update(action.choices.keys())
    return names


# ── Plugin command discovery (tokenpak.commands entry-point group) ────────────
#
# Installed plugins (e.g. the premium tier) register additional CLI verbs under
# the ``tokenpak.commands`` setuptools entry-point group. Each entry point
# resolves to a callable that takes the remaining argv list and returns an int
# exit code; that callable is the plugin's own gated wrapper, so entitlement
# enforcement lives entirely inside the plugin — the core CLI only routes to it.
#
# Built-in verbs always win on a name collision: a plugin can never shadow a
# command that ships in the core CLI. Discovery is lazy (only walked when an
# unknown verb is seen or full help is rendered) and cached for the process.
#
# Discovery is on by default. Set ``TOKENPAK_ENABLE_PLUGINS=0`` to disable it
# (e.g. for a fully hermetic core-only invocation).

_plugin_commands_cache: dict[str, EntryPoint] | None = None


def _plugins_enabled() -> bool:
    """Return False only when discovery is explicitly disabled via env."""
    val = os.environ.get("TOKENPAK_ENABLE_PLUGINS")
    if val is None:
        return True
    return val.strip().lower() not in ("0", "false", "no", "off")


def _discover_plugin_commands(force: bool = False) -> dict[str, EntryPoint]:
    """Discover CLI verbs registered under the ``tokenpak.commands`` group.

    Returns a mapping of ``verb name -> entry point``, excluding any verb whose
    name collides with a built-in command (built-ins always win). The result is
    cached for the lifetime of the process. Returns an empty dict when discovery
    is disabled or no plugins are installed. Never raises — a broken plugin
    environment must not break the core CLI.
    """
    global _plugin_commands_cache
    if _plugin_commands_cache is not None and not force:
        return _plugin_commands_cache

    discovered: dict[str, EntryPoint] = {}
    if not _plugins_enabled():
        _plugin_commands_cache = discovered
        return discovered

    core_names = _core_command_names()
    try:
        from importlib.metadata import entry_points

        eps = entry_points()
        # Python 3.12+ exposes .select(); older returns a dict-like mapping.
        command_eps: Sequence[EntryPoint]
        if hasattr(eps, "select"):
            command_eps = tuple(eps.select(group="tokenpak.commands"))
        else:  # pragma: no cover - legacy importlib.metadata
            legacy_eps = cast(Mapping[str, Sequence[EntryPoint]], eps)
            command_eps = legacy_eps.get("tokenpak.commands", [])

        for ep in command_eps:
            # Built-in verbs win on collision: never let a plugin shadow core.
            if ep.name in core_names or ep.name in discovered:
                continue
            discovered[ep.name] = ep
    except Exception:
        # Any failure to read entry points leaves the core CLI fully functional.
        discovered = {}

    _plugin_commands_cache = discovered
    return discovered


def _dispatch_plugin_command(verb: str, argv: list[str]) -> int:
    """Invoke a discovered plugin verb's callable with the remaining argv.

    ``argv`` is everything after the verb itself. The loaded callable is the
    plugin's own gated wrapper: an unentitled invocation returns the plugin's
    upgrade stub exit code, while an entitled one runs the real command. The
    core CLI does not inspect or alter that gating — it only routes.

    Returns the plugin callable's int exit code (defaulting to 0 when the
    callable returns ``None``), or a non-zero code if the plugin fails to load.
    """
    plugins = _discover_plugin_commands()
    ep = plugins.get(verb)
    if ep is None:
        return 1
    try:
        func = cast(PluginCommand, ep.load())
    except Exception as exc:  # pragma: no cover - defensive
        print(
            f"❌ Failed to load plugin command '{verb}': {exc}",
            file=sys.stderr,
        )
        return 3
    rc = func(argv)
    return rc if isinstance(rc, int) else 0


def _suggest_command(unknown: str) -> Optional[str]:
    """Return the closest known command name, or None if no good match."""
    matches = difflib.get_close_matches(unknown, _ALL_COMMANDS, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _mark_intro_seen() -> None:
    """Write the first-run flag so the welcome message shows only once.

    Writes to the canonical home only. This is frequently the first state
    TokenPak ever creates, so it is also the call that establishes the home
    directory's permissions — hence ``ensure_home()`` rather than a bare
    ``mkdir``.
    """
    try:
        from tokenpak import _paths

        _paths.ensure_home()
        marker = _paths.write_home() / _FIRST_RUN_FLAG_NAME
        marker.touch()
        # The directory is 0700, but the marker inherits the process umask —
        # every file TokenPak creates gets an explicit mode so the guarantee
        # does not depend on how the user's umask happens to be set.
        _paths.secure_file(marker)
    except Exception:
        pass


def _is_first_run() -> bool:
    """True when no home (canonical or legacy) carries the intro marker.

    Reads across both homes so upgrading users are not shown the first-run
    welcome again.
    """
    try:
        from tokenpak import _paths

        return _paths.resolve_existing(_FIRST_RUN_FLAG_NAME) is None
    except Exception:
        return False


def _print_quick_help() -> None:
    """Print the beginner-friendly --help output."""
    print(
        "Usage: tokenpak <command> [options]\n"
        "\n"
        "TokenPak — LLM Proxy with Prompt Packing\n"
        "\n"
        "Quick Start:\n"
        "  setup        Guided first-run setup\n"
        "  start        Start the proxy (localhost:8766)\n"
        "  stop         Stop the running proxy\n"
        "  restart      Restart the proxy\n"
        "  logs         Show recent proxy logs\n"
        "  serve        Serve the proxy (alias for start)\n"
        "  demo         See Prompt Packing in action\n"
        "  cost         View your API spend\n"
        "  savings      View Savings Ledger\n"
        "  status       Check proxy health\n"
        "\n"
        "Tools:\n"
        "  index        Index a directory for compression\n"
        "  config       Config management (sync, validate, migrate)\n"
        "  dashboard    Real-time health dashboard\n"
        "  doctor       Run diagnostics & auto-fix issues\n"
        "  preview      Preview compression dry-run (see token savings)\n"
        "  compress     Compress text/JSON/code directly\n"
        "  vault        Vault index health diagnostic and repair\n"
        "  diff         Show context changes (Pro)\n"
        "  version      Show current version\n"
        "\n"
        "Run `tokenpak help` for all commands.\n"
        "Run `tokenpak <command> --help` for command details."
    )


def _fetch_proxy_uptime(timeout: float = 0.5) -> str:
    try:
        import urllib.request as _urlreq

        proxy_base = _proxy_base_url()
        with _urlreq.urlopen(f"{proxy_base}/health", timeout=timeout) as _r:
            _hdata = json.loads(_r.read())
        uptime_s = _hdata.get("uptime_seconds")
        if uptime_s is not None:
            h, rem = divmod(int(uptime_s), 3600)
            m = rem // 60
            return f"{h}h {m:02d}m" if h else f"{m}m"
        return "unknown"
    except Exception:
        return "unknown"


def _print_full_help() -> None:
    """Print the power-user grouped help output (tier-aware)."""
    try:
        from tokenpak.cli.commands.help import print_full_help

        print_full_help()
    except Exception:
        # Fallback to static help
        print("TokenPak — LLM Proxy with Context Compression\n")
        print("All Commands:\n")
        for group_name, commands in _COMMAND_GROUPS.items():
            print(f"  {group_name}:")
            for cmd, desc in commands:
                print(f"    {cmd:<14} {desc}")
            print()
        _plugin_cmds = _discover_plugin_commands()
        if _plugin_cmds:
            print("  Pro / plugins:")
            for name in sorted(_plugin_cmds):
                print(f"    {name:<14} Provided by an installed plugin")
            print()
        print("Run `tokenpak <command> --help` for command details.")


def cmd_help(args: CommandArgs) -> None:
    """Show tier-aware help. Pass a command name for details, or --minimal for compact list."""
    try:
        from tokenpak.cli.commands.help import run as help_run

        # Build help_args list from parsed arguments
        help_args = []
        if args.more:
            help_args.append("--more")
        elif args.all:
            help_args.append("--all")
        elif args.minimal:
            help_args.append("--minimal")

        if getattr(args, "cmd_name", None):
            help_args.append(args.cmd_name)

        # If no args, call with empty list (shows default help)
        help_run(help_args)
    except Exception:
        _print_full_help()


# ── TOKENPAK_HOME sandbox guard ───────────────────────────────────────────────
#
# TOKENPAK_HOME exists to sandbox writes (CI, multi-tenant hosts). A symlink
# planted inside the override that points at the live config defeats a purely
# textual check, because open(path, "w") follows links.
#
# This started as a closure inside cmd_setup guarding one path. Review found the
# same write reachable through two other unguarded targets and a second command,
# so it is module-level: a guard that protects one of a command's write targets
# protects nothing.


def _home_override_escape(*candidates: Path) -> Optional[str]:
    """Return a refusal message if any candidate resolves outside TOKENPAK_HOME.

    ``None`` when the override is unset, or when every candidate stays inside it.
    Every path a command may WRITE must be passed — including directories, whose
    creation and logs land outside the sandbox just as a config file would.
    """
    raw = os.environ.get("TOKENPAK_HOME", "").strip()
    if not raw:
        return None
    root = Path(raw).expanduser().resolve()
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            # resolve() follows symlinks, which is the point.
            candidate.resolve().relative_to(root)
        except (ValueError, OSError, RuntimeError):
            # RuntimeError: Path.resolve() raises it on a symlink loop up to
            # Python 3.12. Omitting it turned a refusal into an uncaught
            # traceback.
            return (
                f"✗ Refusing to write outside TOKENPAK_HOME: {candidate}\n"
                f"  resolves outside {root}.\n"
                "  TOKENPAK_HOME exists to sandbox; a symlinked target defeats that."
            )
        # Python 3.13 resolve() returns a symlink loop unresolved instead of
        # raising, and the unresolved path sits inside root — so containment
        # passes and the later write raises ELOOP uncaught. Probe the loop
        # directly: stat() is version-independent (kernel ELOOP). A missing
        # path is a legitimate not-yet-created write target.
        try:
            os.stat(candidate)
        except FileNotFoundError:
            pass
        except OSError:
            return (
                f"✗ Refusing to write through an unresolvable symlink: {candidate}\n"
                "  the target cannot be opened (symlink loop or unreadable link chain)."
            )
    return None


# ── Alias commands ────────────────────────────────────────────────────────────


def cmd_init(args: CommandArgs) -> int:
    """Guided first-run setup wizard: API key, port, vault path."""
    import os as _os
    from pathlib import Path as _Path

    import click

    from tokenpak import _paths as _p
    from tokenpak.cli.exit_codes import EXIT_OK, EXIT_USAGE

    config_dir = _p.write_home()
    # Canonical writes go to config.yaml. This wrote config.json, which the
    # rest of the product treats as a *read-compatibility* format — so a user
    # who ran `init` produced state that `setup` would not have produced and
    # that nothing else writes.
    config_file = _p.config_write_path()
    existing_config = _p.config_read_path()
    env_file = config_dir / ".env"

    # `init` had no sandbox guard at all — a second writer of config_write_path()
    # plus .env. Driven through a pty it overwrote the real config while printing
    # a success banner naming the sandbox, so the false claim rode on top of the
    # data loss. Guarded before any prompt, so the refusal costs no keystrokes.
    _escape = _home_override_escape(config_dir, config_file, env_file)
    if _escape:
        from tokenpak.cli.exit_codes import EXIT_FAILURE as _EXIT_FAILURE

        print(_escape)
        return _EXIT_FAILURE

    # `init` is interactive by nature. Every prompt below aborts on a closed
    # stdin, and click raises Abort — which surfaced as a raw traceback and a
    # bare exit 1. Fail once, at the top, with the same contract `setup` uses:
    # EOF is not an answer.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        click.echo("`tokenpak init` is interactive and stdin is not a terminal.")
        click.echo("For a scripted run, use setup, which takes its answers as flags:")
        click.echo("  tokenpak setup --profile balanced --yes")
        return EXIT_USAGE

    # Non-destructive: warn if already configured
    if existing_config is not None:
        overwrite = click.confirm(
            f"Config already exists at {existing_config}. Overwrite?", default=False
        )
        if not overwrite:
            click.echo("Init cancelled. Run `tokenpak start` to start the proxy.")
            return EXIT_OK

    click.echo("\n✨ TokenPak Init — Guided Setup\n")

    # ── API key ──────────────────────────────────────────────────────────────
    click.echo("Step 1/3: API Key")
    click.echo("  Choose how to provide your Anthropic API key:")
    click.echo("  [1] Environment variable (already set, e.g. ANTHROPIC_API_KEY)")
    # Print the resolved path, not a hardcoded ~/.tokenpak/ that is wrong on
    # every install using TOKENPAK_HOME or the canonical home.
    click.echo(f"  [2] Enter key now (saved to {env_file})")

    key_choice = click.prompt("Choice", type=click.Choice(["1", "2"]), default="1")

    api_key: str = ""
    api_key_source: str = "env"

    if key_choice == "1":
        # Probe common env var names
        for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
            val = _os.environ.get(var, "")
            if val.strip():
                api_key = val.strip()
                api_key_source = f"env:{var}"
                click.echo(f"  \u2705 Found {var} in environment")
                break
        if not api_key:
            # Let user name the env var explicitly
            var_name = click.prompt(
                "  Environment variable name",
                default="ANTHROPIC_API_KEY",
            )
            api_key = _os.environ.get(var_name, "").strip()
            api_key_source = f"env:{var_name}"
            if not api_key:
                click.echo(
                    f"  \u26a0\ufe0f  {var_name} is not set. Set it before starting the proxy."
                )
    else:
        # Enter key directly
        api_key = click.prompt("  Paste your Anthropic API key", hide_input=True).strip()
        if not api_key:
            click.echo("  \u274c API key cannot be empty.")
            return EXIT_USAGE
        # Write to .env file
        config_dir.mkdir(parents=True, exist_ok=True)
        env_file.write_text(f"ANTHROPIC_API_KEY={api_key}\n")
        env_file.chmod(0o600)
        api_key_source = f"file:{env_file}"
        click.echo(f"  \u2705 API key saved to {env_file} (mode 600)")

    # Basic non-empty validation
    if not api_key:
        click.echo("  \u26a0\ufe0f  No API key available. You can still continue and set it later.")

    # ── Port ─────────────────────────────────────────────────────────────────
    click.echo("\nStep 2/3: Proxy Port")
    port = click.prompt("  Port", type=int, default=8766)

    # ── Vault path ────────────────────────────────────────────────────────────
    click.echo("\nStep 3/3: Vault Path (optional)")
    click.echo("  The vault is a directory TokenPak uses for context injection.")
    vault_input = click.prompt(
        "  Vault path (leave blank to skip)",
        default="",
        show_default=False,
    ).strip()
    vault_path = str(_Path(vault_input).expanduser()) if vault_input else ""

    # ── Write the canonical config ───────────────────────────────────────────
    config: JsonObject = {
        "version": "1.0",
        "port": port,
        "api_key_source": api_key_source,
    }
    if vault_path:
        config["vault_path"] = vault_path

    import yaml as _yaml

    _p.ensure_home()
    config_dir.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w") as f:
        _yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    _p.secure_file(config_file)

    # ── Done ─────────────────────────────────────────────────────────────────
    click.echo("\n" + "\u2500" * 50)
    click.echo("\u2705 TokenPak is configured and ready to go!\n")
    click.echo(f"   Config:     {config_file}")
    click.echo(f"   Port:       {port}")
    if vault_path:
        click.echo(f"   Vault:      {vault_path}")
    click.echo()
    click.echo("Next step:")
    click.echo("   tokenpak start\n")
    return EXIT_OK


def cmd_setup(args: CommandArgs) -> int:
    """Interactive wizard for first-time TokenPak configuration.

    Scriptable via ``--profile`` / ``--port`` / ``--yes``, because a wizard
    that can only be driven by a human keyboard cannot be tested in CI or a
    container — and because EOF must never stand in for an answer.
    """
    import os
    import subprocess

    import yaml

    from tokenpak import _paths as _tp_paths
    from tokenpak.cli.exit_codes import EXIT_FAILURE

    from .core.profiles import get_profile

    cli_profile = getattr(args, "profile", None)
    cli_port = getattr(args, "port", None)
    assume_yes = bool(getattr(args, "yes", False))

    valid_profiles = ("minimal", "balanced", "aggressive")
    if cli_profile is not None and cli_profile not in valid_profiles:
        print(f"Error: unknown profile '{cli_profile}'. Choose one of: {', '.join(valid_profiles)}")
        return 2

    # Writes go to the canonical home; an existing config is honoured wherever
    # it currently lives so reconfiguring does not silently fork state.
    config_dir = _tp_paths.write_home()
    config_file = _tp_paths.config_write_path()
    existing_config = _tp_paths.config_read_path()
    # ...but never outside an explicit TOKENPAK_HOME. That override exists to
    # sandbox (the standard names CI and multi-tenant hosts), and
    # `read_candidates()` deliberately also lists the real homes so a caller can
    # *find* older state — its docstring is explicit that doing so never implies
    # a valid write target. Honouring a config discovered there turns a sandboxed
    # run into a write against the operator's live config: `TOKENPAK_HOME=/tmp/x
    # tokenpak setup --yes` wrote nothing to /tmp/x and overwrote the real
    # ~/.tokenpak/config.yaml, with no backup.
    if os.environ.get("TOKENPAK_HOME", "").strip():
        if existing_config is not None and _home_override_escape(existing_config):
            existing_config = None
    # EVERY write target must resolve inside the override, not just the config
    # file. Guarding config_file alone still destroyed the real config through
    # config_dir: `startup_log = config_dir / "proxy-startup.log"` is opened "w",
    # so a planted symlink took the startup banner straight into the live config
    # (51 → 1041 bytes, no backup). A guard that covers one of a command's write
    # targets protects nothing.
    _escape = _home_override_escape(config_dir, config_file)
    if _escape:
        print(_escape)
        return EXIT_FAILURE
    is_tty = sys.stdin.isatty() and sys.stdout.isatty()

    # Check for existing config
    if existing_config is not None:
        config_file = existing_config
        print(f"Configuration already exists at {config_file}")
        if assume_yes:
            print("Reconfiguring (--yes).")
        elif not is_tty:
            print("Non-interactive mode: skipping reconfigure. Pass --yes to overwrite.")
            return 0
        else:
            try:
                response = input("Reconfigure? (yes/no) [no]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nSetup cancelled.")
                return 1
            if response not in ("yes", "y"):
                print("Setup cancelled.")
                return 0

    # Detect API keys from environment
    print("\n🔍 Scanning for API keys...\n")
    api_keys = {}

    if os.environ.get("ANTHROPIC_API_KEY"):
        print("✅ Found Anthropic API key")
        api_keys["anthropic"] = os.environ["ANTHROPIC_API_KEY"]

    if os.environ.get("OPENAI_API_KEY"):
        print("✅ Found OpenAI API key")
        api_keys["openai"] = os.environ["OPENAI_API_KEY"]

    if os.environ.get("GOOGLE_API_KEY"):
        print("✅ Found Google API key")
        api_keys["google"] = os.environ["GOOGLE_API_KEY"]

    provider: str | None = None
    if api_keys:
        # Auto-detect a primary provider only for the optional direct-key path.
        available_providers = list(api_keys.keys())
        default_provider = available_providers[0]
        print(f"\nDetected optional provider keys: {', '.join(available_providers)}")
        provider = (
            input(f"Which direct-key provider should be the default? [{default_provider}]: ")
            .strip()
            .lower()
            if is_tty
            else default_provider
        )
        if not provider:
            provider = default_provider
        if provider not in api_keys:
            print(f"Error: no API key was detected for {provider}.")
            return 2
    else:
        print("ℹ️  No provider API keys detected — continuing without them.")
        print("   Authenticated clients can use their existing OAuth/session credentials.")
        print("   TokenPak will preserve the client's selected or default model.")

    # Port precedence: --port, then TOKENPAK_PORT, then the prompt/default.
    # TOKENPAK_PORT must be honoured here because the port-conflict message
    # below tells users to re-run with it — an instruction that has to work.
    try:
        env_port: Optional[int] = int(os.environ["TOKENPAK_PORT"])
    except (KeyError, ValueError):
        env_port = None

    default_port = cli_port or env_port or 8766
    if cli_port is not None or env_port is not None:
        port = default_port
    else:
        port_input = input(f"Port number [{default_port}]: ").strip() if is_tty else ""
        try:
            port = int(port_input) if port_input else default_port
        except ValueError:
            print(f"Error: '{port_input}' is not a valid port number.")
            return 2

    # Ask for profile
    print("\nChoose a compression profile:")
    # No savings percentages here. TokenPak cannot know what a given
    # workload will save before measuring it, and a number printed at the
    # moment of choice is read as a promise. Savings are reported only
    # after measurement, with the input and configuration identified.
    print("  [1] minimal    — compression only; the most conservative option")
    print("  [2] balanced   — compression + caching + routing (recommended default)")
    print("  [3] aggressive — all modules enabled; most aggressive rewriting")
    print()
    print("  Savings depend on your traffic. Measure yours with `tokenpak savings`")
    print("  once requests have flowed through the proxy.")

    profile_map = {"1": "minimal", "2": "balanced", "3": "aggressive"}
    if cli_profile is not None:
        profile_name = cli_profile
        print(f"\nProfile: {profile_name} (from --profile)")
    elif is_tty:
        profile_input = input("\nProfile [2]: ").strip()
        profile_name = profile_map.get(profile_input, "balanced")
    else:
        # EOF is not consent. Previously `tokenpak setup </dev/null` silently
        # selected "balanced", wrote a config and spawned a background daemon
        # without a single answered prompt. Non-interactive runs must state
        # their intent explicitly.
        print()
        print("Cannot prompt for a profile: stdin is not a terminal.")
        print("Re-run with the choice stated explicitly, for example:")
        print("  tokenpak setup --profile balanced --yes")
        print()
        print("Profiles: minimal | balanced | aggressive")
        return 2

    # Build base config
    proxy_config: JsonObject = {"port": port, "host": "localhost"}
    if provider is not None:
        proxy_config["provider"] = provider
    config: JsonObject = {
        "proxy": proxy_config,
        # Provider keys are optional and remain client-owned.  Keep the legacy
        # field present for older validators without persisting environment
        # credentials into TokenPak configuration.
        "api_keys": {},
        "modules": {},
    }

    # Apply profile
    profile = get_profile(profile_name)
    config["modules"] = profile["features"]
    config["profile"] = profile_name

    # Create config directory (0700) and write config (0600). The config can
    # carry provider settings, so it must not be group/world readable.
    _tp_paths.ensure_home()
    config_dir.mkdir(parents=True, exist_ok=True)
    with open(config_file, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    _tp_paths.secure_file(config_file)

    print(f"\n✅ Configuration saved to {config_file}")
    print(f"   Profile: {profile_name} — {profile['description']}")

    # Starting a background daemon is opt-in. Setup used to spawn one
    # unasked and then tell the user, in its own next-steps, to stop it
    # before taking a first measured receipt — creating a state it
    # immediately instructed them to undo. Configuring and running are two
    # decisions; setup owns the first one.
    if not bool(getattr(args, "start", False)):
        print("\nConfigured. No proxy was started.")
        print("\nNext steps:")
        print(f"  1. Start it when you want it:  tokenpak start   (port {port})")
        print("  2. Route your client:          tokenpak integrate")
        print("  3. Check state any time:       tokenpak status")
        print("\n  To configure and start in one step: tokenpak setup --start")
        return 0

    # Start the proxy
    print("\n🚀 Starting proxy...\n")

    # `sys` is already imported at module top (line 15); the redundant local
    # `import sys` here was causing F823 because Python scoping made `sys`
    # a local in cmd_setup() and earlier `sys.stdin.isatty()` calls at the
    # top of the function would have raised UnboundLocalError.

    # Launch via the module entrypoint, which is how the proxy actually runs.
    #
    # This previously searched for a `proxy.py` file and ran it as a script. The
    # first candidate it found — `tokenpak/runtime/proxy.py` — is a four-line
    # compatibility re-export with no `__main__` block, so the child imported it
    # and exited 0 without ever serving. Setup's auto-start had therefore been
    # inert, and the old "probe the port, print ✅ either way" logic is exactly
    # what kept that invisible.
    launch_cmd = [sys.executable, "-m", "tokenpak.proxy"]

    from tokenpak.core.runtime import lifecycle as _lifecycle

    # Precheck: a listener already on our port means we cannot verify that any
    # health response is ours. Report the conflict instead of spawning a child
    # that will die on bind and then claiming the stranger's /health as success.
    if _lifecycle.port_in_use(port):
        existing = _lifecycle.snapshot(port)
        looks_like_tokenpak = bool(existing.health_payload.get("version")) and bool(
            existing.health_payload.get("status")
        )
        print(f"⚠️  Port {port} is already in use.")
        if existing.running:
            print("   A TokenPak proxy that this install started is already running there.")
            print(f"   Config saved to {config_file}. Nothing further to do —")
            print("   run `tokenpak status` to check it, or `tokenpak restart` to reload.")
            return 0
        if looks_like_tokenpak:
            ver = existing.health_payload.get("version")
            print(f"   A TokenPak proxy (v{ver}) is already serving that port, but it was")
            print("   not started by this install, so TokenPak will not manage or stop it.")
            print("   If that proxy is yours, you are already set up — run `tokenpak status`.")
            print("   Otherwise pick another port:")
        else:
            print("   The listener there did not identify itself as a TokenPak proxy.")
            print("   TokenPak did NOT start a proxy, and your client is NOT routed.")
            print("   Free the port, or pick another:")
        print(f"     TOKENPAK_PORT=<port> tokenpak setup --profile {profile_name} --yes")
        return 1

    env = os.environ.copy()
    env["TOKENPAK_PORT"] = str(port)
    # Capture the child's stderr so a startup failure can be reported instead of
    # discarded. DEVNULL here meant a crashing proxy left the user with nothing.
    startup_log = config_dir / "proxy-startup.log"
    try:
        log_handle: Any = open(startup_log, "w")
    except OSError:
        log_handle = subprocess.DEVNULL
    proc = subprocess.Popen(
        launch_cmd,
        env=env,
        start_new_session=True,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )

    # Verify the child before recording it. The PID file is written only for a
    # process we have confirmed is alive and serving, so a stale PID cannot be
    # mistaken later for a running proxy.
    snap = _lifecycle.await_start(proc, port)
    if not snap.running:
        print("❌ Proxy did not start.")
        for reason in snap.reasons:
            print(f"   • {reason}")
        tail = ""
        try:
            tail = "".join(startup_log.read_text().splitlines(keepends=True)[-8:]).rstrip()
        except OSError:
            pass
        if tail:
            print("\n   Last output from the proxy:")
            for line in tail.splitlines():
                print(f"     {line}")
            print(f"\n   Full log: {startup_log}")
        print(f"\n   Config was saved to {config_file}.")
        print("   Diagnose with: tokenpak doctor")
        print("   Retry with:    tokenpak start   (or TOKENPAK_PORT=<port> tokenpak start)")
        _lifecycle.clear_pid()
        return 1

    _lifecycle.write_pid(proc.pid)
    stats_data = _proxy_get("/stats", port=port) or {}
    mode = stats_data.get("compilation_mode")
    mode_suffix = f" (mode: {mode})" if isinstance(mode, str) and mode else ""
    print(f"✅ Proxy running on http://localhost:{port}{mode_suffix}  (PID {proc.pid})")
    if snap.owned is None:
        print("   Note: this proxy build does not report its PID on /health, so")
        print("         ownership could not be cross-checked.")

    # Next steps. Routing state is reported honestly rather than assumed: setup
    # does not route the client, so telling the user they are done would be a
    # false claim, and doctor would immediately contradict it.
    print("\nNext steps:")
    if snap.routed:
        print(f"  1. Your client is already routed to http://localhost:{port}.")
    else:
        print(f"  1. Point your LLM client at http://localhost:{port}")
        print("     Run `tokenpak integrate` for per-client instructions.")
    print("  2. Run: tokenpak status    (check health and routing)")
    print("  3. Run: tokenpak savings   (measured savings, once traffic flows)")
    print()
    print("💡 Quick commands:")
    print("  tokenpak stop       — stop the proxy")
    print("  tokenpak status     — check proxy health")
    print("  tokenpak doctor     — diagnose setup problems")
    print()
    return 0


def cmd_start(args: CommandArgs) -> int:
    """Start the proxy on localhost:8766 (launches proxy.py).

    Returns a documented exit code. It previously returned ``None`` on every
    path, including the ones where the proxy did not start, so a script that
    ran `tokenpak start && tokenpak status` saw success after a failed boot.
    """
    import subprocess

    from tokenpak.cli.exit_codes import (
        EXIT_CORRUPT_STATE,
        EXIT_NOT_CONFIGURED,
        EXIT_OK,
        EXIT_RUNTIME_UNAVAILABLE,
    )
    from tokenpak.core.runtime import lifecycle as _lifecycle

    # --port is declared on this command and was being ignored: the port came
    # from the environment only, so `tokenpak start --port 8799` ran its
    # ownership preflight against 8766 and refused whenever anything else held
    # the default port. Explicit flag wins, then TOKENPAK_PORT, then the default.
    _flag_port = getattr(args, "port", None)
    port = (
        int(_flag_port) if _flag_port is not None else int(os.environ.get("TOKENPAK_PORT", "8766"))
    )

    # Validate config on boot (P1-T5)
    from tokenpak.core.config_loader import config_load_error, load_config

    _config = load_config()

    # A config that exists but cannot be parsed is not a missing config.
    # Validating the empty dict the loader falls back to produced a confident
    # and wrong diagnosis — "Required field 'api_keys' is missing" — for a
    # file whose real problem was a YAML syntax error on line 2.
    _bad = config_load_error()
    if _bad is not None:
        _path, _why = _bad
        import sys as _sys

        print(f"\n✗ Config file could not be parsed: {_path}", file=_sys.stderr)
        for _line in str(_why).splitlines():
            print(f"    {_line}", file=_sys.stderr)
        print("\nThe file exists but is not valid YAML. Nothing was started.", file=_sys.stderr)
        print("  • Fix the syntax above, or", file=_sys.stderr)
        print("  • Move it aside and run 'tokenpak setup' to write a fresh one", file=_sys.stderr)
        return EXIT_CORRUPT_STATE

    try:
        from tokenpak.core.config_validator import ConfigValidator

        validator_factory = cast(Callable[[], ConfigValidator], ConfigValidator)
        _validator = validator_factory()
        _errors = _validator.validate(_config)
        if _errors:
            import sys as _sys

            print(f"\n✗ Config validation failed ({len(_errors)} error(s)):", file=_sys.stderr)
            for _err in _errors:
                print(f"  {_err}", file=_sys.stderr)
            print("\nFix config and retry:", file=_sys.stderr)
            print("  • Run 'tokenpak setup' to regenerate the local config", file=_sys.stderr)
            print(
                "  • Provider API keys are optional when the client supplies OAuth/session auth",
                file=_sys.stderr,
            )
            print("  • Or use: tokenpak config-check <file>", file=_sys.stderr)
            return EXIT_NOT_CONFIGURED
    except Exception as _e:
        print(f"Warning: Config validation skipped ({_e})")

    # One observation of lifecycle state, shared with setup, stop, restart,
    # status and doctor. These commands previously each decided for themselves
    # what "running" meant, and they did not agree: start probed the port,
    # stop read a PID file in a different home than setup wrote it to, so
    # `tokenpak stop` after `tokenpak setup` reported no proxy was running.
    snap = _lifecycle.snapshot(port)

    if snap.running:
        stats = _proxy_get("/stats", port=port) or {}
        mode = stats.get("compilation_mode")
        reqs = snap.health_payload.get("requests_total", "unknown")
        mode_text = f", mode={mode}" if isinstance(mode, str) and mode else ""
        print(f"Proxy already running (port {port}{mode_text}, {reqs} requests).")
        return EXIT_OK

    if snap.health_ok and not snap.pid_alive:
        # Something is serving that port, but it is not a child we recorded.
        print(f"A proxy is already answering on port {port}, but this install did not start it.")
        print("   TokenPak will not manage a process it does not own.")
        print("   Run `tokenpak status` to inspect it, or choose another port:")
        print("     tokenpak start --port <port>")
        return EXIT_RUNTIME_UNAVAILABLE

    if snap.pid_alive and not snap.health_ok:
        print(
            f"Proxy process exists (PID {snap.pid}) but is not answering. Try `tokenpak restart`."
        )
        return EXIT_RUNTIME_UNAVAILABLE

    if snap.foreign_listener:
        print(f"Port {port} is in use by something that is not a TokenPak proxy.")
        print("   Free the port, or choose another:")
        print("     TOKENPAK_PORT=<port> tokenpak start")
        return EXIT_RUNTIME_UNAVAILABLE

    # A PID on file that is not alive is stale — clear it rather than leaving
    # a record that later reads as a running proxy.
    recorded_pid, _ = _lifecycle.read_pid()
    if recorded_pid is not None and not _lifecycle.pid_alive(recorded_pid):
        _lifecycle.clear_pid()

    env = os.environ.copy()
    env["TOKENPAK_PORT"] = str(port)

    if os.environ.get("TOKENPAK_USE_MONOLITH") == "1":
        candidates = [
            Path(__file__).resolve().parent / "runtime" / "proxy.py",
            Path(__file__).resolve().parent.parent / "proxy.py",
            Path.home() / "tokenpak" / "proxy.py",
        ]
        proxy_path = next((c for c in candidates if c.exists()), None)
        if not proxy_path:
            print("Error: proxy.py not found. Falling back to legacy server.")
            serve_args = argparse.Namespace(port=port, telemetry=False, ingest=False, workers=1)
            cmd_serve(serve_args)
            return EXIT_OK
        proc = subprocess.Popen(
            [sys.executable, str(proxy_path)],
            env=env,
            cwd=str(proxy_path.parent),
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tokenpak.proxy.server"],
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # Verify the child before recording it: a PID file written ahead of the
    # check is a claim we have not earned, and it is what let a dead child be
    # reported later as a running proxy.
    started = _lifecycle.await_start(proc, port)
    if not started.running:
        print(f"❌ Proxy did not start on port {port}.")
        for reason in started.reasons:
            print(f"   • {reason}")
        print("   Diagnose with: tokenpak doctor")
        _lifecycle.clear_pid()
        return EXIT_RUNTIME_UNAVAILABLE

    _lifecycle.write_pid(proc.pid)
    stats = _proxy_get("/stats", port=port) or {}
    mode = stats.get("compilation_mode")
    mode_suffix = f" (mode: {mode})" if isinstance(mode, str) and mode else ""
    print(f"\n✅ Proxy running on http://localhost:{port}{mode_suffix}  (PID {proc.pid})\n")
    print("Next steps:")
    if started.routed:
        print("  1. Your client is routed to TokenPak.")
    else:
        print(f"  1. Set your LLM client's base URL to http://localhost:{port}")
    print("  2. Run: tokenpak status    (check health)")
    print("  3. Run: tokenpak savings   (see measured savings once traffic flows)")
    from tokenpak import _paths as _p

    if not _p.is_configured():
        print()
        print("💡 No config found yet. Run: tokenpak setup")
    return EXIT_OK


def cmd_stop(args: CommandArgs) -> None:
    """Stop the proxy this install started."""
    import signal as _signal

    from tokenpak.core.runtime import lifecycle as _lifecycle

    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    pid, source = _lifecycle.read_pid()

    if pid is None:
        print("No proxy PID on record — this install has not started a proxy.")
        # Distinguish "nothing running" from "running, but not ours". Saying
        # "is the proxy running?" while something answers on the port is the
        # kind of answer that sends people in circles.
        if _lifecycle.port_in_use(port):
            print(f"   Something is listening on port {port}, but TokenPak did not start it,")
            print("   so it will not be stopped. Run `tokenpak status` to inspect it.")
        else:
            print("   Tip: run `tokenpak status` to check.")
        return

    if not _lifecycle.pid_alive(pid):
        _lifecycle.clear_pid()
        print(f"Proxy was not running (stale PID {pid} from {source} removed).")
        return

    try:
        os.kill(pid, _signal.SIGTERM)
    except ProcessLookupError:
        _lifecycle.clear_pid()
        print(f"Proxy was not running (stale PID {pid} removed).")
        return
    except Exception as e:
        print(f"Error stopping proxy (PID {pid}): {e}")
        return

    _lifecycle.clear_pid()
    print(f"Proxy stopped (PID {pid}).")


def cmd_restart(args: CommandArgs) -> None:
    """Restart the proxy (stop + start)."""
    cmd_stop(args)
    time.sleep(1)
    cmd_start(args)


def cmd_logs(args: CommandArgs) -> None:
    """Show recent proxy logs."""
    from tokenpak import _paths as _p

    # Resolved across homes, and including the startup log setup captures —
    # a proxy that dies during boot writes there and nowhere else, which is
    # exactly the case a user runs `tokenpak logs` to understand.
    log_candidates = [
        c
        for c in (
            _p.resolve_existing("proxy.log"),
            _p.resolve_existing("proxy-startup.log"),
        )
        if c is not None
    ] + [Path("/tmp/tokenpak-proxy.log")]
    lines = getattr(args, "lines", 50)
    for log_path in log_candidates:
        if log_path.exists():
            try:
                all_lines = log_path.read_text(errors="replace").splitlines()
                for line in all_lines[-lines:]:
                    print(line)
                return
            except Exception as e:
                print(f"Could not read {log_path}: {e}")
                return
    print("No proxy log file found.")
    print("The proxy writes logs to ~/.tokenpak/proxy.log when running.")


# ── End Progressive Disclosure ────────────────────────────────────────────────

from .compression.processors import get_processor
from .compression.wire import pack
from .core.registry import Block, BlockRegistry
from .orchestration.calibration import calibrate_workers, get_recommended_workers
from .telemetry.budget_allocator import BudgetBlock, quadratic_allocate
from .telemetry.miss_detector import DEFAULT_GAPS_PATH, should_expand_retrieval
from .telemetry.tokens import cache_info, count_tokens, truncate_to_tokens
from .vault.walker import walk_directory

# Batch size for SQLite transactions
BATCH_SIZE = 100


def _process_file(
    args: tuple[str, str] | tuple[str, str, bool],
) -> tuple[str, str, Block] | None:
    """
    Process a single file into a block (CPU-bound, parallelizable).

    Args: (path, file_type) or (path, file_type, no_treesitter)
    Returns: (path, content, Block) or None if skipped
    """
    if len(args) == 3:
        path, file_type, no_treesitter = args
    else:
        path, file_type = args
        no_treesitter = False
    try:
        content = Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None

    if not content.strip():
        return None

    processor = get_processor(file_type, no_treesitter=no_treesitter)
    if not processor:
        return None

    process_text = cast(Callable[[str, str], str], processor.process)
    compressed = process_text(content, path)

    block = Block(
        path=path,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        version=1,
        file_type=file_type,
        raw_tokens=count_tokens(content),
        compressed_tokens=count_tokens(compressed),
        compressed_content=compressed,
        quality_score=1.0,
        importance=5.0,
    )
    return path, content, block


def _cmd_reindex(args: CommandArgs) -> None:
    """``tokenpak index --reindex-all`` and ``--reindex-path <path>``.

    Reads registered directories from ``~/.tokenpak/vault.yaml`` and runs the
    existing block-registry indexer against each one. Updates per-path index
    health metadata in the same vault.yaml so the doctor staleness check
    can read it.

    OSS — no license check. Schedule fields (`schedule`,
    `expected_interval_seconds`) are written by the paid surface but
    are merely passive metadata here; the OSS path always reindexes on
    demand.
    """
    from tokenpak.vault import config as vault_config

    cfg_path = vault_config.default_config_path()
    cfg = vault_config.load(cfg_path)

    targets: list[vault_config.VaultPathEntry] = []
    if getattr(args, "reindex_all", False):
        targets = list(cfg.paths)
        if not targets:
            print(
                f"No registered vault paths in {cfg_path}.\n"
                "Add one via `tokenpak vault add <path>` (paid) "
                "or edit vault.yaml directly.",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        requested_value = getattr(args, "reindex_path", None)
        requested = requested_value if isinstance(requested_value, str) else ""
        entry = cfg.find(requested)
        if entry is None:
            print(
                f"Path not registered in {cfg_path}: {requested}\n"
                "Register it before reindexing (`tokenpak vault add <path>` "
                "in paid, or edit vault.yaml directly).",
                file=sys.stderr,
            )
            sys.exit(2)
        targets = [entry]

    # Resolve the index registry DB path.  Honor TOKENPAK_VAULT_INDEX_PATH
    # (proxy-compatible vault directory); fall back to the parser's --db.
    index_root = vault_config.default_index_path()
    db_override = os.environ.get("TOKENPAK_VAULT_INDEX_PATH")
    if db_override:
        index_root = Path(db_override).expanduser()
        index_root.mkdir(parents=True, exist_ok=True)
        registry_db = index_root / "registry.db"
    elif getattr(args, "db", None) and args.db != ".tokenpak/registry.db":
        registry_db = Path(args.db).expanduser()
        registry_db.parent.mkdir(parents=True, exist_ok=True)
    else:
        index_root.mkdir(parents=True, exist_ok=True)
        registry_db = index_root / "registry.db"

    overall_start = time.perf_counter()
    any_failed = False

    for entry in targets:
        target_path = entry.path
        if not Path(target_path).exists():
            print(f"  ⚠ skipping (not found): {target_path}")
            vault_config.update_index_health(cfg, target_path, status="failed", duration_ms=0)
            any_failed = True
            continue

        print(f"Reindexing: {target_path}")
        # Reuse the existing _do_index path. Build a fresh argparse-like Namespace
        # so we don't mutate the caller's args.
        sub_args = argparse.Namespace(
            **{**vars(args), "directory": target_path, "db": str(registry_db)}
        )
        t0 = time.perf_counter()
        try:
            _do_index(sub_args)
            duration_ms = int((time.perf_counter() - t0) * 1000)
            files_indexed = _registry_file_count(str(registry_db))
            vault_config.update_index_health(
                cfg,
                target_path,
                status="ok",
                duration_ms=duration_ms,
                files_indexed=files_indexed,
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - t0) * 1000)
            vault_config.update_index_health(
                cfg,
                target_path,
                status="failed",
                duration_ms=duration_ms,
            )
            print(f"  ✖ failed: {exc}", file=sys.stderr)
            any_failed = True

    # Persist the updated health metadata.
    vault_config.save(cfg, cfg_path)

    elapsed = time.perf_counter() - overall_start
    print(
        f"\nReindex summary: {len(targets)} path(s) in {elapsed:.2f}s (index root: {registry_db})"
    )
    if any_failed:
        sys.exit(1)


def _registry_file_count(db_path: str) -> int:
    """Return the total file count from the registry DB, or 0 on error."""
    try:
        from tokenpak.core.registry import BlockRegistry

        return int(BlockRegistry(db_path).get_stats().get("total_files", 0))
    except Exception:
        return 0


def cmd_index(args: CommandArgs) -> None:
    """Index a directory with parallel processing and batch transactions."""
    # --reindex-all / --reindex-path: OSS reindex flags driven by ~/.tokenpak/vault.yaml.
    if getattr(args, "reindex_all", False) or getattr(args, "reindex_path", None):
        _cmd_reindex(args)
        return

    # --status mode: show stats from BlockRegistry
    if getattr(args, "status", False):
        import os

        from tokenpak.core.registry import BlockRegistry

        db_path = getattr(args, "db", os.path.join(os.getcwd(), ".tokenpak", "registry.db"))
        if not os.path.exists(db_path):
            print(f"No index found at {db_path}. Run `tokenpak index <directory>` first.")
            return
        registry = BlockRegistry(db_path)
        stats = registry.get_stats()
        total = stats.get("total_files", 0)
        sep = "────────────────────────────────────────"
        print("Vault Index Status")
        print(sep)
        print(f"  Database:            {db_path}")
        print(f"  Total indexed files: {total}")
        if total == 0:
            print("  (no files indexed yet — run: tokenpak index <path>)")
        else:
            by_type = stats.get("by_type", {})
            if by_type:
                print()
                print("  By type:")
                for ftype, info in sorted(by_type.items()):
                    if isinstance(info, dict):
                        print(f"    {ftype:<10} {info.get('files', 0):>6} files")
                    else:
                        print(f"    {ftype:<10} {info:>6} files")
            raw = stats.get("total_raw_tokens", 0)
            compressed = stats.get("total_compressed_tokens", 0)
            ratio = stats.get("compression_ratio", 0)
            print()
            print(f"  Tokens raw:          {raw:,}")
            print(f"  Tokens compressed:   {compressed:,}")
            if ratio:
                print(f"  Compression ratio:   {ratio:.2f}x")
        return

    if not args.directory:
        print("error: directory is required when --status is not set", file=sys.stderr)
        print("Usage: tokenpak index <directory> [--recursive] [--status]", file=sys.stderr)
        sys.exit(1)

    # --watch mode: initial index then watch for changes
    if getattr(args, "watch", False):
        from tokenpak.vault.watcher import VaultWatcher, WatcherConfig

        # Run initial full index first
        _do_index(args)
        # Then start watcher
        config = WatcherConfig(
            watch_paths=[args.directory],
            debounce_ms=getattr(args, "debounce", 500),
        )
        watcher = VaultWatcher(config)
        watcher.start(blocking=True)
        return
    if not args.directory:
        print("error: directory is required when --status is not set")
        return
    _do_index(args)


def _do_index(args: CommandArgs) -> None:
    """Core index logic (used by cmd_index and watch mode)."""
    registry = BlockRegistry(args.db)
    files = list(walk_directory(args.directory))

    start_time = time.perf_counter()
    processed = 0
    skipped = 0
    unchanged = 0

    workers = getattr(args, "workers", 1) or 1

    if getattr(args, "recalibrate", False):
        calibration = calibrate_workers(
            args.directory,
            max_workers=getattr(args, "max_workers", 8),
            rounds=getattr(args, "calibration_rounds", 2),
        )
        if "error" in calibration:
            print(f"Calibration skipped: {calibration['error']}")
        else:
            print(
                f"Calibration complete: best_workers={calibration['best_workers']} on {calibration['sample_files']} files"
            )

    if getattr(args, "auto_workers", False):
        workers = get_recommended_workers(
            default_workers=max(1, workers), max_workers=getattr(args, "max_workers", 8)
        )
        print(f"Auto workers selected: {workers}")

    no_treesitter = getattr(args, "no_treesitter", False)

    if workers > 1:
        # Parallel processing path
        print(f"Indexing with {workers} workers...")

        # Phase 1: Parallel file processing (CPU-bound)
        file_args = [(path, file_type, no_treesitter) for path, file_type, _ in files]
        results: list[tuple[str, str, Block]] = []

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_process_file, fa): fa for fa in file_args}
            for future in as_completed(futures):
                file_result = future.result()
                if file_result:
                    results.append(file_result)
                else:
                    skipped += 1

        # Phase 2: Serial DB writes (I/O-bound, needs locking)
        with registry.batch_transaction() as conn:
            batch_count = 0
            for path, content, block in results:
                if not registry.has_changed(path, content):
                    unchanged += 1
                    continue

                registry.add_block_batch(block, conn)
                processed += 1
                batch_count += 1

                if batch_count >= BATCH_SIZE:
                    conn.commit()
                    conn.execute("BEGIN IMMEDIATE")
                    batch_count = 0
    else:
        # Single-threaded path (original behavior)
        with registry.batch_transaction() as conn:
            batch_count = 0

            for path, file_type, _ in files:
                try:
                    content = Path(path).read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    skipped += 1
                    continue

                if not content.strip():
                    skipped += 1
                    continue

                if not registry.has_changed(path, content):
                    unchanged += 1
                    continue

                processor = get_processor(file_type, no_treesitter=no_treesitter)
                if not processor:
                    skipped += 1
                    continue

                process_text = cast(Callable[[str, str], str], processor.process)
                compressed = process_text(content, path)

                block = Block(
                    path=path,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                    version=1,
                    file_type=file_type,
                    raw_tokens=count_tokens(content),
                    compressed_tokens=count_tokens(compressed),
                    compressed_content=compressed,
                    quality_score=1.0,
                    importance=5.0,
                )
                registry.add_block_batch(block, conn)
                processed += 1
                batch_count += 1

                if batch_count >= BATCH_SIZE:
                    conn.commit()
                    conn.execute("BEGIN IMMEDIATE")
                    batch_count = 0

    elapsed = time.perf_counter() - start_time
    stats = registry.get_stats()

    print(
        f"Indexed: {processed} files in {elapsed:.2f}s ({processed / max(elapsed, 0.001):.1f} files/sec)"
    )
    print(f"Skipped: {skipped} | Unchanged: {unchanged}")
    typed_cache_info = cast(Callable[[], object], cache_info)
    print(f"Token cache: {typed_cache_info()}")
    print(json.dumps(stats, indent=2))


def cmd_search(args: CommandArgs) -> None:
    """Search indexed content."""
    registry = BlockRegistry(args.db)

    # Retrieval expansion: if query overlaps with a prior miss, double top_k
    top_k = args.top_k
    gaps_path = getattr(args, "gaps", DEFAULT_GAPS_PATH)
    if should_expand_retrieval(args.query, gaps_path=gaps_path):
        top_k = top_k * 2
        print(f"[miss-detector] expanded due to prior miss: top_k={top_k}", flush=True)

    matches = registry.search(args.query, top_k=top_k)
    if not matches:
        print("No matches found.")
        return

    budget_blocks = []
    type_weights = {"text": 0.8, "code": 0.7, "data": 0.6, "pdf": 0.7}

    for m in matches:
        budget_blocks.append(
            BudgetBlock(
                ref=f"{m.path}#v{m.version}",
                relevance_score=0.8,
                recency_score=0.6,
                quality_score=m.quality_score,
                type_weight=type_weights.get(m.file_type, 0.5),
            )
        )

    alloc = quadratic_allocate(budget_blocks, args.budget)

    wire_blocks = []
    for m in matches:
        ref = f"{m.path}#v{m.version}"
        max_tokens = alloc.get(ref, 200)
        content, token_count = truncate_to_tokens(m.compressed_content, max_tokens)
        wire_blocks.append(
            {
                "ref": ref,
                "type": m.file_type,
                "quality": m.quality_score,
                "tokens": token_count,
                "content": content,
            }
        )

    if getattr(args, "inject_refs", False):
        from .compression.compiler import compile_with_refs

        output = compile_with_refs(wire_blocks, args.query, args.budget)
    else:
        output = pack(wire_blocks, args.budget, {"query": args.query})
    print(output)


def cmd_stats(args: CommandArgs) -> None:
    """Show compression telemetry stats (last 100 requests)."""
    SEP = "─" * 45

    # Try to pull live stats from the running proxy
    proxy_data = None
    try:
        import urllib.request as _urlreq

        proxy_base = _proxy_base_url()
        with _urlreq.urlopen(f"{proxy_base}/health", timeout=3) as r:
            proxy_data = json.loads(r.read())
    except Exception:
        proxy_data = None

    # Also read from the JSONL file for accurate rolling stats
    from tokenpak.proxy.stats import CompressionStats

    cs = CompressionStats()
    file_stats = cs.get_stats()

    # Prefer live proxy data for request counts / uptime when available
    if proxy_data:
        requests_total = proxy_data.get("requests_total", file_stats["requests_total"])
        requests_errors = proxy_data.get("requests_errors", file_stats["requests_errors"])
        avg_ratio = proxy_data.get("compression_ratio_avg", file_stats["avg_ratio"])
        uptime_s = proxy_data.get("uptime_seconds")
    else:
        requests_total = file_stats["requests_total"]
        requests_errors = file_stats["requests_errors"]
        avg_ratio = file_stats["avg_ratio"]
        uptime_s = None

    avg_latency = _as_float(file_stats.get("avg_latency_ms"))

    # A compression ratio or latency of exactly 0 is not something the pipeline
    # can produce — it means the rolling window holds no observations. Printing
    # "0.0% token reduction" / "0ms" told users we measured their traffic and
    # found no benefit. Report the absence instead.
    from tokenpak.core.contracts import measured as _measured
    from tokenpak.core.contracts import no_data as _no_data

    # `ratio` is recorded by the proxy as `saved / input_tokens` — a savings
    # *fraction*, not a compressed/original ratio (see
    # proxy/server_async.py: `ratio = round(saved / input_tokens, 4)`).
    # This previously computed `(1 - ratio) * 100`, which turned a measured 1%
    # saving into a reported "99.4% token reduction".
    observed = bool(file_stats.get("requests_total")) or bool(avg_ratio)
    ratio_m = _measured(avg_ratio) if (observed and avg_ratio) else _no_data("no requests observed")
    latency_m = (
        _measured(avg_latency) if (observed and avg_latency) else _no_data("no requests observed")
    )
    pct_m = (
        _measured(round(avg_ratio * 100, 1))
        if (observed and avg_ratio)
        else _no_data("no requests observed")
    )

    if uptime_s is not None:
        h, rem = divmod(int(uptime_s), 3600)
        m = rem // 60
        uptime_str = f"{h}h {m:02d}m" if h else f"{m}m"
    else:
        uptime_str = "n/a (proxy not running)"

    if getattr(args, "raw", False):
        print(
            json.dumps(
                {
                    "requests_total": requests_total,
                    "requests_errors": requests_errors,
                    "avg_ratio": ratio_m.to_json(),
                    "avg_latency_ms": latency_m.to_json(),
                    "uptime": uptime_str,
                },
                indent=2,
            )
        )
        return

    print("TokenPak Compression Stats (last 100 requests)")
    print(SEP)
    print(f"{'Requests:':<17}{requests_total} total, {requests_errors} errors")
    if ratio_m.is_measured:
        print(f"{'Avg savings:':<17}{pct_m.render(fmt='pct')} of input tokens avoided")
    else:
        print(f"{'Avg savings:':<17}{ratio_m.render()}")
    print(
        f"{'Avg latency:':<17}"
        f"{latency_m.render(fmt='ms') if latency_m.is_measured else latency_m.render()}"
    )
    print(f"{'Uptime:':<17}{uptime_str}")


def cmd_models(args: CommandArgs) -> None:
    """Show per-model breakdown of usage and efficiency."""
    days = getattr(args, "days", 30)
    target_model = getattr(args, "model", None)

    # Try monitor.db first (proxy's live data source)
    db_rows = _monitor_db_models(days=days)

    if db_rows:
        if target_model:
            db_rows = [r for r in db_rows if target_model.lower() in r["model"].lower()]
            if not db_rows:
                print(f"No data found for model matching '{target_model}'")
                return
            for r in db_rows:
                cache_total = r["input_tokens"] + r["cache_read"]
                cache_pct = r["cache_read"] / cache_total * 100 if cache_total else 0
                print(f"Model: {r['model']}")
                print("─" * 60)
                print(
                    f"Requests: {r['requests']:,} | Tokens: {r['input_tokens'] + r['output_tokens']:,}"
                )
                print(f"  Input:  {r['input_tokens']:,} | Output: {r['output_tokens']:,}")
                print(f"  Cache Read: {r['cache_read']:,} ({cache_pct:.1f}% hit rate)")
                print(f"  Compressed: {r['compressed']:,} tokens saved")
                print(f"Cost: ${r['cost']:.4f} | Avg latency: {r['avg_latency']:.0f}ms")
                print()
            return

        if getattr(args, "raw", False):
            print(json.dumps(db_rows, indent=2))
            return

        total_requests = sum(r["requests"] for r in db_rows)
        total_input = sum(r["input_tokens"] for r in db_rows)
        total_output = sum(r["output_tokens"] for r in db_rows)
        total_cache = sum(r["cache_read"] for r in db_rows)
        total_cost = sum(r["cost"] for r in db_rows)
        total_compressed = sum(r["compressed"] for r in db_rows)
        overall_cache_pct = (
            total_cache / (total_input + total_cache) * 100 if (total_input + total_cache) else 0
        )

        print("TokenPak Models Dashboard")
        print("=" * 100)
        print(
            f"{'Model':<32} {'Requests':>10} {'Input':>10} {'Output':>10} {'Cache%':>8} {'Cost':>10}"
        )
        print("─" * 100)

        for r in db_rows:
            if r["requests"] == 0:
                continue
            cache_total = r["input_tokens"] + r["cache_read"]
            cache_pct = r["cache_read"] / cache_total * 100 if cache_total else 0
            model_short = r["model"][:30]
            print(
                f"{model_short:<32} {r['requests']:>10,} {r['input_tokens']:>10,} "
                f"{r['output_tokens']:>10,} {cache_pct:>7.1f}% ${r['cost']:>9.4f}"
            )

        print("─" * 100)
        print(
            f"{'TOTAL':<32} {total_requests:>10,} {total_input:>10,} "
            f"{total_output:>10,} {overall_cache_pct:>7.1f}% ${total_cost:>9.4f}"
        )
        print()
        print(f"💰 Total Cost: ${total_cost:.4f} | Compressed: {total_compressed:,} tokens saved")
        return

    # No monitor DB data — emit a clear user-facing message instead of crashing
    print("No model data available — monitor DB is empty or not yet populated.")


def _apply_safe_mode_defaults() -> None:
    """Apply the legacy ``--safe`` compatibility settings atomically."""
    import os as _os

    # TOKENPAK_COMPACT is a no-op compatibility value; the threshold remains
    # available to explicit legacy helper callers. Neither controls body
    # compaction in the default HTTP proxy path.
    _os.environ["TOKENPAK_COMPACT"] = "0"
    _os.environ["TOKENPAK_COMPACT_THRESHOLD_TOKENS"] = "4500"
    _os.environ["TOKENPAK_BUDGET_CONTROLLER"] = "0"  # disable budget controller
    # TOKENPAK_VALIDATION_GATE: already True in both old and new defaults, no change


def _maybe_show_compression_notice(safe: bool) -> None:
    """Compatibility no-op: the default HTTP path does not compact bodies."""
    _ = safe


def _resolve_serve_port(flag_port: int | None) -> int:
    """Resolve the port for ``tokenpak serve``.

    Precedence: explicit ``--port`` flag > ``TOKENPAK_PORT`` env var >
    ``~/.tpk/config.yaml`` ``port`` key > built-in default (8766). This
    mirrors the flag > env precedence already used by ``cmd_start`` for the
    same flag, extended with the config-file step ``cmd_start`` does not
    need (it forwards ``TOKENPAK_PORT`` to a subprocess that reads config
    itself; ``cmd_serve`` starts the proxy in-process and must resolve
    config here).

    Raises:
        ValueError: ``TOKENPAK_PORT`` is set but is not a valid integer.
    """
    if flag_port is not None:
        return int(flag_port)

    env_val = os.environ.get("TOKENPAK_PORT")
    if env_val is not None and env_val.strip():
        try:
            return int(env_val.strip())
        except ValueError:
            raise ValueError(
                f"TOKENPAK_PORT={env_val!r} is not a valid port number (expected an integer)."
            ) from None

    from tokenpak.core.config_loader import get as _config_get

    return _config_get("port", 8766, None, int)


def cmd_serve(args: CommandArgs) -> int | None:
    """Start monitoring proxy or telemetry server (if available)."""
    # Resolve the port before anything else touches it: --port flag >
    # TOKENPAK_PORT env > config `port` > 8766. `p_serve`'s --port default is
    # None (not a literal 8766) specifically so this resolution can tell "no
    # flag was passed" from "the flag was 8766" — see _resolve_serve_port.
    try:
        args.port = _resolve_serve_port(getattr(args, "port", None))
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    # Apply one-invocation proxy settings before importing any proxy module.
    # Proxy profile presets are evaluated at import time, while the stats
    # footer reads its environment override per request.  Leaving either flag
    # unset preserves the existing environment/config/default resolution.
    profile = getattr(args, "profile", None)
    if profile is not None:
        os.environ["TOKENPAK_PROFILE"] = profile
    if getattr(args, "stats_footer", False):
        os.environ["TOKENPAK_STATS_FOOTER"] = "1"

    # --safe: apply legacy compatibility settings before proxy imports.
    if getattr(args, "safe", False):
        _apply_safe_mode_defaults()

    # Retained as an internal compatibility seam; it intentionally emits nothing.
    _maybe_show_compression_notice(safe=getattr(args, "safe", False))

    if getattr(args, "telemetry", False):
        import uvicorn

        from .telemetry.server import create_app

        str(_paths_mod().write_home() / "data" / "session.jsonl")
        app = create_app()
        # Phase 5A: register ingest router
        try:
            from tokenpak.vault.ingest.api import router as ingest_router

            app.include_router(ingest_router)
        except Exception as _ingest_err:
            print(f"[warn] Ingest router not loaded: {_ingest_err}")
        workers = getattr(args, "workers", 1)
        print(f"Starting TokenPak telemetry server on port {args.port} (workers={workers})")
        uvicorn.run(app, host="127.0.0.1", port=args.port, workers=workers)
        return None
    if getattr(args, "ingest", False):
        import uvicorn

        from tokenpak.vault.ingest.api import create_ingest_app

        app = create_ingest_app()
        port = args.port
        print(f"TokenPak Ingest API — http://127.0.0.1:{port}")
        print("  POST /ingest")
        print("  POST /ingest/batch")
        print("  GET  /health")
        uvicorn.run(app, host="127.0.0.1", port=port)
        return None
    # Multi-worker mode: route to ingest API via uvicorn (proxy doesn't support workers)
    workers = getattr(args, "workers", 1) or 1
    if workers > 1:
        import uvicorn

        from tokenpak.vault.ingest.api import create_ingest_app

        port = args.port
        print(f"TokenPak Ingest API — http://127.0.0.1:{port}")
        print(f"  Workers: {workers}")
        print("  POST /ingest")
        print("  POST /ingest/batch")
        print("  GET  /health")
        uvicorn.run(
            "tokenpak.vault.ingest.api:create_ingest_app",
            host="127.0.0.1",
            port=port,
            workers=workers,
            factory=True,
        )
        return None
    # Default (single-worker): start the TokenPak proxy server
    shutdown_timeout = getattr(args, "shutdown_timeout", None)
    try:
        from tokenpak.proxy.server import start_proxy

        start_proxy(
            host="127.0.0.1",
            port=args.port,
            blocking=True,
            shutdown_timeout=shutdown_timeout,
        )
    except Exception as e:
        print(f"Serve mode unavailable: {e}")
        print("Run the existing proxy directly if needed.")
    return None


def cmd_benchmark(args: CommandArgs) -> None:
    """Run compression benchmark (default) or latency benchmark (--latency)."""
    file_arg = getattr(args, "file", None)
    use_samples = getattr(args, "samples", False)
    as_json = getattr(args, "json", False)
    latency_mode = getattr(args, "latency", False)

    if latency_mode:
        # Legacy latency benchmark — requires a directory
        directory = getattr(args, "directory", None) or "."
        from .orchestration.benchmark import run_benchmark

        run_benchmark(directory, args.iterations, compare=args.compare)
    else:
        # Compression benchmark (new default)
        from .orchestration.benchmark import run_compression_benchmark

        run_compression_benchmark(file=file_arg, use_samples=use_samples, as_json=as_json)


def cmd_calibrate(args: CommandArgs) -> None:
    """Run static worker calibration and save host profile."""
    result = calibrate_workers(args.directory, max_workers=args.max_workers, rounds=args.rounds)
    print(json.dumps(result, indent=2))


class Colors:
    """ANSI color codes."""

    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    RESET = "\033[0m"

    @staticmethod
    def ok(text: str) -> str:
        return f"{Colors.GREEN}✅{Colors.RESET}  {text}"

    @staticmethod
    def warn(text: str) -> str:
        return f"{Colors.YELLOW}⚠️{Colors.RESET}   {text}"

    @staticmethod
    def fail(text: str) -> str:
        return f"{Colors.RED}❌{Colors.RESET}  {text}"


def cmd_requests(args: CommandArgs) -> None:
    """Live request explorer: tail or show a request by id."""
    import json as _json
    import time as _time

    from tokenpak.cli.request_explorer import (
        REQUESTS_PATH,
        age_label,
        cache_pct,
        get_request_by_id,
        load_requests,
        status_label,
        to_view,
    )

    action = getattr(args, "requests_cmd", None) or getattr(args, "action", None)
    request_id = getattr(args, "request_id", None)

    if action and action not in ("tail", "show"):
        # Allow `tokenpak requests <id>`
        request_id = action
        action = "show"

    if action is None:
        action = "show" if request_id else "tail"

    if action == "tail":
        limit = getattr(args, "limit", 10)
        follow = not getattr(args, "once", False)

        if not REQUESTS_PATH.exists():
            print("No request ledger found yet. Run requests through the proxy first.")
            return

        def _print_rows(rows: Sequence[dict[str, Any]], with_header: bool = False) -> None:
            header = (
                "ID         Model              Input    Output   Cache%  Saved $  Status     Age"
            )
            if with_header:
                print(header)
                print("─" * len(header))
            for row in rows:
                view = to_view(row)
                cache = f"{cache_pct(view):>5.0f}%"
                saved = (
                    f"${view.saved_cost:.2f}"
                    if view.saved_cost >= 0.01
                    else f"${view.saved_cost:.4f}"
                )
                print(
                    f"{view.request_id[:8]:<10} {view.model:<17} {view.input_tokens:>6}  {view.output_tokens:>6}  {cache:>6}  {saved:>7}  {status_label(view):<8} {age_label(view.timestamp):>4}"
                )

        rows = load_requests(limit=limit)
        _print_rows(rows, with_header=True)

        if not follow:
            return None

        # Follow new entries
        with REQUESTS_PATH.open("r") as f:
            f.seek(0, 2)
            try:
                while True:
                    line = f.readline()
                    if not line:
                        _time.sleep(0.5)
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = _json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    _print_rows([row], with_header=False)
            except KeyboardInterrupt:
                return

    # default: show single request
    if not request_id:
        print("Provide a request id (e.g. tokenpak requests <id>).")
        return None

    row = get_request_by_id(request_id)
    if not row:
        print(f"Request '{request_id}' not found.")
        return None

    view = to_view(row)
    print(f"Request ID: {view.request_id}")
    print("─" * 45)
    print(f"Model:     {view.model}")
    print(f"Status:    {status_label(view)}")
    print(f"Age:       {age_label(view.timestamp)}")
    if view.session_id:
        print(f"Session:   {view.session_id}")

    print("\nTokens:")
    print(f"  Input:   {view.input_tokens:,}")
    print(f"  Output:  {view.output_tokens:,}")
    if view.cache_read:
        print(f"  Cache:   {view.cache_read:,} (read)")
    print(f"  Saved:   ${view.saved_cost:.4f}")


def cmd_aggregate(args: CommandArgs) -> None:
    """Aggregate request ledger across machines."""
    import json as _json

    from tokenpak.cli.aggregate import (
        aggregate_records,
        default_machine_name,
        load_requests,
        parse_since,
        render_table,
    )

    since_raw = getattr(args, "since", None)
    since_dt = parse_since(since_raw)
    machine = default_machine_name()
    rows, totals = aggregate_records(load_requests(since=since_dt), machine)

    if getattr(args, "as_json", False):
        payload = {
            "machine": machine,
            "since": since_raw,
            "summary": totals,
            "rows": [r.__dict__ for r in rows],
        }
        print(_json.dumps(payload, indent=2))
        return

    print(render_table(rows, totals))


def cmd_attribution(args: CommandArgs) -> None:
    """View savings breakdown by agent/skill/model."""
    import json as _json

    from tokenpak.telemetry.attribution import AttributionTracker, format_attribution

    tracker = AttributionTracker()
    tracker.load()
    days = getattr(args, "days", 7)

    if getattr(args, "as_json", False):
        import time

        since = time.time() - (days * 86400)
        data = {
            "by_source": tracker.rollup_by_source(since=since),
            "by_model": tracker.rollup_by_model(since=since),
            "leakage_pct": tracker.leakage_pct(since=since),
        }
        print(_json.dumps(data, indent=2))
        return

    print(format_attribution(tracker, days=days))


def cmd_timeline(args: CommandArgs) -> None:
    """View savings trend over 7/30 days."""
    import json as _json

    from tokenpak.telemetry.timeline import format_timeline, get_timeline

    days = getattr(args, "days", 7)
    entries = get_timeline(days=days)

    if getattr(args, "as_json", False):
        print(_json.dumps(entries, indent=2))
        return

    show_chart = getattr(args, "chart", False)
    print(format_timeline(entries, show_chart=show_chart))


def cmd_explain(args: CommandArgs) -> None:
    """Explain what a named workflow profile sets."""
    # Single source of truth for profile presets is the proxy config layer.
    # Imported lazily to avoid an import cycle at module load time.
    from tokenpak.proxy.config import _PROFILE_PRESETS

    profile = getattr(args, "profile", None)

    if profile and profile not in _PROFILE_PRESETS:
        print(f"❌ Unknown profile: '{profile}'")
        print(f"   Valid profiles: {', '.join(_PROFILE_PRESETS)}")
        return

    profiles_to_show = [profile] if profile else list(_PROFILE_PRESETS)

    for name in profiles_to_show:
        flags = _PROFILE_PRESETS[name]
        print(f"\n🎛️  Profile: {name}")
        print("─" * 40)
        for key, value in flags.items():
            print(f"  {key:<40} = {value}")

    if not profile:
        print("\nTip: Set with TOKENPAK_PROFILE=<name> or use `tokenpak explain --profile <name>`")


def cmd_preview(args: CommandArgs) -> int | None:
    """Preview compression result for input text (dry-run)."""
    import sys
    from pathlib import Path

    from tokenpak.cli.exit_codes import EXIT_FAILURE

    def _bail(*lines: str) -> int:
        """Refuse the input, honouring the --json contract.

        --json is a machine-readable contract: it emits JSON on *every* path,
        including failures, so a caller parsing stdout never hits prose. The
        input guards below originally printed prose and returned before the
        --json branch, which broke that for `preview --json <path>`.
        """
        if getattr(args, "json", False):
            # Build the contract object rather than hand-rolling a dict. The
            # invariant is that a non-MEASURED state carries **null** numerics,
            # never 0 — `PreviewResult.validate()` raises on zeros, and
            # test_cli_preview_json_nulls_are_not_zeros asserts it. Hand-rolling
            # produced a second, incompatible shape under the same
            # `state: "error"` label, so a consumer branching on
            # `saved_tokens is None` read a refusal as "measured, saved nothing".
            from tokenpak.services.preview import PreviewResult, PreviewState

            _err = PreviewResult(
                state=PreviewState.ERROR,
                reason=" ".join(line.strip() for line in lines),
            )
            _err.validate()
            print(json.dumps(_err.to_json(), indent=2))
        else:
            for line in lines:
                print(line)
        return EXIT_FAILURE

    # Get input text
    if args.file:
        src = Path(args.file)
        if not src.is_file():
            return _bail(
                f"✗ No such file: {args.file}",
                '  Check the path, or pass text directly: tokenpak preview "<text>"',
            )
        try:
            text = src.read_text()
        except OSError as exc:
            return _bail(f"✗ Could not read {args.file}: {exc}")
        # A .json/.jsonl file that does not parse would otherwise be compressed
        # as raw prose and reported as a confident, meaningless number. Refuse
        # instead: a silent wrong answer is worse than an error.
        suffix = src.suffix.lower()
        if suffix in (".json", ".jsonl"):
            import json as _json

            try:
                if suffix == ".json":
                    _json.loads(text)
                else:
                    for lineno, line in enumerate(text.splitlines(), 1):
                        if line.strip():
                            _json.loads(line)
            except ValueError as exc:
                return _bail(
                    f"✗ {args.file} is not valid {suffix.lstrip('.').upper()}: {exc}",
                    f"  Fix the file, or pipe it as plain text: cat {args.file} | tokenpak preview",
                )
    elif args.input:
        text = args.input
        # The positional argument is literal TEXT. Passing a filename — the most
        # natural first move, since the menu advertises a dry-run preview —
        # would otherwise compress the *path string* and report ~0% savings with
        # no hint anything was wrong. Catch that specific mistake.
        stripped = text.strip()
        if len(stripped) < 4096 and "\n" not in stripped:
            try:
                looks_like_file = Path(stripped).is_file()
            except OSError:
                looks_like_file = False
            if looks_like_file:
                return _bail(
                    "✗ That is a file path, but this argument takes literal text.",
                    f"  Did you mean:  tokenpak preview --file {stripped}",
                )
    else:
        # Read from stdin
        text = sys.stdin.read()

    # Measured, not simulated. Every number below comes from running the real
    # compression pipeline and is contract-checked before display; see
    # tokenpak/services/preview.py for the invariants.
    from tokenpak.services.preview import PreviewState, run_preview

    input_source = args.file if args.file else ("argv" if args.input else "stdin")
    result = run_preview(text, input_source=input_source)

    # --json is a machine-readable contract: it emits JSON on every path,
    # including failures. A caller parsing stdout must never hit prose.
    # One code per state, from the documented table. These all exited 1, so a
    # caller could not tell "you gave me nothing to measure" from "the
    # compressor failed" — and the --json body already said `no_data` while
    # the exit code said generic-failure.
    from tokenpak.cli.exit_codes import (
        EXIT_NO_DATA,
        EXIT_RUNTIME_UNAVAILABLE,
    )

    _state_exit = {
        PreviewState.NO_DATA: EXIT_NO_DATA,
        PreviewState.UNAVAILABLE: EXIT_RUNTIME_UNAVAILABLE,
        PreviewState.ERROR: EXIT_FAILURE,
    }

    if args.json:
        print(json.dumps(result.to_json(), indent=2))
        if result.state is not PreviewState.MEASURED:
            sys.exit(_state_exit[result.state])
        return None

    if result.state is not PreviewState.MEASURED:
        # No fabricated fallback: say what happened and exit non-zero.
        label = {
            PreviewState.NO_DATA: "Nothing to preview",
            PreviewState.UNAVAILABLE: "Preview unavailable",
            PreviewState.ERROR: "Preview failed",
        }[result.state]
        print(f"{label}: {result.reason}")
        if result.state is PreviewState.NO_DATA:
            print("Usage: tokenpak preview <text> [--file FILE] [--json|--raw|--verbose]")
        sys.exit(_state_exit[result.state])

    inp = result.input_tokens or 0
    out = result.output_tokens or 0
    saved = result.saved_tokens or 0
    ratio = result.compression_ratio or 0.0
    prov = result.provenance

    if args.raw:
        print(f"Input:     {inp:,} tokens")
        print(f"Output:    {out:,} tokens")
        print(f"Saved:     {saved:,} tokens ({ratio * 100:.1f}%)")
        print(f"Applied:   {'yes' if result.applied else 'no (compression would expand input)'}")
        print()
        print("Blocks:")
        for block in result.blocks:
            state = "retained" if block.retained else "removed"
            print(f"  - {block.block_id} [{block.segment_type}]: {block.raw_chars} chars ({state})")
    else:
        # Pretty format (default)
        mode = resolve_mode(args)
        fmt = OutputFormatter("Preview", mode=mode)
        print(fmt.header())
        print()

        print(f"  Input:          {inp:,} tokens")
        print(f"  → Compressed:   {out:,} tokens")
        print(f"  Savings:        {saved:,} tokens ({ratio * 100:.1f}% reduction)")
        if not result.applied:
            print("  Applied:        no — the original is retained unchanged.")
            if prov and prov.input_kind == "single_turn":
                # Do not let a 0% single-turn preview read as "TokenPak does
                # not work". State honestly where the savings actually come from.
                print()
                print("  This input is a single turn. TokenPak's savings come from")
                print("  redundancy repeated across conversation turns, so there is")
                print("  little for it to remove here. To measure your own savings,")
                print("  preview a conversation export (a JSON array of messages,")
                print("  a provider request body, or JSONL — one message per line),")
                print("  or run `tokenpak serve` and measure live traffic.")
        print()

        retained = [b for b in result.blocks if b.retained]
        removed = [b for b in result.blocks if not b.retained]
        if retained:
            print(f"  Retained blocks ({len(retained)}):")
            for block in retained:
                print(f"    • {block.segment_type:<20} {block.raw_chars:>6,} chars")
            print()
        if removed:
            print(f"  Removed blocks ({len(removed)}):")
            for block in removed:
                print(f"    • {block.segment_type:<20} {block.raw_chars:>6,} chars")
            print()
        if not result.blocks:
            print("  Blocks:         pipeline identified no separable segments")
            print()

        print(f"  Mode: {prov.mode if prov else 'unknown'} | Duration: {result.duration_ms:.1f}ms")

        if args.verbose and prov:
            print()
            print("  Measurement provenance:")
            print(f"    Input          {prov.input_source} ({prov.input_bytes:,} bytes)")
            print(f"    Input SHA-256  {prov.input_sha256}")
            print(f"    Tokenizer      {prov.tokenizer}")
            print(f"    Stages run     {', '.join(prov.stages_run) or '(none)'}")
            print(f"    TokenPak       v{prov.tokenpak_version}")

    return None


def cmd_dashboard(args: CommandArgs) -> int | None:
    """Real-time TokenPak health dashboard or public web dashboard URL."""
    import webbrowser

    from .telemetry.token_manager import load_or_create_token, regenerate_token

    if getattr(args, "dashboard_action", None) in {"connect", "disconnect"}:
        from .cli.commands.dashboard_tunnel import cmd_dashboard_tunnel

        return cmd_dashboard_tunnel(args)

    # --show-token: display current token
    if getattr(args, "show_token", False):
        try:
            token = load_or_create_token()
        except Exception as e:
            print(f"Error: {e}")
            return None
        print(f"Dashboard token: {token}")
        print("File: ~/.tokenpak/dashboard_token")
        return None

    # --new-token: regenerate token
    if getattr(args, "new_token", False):
        token = regenerate_token()
        print(f"Token regenerated: {token}")
        print("Old token is now invalid.")
        return None

    # --public: advanced public URL with token
    if getattr(args, "public", False):
        from tokenpak.core.config_loader import get as _cfg  # noqa: F401

        port = int(_cfg("port", 8766, "TOKENPAK_PORT", int))
        token = load_or_create_token()
        hostname = socket.gethostname()
        try:
            ip = socket.gethostbyname(hostname)
        except Exception:
            ip = "localhost"
        url = f"http://{ip}:{port}/dashboard?token={token}"
        print("\n✅ TokenPak Dashboard (Advanced Public Mode)")
        print("─────────────────────────────────")
        print(f"URL:   {url}")
        print(f"Token: {token}")
        print("\n⚠️  Share this URL only with trusted users.")
        print("Default remote access: tokenpak dashboard connect <host>")
        print("Regenerate token: tokenpak dashboard --new-token\n")
        webbrowser.open(url)
        return None

    # Default: TUI dashboard

    from .cli.commands.dashboard import run_dashboard

    run_dashboard(
        fleet=getattr(args, "fleet", False),
        json_export=getattr(args, "json_export", False),
        layout=getattr(args, "layout", "home"),
    )
    return None


def _cmd_dashboard_public(args: CommandArgs) -> None:
    """Print publicly accessible dashboard URLs with accessibility checks."""
    import webbrowser

    from .proxy.network_utils import get_reachable_addresses, is_port_accessible

    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    new_token = getattr(args, "new_token", False)

    # Load or create dashboard token
    # Read an existing token wherever it lives; mint new ones canonically.
    _pm = _paths_mod()
    token_path = _pm.resolve_existing("dashboard_token") or (_pm.write_home() / "dashboard_token")
    if new_token or not token_path.exists():
        import secrets

        token = secrets.token_urlsafe(24)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(token)
    else:
        token = token_path.read_text().strip()

    # Detect all reachable addresses
    urls = get_reachable_addresses(port, detect_public=True)

    print("\n✅ TokenPak Dashboard")
    print(f"{'─' * 50}")

    first_accessible = None
    for url in urls:
        host = url.replace("http://", "").split(":")[0]
        accessible = is_port_accessible(host, port, timeout=2)
        status = "✅" if accessible else "⚠️"
        full_url = f"{url}?token={token}"
        print(f"{status} {full_url}")
        if accessible and first_accessible is None:
            first_accessible = full_url

    print("\n💡 Copy and share the link with trusted users.")
    print(f"🔑 Token: {token}")
    print("\nRegenerate token: tokenpak dashboard --public --new-token\n")

    # Open the first accessible URL in browser
    if first_accessible:
        webbrowser.open(first_accessible)
    elif urls:
        # Fall back to localhost even if not yet running
        webbrowser.open(f"{urls[0]}?token={token}")


def cmd_doctor(args: CommandArgs) -> None:
    """Run comprehensive diagnostics on TokenPak installation."""
    if getattr(args, "conformance", False):
        # Fast path — TIP self-conformance only. Mirrors `tokenpak tip
        # conformance` so existing operators who learned the v1.3.7
        # ``doctor --conformance`` flag get the same surface back.
        from .cli.commands.tip import cmd_tip_conformance

        a = argparse.Namespace(json=bool(getattr(args, "json_output", False)))
        sys.exit(cmd_tip_conformance(a))
    if getattr(args, "fleet", False):
        from .cli.commands.doctor import run_fleet_doctor

        rc = run_fleet_doctor(
            fix=getattr(args, "fix", False), deploy=getattr(args, "deploy", False)
        )
        sys.exit(rc)
    from .cli.commands.doctor import run_doctor

    rc = run_doctor(
        fix=getattr(args, "fix", False),
        output_json=getattr(args, "json_output", False) is True,
        verbose=getattr(args, "verbose", False),
        claude_code=getattr(args, "claude_code", False),
        lifecycle=getattr(args, "lifecycle", False),
    )
    # rc: 0=all pass/warnings only, 2=errors  (1 mapped to 0 for CLI compat)
    # Translate: 0/1→no exit, 2→exit(1) to preserve legacy callers expecting exit(1) on fail
    if rc == 2:
        sys.exit(1)


def cmd_diagnose(args: CommandArgs) -> None:
    """Run health check, vault index integrity, cache stats, and proxy status."""
    from .cli.cli_diagnose import cmd_diagnose as _run_diagnose

    _run_diagnose(args)


#: What each companion wrapper launches, and the binary it needs on PATH.
_COMPANION_CLIENTS = {
    "claude": ("Claude Code", "claude"),
    "codex": ("Codex", "codex"),
}

#: TokenPak-owned flags per wrapper. Everything else is passed through
#: verbatim to the client, which is why `--help` had to be intercepted.
_COMPANION_WRAPPER_FLAGS = {
    "claude": [("--budget USD", "Daily companion spend ceiling")],
    "codex": [
        ("--budget USD", "Daily companion spend ceiling"),
        ("--install-only", "Provision companion state and exit without launching"),
        ("--receipt-out PATH", "Write an accounting receipt to PATH"),
        ("--run-id ID", "Correlation id recorded on the receipt"),
        ("--receipt-only", "Emit the receipt without launching"),
    ],
}


def _wants_wrapper_help(tail: list[str]) -> bool:
    """Whether *tail* is asking for the wrapper's own help.

    Only when help is the first token: `tokenpak codex --help` is a question
    about TokenPak, while `tokenpak codex exec foo --help` is a question the
    client should answer. Anything after `--` is the client's business.
    """
    return bool(tail) and tail[0] in ("-h", "--help")


def _print_companion_wrapper_help(name: str) -> None:
    """Print TokenPak's own help for a companion wrapper. Creates nothing."""
    label, binary = _COMPANION_CLIENTS[name]
    print(f"tokenpak {name} — launch {label} with the TokenPak companion active")
    print()
    print(f"Usage: tokenpak {name} [tokenpak flags] [{binary} arguments ...]")
    print()
    print(f"Arguments TokenPak does not recognise are passed to `{binary}` verbatim.")
    print()
    print("TokenPak flags:")
    for flag, desc in _COMPANION_WRAPPER_FLAGS[name]:
        print(f"  {flag:<22} {desc}")
    print(f"  -h, --help             Show this message ({binary}'s own help: `{binary} --help`)")
    if name == "codex":
        print()
        print("Subcommands:")
        print("  doctor                 Diagnose the Codex companion installation")
        print("  uninstall              Remove companion state Codex provisioning created")
    print()
    if _which(binary) is None:
        print(f"Note: `{binary}` was not found on PATH — install {label} before launching.")


def _which(binary: str) -> Optional[str]:
    import shutil

    return shutil.which(binary)


def _require_companion_client(name: str) -> None:
    """Exit with a documented code if the client binary is absent.

    Runs *before* provisioning.

    Provisioning ran first and reported success for a client that was never
    installed, leaving config, hooks and skill files behind for a binary the
    user does not have.
    """
    from tokenpak.cli.exit_codes import EXIT_MISSING_PREREQUISITE

    label, binary = _COMPANION_CLIENTS[name]
    if _which(binary) is None:
        print(
            f"tokenpak {name}: `{binary}` is not on PATH — install {label} first.",
            file=sys.stderr,
        )
        sys.exit(EXIT_MISSING_PREREQUISITE)


def cmd_claude(args: CommandArgs) -> None:
    """Launch Claude Code with tokenpak companion active."""
    import os

    _require_companion_client("claude")
    if getattr(args, "budget", None) is not None:
        os.environ["TOKENPAK_COMPANION_BUDGET"] = str(args.budget)
    from .companion import launch

    launch(args=list(args.args))


def cmd_codex(args: CommandArgs) -> None:
    """Launch Codex with tokenpak companion active.

    Also routes the ``doctor`` and ``uninstall`` subcommands when they
    appear as the first positional argument.
    """
    import os
    import sys

    forwarded, trailing_receipt_out, trailing_run_id = _extract_codex_accounting_flags(
        list(args.args)
    )
    forwarded, trailing_receipt_only = _extract_codex_receipt_only_flag(forwarded)
    receipt_out = getattr(args, "receipt_out", None) or trailing_receipt_out
    run_id = getattr(args, "run_id", None) or trailing_run_id
    receipt_only = bool(getattr(args, "receipt_only", False) or trailing_receipt_only)
    if bool(receipt_out) != bool(run_id):
        print(
            "tokenpak codex: --receipt-out and --run-id must be provided together",
            file=sys.stderr,
        )
        sys.exit(2)
    if receipt_only and getattr(args, "budget", None) is not None:
        print(
            "tokenpak codex: --receipt-only cannot be combined with --budget",
            file=sys.stderr,
        )
        sys.exit(2)
    if not receipt_only and getattr(args, "budget", None) is not None:
        os.environ["TOKENPAK_COMPANION_BUDGET"] = str(args.budget)
    if receipt_only and not (receipt_out and run_id):
        print(
            "tokenpak codex: --receipt-only requires --receipt-out and --run-id",
            file=sys.stderr,
        )
        sys.exit(2)
    if receipt_only and getattr(args, "install_only", False):
        print(
            "tokenpak codex: --receipt-only cannot be combined with --install-only",
            file=sys.stderr,
        )
        sys.exit(2)
    if forwarded and forwarded[0] == "doctor":
        from .companion.codex.doctor import main as doctor_main

        sys.exit(doctor_main(forwarded[1:]))
    if forwarded and forwarded[0] == "uninstall":
        from .companion.codex.uninstall import main as uninstall_main

        sys.exit(uninstall_main(forwarded[1:]))
    # Preflight *after* doctor/uninstall — diagnosing or removing state for an
    # absent client is legitimate — but before any provisioning. Launching
    # used to provision 21 files and then fail with an opaque 120 for a binary
    # the user does not have installed.
    _require_companion_client("codex")
    if getattr(args, "install_only", False):
        forwarded = ["--install-only", *forwarded]
    if receipt_only:
        forwarded = ["--receipt-only", *forwarded]
    from .companion.codex import launch

    sys.exit(launch(args=forwarded, receipt_out=receipt_out, run_id=run_id))


def _extract_codex_accounting_flags(
    forwarded: list[str],
) -> tuple[list[str], str | None, str | None]:
    """Consume TokenPak accounting flags even when placed after Codex args."""
    receipt_out: str | None = None
    run_id: str | None = None
    stripped: list[str] = []
    index = 0
    while index < len(forwarded):
        token = forwarded[index]
        if token == "--receipt-out":
            if index + 1 >= len(forwarded):
                print(
                    "tokenpak codex: --receipt-out requires a path",
                    file=sys.stderr,
                )
                sys.exit(2)
            receipt_out = forwarded[index + 1]
            index += 2
            continue
        if token.startswith("--receipt-out="):
            receipt_out = token.split("=", 1)[1]
            index += 1
            continue
        if token == "--run-id":
            if index + 1 >= len(forwarded):
                print("tokenpak codex: --run-id requires an id", file=sys.stderr)
                sys.exit(2)
            run_id = forwarded[index + 1]
            index += 2
            continue
        if token.startswith("--run-id="):
            run_id = token.split("=", 1)[1]
            index += 1
            continue
        stripped.append(token)
        index += 1
    return stripped, receipt_out, run_id


def _extract_codex_receipt_only_flag(forwarded: list[str]) -> tuple[list[str], bool]:
    """Consume TokenPak's Vanilla receipt-only flag wherever it appears."""
    stripped: list[str] = []
    receipt_only = False
    for token in forwarded:
        if token == "--receipt-only":
            receipt_only = True
            continue
        stripped.append(token)
    return stripped, receipt_only


def _codex_namespace_from_tail(tail: list[str]) -> argparse.Namespace:
    """Parse TokenPak-owned ``codex`` flags while preserving Codex-native flags."""
    budget = None
    install_only = False
    passthrough: list[str] = []
    stripped, receipt_out, run_id = _extract_codex_accounting_flags(tail)
    stripped, receipt_only = _extract_codex_receipt_only_flag(stripped)

    index = 0
    while index < len(stripped):
        token = stripped[index]
        if token == "--budget":
            if index + 1 >= len(stripped):
                print("tokenpak codex: --budget requires USD", file=sys.stderr)
                sys.exit(2)
            try:
                budget = float(stripped[index + 1])
            except ValueError:
                print("tokenpak codex: --budget must be numeric", file=sys.stderr)
                sys.exit(2)
            index += 2
            continue
        if token.startswith("--budget="):
            try:
                budget = float(token.split("=", 1)[1])
            except ValueError:
                print("tokenpak codex: --budget must be numeric", file=sys.stderr)
                sys.exit(2)
            index += 1
            continue
        if token == "--install-only":
            install_only = True
            index += 1
            continue
        passthrough.append(token)
        index += 1

    return argparse.Namespace(
        command="codex",
        func=cmd_codex,
        budget=budget,
        install_only=install_only,
        receipt_only=receipt_only,
        receipt_out=receipt_out,
        run_id=run_id,
        args=passthrough,
        db=".tokenpak/registry.db",
    )


def cmd_test(args: CommandArgs) -> None:
    """Interactive A/B test — auto-detects platforms, providers, models."""
    from .cli.commands.test import run

    run(args)


def cmd_prove(args: CommandArgs) -> None:
    """Run an A/B value proof comparing direct API vs tokenpak."""
    action = getattr(args, "prove_action", None)

    if action == "run":
        from .prove.runner import run_proof
        from .prove.scenario import resolve_scenario

        scenario_name = getattr(args, "scenario", "default")
        scenario = resolve_scenario(scenario_name)
        # Apply CLI overrides
        if getattr(args, "model", None):
            scenario.model = args.model
            from .prove.scenario import _detect_provider

            scenario.provider = _detect_provider(args.model)
        if getattr(args, "provider", None):
            scenario.provider = args.provider
        no_live = getattr(args, "no_live", False)
        run_proof(scenario, live=not no_live)

    elif action == "list":
        from .prove.scenario import list_scenarios

        scenarios = list_scenarios()
        if not scenarios:
            print("No scenarios found.")
            print("Create one at: ~/.tokenpak/prove/scenarios/<name>.md")
            return
        print("\n  Available scenarios:\n")
        for s in scenarios:
            turns = s.get("turns", "?")
            print(f"    {s['id']:24s}  {s.get('name', ''):30s}  {turns} turns  ({s['source']})")
        print()

    elif action == "show":
        import json

        proof_id = getattr(args, "proof_id", "")
        results_dir = __import__("pathlib").Path.home() / ".tokenpak" / "prove" / "results"
        path = results_dir / f"{proof_id}.json"
        if not path.exists():
            matches = list(results_dir.glob(f"{proof_id}*.json")) if results_dir.exists() else []
            if matches:
                path = matches[0]
            else:
                print(f"Proof '{proof_id}' not found in {results_dir}")
                return
        data = json.loads(path.read_text())
        print(json.dumps(data, indent=2))

    elif action == "create":
        _prove_create_scenario(args)

    elif action == "providers":
        from .prove.adapter import list_providers

        providers = list_providers()
        print("\n  Registered providers:\n")
        for p in providers:
            raw_models = p.get("models")
            provider_models = (
                [model for model in raw_models if isinstance(model, str)]
                if isinstance(raw_models, list)
                else []
            )
            models = ", ".join(provider_models[:5])
            if len(provider_models) > 5:
                models += f", ... (+{len(provider_models) - 5})"
            name = str(p.get("name", "unknown"))
            provider_format = str(p.get("format", "unknown"))
            source = str(p.get("source", "unknown"))
            print(f"    {name:12s}  format={provider_format:10s}  ({source})")
            print(f"    {'':12s}  models: {models}")
        print("\n  Add custom providers: ~/.tokenpak/prove/providers.yaml")
        print()

    else:
        print("Usage: tokenpak prove {run|list|show|create|providers}")
        print("  run [scenario]   Run a value proof (default: 'default')")
        print("  list             List available scenarios")
        print("  show <proof_id>  Show a past proof result")
        print("  create           Create a new scenario")
        print("  providers        List registered providers + models")


def _prove_create_scenario(args: CommandArgs) -> None:
    """Create a new scenario .md file from CLI args or interactively."""
    from pathlib import Path

    name = getattr(args, "name", None)
    if not name:
        print("Usage: tokenpak prove create --name <scenario-name>")
        return

    scenarios_dir = Path.home() / ".tokenpak" / "prove" / "scenarios"
    scenarios_dir.mkdir(parents=True, exist_ok=True)
    path = scenarios_dir / f"{name}.md"

    if path.exists():
        print(f"Scenario '{name}' already exists at {path}")
        return

    # Get prompts from args or generate template
    prompts = getattr(args, "prompts", [])
    model = getattr(args, "model", None) or "claude-sonnet-4-6"

    lines = [
        "---",
        f"name: {name}",
        f"model: {model}",
        "system: You are a helpful software engineer. Be concise and precise.",
        "max_tokens: 4096",
        "---",
        "",
    ]

    if prompts:
        for i, prompt in enumerate(prompts, 1):
            lines.append(f"## Turn {i}")
            lines.append("")
            lines.append(prompt)
            lines.append("")
    else:
        lines.append("## Turn 1: Exploration")
        lines.append("")
        lines.append("Describe your first prompt here.")
        lines.append("")
        lines.append("## Turn 2: Implementation")
        lines.append("")
        lines.append("Describe your second prompt here.")
        lines.append("")
        lines.append("## Turn 3: Verification")
        lines.append("")
        lines.append("Describe your third prompt here.")
        lines.append("")

    path.write_text("\n".join(lines))
    print(f"Created scenario: {path}")
    print("Edit the file to customize your test prompts, then run:")
    print(f"  tokenpak prove run {name}")


def _build_claude_parser(sub: Subparsers) -> None:
    p = sub.add_parser(
        "claude",
        help="Launch Claude Code with companion active",
        description=(
            "Launch Claude Code with tokenpak companion active.\n\n"
            "All arguments are forwarded verbatim to the claude binary.\n\n"
            "Examples:\n"
            "  tokenpak claude\n"
            "  tokenpak claude --budget 5.00\n"
            '  tokenpak claude --print "Fix the bug"\n'
            '  tokenpak claude --model claude-sonnet-4-6 --print "Review this PR"'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--budget",
        type=float,
        default=None,
        metavar="USD",
        help="Daily spend cap in USD; sets TOKENPAK_COMPANION_BUDGET env var",
    )
    p.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded verbatim to claude",
    )
    p.set_defaults(func=cmd_claude)


def _build_codex_parser(sub: Subparsers) -> None:
    p = sub.add_parser(
        "codex",
        help="Launch Codex with companion active",
        description=(
            "Launch OpenAI Codex CLI with tokenpak companion active.\n\n"
            "Registers the MCP server, installs hooks, and writes AGENTS.md,\n"
            "then launches Codex with any user-provided arguments.\n\n"
            "Examples:\n"
            "  tokenpak codex\n"
            "  tokenpak codex --install-only    # set up without launching Codex\n"
            "  tokenpak codex doctor            # verify installation\n"
            "  tokenpak codex uninstall         # clean selected home; preserve shared skills in use\n"
            "  tokenpak codex --budget 5.00\n"
            '  tokenpak codex "Fix the login bug"\n'
            "  tokenpak codex --model o3 -s workspace-write"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--budget",
        type=float,
        default=None,
        metavar="USD",
        help="Daily spend cap in USD; sets TOKENPAK_COMPANION_BUDGET env var",
    )
    p.add_argument(
        "--install-only",
        action="store_true",
        help="Run setup (MCP, hooks, AGENTS.md, skills) and exit without launching codex",
    )
    p.add_argument(
        "--receipt-only",
        action="store_true",
        help=(
            "Launch vanilla Codex and write a no-body receipt without installing "
            "or activating companion setup"
        ),
    )
    p.add_argument(
        "--receipt-out",
        default=None,
        metavar="PATH",
        help="Write a no-body accounting receipt for this Codex process",
    )
    p.add_argument(
        "--run-id",
        default=None,
        metavar="ID",
        help="Stable run identifier to include in the accounting receipt",
    )
    p.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Arguments forwarded verbatim to codex (or `doctor` / `uninstall`)",
    )
    p.set_defaults(func=cmd_codex)


def cmd_creds(args: CommandArgs) -> None:
    """Discover and inspect credentials across platforms."""
    import sys

    from .creds.cli import main as creds_main

    sys.exit(creds_main(list(args.args)))


def _build_creds_parser(sub: Subparsers) -> None:
    p = sub.add_parser(
        "creds",
        help="Discover credentials across platforms + doctor",
        description=(
            "Inspect, manage, and dry-run-route credentials tokenpak can see from\n"
            "all registered providers (Codex CLI, Claude CLI, env vars,\n"
            "~/.tokenpak/credentials.toml, and external client profiles).\n\n"
            "Proxy fast-path integration still deferred — `creds route` is a\n"
            "dry-run (what would I pick) with no side effects.\n\n"
            "Examples:\n"
            "  tokenpak creds list                                  # show all\n"
            "  tokenpak creds doctor                                # hazards\n"
            "  tokenpak creds add                                   # BYOK (interactive)\n"
            "  tokenpak creds add --id openai-work --platform openai \\\n"
            "       --kind api_key --key sk-...\n"
            "  tokenpak creds remove openai-work\n"
            "  tokenpak creds test openai-work                      # cheap live probe\n"
            "  tokenpak creds route api.anthropic.com               # what'd I pick?\n"
            "  tokenpak creds route api.openai.com --caller openclaw:main:* \\\n"
            "       --tag codex-personal"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "args",
        nargs=argparse.REMAINDER,
        help="Subcommand + args (list | doctor)",
    )
    p.set_defaults(func=cmd_creds)


def _build_prove_parser(sub: Subparsers) -> None:
    p = sub.add_parser(
        "prove",
        help="A/B value proof: direct API vs tokenpak",
        description=(
            "Run the same multi-turn prompt scenario through direct API and through\n"
            "tokenpak, then compare metrics side-by-side.\n\n"
            "Scenarios are .md files with YAML frontmatter and ## Turn headings.\n"
            "Create your own at: ~/.tokenpak/prove/scenarios/<name>.md\n\n"
            "Examples:\n"
            "  tokenpak prove run                       # run default scenario\n"
            "  tokenpak prove run my-scenario            # run custom scenario\n"
            "  tokenpak prove run default --model gpt-4o # override model\n"
            "  tokenpak prove list                       # list all scenarios\n"
            "  tokenpak prove show prf_a1b2c3d4          # show past result\n"
            "  tokenpak prove create --name my-test      # create new scenario"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    prove_sub = p.add_subparsers(dest="prove_action")

    # prove run
    p_run = prove_sub.add_parser("run", help="Run a value proof")
    p_run.add_argument(
        "scenario", nargs="?", default="default", help="Scenario name (default: 'default')"
    )
    p_run.add_argument("--model", "-m", help="Override model from scenario")
    p_run.add_argument("--provider", help="Override provider (anthropic|openai)")
    p_run.add_argument("--no-live", action="store_true", help="Skip launching live display windows")
    p_run.set_defaults(func=cmd_prove)

    # prove list
    p_list = prove_sub.add_parser("list", help="List available scenarios")
    p_list.set_defaults(func=cmd_prove)

    # prove show
    p_show = prove_sub.add_parser("show", help="Show a past proof result")
    p_show.add_argument("proof_id", help="Proof ID (e.g. prf_a1b2c3d4)")
    p_show.set_defaults(func=cmd_prove)

    # prove create
    p_create = prove_sub.add_parser("create", help="Create a new scenario")
    p_create.add_argument("--name", required=True, help="Scenario name")
    p_create.add_argument("--model", help="Model to use (default: claude-sonnet-4-6)")
    p_create.add_argument("prompts", nargs="*", help="Turn prompts (one per positional arg)")
    p_create.set_defaults(func=cmd_prove)

    # prove providers
    p_providers = prove_sub.add_parser("providers", help="List registered providers + models")
    p_providers.set_defaults(func=cmd_prove)

    p.set_defaults(func=cmd_prove)


def _build_test_parser(sub: Subparsers) -> None:
    p = sub.add_parser(
        "test",
        help="Interactive A/B test with auto-detection",
        description=(
            "Launch an interactive test that auto-detects your available\n"
            "platforms, providers, and models, then runs a 5-turn A/B\n"
            "comparison (with vs without tokenpak) with live display.\n\n"
            "Run: tokenpak test"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.set_defaults(func=cmd_test)


def _build_stub_parsers(sub: Subparsers) -> None:
    """Register stub parsers for commands advertised in help/registry but not yet implemented.

    These prevent 'invalid choice' argparse errors and give users a clear message
    instead of a traceback.
    """
    _STUBS = {
        "audit": ("Audit log surface (Pro); see docs/guides/enterprise/security-architecture.md"),
        "compliance": (
            "Compliance report surface (Pro); see docs/guides/enterprise/compliance-mapping.md"
        ),
        "watch": ("Not implemented. Use `tokenpak dashboard` for savings you can look at."),
    }

    def _make_stub(name: str, desc: str) -> CommandHandler:
        def handler(args: CommandArgs) -> None:
            print(f"tokenpak {name}: {desc}")
            print("This command is planned but not yet available in this version.")

        return handler

    for name, desc in _STUBS.items():
        p = sub.add_parser(name, help=desc)
        p.set_defaults(func=_make_stub(name, desc))

    # ── License commands (Free OSS tier, Pro-ready surface) ──────────────────
    p_license = sub.add_parser(
        "license",
        help="Show license and edition info",
    )
    p_license.add_argument(
        "--json", dest="as_json", action="store_true", help="Machine-readable JSON output"
    )
    p_license.set_defaults(
        func=lambda args: __import__(
            "tokenpak.cli.commands.license_cmd", fromlist=["run_license"]
        ).run_license(args)
    )

    p_plan = sub.add_parser(
        "plan",
        help="List available plans and your current tier",
    )
    p_plan.add_argument(
        "--json", dest="as_json", action="store_true", help="Machine-readable JSON output"
    )
    p_plan.set_defaults(
        func=lambda args: __import__(
            "tokenpak.cli.commands.license_cmd", fromlist=["run_plan"]
        ).run_plan(args)
    )

    p_activate = sub.add_parser(
        "activate",
        help="Store a Pro license key",
    )
    p_activate.add_argument("key", nargs="?", default="", help="Your license key")
    p_activate.add_argument("--email", default="", help="Optional email for the license")
    p_activate.set_defaults(
        func=lambda args: __import__(
            "tokenpak.cli.commands.license_cmd", fromlist=["run_activate"]
        ).run_activate(args)
    )

    p_deactivate = sub.add_parser(
        "deactivate",
        help="Remove stored license and revert to Free (OSS)",
    )
    p_deactivate.set_defaults(
        func=lambda args: __import__(
            "tokenpak.cli.commands.license_cmd", fromlist=["run_deactivate"]
        ).run_deactivate(args)
    )

    # ── `integrate` — real implementation (Free GTM feature) ─────────────────
    p_integrate = sub.add_parser(
        "integrate",
        help="Show setup instructions for LLM clients (Claude Code, Cursor, Cline, Continue, Aider, SDKs)",
        description=(
            "Show one-step setup instructions for pointing your LLM client at tokenpak.\n\n"
            "Examples:\n"
            "  tokenpak integrate                # list detected clients + SDKs\n"
            "  tokenpak integrate cursor         # show Cursor setup\n"
            "  tokenpak integrate claude-code    # show Claude Code setup\n"
            "  tokenpak integrate --all          # dump instructions for every client"
        ),
    )
    p_integrate.add_argument(
        "client",
        nargs="?",
        default=None,
        help="Client key: claude-code | cursor | cline | continue | aider | codex | openai-sdk | anthropic-sdk | litellm",
    )
    p_integrate.add_argument(
        "--all",
        action="store_true",
        help="Show instructions for every supported client",
    )
    p_integrate.add_argument(
        "--proxy-url",
        default=None,
        help="Override the printed proxy URL (default: $TOKENPAK_PROXY_URL or http://localhost:8766)",
    )
    p_integrate.add_argument(
        "--apply",
        action="store_true",
        help="Auto-write config files for the given client (headless / scripted path)",
    )
    p_integrate.add_argument(
        "--revert",
        action="store_true",
        help="Restore the most recent backup for the given client (undoes --apply)",
    )
    p_integrate.add_argument(
        "--tier",
        choices=["strict", "standard", "auto", "fleet"],
        default=None,
        help="Permission tier to apply with --apply (claude-code / codex only; "
        "default: standard). 'fleet' is the legacy full-bypass alias for "
        "both TokenPak launchers and never persists into client config.",
    )
    p_integrate.add_argument(
        "--yes",
        action="store_true",
        help="Confirm dangerous choices non-interactively (required for legacy --tier fleet)",
    )

    def _integrate_dispatch(args: CommandArgs) -> object:
        from tokenpak.cli.commands.integrate import run_integrate

        return run_integrate(args)

    p_integrate.set_defaults(func=_integrate_dispatch)

    # ── `permissions` — persistent tiers + launcher defaults ─────────────────
    p_permissions = sub.add_parser(
        "permissions",
        help="Manage persistent tiers and per-client launcher permission defaults",
        description=(
            "Manage the TokenPak permission tier system.\n\n"
            "Persistent tiers (strict/standard/auto) are written into the client's\n"
            "own config (Claude Code settings.json / Codex config.toml). Launcher\n"
            "defaults are TokenPak-scoped only: `tokenpak claude` / `tokenpak codex`\n"
            "inject session arguments and print a warning — client configs are never\n"
            "modified by launcher defaults.\n\n"
            "Examples:\n"
            "  tokenpak permissions show                      # tiers + launcher defaults\n"
            "  tokenpak permissions set auto                  # both clients\n"
            "  tokenpak permissions set strict --client codex # one client\n"
            "  tokenpak permissions launcher approval-bypass --client codex\n"
            "  tokenpak permissions launcher sandbox-bypass --client codex\n"
            "  tokenpak permissions launcher full-bypass --client both\n"
            "  tokenpak permissions launcher inherit --client both\n"
            "  tokenpak permissions set fleet                 # legacy full-bypass alias"
        ),
    )
    perm_sub = p_permissions.add_subparsers(dest="permissions_cmd")
    pp_show = perm_sub.add_parser(
        "show", help="Show per-client persistent tiers and launcher defaults"
    )
    pp_show.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Output one schema-versioned JSON object",
    )
    pp_show.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress normal output; safety warnings still go to stderr",
    )
    pp_set = perm_sub.add_parser(
        "set", help="Set a persistent tier or the full-bypass compatibility alias"
    )
    pp_set.add_argument(
        "tier",
        choices=["strict", "standard", "auto", "fleet"],
        help=("Tier to apply ('fleet' is a legacy full-bypass alias and requires --client both)"),
    )
    pp_set.add_argument(
        "--client",
        choices=["claude-code", "codex", "both"],
        default="both",
        help="Which client to configure",
    )
    pp_set.add_argument(
        "--yes",
        action="store_true",
        help="Confirm the `permissions set fleet` full-bypass alias non-interactively",
    )
    pp_reset = perm_sub.add_parser(
        "reset",
        help="Remove managed tier keys and reset every launcher default to inherit",
    )
    pp_reset.add_argument(
        "--client",
        choices=["claude-code", "codex", "both"],
        default="both",
        help="Which client to reset",
    )

    pp_launcher = perm_sub.add_parser(
        "launcher",
        help="Set a TokenPak-launcher-only permission default",
        description=(
            "Set session-only permission defaults for `tokenpak claude` and\n"
            "`tokenpak codex`. These settings never modify client config files.\n"
            "Every bypass mode requires confirmation and prints a warning on each\n"
            "affected launch. Managed administrator policy can still constrain or\n"
            "reject the client launch.\n\n"
            "Modes:\n"
            "  inherit          inject nothing; use client and managed policy\n"
            "  approval-bypass disable prompts; keep sandbox limits (Codex only)\n"
            "  sandbox-bypass  disable sandbox; keep approvals (Codex only)\n"
            "  full-bypass     disable local prompts and sandbox/permission checks\n\n"
            "Use `launcher <mode> --client <client>` to configure. Choose `inherit`\n"
            "to disable a launcher override. Use `permissions show` to inspect."
        ),
    )
    pp_launcher.add_argument(
        "launcher_mode",
        choices=["inherit", "approval-bypass", "sandbox-bypass", "full-bypass"],
        help=(
            "inherit | approval-bypass | sandbox-bypass | full-bypass "
            "(partial bypass modes are Codex-only)"
        ),
    )
    pp_launcher.add_argument(
        "--client",
        choices=["claude-code", "codex", "both"],
        required=True,
        help="Client scope; explicit selection is required for safety",
    )
    pp_launcher.add_argument(
        "--yes",
        action="store_true",
        help="Confirm a bypass mode non-interactively; warnings still print",
    )
    pp_launcher.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Output one schema-versioned result object",
    )
    pp_launcher.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress success output; safety warnings still go to stderr",
    )

    def _permissions_dispatch(args: CommandArgs) -> object:
        from tokenpak.cli.commands.permissions import run_permissions

        return run_permissions(args)

    p_permissions.set_defaults(func=_permissions_dispatch)

    # ── OpenClaw adapter sync subcommand ─────────────────────────
    p_openclaw = sub.add_parser(
        "openclaw",
        help="Manage OpenClaw integration (refresh model list, detect, setup)",
    )
    oc_sub = p_openclaw.add_subparsers(dest="openclaw_cmd")

    p_oc_refresh = oc_sub.add_parser(
        "refresh-models",
        help="Re-sync OpenClaw providers.models from the live tokenpak registry "
        "(picks up new models like opus-4-7 without editing config)",
    )
    p_oc_refresh.add_argument(
        "--proxy-url",
        default=None,
        help="Proxy URL to query (default: $TOKENPAK_PROXY_URL or http://localhost:8766)",
    )
    p_oc_refresh.add_argument(
        "--config-path",
        default=None,
        help="Target a specific openclaw.json (default: refresh every install "
        "discovered on this host — main, governor, etc.)",
    )

    p_oc_detect = oc_sub.add_parser(
        "detect",
        help="Check whether OpenClaw is installed on this host",
    )

    def _openclaw_dispatch(args: CommandArgs) -> object:
        import os as _os
        from pathlib import Path as _Path

        sub_cmd = getattr(args, "openclaw_cmd", None)
        if sub_cmd == "detect":
            from tokenpak.sdk.openclaw import discover_openclaw_configs

            configs = discover_openclaw_configs()
            if configs:
                print(
                    f"✅ OpenClaw detected ({len(configs)} install{'s' if len(configs) != 1 else ''})"
                )
                for p in configs:
                    print(f"  · {p}")
                return 0
            print("✗ OpenClaw not installed on this host")
            return 1
        if sub_cmd == "refresh-models":
            from tokenpak.sdk.openclaw import setup_openclaw

            proxy_url = (
                getattr(args, "proxy_url", None)
                or _os.environ.get("TOKENPAK_PROXY_URL")
                or "http://localhost:8766"
            )
            cp = getattr(args, "config_path", None)
            config_path = _Path(cp).expanduser() if isinstance(cp, str) and cp else None
            result = setup_openclaw(proxy_url=proxy_url, config_path=config_path)
            if "error" in result:
                print(f"✖ {result['error']}")
                return 1
            raw_configs = result.get("configs", [])
            configs = raw_configs if isinstance(raw_configs, list) else []
            print("")
            print("  OpenClaw model refresh")
            print("  " + "─" * 40)
            print(f"  Proxy         {proxy_url}")
            print(f"  Installs      {len(configs)}")
            print("")
            for cfg in configs:
                if not isinstance(cfg, dict):
                    continue
                print(f"  {cfg.get('path', '?')}")
                if cfg.get("error"):
                    print(f"    ✖ {cfg['error']}")
                    continue
                src = cfg.get("models_source", "?")
                added = cfg.get("providers_added", [])
                updated = cfg.get("providers_updated", [])
                cc = cfg.get("claude_code_backend", False)
                print(f"    Models source {src}")
                if added:
                    print(f"    Providers +   {', '.join(added)}")
                if updated:
                    print(f"    Providers ~   {', '.join(updated)}")
                if cc:
                    print("    Backend       tokenpak-claude-code enabled")
                if not (added or updated):
                    print("    No changes — already in sync.")
            print("")
            return 0
        # Default: print help for the OpenClaw subcommand
        p_openclaw.print_help()
        return 0

    p_openclaw.set_defaults(func=_openclaw_dispatch)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokenpak",
        description="TokenPak — LLM Proxy with Prompt Packing",
        add_help=False,  # we handle --help ourselves for progressive disclosure
    )
    parser.add_argument(
        "--help", "-h", action="store_true", default=False, help="Show quick-start help"
    )
    parser.add_argument("--db", default=".tokenpak/registry.db", help="Registry SQLite path")

    sub = parser.add_subparsers(dest="command", required=False)

    # ── Progressive disclosure: help + aliases ────────────────────────────────
    p_menu = sub.add_parser("menu", help="Interactive command browser (arrow-key navigation)")
    p_menu.set_defaults(
        func=lambda args: __import__("tokenpak.cli.commands.menu", fromlist=["run_menu"]).run_menu()
    )

    p_help = sub.add_parser("help", help="Show all commands grouped by category")
    p_help.add_argument("cmd_name", nargs="?", default=None, help="Command name for detailed help")
    p_help.add_argument(
        "--more", action="store_true", help="Show essential + intermediate commands"
    )
    p_help.add_argument("--all", action="store_true", help="Show all commands")
    p_help.add_argument("--minimal", action="store_true", help="Show compact one-line command list")
    p_help.set_defaults(func=cmd_help)

    p_init = sub.add_parser("init", help="Guided first-run setup (API key, port, vault path)")
    p_init.set_defaults(func=cmd_init)

    p_setup = sub.add_parser(
        "setup",
        help="Interactive first-time configuration wizard",
        description=(
            "Guided first-run configuration. Prompts interactively when stdin is a "
            "terminal. For CI, containers, or any scripted run, state the choices "
            "explicitly: `tokenpak setup --profile balanced --yes`. Setup will not "
            "assume an answer from a closed stdin."
        ),
    )
    p_setup.add_argument(
        "--profile",
        choices=["minimal", "balanced", "aggressive"],
        default=None,
        help="Compression profile to use, instead of prompting",
    )
    p_setup.add_argument(
        "--port",
        type=int,
        default=None,
        help="Proxy port to configure (default 8766)",
    )
    p_setup.add_argument(
        "--yes",
        action="store_true",
        help="Overwrite an existing config without confirming",
    )
    p_setup.add_argument(
        "--start",
        action="store_true",
        help="Also start the proxy after writing config (default: config only)",
    )
    p_setup.set_defaults(func=cmd_setup)

    p_start = sub.add_parser(
        "start",
        help="Start the proxy (localhost:8766)",
        description=(
            "Start the TokenPak proxy server, which routes LLM API requests through\n"
            "Prompt Packing. The proxy listens on localhost:PORT and forwards\n"
            "compressed requests to your configured LLM providers.\n\n"
            "Example:\n"
            "  tokenpak start --port 8888 --workers 4\n\n"
            "(See also `tokenpak serve` for telemetry/ingest variants.)\n"
            "The proxy reads config from tokenpak.yaml or ~/.tokenpak/config.yaml"
        ),
    )
    # default=None, not 8766: cmd_start must be able to tell "user passed a
    # port" from "user passed nothing". With a concrete default, honouring the
    # flag would silently override TOKENPAK_PORT on every unflagged run.
    p_start.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to listen on (default: 8766, or TOKENPAK_PORT)",
    )
    p_start.add_argument(
        "--workers", type=int, default=2, help="Number of worker processes (default: 2)"
    )
    p_start.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Logging level (default: info)",
    )
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop the running proxy")
    p_stop.set_defaults(func=cmd_stop)

    p_restart = sub.add_parser("restart", help="Restart the proxy")
    p_restart.set_defaults(func=cmd_restart)

    p_logs = sub.add_parser("logs", help="Show recent proxy logs")
    p_logs.add_argument(
        "--lines", "-n", type=int, default=50, help="Number of log lines to show (default: 50)"
    )
    p_logs.set_defaults(func=cmd_logs)
    # ── End aliases ───────────────────────────────────────────────────────────

    _build_route_parser(sub)
    _build_validate_parser(sub)
    _build_vault_health_parser(sub)
    _build_config_check_parser(sub)
    _build_validate_config_parser(sub)

    p_index = sub.add_parser("index", help="Index a directory")
    p_index.add_argument("directory", nargs="?", default=None, help="Directory to index")
    p_index.add_argument("--status", action="store_true", help="Show indexed file count by type")
    p_index.add_argument("--budget", type=int, default=8000)
    p_index.add_argument(
        "--workers", "-w", type=int, default=4, help="Parallel workers (default: 4)"
    )
    p_index.add_argument(
        "--auto-workers",
        action="store_true",
        help="Use hybrid calibration (static baseline + dynamic adjustment)",
    )
    p_index.add_argument(
        "--recalibrate", action="store_true", help="Run static calibration before indexing"
    )
    p_index.add_argument(
        "--calibration-rounds",
        type=int,
        default=2,
        help="Calibration rounds per candidate worker count",
    )
    p_index.add_argument(
        "--max-workers", type=int, default=8, help="Upper worker cap for auto/recalibration"
    )
    p_index.add_argument(
        "--watch", action="store_true", help="Watch directory and auto-reindex on file changes"
    )
    p_index.add_argument(
        "--debounce",
        type=int,
        default=500,
        help="Debounce delay in ms for watch mode (default: 500)",
    )
    p_index.add_argument(
        "--no-treesitter",
        action="store_true",
        help="Force regex-based code processing (skip tree-sitter)",
    )
    p_index.add_argument(
        "--reindex-all",
        action="store_true",
        dest="reindex_all",
        help="Reindex every directory registered in ~/.tokenpak/vault.yaml",
    )
    p_index.add_argument(
        "--reindex-path",
        dest="reindex_path",
        default=None,
        metavar="PATH",
        help="Reindex a single directory registered in ~/.tokenpak/vault.yaml",
    )
    p_index.set_defaults(func=cmd_index)

    p_search = sub.add_parser("search", help="Search indexed content")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--budget", type=int, default=8000)
    p_search.add_argument("--top-k", type=int, default=10)
    p_search.add_argument(
        "--gaps",
        default=DEFAULT_GAPS_PATH,
        help="Path to gaps.json for miss-based retrieval expansion",
    )
    p_search.add_argument(
        "--inject-refs",
        action="store_true",
        help="Enable compile-time reference injection (GitHub, URLs)",
    )
    p_search.set_defaults(func=cmd_search)

    p_stats = sub.add_parser("stats", help="Show registry stats")
    p_stats.set_defaults(func=cmd_stats)

    p_models = sub.add_parser("models", help="Show per-model usage and efficiency breakdown")
    p_models.add_argument(
        "model",
        nargs="?",
        default=None,
        help="Show details for a specific model (partial match, e.g. 'sonnet', 'gpt-4')",
    )
    p_models.add_argument("--raw", action="store_true", help="Output as JSON")
    p_models.set_defaults(func=cmd_models)

    p_serve = sub.add_parser("serve", help="Start monitoring proxy or telemetry server")
    # default=None, not 8766: cmd_serve must be able to tell "user passed a
    # port" from "user passed nothing". With a concrete default, honouring
    # the flag would silently override TOKENPAK_PORT and the config file's
    # `port` key on every unflagged run (see _resolve_serve_port).
    p_serve.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port to listen on (default: 8766, or TOKENPAK_PORT, or config's `port`)",
    )
    p_serve.add_argument("--telemetry", action="store_true", help="Start telemetry ingest server")
    p_serve.add_argument("--ingest", action="store_true", help="Start Phase 5A ingest API server")
    p_serve.add_argument("--workers", type=int, default=1, help="Number of uvicorn workers")
    p_serve.add_argument(
        "--profile",
        choices=["safe", "balanced", "aggressive", "agentic", "transparent"],
        default=None,
        help=("Workflow profile for this proxy process (default: TOKENPAK_PROFILE or balanced)"),
    )
    p_serve.add_argument(
        "--stats-footer",
        action="store_true",
        default=False,
        help=(
            "Print a per-request token-savings receipt (estimated dollars) "
            "in the proxy terminal (default: off)"
        ),
    )
    p_serve.add_argument(
        "--shutdown-timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help=(
            "Seconds to wait for in-flight requests to complete before forcing shutdown "
            "(default: 30, or TOKENPAK_SHUTDOWN_TIMEOUT env var)"
        ),
    )
    p_serve.add_argument(
        "--safe",
        action="store_true",
        default=False,
        help=(
            "Apply legacy safety compatibility settings for this process; "
            "does not toggle default HTTP body compaction."
        ),
    )
    p_serve.set_defaults(func=cmd_serve)

    p_monitor = sub.add_parser("monitor", help="Start the live monitor dashboard")
    p_monitor.add_argument("--port", type=int, default=8767, help="Dashboard port (default: 8767)")
    p_monitor.set_defaults(func=cmd_monitor)

    p_bench = sub.add_parser(
        "benchmark", help="Benchmark compression performance on sample or real data"
    )
    p_bench.add_argument(
        "directory",
        nargs="?",
        default=None,
        help="Directory to benchmark (used with --latency mode)",
    )
    p_bench.add_argument("--file", default=None, metavar="PATH", help="Benchmark a specific file")
    p_bench.add_argument(
        "--samples",
        action="store_true",
        help="Use built-in sample data (default when no file/directory given)",
    )
    p_bench.add_argument(
        "--json", dest="json", action="store_true", default=False, help="Output results as JSON"
    )
    p_bench.add_argument(
        "--latency",
        action="store_true",
        help="Run latency/indexing benchmark instead of compression benchmark",
    )
    p_bench.add_argument(
        "--iterations", type=int, default=3, help="Iterations for latency benchmark (default: 3)"
    )
    p_bench.add_argument(
        "--compare", action="store_true", help="Compare baseline vs optimized (latency mode only)"
    )
    p_bench.set_defaults(func=cmd_benchmark)

    p_cal = sub.add_parser("calibrate", help="Calibrate best worker count for this host")
    p_cal.add_argument("directory", help="Directory to sample for calibration")
    p_cal.add_argument("--max-workers", type=int, default=8)
    p_cal.add_argument("--rounds", type=int, default=2)
    p_cal.set_defaults(func=cmd_calibrate)

    p_doctor = sub.add_parser("doctor", help="Run system diagnostics")
    p_doctor.add_argument("--fix", action="store_true", help="Auto-fix issues where possible")
    p_doctor.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output results as machine-readable JSON",
    )
    p_doctor.add_argument(
        "--fleet", action="store_true", help="Check all agents in ~/.tokenpak/fleet.yaml"
    )
    p_doctor.add_argument(
        "--deploy", action="store_true", help="Push latest doctor to all agents (use with --fleet)"
    )
    p_doctor.add_argument(
        "--verbose", "-v", action="store_true", help="Show extra detail for each check"
    )
    p_doctor.add_argument(
        "--claude-code",
        dest="claude_code",
        action="store_true",
        help="Run Claude Code integration checks (ENABLE_TOOL_SEARCH, mode, IDE detection)",
    )
    p_doctor.add_argument(
        "--conformance",
        dest="conformance",
        action="store_true",
        help="Run TIP self-conformance checks (alias for `tokenpak tip conformance`)",
    )
    p_doctor.add_argument(
        "--lifecycle",
        dest="lifecycle",
        action="store_true",
        help="Show only the compact lifecycle summary (installed/setup/routed/proxy/update)",
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_diagnose = sub.add_parser("diagnose", help="Health check — config, vault, cache, proxy, disk")
    p_diagnose.add_argument(
        "--json", dest="json_output", action="store_true", default=False, help="Output as JSON"
    )
    p_diagnose.add_argument("--verbose", action="store_true", default=False, help="Verbose output")
    p_diagnose.set_defaults(func=cmd_diagnose)

    p_dashboard = sub.add_parser(
        "dashboard", help="Real-time health dashboard (TUI) or public web URL"
    )
    p_dashboard.add_argument("--fleet", action="store_true", help="Show fleet-wide summary (TUI)")
    p_dashboard.add_argument(
        "--json",
        dest="json_export",
        action="store_true",
        help="Export dashboard as JSON (non-interactive)",
    )
    p_dashboard.add_argument(
        "--layout",
        choices=("home", "dispatch", "spend", "debug", "fleet"),
        default="home",
        help="Select read-only cockpit layout for terminal or JSON output",
    )
    p_dashboard.add_argument(
        "--public",
        action="store_true",
        help="Advanced: show public URL with token for non-tunneled access",
    )
    p_dashboard.add_argument(
        "--show-token",
        dest="show_token",
        action="store_true",
        help="Display current dashboard token",
    )
    p_dashboard.add_argument(
        "--new-token", dest="new_token", action="store_true", help="Regenerate dashboard token"
    )
    p_dashboard_sub = p_dashboard.add_subparsers(dest="dashboard_action", metavar="<action>")
    p_dashboard_connect = p_dashboard_sub.add_parser(
        "connect",
        help="Open a remote dashboard through an SSH local tunnel",
        description="Open a remote dashboard through an SSH local tunnel.",
    )
    p_dashboard_connect.add_argument("host", help="SSH host or user@host to connect to")
    p_dashboard_connect.add_argument(
        "--remote-port",
        type=int,
        default=8766,
        help="Remote dashboard port",
    )
    p_dashboard_connect.add_argument(
        "--local-port",
        default="auto",
        help="Local listener port, or 'auto' to start at 8766 and choose the next free port",
    )
    p_dashboard_connect.add_argument(
        "--ssh-user",
        default=None,
        help="SSH username when HOST does not include user@",
    )
    p_dashboard_connect.add_argument(
        "--open",
        dest="open_browser",
        action="store_true",
        default=True,
        help="Open the dashboard URL in the default browser",
    )
    p_dashboard_connect.add_argument(
        "--no-open",
        dest="open_browser",
        action="store_false",
        help="Print the dashboard URL without opening a browser",
    )
    p_dashboard_connect.add_argument(
        "--health-timeout",
        type=float,
        default=20.0,
        help="Seconds to wait for /health to report OK",
    )
    p_dashboard_connect.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output connection result as JSON",
    )
    p_dashboard_disconnect = p_dashboard_sub.add_parser(
        "disconnect",
        help="Close a dashboard SSH local tunnel",
        description="Close a dashboard SSH local tunnel.",
    )
    p_dashboard_disconnect.add_argument("host", help="SSH host or user@host to disconnect")
    p_dashboard_disconnect.add_argument(
        "--ssh-user",
        default=None,
        help="SSH username when HOST does not include user@",
    )
    p_dashboard_disconnect.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="Output disconnect result as JSON",
    )

    p_dashboard.set_defaults(func=cmd_dashboard)

    p_preview = sub.add_parser(
        "preview", help="Preview compression dry-run (show token savings before sending)"
    )
    p_preview.add_argument(
        "input", nargs="?", default=None, help="Input text to preview (or reads from stdin)"
    )
    p_preview.add_argument("--file", type=str, help="Read input from file instead of command line")
    p_preview.add_argument(
        "--raw", action="store_true", help="Show raw compression output (no formatting)"
    )
    p_preview.add_argument("--verbose", action="store_true", help="Show detailed block breakdown")
    p_preview.add_argument("--json", action="store_true", help="Output as JSON (machine-readable)")
    p_preview.set_defaults(func=cmd_preview)

    p_agg = sub.add_parser("aggregate", help="Aggregate request ledger across machines")
    p_agg.add_argument("--since", default="7d", help="Time window, e.g. 7d, 24h, 30m, or ISO date")
    p_agg.add_argument("--json", dest="as_json", action="store_true", help="JSON output")
    p_agg.set_defaults(func=cmd_aggregate)

    p_req = sub.add_parser("requests", help="Live request explorer")
    p_req.add_argument("action", nargs="?", default="tail", help="tail | show | <request_id>")
    p_req.add_argument("request_id", nargs="?", help="Request id (for show)")
    p_req.add_argument("--limit", "-n", type=int, default=10, help="Number of rows to show")
    p_req.add_argument("--once", action="store_true", help="Print once and exit")
    p_req.set_defaults(func=cmd_requests)

    p_attr = sub.add_parser("attribution", help="View savings by agent/skill/model")
    p_attr.add_argument("--days", type=int, default=7, help="Number of days (default 7)")
    p_attr.add_argument("--agent", type=str, help="Filter by agent name")
    p_attr.add_argument("--model", type=str, help="Filter by model")
    p_attr.add_argument("--json", dest="as_json", action="store_true", help="JSON output")
    p_attr.set_defaults(func=cmd_attribution)

    p_explain = sub.add_parser("explain", help="Explain what a workflow profile sets")
    p_explain.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Profile name (safe|balanced|aggressive|agentic); omit to show all",
    )
    p_explain.set_defaults(func=cmd_explain)

    p_timeline = sub.add_parser("timeline", help="View savings trend over 7/30 days")
    p_timeline.add_argument("--days", type=int, default=7, help="Number of days (default 7)")
    p_timeline.add_argument("--chart", action="store_true", help="Show ASCII sparkline chart")
    p_timeline.add_argument("--json", dest="as_json", action="store_true", help="JSON output")
    p_timeline.set_defaults(func=cmd_timeline)

    _build_trigger_parser(sub)
    _build_cost_parser(sub)
    _build_budget_parser(sub)
    _build_forecast_parser(sub)
    _build_goals_parser(sub)
    _build_lock_parser(sub)
    _build_agent_parser(sub)
    _build_replay_parser(sub)
    _build_status_parser(sub)
    _build_upgrade_parser(sub)
    _build_usage_parser(sub)
    _build_savings_parser(sub)
    _build_recommendations_parser(sub)
    _build_compare_parser(sub)
    _build_leaderboard_parser(sub)
    _build_report_parser(sub)
    _build_alerts_parser(sub)
    _build_alerts_cmd_parser(sub)
    _build_debug_parser(sub)
    _build_demo_parser(sub)
    _build_diff_parser(sub)
    _build_run_parser(sub)
    _build_macro_parser(sub)
    _build_fingerprint_parser(sub)
    _build_learn_parser(sub)
    _build_user_template_parser(sub)
    _build_version_parser(sub)
    _build_update_parser(sub)
    _build_uninstall_parser(sub)
    _build_config_mgmt_parser(sub)
    _build_fleet_parser(sub)
    _build_compress_parser(sub)
    _build_optimize_parser(sub)
    _build_last_parser(sub)
    _build_prune_parser(sub)
    _build_retrieval_parser(sub)
    _build_claude_parser(sub)
    _build_codex_parser(sub)
    _build_creds_parser(sub)
    _build_pak_parser(sub)
    _build_tip_parser(sub)
    _build_features_parser(sub)
    _build_pakplan_parser(sub)
    _build_dispatch_parser(sub)
    _build_home_parser(sub)
    _build_prove_parser(sub)
    _build_test_parser(sub)
    _build_telemetry_parser(sub)

    # --- Stub parsers for commands advertised in help/registry but not yet wired ---
    _build_stub_parsers(sub)

    return parser


def _get_active_providers(health: HealthPayload) -> list[str]:
    """Extract active provider names from health endpoint circuit breakers."""
    cbs = health.get("circuit_breakers", {})
    providers = cbs.get("providers", {})
    if not providers:
        return []
    # All providers with circuit breakers are "active" in the proxy config
    return sorted([provider for provider in providers if provider])


def _get_cache_hit_rate(cache: CachePayload) -> float | None:
    """Calculate cache hit rate, preserving no-observation as unavailable."""
    if not cache:
        return None
    hits = cache.get("cache_hits")
    misses = cache.get("cache_misses")
    if (
        not isinstance(hits, (int, float))
        or isinstance(hits, bool)
        or not isinstance(misses, (int, float))
        or isinstance(misses, bool)
    ):
        return None
    total = hits + misses
    return (hits / total * 100) if total > 0 else None


def _cmd_status_legacy(args: CommandArgs) -> None:
    """Show system status — legacy technical output (now accessible via --full)."""
    mode = resolve_mode(args)
    fmt = OutputFormatter("Status", mode=mode, minimal=getattr(args, "minimal", False))

    # Fetch live proxy data
    health = cast(HealthPayload | None, _proxy_get("/health"))
    stats = cast(StatsPayload | None, _proxy_get("/stats"))
    cache = cast(CachePayload | None, _proxy_get("/cache-stats"))

    if mode == OutputMode.RAW:
        print(
            fmt.raw(
                {
                    "section": "status",
                    "proxy": health,
                    "stats": stats.get("session") if stats else None,
                    "cache": cache,
                    "active_providers": _get_active_providers(health) if health else [],
                    "cache_hit_rate": _get_cache_hit_rate(cache) if cache else None,
                }
            )
        )
        return

    print(fmt.header())
    print()

    if health:
        session_value = stats.get("session") if stats else None
        s = session_value if isinstance(session_value, dict) else None

        def _number(container: Mapping[str, object] | None, key: str) -> int | float | None:
            value = container.get(key) if container is not None else None
            return (
                value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
            )

        def _count(value: int | float | None) -> str:
            return f"{int(value):,}" if value is not None else "unknown"

        def _cost(value: int | float | None) -> str:
            return f"${float(value):.4f}" if value is not None else "unknown"

        uptime_value = health.get("uptime_seconds")
        if isinstance(uptime_value, (int, float)) and not isinstance(uptime_value, bool):
            uptime_s = max(0.0, float(uptime_value))
            h, rem = divmod(int(uptime_s), 3600)
            m = rem // 60
            uptime_str = f"{h}h {m:02d}m" if h else f"{m}m"
        else:
            uptime_str = "unknown"

        # Proxy status line
        print(
            fmt.signal(
                FS.ENABLED,
                f"Proxy: running (port {os.environ.get('TOKENPAK_PORT', '8766')})",
                tone="info",
            )
        )
        print(f"  Uptime:          {uptime_str}")
        print(f"  Requests:        {health.get('requests_total', 'unknown')}")
        print(f"  Errors:          {health.get('requests_errors', 'unknown')}")
        print(f"  Compilation:     {(stats or {}).get('compilation_mode', 'unknown')}")
        print()

        # Active Providers (NEW)
        active_providers = _get_active_providers(health)
        if active_providers:
            providers_str = ", ".join(active_providers)
            print(
                fmt.signal(
                    FS.ENABLED,
                    f"Providers: {len(active_providers)} active ({providers_str})",
                    tone="info",
                )
            )
        else:
            print(fmt.signal(FS.DISABLED, "Providers: none configured", tone="warn"))
        print()

        # Token savings
        inp = _number(s, "input_tokens")
        sent = _number(s, "sent_input_tokens")
        saved = _number(s, "saved_tokens")
        protected = _number(s, "protected_tokens")
        pct = saved / inp * 100 if inp is not None and inp > 0 and saved is not None else None
        pct_text = f"{pct:.1f}%" if pct is not None else "unknown"
        print(f"  Tokens in:       {_count(inp)}")
        print(f"  Tokens sent:     {_count(sent)}")
        print(f"  Tokens saved:    {_count(saved)} ({pct_text})")
        print(f"  Protected:       {_count(protected)}")
        print()

        # Cost
        cost = _number(s, "cost")
        cost_saved = _number(s, "cost_saved")
        print(f"  Cost:            {_cost(cost)}")
        if cost_saved is not None:
            print(f"  Cost saved:      {_cost(cost_saved)}")
        print()

        # Compact savings status bar — prefer today's stats over session stats.
        today_value = stats.get("today") if stats else None
        _today = today_value if isinstance(today_value, dict) else None
        _today_input = _number(_today, "input_tokens")
        _today_compressed = _number(_today, "compressed_tokens")
        _today_cache_read = _number(_today, "cache_read_tokens")
        _today_total_saved_tok = (
            _today_compressed + _today_cache_read
            if _today_compressed is not None and _today_cache_read is not None
            else None
        )

        # Compression % from today's data
        _avg_compression = (
            _today_compressed / _today_input * 100
            if _today_input is not None and _today_input > 0 and _today_compressed is not None
            else None
        )

        # Token count formatter: K/M/raw
        def _fmt_tokens(n: float) -> str:
            if n >= 1_000_000:
                return f"{n / 1_000_000:.1f}M"
            if n >= 1_000:
                return f"{n // 1_000}K"
            return str(n)

        # Cost saved from DB (injected by get_savings_report, read below)
        _db_cost_saved: float | None = None
        try:
            from .telemetry.query_dsl import get_savings_report as _gsr

            _db_report = _gsr(days=1)
            _db_cost_saved = float(_db_report.savings_amount) if _db_report else None
        except Exception:
            pass

        _savings_parts = []
        if _avg_compression is not None and _avg_compression > 0:
            _savings_parts.append(f"{_avg_compression:.1f}% avg compression")
        if _today_total_saved_tok is not None and _today_total_saved_tok > 0:
            _savings_parts.append(f"{_fmt_tokens(_today_total_saved_tok)} tokens saved today")
        if _db_cost_saved is not None and _db_cost_saved > 0:
            _savings_parts.append(f"~${_db_cost_saved:.2f} saved today")

        if _savings_parts:
            print(f"  💰 Savings: {' | '.join(_savings_parts)}")
        elif stats is None and _db_cost_saved is None:
            print("  💰 Savings: unavailable (stats endpoint not available)")
        else:
            print("  💰 Savings: no data yet (run some requests first)")
        print()

        # Today's savings (from telemetry DB)
        try:
            from .telemetry.query_dsl import get_savings_report

            _daily = get_savings_report(days=1)
            if _daily.savings_amount > 0 or _daily.total_cost > 0:
                _daily_hit = (
                    f"{_daily.cache_hit_rate * 100:.0f}% cache hit"
                    if _daily.cache_hit_rate > 0
                    else ""
                )
                _daily_suffix = f" ({_daily_hit})" if _daily_hit else ""
                print(f"  📅 Today's savings: ${_daily.savings_amount:.2f}{_daily_suffix}")
            else:
                print("  📅 Today's savings: $0.00")
            print()
        except Exception:
            pass

        # Cache (with NEW cache hit rate display)
        if cache:
            hits = _number(cache, "cache_hits")
            misses = _number(cache, "cache_misses")
            hit_rate = _get_cache_hit_rate(cache)
            read_tokens = _number(cache, "cache_read_tokens")
            if hits is None or misses is None:
                print("  Cache hit rate:  unavailable")
            elif hit_rate is None:
                print("  Cache hit rate:  no cache observations yet")
            else:
                print(
                    f"  Cache hit rate:  {hit_rate:.0f}% "
                    f"({_count(hits)} hits / {_count(misses)} misses)"
                )
            print(f"  Cache reads:     {_count(read_tokens)} tokens")
            miss_reasons = cache.get("miss_reasons", {})
            if miss_reasons and any(v > 0 for v in miss_reasons.values()):
                reasons = [f"{k}={v}" for k, v in miss_reasons.items() if v > 0]
                print(f"  Miss reasons:    {', '.join(reasons)}")
            print()

        # Budget tracking (local DB)
        try:
            from tokenpak.telemetry.budget import BudgetTracker

            tracker = BudgetTracker()
            budget_rows = []
            for period in ("daily", "monthly"):
                status = tracker.get_status(period)
                if status:
                    budget_rows.append(
                        (
                            f"{period.capitalize()} budget",
                            f"${status.spent_usd:.4f} / ${status.limit_usd:.2f} ({status.percent_used:.1f}%)",
                        )
                    )
            if budget_rows:
                print(fmt.kv(budget_rows))
        except Exception:
            pass

        # Circuit breakers
        cbs = health.get("circuit_breakers", {})
        providers = cbs.get("providers", {})
        if providers:
            cb_parts = [
                f"{provider} {'🔴' if state.get('state') == 'open' else '✅'}"
                for provider, state in providers.items()
            ]
            print(f"  Circuits:        {' | '.join(cb_parts)}")
    else:
        print(fmt.signal(FS.DISABLED, "Proxy: not reachable", tone="warn"))
        print("  Run `tokenpak serve` or check if proxy.py is running.")
        print()


def cmd_status(args: CommandArgs) -> None:
    """Show savings-first status (default) with optional drill-down views."""
    is_full = getattr(args, "full", False)
    is_json = getattr(args, "as_json", False)
    is_minimal = getattr(args, "minimal", False)
    no_meme = getattr(args, "no_meme", False)
    by_source = getattr(args, "by_source", False)
    by_provider = getattr(args, "by_provider", False)
    tip_cache = getattr(args, "tip_cache", False)

    # --raw dispatches to legacy (raw JSON mode)
    if getattr(args, "raw", False):
        _cmd_status_legacy(args)
        return

    # Delegate to savings-first status.py
    try:
        from tokenpak.cli.commands.status import run as savings_status_run

        proxy_url = f"http://127.0.0.1:{os.environ.get('TOKENPAK_PORT', '8766')}"
        savings_status_run(
            proxy_base=proxy_url,
            minimal=is_minimal,
            full=is_full,
            by_source=by_source,
            by_provider=by_provider,
            tip_cache=tip_cache,
            as_json=is_json,
            no_meme=no_meme,
            days=getattr(args, "days", 0),
            hours=getattr(args, "hours", 0),
            fleet=getattr(args, "fleet", False),
            since=getattr(args, "since", None),
        )
    except Exception as e:
        print(f"⚠️  Savings-first status failed ({e}), falling back to legacy output...")
        _cmd_status_legacy(args)


def cmd_usage(args: CommandArgs) -> None:
    """Show model token usage summary."""
    from .telemetry.query_dsl import get_model_usage

    mode = resolve_mode(args)
    fmt = OutputFormatter("Usage", mode=mode, minimal=getattr(args, "minimal", False))
    days = getattr(args, "days", 30)
    rows = get_model_usage(days=days)

    if mode == OutputMode.RAW:
        print(fmt.raw({"section": "usage", "days": days, "rows": [r.__dict__ for r in rows]}))
        return

    total_requests = sum(r.request_count for r in rows)
    total_tokens = sum(r.total_input_tokens + r.total_output_tokens for r in rows)

    if fmt.minimal:
        print(fmt.minimal_line([f"{total_requests} req", f"{total_tokens:,} tok", f"{days}d"]))
        return

    print(fmt.header())
    print()
    print(
        fmt.kv(
            [
                ("Requests", f"{total_requests:,}"),
                ("Tokens", f"{total_tokens:,}"),
                ("Window", f"{days}d"),
            ]
        )
    )

    if mode == OutputMode.VERBOSE:
        print()
        for r in rows[:10]:
            print(
                f"{FS.ENABLED} {r.model} ({r.provider})  req={r.request_count} in={r.total_input_tokens} out={r.total_output_tokens}"
            )


def _savings_verification_payload() -> dict[str, object]:
    """Return an honest independent-count check for ``savings --verify``."""
    from .telemetry.tokens_verify import TokenizerUnavailableError, verify_packaged_corpus

    scope_note = (
        "Stored request logs contain counts, not source text. This checks the "
        "TokenPak UTF-8 byte estimator against cl100k_base on a packaged "
        "fixture corpus; it does not recount logged requests."
    )
    try:
        result = verify_packaged_corpus()
    except TokenizerUnavailableError as exc:
        return {
            "available": False,
            "scope": "packaged-fixture-corpus",
            "scope_note": scope_note,
            "reported_method": "utf8-bytes-div-4",
            "independent_method": "tiktoken:cl100k_base",
            "message": str(exc),
        }

    return {
        "available": True,
        "scope": "packaged-fixture-corpus",
        "scope_note": scope_note,
        "reported_method": "utf8-bytes-div-4",
        "independent_method": "tiktoken:cl100k_base",
        "reported_count": result.reported_count,
        "independent_count": result.independent_count,
        "absolute_divergence": result.absolute_divergence,
        "relative_divergence": result.relative_divergence,
    }


def _print_savings_verification(verification: dict[str, object] | None) -> None:
    if verification is None:
        return

    print()
    print("Independent token-count verification")
    print(f"  Scope: {verification['scope_note']}")
    if verification["available"] is not True:
        print(f"  Unavailable: {verification['message']}")
        return

    reported_count = _as_int(verification["reported_count"])
    independent_count = _as_int(verification["independent_count"])
    absolute_divergence = _as_int(verification["absolute_divergence"])
    relative_divergence = _as_float(verification["relative_divergence"])
    print(f"  Reported ({verification['reported_method']}): {reported_count:,}")
    print(f"  Independent ({verification['independent_method']}): {independent_count:,}")
    print(
        f"  Divergence: {absolute_divergence:,} tokens "
        f"({relative_divergence * 100:.1f}% of reported)"
    )


def cmd_savings(args: CommandArgs) -> None:
    """Show compression savings summary."""
    mode = resolve_mode(args)
    fmt = OutputFormatter("Savings", mode=mode, minimal=getattr(args, "minimal", False))
    days = getattr(args, "days", 30)
    as_json = getattr(args, "as_json", False)
    verification = _savings_verification_payload() if getattr(args, "verify", False) else None

    # Try monitor.db first (proxy's live data source)
    monitor_data = _monitor_db_savings(days=days)

    if monitor_data and monitor_data["actual_cost"] > 0:
        actual = monitor_data["actual_cost"]
        cache_hit_rate = monitor_data["cache_hit_rate"]
        compressed = monitor_data["compressed_tokens"]
        cache_read = monitor_data["cache_read"]

        total_input = monitor_data["total_input"] + monitor_data["total_output"]
        avg_rate = actual / total_input if total_input > 0 else 0
        savings_amount = (cache_read + compressed) * avg_rate
        estimated_without = actual + savings_amount
        savings_pct = (savings_amount / estimated_without * 100) if estimated_without > 0 else 0

        if mode == OutputMode.RAW or as_json:
            payload: dict[str, object] = {
                "section": "savings",
                "days": days,
                "actual_cost": actual,
                "savings_amount": savings_amount,
                "savings_pct": savings_pct,
                "cache_hit_rate": cache_hit_rate,
                "estimated_without_compression": estimated_without,
            }
            if verification is not None:
                payload["verification"] = verification
            print(json.dumps(payload))
            return

        if fmt.minimal:
            print(fmt.minimal_line([f"{savings_pct:.1f}%", f"${savings_amount:.2f}", f"{days}d"]))
            _print_savings_verification(verification)
            return

        print(fmt.header())
        print()
        print(
            fmt.kv(
                [
                    ("Actual Cost", f"${actual:.2f}"),
                    ("Est. Baseline", f"${estimated_without:.2f}"),
                    ("Est. Savings", f"${savings_amount:.2f} ({savings_pct:.1f}%)"),
                    ("Cache Hit Rate", f"{cache_hit_rate * 100:.1f}%"),
                    ("Compressed Tokens", f"{compressed:,}"),
                ]
            )
        )
        _print_savings_verification(verification)
        return

    # Fallback to telemetry.db
    from .telemetry.query_dsl import get_savings_report

    report = get_savings_report(days=days)

    if mode == OutputMode.RAW or as_json:
        payload = {"section": "savings", "days": days, **report.__dict__}
        if verification is not None:
            payload["verification"] = verification
        print(fmt.raw(payload))
        return

    # Check for empty database
    if report.total_cost == 0.0 and report.savings_amount == 0.0:
        print("No savings data yet. Run your first request through the proxy to start tracking.")
        _print_savings_verification(verification)
        return

    if fmt.minimal:
        print(
            fmt.minimal_line(
                [f"{report.savings_pct:.1f}%", f"${report.savings_amount:.2f}", f"{days}d"]
            )
        )
        _print_savings_verification(verification)
        return

    print(fmt.header())
    print()
    print(
        fmt.kv(
            [
                ("Savings", f"${report.savings_amount:.2f}"),
                ("Savings %", f"{report.savings_pct:.1f}%"),
                ("Actual Cost", f"${report.total_cost:.2f}"),
                ("Baseline", f"${report.estimated_without_compression:.2f}"),
                ("Cache Hit", f"{report.cache_hit_rate * 100:.1f}%"),
            ]
        )
    )

    # Attribution v2 breakdown (additive; only shown when TOKENPAK_ATTRIBUTION_V2 is set)
    try:
        from .services.optimization.attribution_stage import is_attribution_v2_enabled

        if is_attribution_v2_enabled():
            from .core.paths import get_db_path as _get_db_path
            from .telemetry.savings import format_savings_by_source
            from .telemetry.storage import TelemetryDB

            _db_path = _get_db_path("telemetry.db")
            if _db_path.exists():
                _db = TelemetryDB(_db_path)
                _rows = _db.query_savings_by_source(days=days)
                if _rows:
                    from .telemetry.savings import SourceSummary

                    by_source = {
                        r["source"]: SourceSummary(
                            source=r["source"],
                            saved_tokens=r["saved_tokens"],
                            estimated_cost_saved=r["estimated_cost_saved"],
                            cost_available=bool(r["cost_available"]),
                            request_count=r["request_count"],
                            credited_to_tokenpak=bool(r["credited_to_tokenpak"]),
                        )
                        for r in _rows
                    }
                    print()
                    print(format_savings_by_source(by_source, days=days))
    except Exception:
        pass

    _print_savings_verification(verification)


def cmd_compare(args: CommandArgs) -> None:
    """Show before/after cost comparison for last N requests."""

    from .telemetry.pricing_rates import calculate_request_cost_baseline
    from .telemetry.query_dsl import get_recent_events

    limit = getattr(args, "last", 1)
    recent = get_recent_events(limit=limit)

    if not recent:
        print("No recent requests found.")
        return

    # This used to invent its own inputs: cache_read was assumed to be 30% of
    # input ("adjust per actual data"), the request duration defaulted to a
    # hardcoded 5.1s, and the baseline was priced at flat Opus rates for every
    # model. It then printed the difference as "💰 Saved: $X (Y% cheaper)".
    # Every number on the line was constructed, and none was labelled as such.
    #
    # What the store actually records per request: model, billed input, billed
    # output, and the actual cost. There is no per-request cache-read split, so
    # that comparison is not available and is reported as unavailable rather
    # than assumed.
    print("Per-request cost — measured against list-price baseline")
    print("─" * 56)
    print()

    for idx, evt in enumerate(recent[:limit], 1):
        model = str(evt.get("model") or "unknown")
        input_tokens = _as_int(evt.get("input_tokens"))
        output_tokens = _as_int(evt.get("output_tokens"))
        actual_cost = evt.get("cost")

        print(f"Request #{idx}: {model}")
        print(f"  Tokens            {input_tokens:,} in / {output_tokens:,} out  (billed)")

        if input_tokens <= 0 and output_tokens <= 0:
            print("  Cost              unavailable — no billed token counts recorded")
            print()
            continue

        baseline = calculate_request_cost_baseline(model, input_tokens, output_tokens)
        print(
            f"  List-price basis  ${baseline:.4f}  (computed from billed tokens × published rates)"
        )

        if isinstance(actual_cost, (int, float)) and actual_cost >= 0:
            print(f"  Charged           ${float(actual_cost):.4f}  (measured)")
            delta = baseline - float(actual_cost)
            if delta > 0 and baseline > 0:
                print(
                    f"  Difference        ${delta:.4f}  ({delta / baseline * 100:.1f}% below basis)"
                )
            elif delta < 0:
                # Never render a negative saving as a saving.
                print(f"  Difference        ${abs(delta):.4f} above basis")
            else:
                print("  Difference        none")
        else:
            print("  Charged           unavailable — no cost recorded for this request")
            print("  Difference        unavailable")

        print("  Cache split       unavailable — not recorded per request")
        print()


def cmd_leaderboard(args: CommandArgs) -> None:
    """Show per-model efficiency ranking."""
    from .telemetry.query_dsl import get_model_usage, get_savings_report

    days = getattr(args, "days", 1)
    usage = get_model_usage(days=days)
    savings = get_savings_report(days=days)

    if not usage:
        print("No model usage data available.")
        print("Run requests through the proxy to gather metrics.")
        return

    # Every column here used to be a constant.
    #
    #   cost         = Opus list price applied to every model, whatever it was
    #   saved        = cost * 0.35   ("assume 30% cache + 5% compression")
    #   cache_pct    = 96 / 94 / 98, selected by substring-matching the model name
    #   compress_pct = 5.1 / 8.2 / 3.2, likewise
    #
    # so "🏆 Most Efficient" ranked models by which literal the name matched —
    # anything that was not opus or sonnet won at 98%, always — and the table
    # presented all of it as per-model measurement.
    #
    # Per-model compression and savings ARE recorded, in tp_costs; this now
    # reads them. Per-model cache-hit rate is not recorded anywhere, so that
    # column is gone rather than invented.
    from .telemetry.pricing_rates import calculate_request_cost_baseline
    from .telemetry.query_dsl import get_model_compression_breakdown

    measured = {b.model: b for b in get_model_compression_breakdown(days=days)}

    model_stats: list[dict[str, object]] = []
    for u in usage:
        model = u.model or "unknown"
        cost = calculate_request_cost_baseline(model, u.total_input_tokens, u.total_output_tokens)
        obs = measured.get(model)
        model_stats.append(
            {
                "model": model,
                "requests": u.request_count,
                "cost": cost,
                # None means "not measured", and prints as such. It is never 0.
                "saved": (obs.savings_amount if obs is not None else None),
                "tokens_saved": (obs.tokens_saved if obs is not None else None),
            }
        )

    model_stats.sort(key=lambda x: cast(float, x["cost"]), reverse=True)

    print(f"TokenPak model usage — last {days} day(s)")
    print("─" * 62)
    print("Cost is billed tokens priced at published rates. Savings are")
    print("measured from recorded baselines; blank means not measured.")
    print()

    print(f"{'Model':<26} {'Requests':>9} {'Cost':>11} {'Saved':>11}")
    print("-" * 62)
    for stat in model_stats:
        saved = stat["saved"]
        saved_col = f"${saved:>10.4f}" if isinstance(saved, (int, float)) else f"{'—':>11}"
        print(
            f"{cast(str, stat['model']):<26} {cast(int, stat['requests']):>9} "
            f"${cast(float, stat['cost']):>10.4f} {saved_col}"
        )

    # Aggregate savings come from the report that carries the measured-data
    # contract, so "we could not read the store", "nothing measured yet" and
    # "measured zero" stay distinguishable.
    print()
    if not savings.available:
        print("Total savings:  unavailable — telemetry store could not be read")
    elif savings.observations == 0:
        print("Total savings:  not yet measured — no compression observations recorded")
    else:
        print(
            f"Total savings:  ${savings.savings_amount:.4f} "
            f"({savings.savings_pct:.1f}%) over {savings.observations:,} observation(s)"
        )


def cmd_report(args: CommandArgs) -> None:
    """Generate and display daily savings report."""
    from .cli.daily_report import generate_report

    format_type: Literal["terminal", "markdown", "json"] = "terminal"
    if getattr(args, "markdown", False):
        format_type = "markdown"
    elif getattr(args, "json", False):
        format_type = "json"

    report = generate_report(format=format_type)

    if format_type == "json":
        import json as _json

        print(_json.dumps(report, indent=2))
    else:
        print(report)


def cmd_check_alerts(args: CommandArgs) -> None:
    """Evaluate alert rules and return exit code 1 if any fired."""
    from .alerts import check_alerts

    fired = check_alerts()

    if not fired:
        print("✅ All alert rules clear")
        sys.exit(0)

    # Print fired alerts
    for rule, value in fired:
        msg = rule.message
        if value is not None and "{value" in msg:
            msg = msg.format(value=value)
        print(f"⚠️ {msg}")

    print(f"\n{len(fired)} alert(s) fired.")
    sys.exit(1)


def _build_status_parser(sub: Subparsers) -> None:
    p_status = sub.add_parser("status", help="Show savings report (default) or full system status")
    p_status.add_argument("--limit", type=int, default=20, help="Max retry events to show")
    p_status.add_argument("--full", action="store_true", help="Expanded view with all details")
    p_status.add_argument(
        "--by-source",
        dest="by_source",
        action="store_true",
        help="Breakdown by request source (Claude Code, Codex, API, etc.)",
    )
    p_status.add_argument(
        "--by-provider",
        dest="by_provider",
        action="store_true",
        help="Breakdown by provider (Anthropic, OpenAI, Google, etc.)",
    )
    p_status.add_argument(
        "--tip-cache",
        dest="tip_cache",
        action="store_true",
        help="Show compact TIP cache attribution only",
    )
    p_status.add_argument("--minimal", action="store_true", help="One-line savings summary")
    p_status.add_argument("--json", dest="as_json", action="store_true", help="Full JSON data dump")
    p_status.add_argument("--no-meme", dest="no_meme", action="store_true", help="Suppress tagline")
    p_status.add_argument(
        "--days", type=int, default=0, help="Filter to last N days (combinable with --hours)"
    )
    p_status.add_argument(
        "--hours", type=int, default=0, help="Filter to last N hours (combinable with --days)"
    )
    p_status.add_argument(
        "--fleet", action="store_true", help="Fleet rollup view — reads rollup_daily"
    )
    p_status.add_argument(
        "--since", default=None, help="With --fleet: window in days, e.g. '7d' (default: 7d)"
    )
    p_status.set_defaults(func=cmd_status)


def _build_usage_parser(sub: Subparsers) -> None:
    p_usage = sub.add_parser("usage", help="Show model usage summary")
    p_usage.add_argument("--days", type=int, default=30, help="Rolling window in days")
    p_usage.set_defaults(func=cmd_usage)


def _build_savings_parser(sub: Subparsers) -> None:
    p_savings = sub.add_parser("savings", help="Show savings summary")
    p_savings.add_argument("--days", type=int, default=30, help="Rolling window in days")
    p_savings.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON output",
    )
    p_savings.add_argument(
        "--verify",
        action="store_true",
        help="Cross-check token counting on the packaged fixture corpus",
    )
    p_savings.set_defaults(func=cmd_savings)


def _build_recommendations_parser(sub: Subparsers) -> None:
    """Build `tokenpak recommendations` parser via the modular CLI command."""
    from tokenpak.cli.commands.recommendations import build_parser

    build_parser(sub)


def _build_upgrade_parser(sub: Subparsers) -> None:
    """Build `tokenpak upgrade` parser via the modular CLI command."""
    from tokenpak.cli.commands.upgrade import build_parser

    build_parser(sub)


def _build_compare_parser(sub: Subparsers) -> None:
    """Build compare command parser."""
    p_compare = sub.add_parser("compare", help="Show before/after cost on last request")
    p_compare.add_argument("--last", type=int, default=1, help="Show last N requests (default: 1)")
    p_compare.set_defaults(func=cmd_compare)


def _build_leaderboard_parser(sub: Subparsers) -> None:
    """Build leaderboard command parser."""
    p_leaderboard = sub.add_parser("leaderboard", help="Show per-model efficiency ranking")
    p_leaderboard.add_argument(
        "--days", type=int, default=1, help="Rolling window in days (default: today)"
    )
    p_leaderboard.set_defaults(func=cmd_leaderboard)


def _build_report_parser(sub: Subparsers) -> None:
    """Build report command parser."""
    p_report = sub.add_parser("report", help="Generate daily savings report")
    p_report.add_argument(
        "--markdown", action="store_true", help="Output markdown format (for messaging)"
    )
    p_report.add_argument("--json", action="store_true", help="Output JSON format")
    p_report.set_defaults(func=cmd_report)


def _build_alerts_parser(sub: Subparsers) -> None:
    """Build check-alerts command parser."""
    p_alerts = sub.add_parser("check-alerts", help="Evaluate alert rules and check health")
    p_alerts.set_defaults(func=cmd_check_alerts)


def _cmd_alerts_dispatch(args: CommandArgs) -> None:
    """Dispatch to alerts sub-command."""
    if not hasattr(args, "alerts_cmd") or args.alerts_cmd is None:
        print("Usage: tokenpak alerts <subcommand>")
        print("  test    Send a test alert to a delivery channel")
        import sys

        sys.exit(0)
    args.func(args)


def _cmd_alerts_test(args: CommandArgs) -> None:
    from tokenpak.cli.commands.alerts import cmd_alerts_test

    cmd_alerts_test(args)


def _build_alerts_cmd_parser(sub: Subparsers) -> None:
    """Build alerts command with test subcommand."""
    p = sub.add_parser("alerts", help="Test and manage alert delivery channels")
    asub = p.add_subparsers(dest="alerts_cmd")
    p.set_defaults(func=_cmd_alerts_dispatch)

    p_test = asub.add_parser("test", help="Send a test alert to a delivery channel")
    p_test.add_argument(
        "--channel",
        required=True,
        choices=["webhook", "slack"],
        help="Channel type to test",
    )
    p_test.add_argument("--url", default=None, help="Webhook URL (for --channel webhook)")
    p_test.add_argument(
        "--webhook", default=None, help="Slack incoming-webhook URL (for --channel slack)"
    )
    p_test.set_defaults(func=_cmd_alerts_test)


def _build_debug_parser(sub: Subparsers) -> None:
    """Build debug mode subcommand parser."""
    p_debug = sub.add_parser("debug", help="Toggle verbose debug logging or manage captured traces")
    dsub = p_debug.add_subparsers(dest="debug_cmd", required=False)

    dsub.add_parser("on", help="Enable debug mode").set_defaults(func=cmd_debug_on)
    dsub.add_parser("off", help="Disable debug mode").set_defaults(func=cmd_debug_off)
    dsub.add_parser("status", help="Show debug mode state").set_defaults(func=cmd_debug_status)

    p_list = dsub.add_parser("list", help="List captured debug traces")
    p_list.add_argument("--json", action="store_true", dest="json_out", help="Output as JSON")
    p_list.set_defaults(func=cmd_debug_list)

    p_export = dsub.add_parser("export", help="Decrypt and print a captured trace")
    p_export.add_argument("trace_id", help="Trace ID to export")
    p_export.add_argument("--json", action="store_true", dest="json_out", help="Output as JSON")
    p_export.set_defaults(func=cmd_debug_export)

    p_receipt = dsub.add_parser(
        "receipt", help="Render the Receipt v1 proof object for a recorded request"
    )
    p_receipt.add_argument(
        "request_id",
        nargs="?",
        help="Request ID to render a receipt for (omit to print the support-bundle pointer)",
    )
    p_receipt.add_argument(
        "--raw",
        action="store_true",
        help="Show the receipt without redaction (default: redaction-safe)",
    )
    p_receipt.set_defaults(func=cmd_debug_receipt)

    p_debug.set_defaults(func=lambda a: p_debug.print_help())


def cmd_debug_on(args: CommandArgs) -> None:
    """Enable debug mode."""
    from tokenpak.core.config import set_debug_enabled

    set_debug_enabled(True)
    print("✅ Debug mode enabled")
    print("   Debug logs will appear on stderr during proxy requests.")
    print("   Disable with: tokenpak debug off")


def cmd_debug_off(args: CommandArgs) -> None:
    """Disable debug mode."""
    from tokenpak.core.config import set_debug_enabled

    set_debug_enabled(False)
    print("✅ Debug mode disabled")


def cmd_debug_status(args: CommandArgs) -> None:
    """Show debug mode state."""
    import os

    from tokenpak.core.config import CONFIG_PATH, get_debug_enabled

    enabled = get_debug_enabled()
    env_override = os.environ.get("TOKENPAK_DEBUG")

    status = "🟢 ON" if enabled else "⚪ OFF"
    print(f"Debug mode: {status}")

    if env_override is not None:
        print(f"  Source: TOKENPAK_DEBUG env var = {env_override}")
    else:
        print(f"  Source: {CONFIG_PATH}")


def cmd_debug_list(args: CommandArgs) -> None:
    """List captured debug traces."""
    import json as _json

    from .debug.capture import list_captures

    captures = list_captures()
    json_out = getattr(args, "json_out", False)

    if json_out:
        print(_json.dumps(captures, indent=2))
        return

    if not captures:
        print("No debug captures found.")
        print("  Set TOKENPAK_DEBUG_CAPTURE=encrypted or TOKENPAK_DEBUG_CAPTURE=hash_only")
        return

    print(f"{'TRACE ID':<40} {'MODE':<12} {'SIZE':>10}  TIMESTAMP")
    print("-" * 80)
    for c in captures:
        ts = c.get("timestamp", "")[:19] if c.get("timestamp") else ""
        print(f"{c['trace_id']:<40} {c['mode']:<12} {c['size_bytes']:>10}  {ts}")
    print(f"\n{len(captures)} capture(s)")


def cmd_debug_export(args: CommandArgs) -> None:
    """Decrypt and print a captured trace."""
    import json as _json

    from .debug.capture import export_capture

    json_out = getattr(args, "json_out", False)
    try:
        record = export_capture(args.trace_id)
    except FileNotFoundError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"❌ Decryption failed: {exc}", file=sys.stderr)
        sys.exit(1)

    if json_out:
        print(_json.dumps(record, indent=2))
        return

    print(f"Trace ID:   {record.get('trace_id', args.trace_id)}")
    print(f"Timestamp:  {record.get('timestamp', '')}")
    print(f"Mode:       {record.get('mode', '')}")
    for k, v in record.items():
        if k in ("trace_id", "timestamp", "mode"):
            continue
        if isinstance(v, dict):
            print(f"\n[{k}]")
            print(_json.dumps(v, indent=2))
        else:
            print(f"{k}: {v}")


def cmd_debug_receipt(args: CommandArgs) -> None:
    """Render the Receipt v1 proof object for a recorded request.

    Thin shim over the already-tested render path
    (`tokenpak.cli.commands.debug._render_request_receipt`). Prints the
    redaction-safe receipt JSON for a recorded request, or a support-bundle
    pointer when the id is missing / no record exists. ``--raw`` shows the
    receipt without redaction.
    """
    from tokenpak.cli.commands.debug import _render_request_receipt

    request_id = getattr(args, "request_id", None)
    redact = not getattr(args, "raw", False)
    print(_render_request_receipt(request_id, redact=redact))


def _build_learn_parser(sub: Subparsers) -> None:
    """Build `tokenpak learn` subcommand parser."""
    p_learn = sub.add_parser("learn", help="Show or reset learned patterns from telemetry")
    lsub = p_learn.add_subparsers(dest="learn_cmd", required=False)
    lsub.add_parser("status", help="Show learned patterns summary").set_defaults(
        func=cmd_learn_status
    )
    lsub.add_parser("reset", help="Clear all learned data").set_defaults(func=cmd_learn_reset)
    p_learn.set_defaults(func=lambda a: p_learn.print_help())


def cmd_learn_status(args: CommandArgs) -> None:
    """Show learned patterns from routing, compression, and context data."""
    from tokenpak.orchestration.learning import cmd_learn_status as _learn_status
    from tokenpak.orchestration.learning import learn

    learn()
    _learn_status()


def cmd_learn_reset(args: CommandArgs) -> None:
    """Clear all learned data."""
    from tokenpak.orchestration.learning import reset

    reset()
    print("✓ Learning store cleared.")


def _build_user_template_parser(sub: Subparsers) -> None:
    """Build `tokenpak template` subcommand parser for local user templates."""
    from .cli.user_templates import (
        cmd_template_add,
        cmd_template_list,
        cmd_template_remove,
        cmd_template_show,
        cmd_template_use,
    )

    p_tmpl = sub.add_parser("template", help="Manage local user prompt templates")
    tsub = p_tmpl.add_subparsers(dest="template_cmd", required=False)

    # list
    tsub.add_parser("list", help="List all saved templates").set_defaults(func=cmd_template_list)

    # add
    p_add = tsub.add_parser("add", help="Add or update a template")
    p_add.add_argument("name", help="Template name")
    p_add.add_argument(
        "--content", default=None, help="Template content (use {{var}} for variables)"
    )
    p_add.set_defaults(func=cmd_template_add)

    # show
    p_show = tsub.add_parser("show", help="Display a template")
    p_show.add_argument("name", help="Template name")
    p_show.set_defaults(func=cmd_template_show)

    # remove
    p_rm = tsub.add_parser("remove", help="Delete a template")
    p_rm.add_argument("name", help="Template name")
    p_rm.set_defaults(func=cmd_template_remove)

    # use
    p_use = tsub.add_parser("use", help="Expand a template with variables")
    p_use.add_argument("name", help="Template name")
    p_use.add_argument(
        "--var",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Variable substitution (repeatable)",
    )
    p_use.set_defaults(func=cmd_template_use)
    p_tmpl.set_defaults(
        func=_bare_help(
            "template",
            "Manage local user prompt templates",
            ["list", "add", "show", "remove", "use"],
        )
    )


# ── Version Control Commands ──────────────────────────────────────────────────

# The proxy ships from the same wheel as the CLI, so the "expected" proxy version
# always equals the installed package version. Derive it from ``tokenpak.__version__``
# (instead of a hardcoded literal) so ``tokenpak version`` stays honest across releases.
# Guard the import: when this module is imported *during* ``tokenpak`` package
# initialization, ``__version__`` may not be bound yet, so fall back to the installed
# package metadata to avoid a circular-import failure.
try:
    from tokenpak import __version__ as PROXY_VERSION
except ImportError:  # pragma: no cover - circular import during package init
    try:
        from importlib.metadata import version as _pkg_version

        PROXY_VERSION = _pkg_version("tokenpak")
    except Exception:
        PROXY_VERSION = "unknown"
_LOCK_FILE = Path.home() / "vault" / "System" / "tokenpak.lock.json"
_TOKENPAK_CFG = Path.home() / ".tokenpak" / "config.json"
_PROXY_URL = "http://localhost:8766"


def _compute_config_hash(cfg: JsonObject) -> str:
    import hashlib as _hl

    normalized = {k: v for k, v in sorted(cfg.items()) if k != "meta"}
    raw = json.dumps(normalized, sort_keys=True).encode()
    return "sha256:" + _hl.sha256(raw).hexdigest()[:12]


def _get_proxy_version() -> ProxyVersionPayload:
    """Query the proxy ``/health`` endpoint (which carries ``version``). Returns dict or raises."""
    import urllib.request as _ur

    try:
        with _ur.urlopen(f"{_PROXY_URL}/health", timeout=3) as resp:
            return cast(ProxyVersionPayload, _json_object(json.loads(resp.read())))
    except Exception as e:
        return {"error": str(e)}


def _load_lock() -> JsonObject:
    if _LOCK_FILE.exists():
        try:
            return _json_object(json.loads(_LOCK_FILE.read_text()))
        except Exception:
            return {}
    return {}


def _save_lock(lock: JsonObject) -> None:
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LOCK_FILE.write_text(json.dumps(lock, indent=2) + "\n")


def _maybe_write_env_stub(*, force: bool) -> None:
    """Write a placeholders-only .env.example under the resolved TokenPak home.

    Scaffold-only: never writes a real .env and never writes credential values.
    """
    from tokenpak import _paths
    from tokenpak.cli.commands.config_env import write_env_stub

    created, target = write_env_stub(_paths.home(), force=force)
    if created:
        print(f"Created env template: {target} (placeholders only)")
    else:
        print(f"Env template already exists: {target} (use --force to overwrite)")


def cmd_config(args: CommandArgs) -> None:
    """Config management: show, init, edit."""
    from tokenpak.core.config_loader import config_read_path, generate_default_yaml, get_all

    subcmd = getattr(args, "config_cmd", "show")
    config_path = config_read_path()

    if subcmd == "show":
        cfg = get_all()
        if args.json:
            print(json.dumps(cfg, indent=2))
        else:
            print(f"Config: {config_path}")
            print(f"Exists: {'yes' if config_path.exists() else 'no'}")
            print()
            # Group by section
            sections: dict[str, list[tuple[str, object]]] = {}
            for k, v in sorted(cfg.items()):
                parts = k.split(".", 1)
                section = parts[0] if len(parts) > 1 else "core"
                if section not in sections:
                    sections[section] = []
                display_key = parts[1] if len(parts) > 1 else k
                sections[section].append((display_key, v))

            for section, items in sorted(sections.items()):
                print(f"  [{section}]")
                for key, val in items:
                    print(f"    {key:<30} = {val}")
                print()

    elif subcmd == "init":
        if config_path.exists() and not getattr(args, "force", False):
            print(f"Config already exists: {config_path}")
            print("Use --force to overwrite.")
            if getattr(args, "with_env_stub", False):
                _maybe_write_env_stub(force=getattr(args, "force", False))
            return
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(generate_default_yaml())
        print(f"Created: {config_path}")
        if getattr(args, "with_env_stub", False):
            _maybe_write_env_stub(force=getattr(args, "force", False))

    elif subcmd == "path":
        print(str(config_path))


def cmd_version(args: CommandArgs) -> None:
    """Show current versions of proxy, config, and CLI."""
    from tokenpak import __version__ as cli_ver

    # CLI version
    print(f"TokenPak CLI     : {cli_ver}")
    print(f"Proxy (expected) : {PROXY_VERSION}")

    # Proxy version (live)
    proxy_info = _get_proxy_version()
    if "error" in proxy_info:
        print(f"Proxy (running)  : ✗ not reachable ({proxy_info['error']})")
    else:
        uptime = proxy_info.get("uptime", 0)
        h, m = divmod(uptime // 60, 60)
        print(
            f"Proxy (running)  : {proxy_info.get('version', '?')}  uptime={h}h{m:02d}m  python={proxy_info.get('pythonVersion', '?')}"
        )
        print(f"Proxy config hash: {proxy_info.get('configHash', '?')}")

    # config.json meta
    try:
        cfg = _json_object(json.loads(_TOKENPAK_CFG.read_text()))
        meta = _json_object(cfg.get("meta"))
        print(f"Config version   : {meta.get('configVersion', 'unknown')}")
        print(f"Config hash      : {meta.get('configHash', 'unknown')}")
        print(f"Last updated     : {meta.get('lastUpdated', 'unknown')}")
    except Exception as e:
        print(f"Config           : ✗ could not read ({e})")

    # Lock file drift check
    lock = _load_lock()
    if lock:
        print(f"\nLock file        : {_LOCK_FILE}")
        print(f"  Locked version : {lock.get('proxyVersion', '?')}")
        print(f"  Locked hash    : {lock.get('configHash', '?')}")
        print(f"  Locked by      : {lock.get('lockedBy', '?')} at {lock.get('lockedAt', '?')}")
        # Drift check
        try:
            cfg = _json_object(json.loads(_TOKENPAK_CFG.read_text()))
            current_hash = _compute_config_hash(cfg)
            if lock.get("configHash") and lock["configHash"] != current_hash:
                print("\n  ⚠️  Config drift detected!")
                print(f"  Lock hash    : {lock['configHash']}")
                print(f"  Current hash : {current_hash}")
                print("  Run `tokenpak config sync` to reconcile.")
            else:
                print("  ✓ Config matches lock file")
        except Exception:
            pass
    else:
        print(f"\n  Lock file not found at {_LOCK_FILE}")


def _tokenpak_is_user_install() -> bool:
    """True when the running tokenpak package lives in the per-user site (``~/.local``)."""
    try:
        import site

        import tokenpak as _tp

        base = (site.getuserbase() or "").replace(os.sep, "/")
        loc = (os.path.dirname(os.path.abspath(_tp.__file__)) or "").replace(os.sep, "/")
        return bool(base) and loc.startswith(base)
    except Exception:
        return False


# ── Update check (cached PyPI version; consumed by `doctor` + update nudge) ────

_UPDATE_NUDGE_OPTOUT_ENV = "TOKENPAK_NO_UPDATE_CHECK"
_UPDATE_NUDGE_TTL_SECONDS = 24 * 60 * 60
_UPDATE_NUDGE_TIMEOUT_SECONDS = 1.5
_KEEP_SKIPPED_VERSION = object()
_KEEP_AUTOMATIC_CHECKS = object()
_UPDATE_NUDGE_EXCLUDED_COMMANDS = frozenset(
    {
        "update",
        "upgrade",
        "menu",
        "requests",
        "start",
        "stop",
        "restart",
        "serve",
        "monitor",
        "dashboard",
        "claude",
        "codex",
    }
)
_MACHINE_OUTPUT_ATTRS = (
    "as_json",
    "export_csv",
    "json",
    "json_export",
    "json_out",
    "json_output",
    "output_json",
    "markdown",
    "minimal",
    "print_url",
    "quiet",
    "raw",
)
_MACHINE_OUTPUT_FORMATS = frozenset({"csv", "json", "jsonl", "markdown", "raw"})


def _machine_output_requested(args: CommandArgs) -> bool:
    """Recognize argparse destinations used for machine-readable output."""
    if any(bool(getattr(args, attr, False)) for attr in _MACHINE_OUTPUT_ATTRS):
        return True
    return str(getattr(args, "format", "")).lower() in _MACHINE_OUTPUT_FORMATS


def _update_nudge_suppressed(args: CommandArgs) -> bool:
    """Suppress automatic checks for machine, launcher, and long-running flows."""
    if _machine_output_requested(args):
        return True
    if getattr(args, "watch", False):
        return True
    return getattr(args, "trigger_cmd", None) in {"watch", "daemon"}


def _update_nudge_opted_out() -> bool:
    """True when ``TOKENPAK_NO_UPDATE_CHECK`` is set to a truthy value."""
    return os.environ.get(_UPDATE_NUDGE_OPTOUT_ENV, "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _update_cache_path() -> Path:
    """Path to the cached update-check result under the resolved TokenPak home."""
    from tokenpak import _paths

    return _paths.home() / "update_check.json"


def _ensure_update_cache_parent(path: Path) -> None:
    """Create and harden the TokenPak home for private update-check state."""
    parent = path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass


def _lock_update_cache_fd(fd: int, platform_name: Optional[str] = None) -> None:
    """Acquire an exclusive one-byte lock on POSIX or Windows."""
    platform_name = os.name if platform_name is None else platform_name
    if platform_name == "nt":
        import msvcrt

        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
            os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt_api = cast(Any, msvcrt)
        msvcrt_api.locking(fd, msvcrt_api.LK_LOCK, 1)
        return

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock_update_cache_fd(fd: int, platform_name: Optional[str] = None) -> None:
    """Release a lock acquired by :func:`_lock_update_cache_fd`."""
    platform_name = os.name if platform_name is None else platform_name
    if platform_name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt_api = cast(Any, msvcrt)
        msvcrt_api.locking(fd, msvcrt_api.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _update_cache_lock() -> Iterator[Path]:
    """Serialize cache probes and read-modify-write updates across processes."""
    path = _update_cache_path()
    _ensure_update_cache_parent(path)
    lock_path = path.with_suffix(".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        _lock_update_cache_fd(fd)
        locked = True
        yield path
    finally:
        try:
            if locked:
                _unlock_update_cache_fd(fd)
        finally:
            os.close(fd)


def _read_update_cache_state(path: Optional[Path] = None) -> JsonObject:
    """Return the complete update-check cache, or an empty mapping on failure."""
    try:
        cache_path = _update_cache_path() if path is None else path
        return _json_object(json.loads(cache_path.read_text()))
    except Exception:
        return {}


def _read_update_cache() -> Tuple[float, Optional[str]]:
    """Return ``(checked_at_epoch, cached_latest_version)``.

    Reads the cached result only — never issues a network probe. Returns
    ``(0.0, None)`` on any failure (no cache yet, unreadable, malformed).
    """
    data = _read_update_cache_state()
    latest = data.get("latest")
    return _as_float(data.get("checked_at")), latest if isinstance(latest, str) else None


def _write_update_cache_unlocked(
    path: Path,
    latest: Optional[str],
    *,
    checked_at: Optional[float] = None,
    skipped_version: object = _KEEP_SKIPPED_VERSION,
    skipped_at: Optional[float] = None,
    automatic_checks: object = _KEEP_AUTOMATIC_CHECKS,
) -> None:
    """Persist cache state while the caller holds ``_update_cache_lock``."""
    previous = _read_update_cache_state(path)
    payload: JsonObject = {
        "checked_at": float(time.time() if checked_at is None else checked_at),
        "latest": latest,
    }
    if automatic_checks is _KEEP_AUTOMATIC_CHECKS:
        automatic_checks = previous.get("automatic_checks")
    if isinstance(automatic_checks, bool):
        payload["automatic_checks"] = automatic_checks
    if skipped_version is _KEEP_SKIPPED_VERSION:
        skipped_version = previous.get("skipped_version")
    if skipped_version:
        payload["skipped_version"] = str(skipped_version)
        payload["skipped_at"] = (
            float(skipped_at)
            if skipped_at is not None
            else previous.get("skipped_at", payload["checked_at"])
        )

    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def _write_update_cache(
    latest: Optional[str],
    *,
    checked_at: Optional[float] = None,
    skipped_version: object = _KEEP_SKIPPED_VERSION,
    skipped_at: Optional[float] = None,
    automatic_checks: object = _KEEP_AUTOMATIC_CHECKS,
) -> None:
    """Atomically persist update state without breaking the invoking command."""
    try:
        with _update_cache_lock() as path:
            _write_update_cache_unlocked(
                path,
                latest,
                checked_at=checked_at,
                skipped_version=skipped_version,
                skipped_at=skipped_at,
                automatic_checks=automatic_checks,
            )
    except Exception:
        return


def _automatic_update_checks_enabled() -> Optional[bool]:
    """Return saved consent: ``True``/``False``, or ``None`` when undecided."""
    value = _read_update_cache_state().get("automatic_checks")
    return value if isinstance(value, bool) else None


def _set_automatic_update_checks(enabled: bool) -> bool:
    """Persist automatic-check consent without issuing or fabricating a check."""
    try:
        with _update_cache_lock() as path:
            state = _read_update_cache_state(path)
            latest_value = state.get("latest")
            _write_update_cache_unlocked(
                path,
                latest_value if isinstance(latest_value, str) else None,
                checked_at=_as_float(state.get("checked_at")),
                automatic_checks=bool(enabled),
            )
        return True
    except Exception:
        return False


def _mark_update_skipped(latest: str) -> None:
    """Suppress the nudge for exactly ``latest`` while allowing newer nudges."""
    now = time.time()
    _write_update_cache(
        latest,
        checked_at=now,
        skipped_version=latest,
        skipped_at=now,
    )


def _fetch_latest_pypi_version(timeout: float = 5.0) -> str:
    """Delegate to the privacy-bounded core PyPI metadata primitive."""
    from tokenpak.core._update_check import fetch_latest_pypi_version

    return fetch_latest_pypi_version(timeout=timeout)


def _latest_for_update_nudge(
    now: Optional[float] = None,
) -> Tuple[Optional[str], JsonObject]:
    """Return cached/fresh PyPI state with one automatic attempt per 24 hours."""
    now = time.time() if now is None else now
    try:
        with _update_cache_lock() as path:
            state = _read_update_cache_state(path)
            # Revalidate durable consent while holding the same cross-process
            # lock used by --disable-checks.  A caller that observed stale
            # consent before acquiring this lock must not issue a request after
            # another process has completed the disable operation.
            if state.get("automatic_checks") is not True:
                return None, state
            checked_at = _as_float(state.get("checked_at"))
            latest_value = state.get("latest")
            latest = latest_value if isinstance(latest_value, str) else None
            age = now - checked_at
            if checked_at > 0 and 0 <= age < _UPDATE_NUDGE_TTL_SECONDS:
                return latest, state

            # Record before the request: failures and crashes are cached too.
            _write_update_cache_unlocked(path, None, checked_at=now)
            try:
                latest = _fetch_latest_pypi_version(timeout=_UPDATE_NUDGE_TIMEOUT_SECONDS)
            except Exception:
                return None, _read_update_cache_state(path)

            _write_update_cache_unlocked(path, latest, checked_at=now)
            return latest, _read_update_cache_state(path)
    except Exception:
        return None, _read_update_cache_state()


def _update_nudge_allowed(command: str, machine_output: bool = False) -> bool:
    """Return whether this invocation may perform an interactive update prompt."""
    if machine_output or command in _UPDATE_NUDGE_EXCLUDED_COMMANDS:
        return False
    if _update_nudge_opted_out() or _no_tui():
        return False
    if os.environ.get("TOKENPAK_NONINTERACTIVE") or os.environ.get("CI"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    try:
        return bool(sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:
        return False


def _prompt_for_update_check_consent() -> bool:
    """Request and persist first-run consent before any automatic PyPI check."""
    print("\nTokenPak can check PyPI once per day for new releases.")
    print("    The check sends no TokenPak project, prompt, completion, usage, credential,")
    print("    file, tool-inventory, vault, or proxy-log data.")
    print("    PyPI still sees ordinary HTTPS/TLS transport metadata such as your IP,")
    print("    TLS handshake metadata, and HTTP headers.")
    try:
        choice = input("    Enable daily update checks? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return False

    enabled = choice in {"y", "yes"}
    if not _set_automatic_update_checks(enabled):
        print("    Could not save the update-check preference; no check was made.")
        return False
    if enabled:
        print("    Update checks enabled. Disable anytime with `tokenpak update --disable-checks`.")
        return True

    print("    Update checks disabled. Enable anytime with `tokenpak update --enable-checks`.")
    return False


def _maybe_prompt_for_update(command: str, machine_output: bool = False) -> None:
    """Offer consent and then Update/Skip after a successful interactive command."""
    try:
        if not _update_nudge_allowed(command, machine_output):
            return
        consent = _automatic_update_checks_enabled()
        if consent is None:
            if not _prompt_for_update_check_consent():
                return
        elif not consent:
            return

        latest, state = _latest_for_update_nudge()
        if not latest:
            return

        from packaging.version import InvalidVersion, Version

        from tokenpak import __version__ as current

        try:
            if Version(latest) <= Version(current):
                return
        except InvalidVersion:
            return
        if state.get("skipped_version") == latest:
            return

        print(f"\n⚠️  TokenPak {latest} is available (you have {current}).")
        print("    Options: [U]pdate now  [S]kip this version")
        try:
            choice = input("    Choose [u/S]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if choice in {"u", "update", "y", "yes"}:
            cmd_update(
                argparse.Namespace(
                    check=False,
                    force=False,
                    core_only=False,
                    dry_run=False,
                )
            )
        elif choice in {"", "s", "skip", "n", "no"}:
            _mark_update_skipped(latest)
            print(f"    Skipped {latest}; you'll be notified when a newer version is available.")
        else:
            print("    No update applied. Run `tokenpak update` whenever you're ready.")
    except Exception:
        return


def _pip_upgrade_tokenpak(verbose: bool = True) -> Tuple[bool, str, str]:
    """Upgrade the running ``tokenpak`` package, tolerant of PEP 668.

    Installs into whichever interpreter is currently executing (``sys.executable``).
    On an externally-managed interpreter (e.g. a distro system Python — PEP 668) a
    plain ``pip install`` is refused; we retry into the per-user site with
    ``--break-system-packages``, which writes only to the user site (``~/.local``)
    and never touches system/distro-managed packages. pipx-managed installs are
    detected and upgraded through the external ``pipx`` executable instead of
    running pip inside the pipx venv.

    Returns ``(ok, method, detail)`` where ``method`` is ``pip`` / ``pip-bsp`` /
    ``pipx`` and ``detail`` carries trimmed stderr on failure.
    """
    import shutil as _shutil
    import subprocess as _sp

    # pipx-managed: running pip inside the pipx venv is the wrong tool.
    if "/pipx/venvs/" in (sys.prefix or "").replace(os.sep, "/"):
        pipx = _shutil.which("pipx")
        if not pipx:
            return False, "pipx", "pipx executable not found on PATH"
        result = _sp.run(
            [pipx, "upgrade", "tokenpak"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return True, "pipx", ""
        return False, "pipx", (result.stderr or result.stdout or "")[:400]

    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    # Match where the package currently lives so we upgrade it in place.
    scope = [] if in_venv else (["--user"] if _tokenpak_is_user_install() else [])

    def _run(extra: Sequence[str]) -> _sp.CompletedProcess[str]:
        return _sp.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", *extra, "tokenpak"],
            capture_output=True,
            text=True,
        )

    result = _run(scope)
    if result.returncode == 0:
        return True, "pip", ""

    blob = ((result.stderr or "") + (result.stdout or "")).lower()
    if "externally-managed-environment" in blob or "externally managed" in blob:
        if verbose:
            print(
                "  ⚠ Externally-managed environment (PEP 668); retrying into the "
                "user site with --break-system-packages (writes only to ~/.local)…"
            )
        retry_scope = scope if in_venv or "--user" in scope else ["--user"]
        result = _run(retry_scope + ["--break-system-packages"])
        if result.returncode == 0:
            return True, "pip-bsp", ""

    return False, "pip", (result.stderr or result.stdout or "")[:400]


def cmd_update(args: CommandArgs) -> int | None:
    """Update TokenPak proxy and CLI to latest."""
    import subprocess as _sp

    check_only = getattr(args, "check", False)
    enable_checks = getattr(args, "enable_checks", False)
    disable_checks = getattr(args, "disable_checks", False)
    check_status = getattr(args, "check_status", False)
    force = getattr(args, "force", False)
    core_only = getattr(args, "core_only", False)
    dry_run = getattr(args, "dry_run", False)

    if check_status:
        consent = _automatic_update_checks_enabled()
        label = "enabled" if consent is True else "disabled"
        if consent is None:
            label += " (no consent decision saved)"
        print(f"Automatic update checks: {label}")
        checked_at, latest = _read_update_cache()
        if checked_at > 0:
            import datetime as _dt

            checked = _dt.datetime.fromtimestamp(checked_at, tz=_dt.timezone.utc).isoformat(
                timespec="seconds"
            )
            print(f"Last automatic attempt: {checked}")
        if latest:
            print(f"Latest cached version: {latest}")
        return 0

    if enable_checks or disable_checks:
        enabled = bool(enable_checks)
        if not _set_automatic_update_checks(enabled):
            print("✗ Could not save the update-check preference.", file=sys.stderr)
            return 1
        if enabled:
            print("✓ Automatic update checks enabled (at most once every 24 hours).")
            print("  The next eligible interactive command may check PyPI.")
        else:
            print("✓ Automatic update checks disabled. No automatic PyPI requests will be made.")
        return 0

    if dry_run:
        print("🔍 Dry run — showing what would change (no changes applied)\n")

    # Check latest version from PyPI
    print("Checking for updates...")
    try:
        latest = _fetch_latest_pypi_version(timeout=5)
    except Exception as e:
        print(f"  ✗ Could not reach PyPI: {e}")
        latest = None

    from packaging.version import Version as _PV

    from tokenpak import __version__ as current_ver

    print(f"  Current : {current_ver}")
    if latest:
        print(f"  Latest  : {latest}")
        if _PV(current_ver) == _PV(latest):
            print("  ✓ Already up to date!")
            if not force:
                return None
        elif _PV(current_ver) > _PV(latest):
            print(f"  → Local version is newer than PyPI ({current_ver} > {latest})")
            if not force:
                return None
        else:
            print(f"  → Upgrade available: {current_ver} → {latest}")

    if check_only:
        return None

    if dry_run:
        print("\nWould run: pip install --upgrade tokenpak")
        print(
            "  (retries into the user site with --break-system-packages on "
            "externally-managed / PEP 668 environments)"
        )
        print("Would restart proxy if running.")
        return None

    # Check if proxy is running first
    proxy_info = _get_proxy_version()
    proxy_running = "error" not in proxy_info

    print("\nUpdating TokenPak...")
    ok, method, detail = _pip_upgrade_tokenpak()
    if ok:
        if method == "pip-bsp":
            print("  ✓ tokenpak updated (user site, --break-system-packages)")
        elif method == "pipx":
            print("  ✓ tokenpak updated with pipx")
        else:
            print("  ✓ tokenpak updated")
    elif method == "pipx":
        print(f"  ✗ pipx upgrade failed: {detail or 'unknown error'}")
        print("    Retry with: pipx upgrade tokenpak")
        return 1
    else:
        print(f"  ✗ pip install failed:\n{detail}")
        print(
            "\n  Manual upgrade options:\n"
            "    • inside a virtualenv:  pip install --upgrade tokenpak\n"
            f"    • user site (PEP 668):  {sys.executable} -m pip install "
            "--user --upgrade --break-system-packages tokenpak\n"
            "    • pipx install:         pipx upgrade tokenpak"
        )
        return 1

    _write_update_cache(latest or current_ver, skipped_version=None)

    # Restart proxy if it was running
    if proxy_running and not core_only:
        print("\nRestarting proxy...")
        try:
            _sp.Popen(
                [sys.executable, "-m", "tokenpak", "restart"],
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
            )
            print("  ✓ Proxy restart initiated")
        except Exception as e:
            print(f"  ⚠ Could not restart proxy: {e}")

    # Update lock file
    try:
        cfg = _json_object(json.loads(_TOKENPAK_CFG.read_text()))
        import datetime as _dt

        meta = _json_object(cfg.get("meta"))
        lock: JsonObject = {
            "proxyVersion": latest or current_ver,
            "configVersion": meta.get("configVersion", "unknown"),
            "configHash": _compute_config_hash(cfg),
            "lockedAt": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "lockedBy": "tokenpak-update",
        }
        _save_lock(lock)
        print(f"  ✓ Lock file updated at {_LOCK_FILE}")
    except Exception as e:
        print(f"  ⚠ Could not update lock file: {e}")

    print("\n✓ Update complete.")
    return None


def cmd_uninstall(args: CommandArgs) -> int:
    """Un-route (``--soft``) or purge + offer package removal (``--hard``)."""
    from .cli.commands.uninstall import run_uninstall

    return run_uninstall(
        soft=getattr(args, "soft", False),
        hard=getattr(args, "hard", False),
        dry_run=getattr(args, "dry_run", False),
        yes=getattr(args, "yes", False),
        keep_data=getattr(args, "keep_data", False),
        output_json=getattr(args, "json", False),
    )


def cmd_config_sync(args: CommandArgs) -> None:
    """Pull latest config from canonical source (git/vault)."""
    import subprocess as _sp

    source = getattr(args, "source", "git")
    dry_run = getattr(args, "dry_run", False)

    print(f"Syncing config from source: {source}")

    if source == "git":
        vault_dir = Path.home() / "vault"
        if not vault_dir.exists():
            print(f"  ✗ Vault not found at {vault_dir}")
            return
        # Pull latest vault
        result = _sp.run(
            ["bash", str(vault_dir / "scripts" / "vault-sync.sh")],
            capture_output=True,
            text=True,
            cwd=str(vault_dir),
        )
        if result.returncode == 0:
            print("  ✓ Vault synced")
        else:
            print(f"  ⚠ Vault sync output: {result.stdout[-200:]}")

        # Compare lock file with current config
        lock = _load_lock()
        try:
            cfg = _json_object(json.loads(_TOKENPAK_CFG.read_text()))
            current_hash = _compute_config_hash(cfg)
            lock_hash = lock.get("configHash", "")
            if lock_hash and lock_hash != current_hash:
                print("\n  Config drift detected:")
                print(f"    Lock hash    : {lock_hash}")
                print(f"    Current hash : {current_hash}")
                if dry_run:
                    print("  (dry-run: no changes applied)")
                else:
                    print("  Config is in sync after vault pull.")
            else:
                print("  ✓ Config matches lock — no drift")
        except Exception as e:
            print(f"  ⚠ Could not compare hashes: {e}")

    elif source == "url":
        url = getattr(args, "url", None)
        if not url:
            print("  ✗ --url required for source=url")
            return
        try:
            import urllib.request as _ur

            with _ur.urlopen(url, timeout=10) as resp:
                remote_cfg = _json_object(json.loads(resp.read()))
            print(f"  ✓ Fetched config from {url}")
            if dry_run:
                print("  (dry-run: not applying)")
            else:
                # Merge: remote wins on conflicts, local additions preserved
                cfg = _json_object(json.loads(_TOKENPAK_CFG.read_text()))
                merged = {**remote_cfg, **cfg}  # local wins (conservative)
                merged["meta"] = remote_cfg.get("meta", {})
                _TOKENPAK_CFG.write_text(json.dumps(merged, indent=2))
                print("  ✓ Config merged (local additions preserved)")
        except Exception as e:
            print(f"  ✗ Failed to fetch config: {e}")
    else:
        print(f"  ✗ Unknown source: {source}. Use --source=git or --source=url")


def cmd_config_validate(args: CommandArgs) -> int | None:
    """Validate config against schema.

    With --config FILE: validates a proxy config file (JSON/YAML) against JSON Schema.
    Without --config: validates the TokenPak meta config (config.json).
    """
    from tokenpak.cli.exit_codes import (
        EXIT_CORRUPT_STATE,
        EXIT_FAILURE,
        EXIT_NOT_CONFIGURED,
        EXIT_OK,
    )

    # Route to JSON schema validator when --config is provided
    config_file = getattr(args, "config_file", None)
    if config_file:
        from tokenpak.cli.commands.validate_config import run as _schema_validate

        rc = _schema_validate(config_file)
        if rc != 0:
            sys.exit(rc)
        return None

    # --- TokenPak meta config validation (legacy) ---
    required_meta_fields = ["configVersion", "tokenpakVersion", "lastUpdated", "configHash"]

    try:
        cfg = _json_object(json.loads(_TOKENPAK_CFG.read_text()))
    except Exception as exc:
        # A validator that cannot read its target has not validated anything.
        # Returning 0 here reported success to `set -e` scripts and CI.
        # The legacy meta config (config.json) is a different artifact from the
        # config `setup` writes (config.yaml), and nothing creates it on a modern
        # install. Telling the user to run `setup` was a loop: setup succeeds,
        # this still fails, forever. A correctly-configured install with no
        # legacy meta config has nothing to validate here and is not an error —
        # returning non-zero for it broke `config validate && …` wrappers.
        # A file that exists but will not parse is its own state, distinct from
        # one that is absent — the repo carries EXIT_CORRUPT_STATE and a test
        # named test_corrupt_config_is_not_missing_config for exactly this.
        # `config_read_path()` tests existence only, and its candidate list
        # includes config.json, so probing with it would hand back the very file
        # that just failed to parse and report success on it.
        if _TOKENPAK_CFG.exists():
            print(f"✗ The TokenPak meta config at {_TOKENPAK_CFG} exists but could not be read.")
            print(f"  {exc}")
            print("  This is a corrupt config, not a missing one — repair or remove the file.")
            return EXIT_CORRUPT_STATE

        _modern = None
        try:
            from tokenpak import _paths as _vp_paths

            _modern = _vp_paths.config_read_path()
        except Exception:
            _modern = None
        if _modern is not None and _modern != _TOKENPAK_CFG:
            # config_read_path() is an EXISTENCE probe. Claiming "✓ configured
            # at <file>" on the strength of it made `config validate` exit 0 on a
            # config that `start` (8) and `status` both refuse to parse — a
            # fail-open regression against merge-base, on the file every modern
            # install has. Validate means read it.
            try:
                import yaml as _v_yaml

                with open(_modern, encoding="utf-8") as _vf:
                    _v_yaml.safe_load(_vf)
            except FileNotFoundError:
                pass  # vanished between probe and read; fall through to the
                # not-configured path below rather than claim either way.
            except Exception as _v_exc:
                print(f"✗ The config at {_modern} could not be parsed: {_v_exc}")
                print("  This is a corrupt config, not a missing one — repair or remove the file.")
                return EXIT_CORRUPT_STATE
            else:
                print("✓ No legacy meta config to validate.")
                print(f"  This install is configured at {_modern}.")
                print("  To validate a proxy config file: tokenpak config validate --config <file>")
                return EXIT_OK
        print(f"✗ Could not read the TokenPak meta config at {_TOKENPAK_CFG}: {exc}")
        print("  This install has no configuration at all — run `tokenpak setup`.")
        print("  To validate a proxy config file instead: --config <file>")
        return EXIT_NOT_CONFIGURED

    errors: list[str] = []
    warnings: list[str] = []

    # Check meta fields
    meta = _json_object(cfg.get("meta"))
    for field in required_meta_fields:
        if field not in meta:
            warnings.append(f"meta.{field} missing")

    # Check configHash integrity
    if "configHash" in meta:
        computed = _compute_config_hash(cfg)
        stored = meta["configHash"]
        if stored != computed:
            warnings.append(f"configHash mismatch: stored={stored}, computed={computed}")
        else:
            print(f"  ✓ configHash valid ({stored})")

    # Check lock file consistency
    lock = _load_lock()
    if lock:
        if lock.get("configHash") and lock["configHash"] != _compute_config_hash(cfg):
            warnings.append("Config hash doesn't match lock file")
        else:
            print("  ✓ Lock file consistent")

    if errors:
        print("\n❌ Errors:")
        for e in errors:
            print(f"   {e}")
    if warnings:
        print("\n⚠️  Warnings:")
        for w in warnings:
            print(f"   {w}")
    if not errors and not warnings:
        print("✓ Config valid — all checks passed")

    # Errors are failures and must be visible to callers. Warnings are not.
    if errors:
        return EXIT_FAILURE
    return None


def cmd_config_pull(args: CommandArgs) -> None:
    """Pull config from git or URL (alias for sync with explicit source)."""
    cmd_config_sync(args)


def cmd_config_migrate(args: CommandArgs) -> None:
    """Merge legacy config.json settings into config.yaml.

    Reads ~/.tokenpak/config.json (or --config-json path), merges any
    recognised sections (logging, validation, plugins) into config.yaml
    under the canonical keys, then renames config.json to config.json.bak.
    """
    import json as _json

    try:
        import yaml as _yaml

        _has_yaml = True
    except ImportError:
        _has_yaml = False

    from tokenpak.core.config_loader import config_read_path

    config_path = config_read_path()

    # config.json is a read-compatibility format and may sit in either home;
    # resolve it rather than assuming the legacy one.
    _pm = _paths_mod()
    _explicit = getattr(args, "config_json", None)
    json_path = Path(
        _explicit
        if _explicit
        else (_pm.resolve_existing("config.json") or (_pm.write_home() / "config.json"))
    )
    dry_run = getattr(args, "dry_run", False)

    print("TokenPak Config Migration")
    print(f"  Source : {json_path}")
    print(f"  Target : {config_path}")
    print()

    if not json_path.exists():
        print(f"✗ Legacy config not found: {json_path}")
        print("  Nothing to migrate.")
        return

    # Load legacy JSON
    try:
        legacy = _json.loads(json_path.read_text())
    except Exception as e:
        print(f"✗ Could not read {json_path}: {e}")
        return

    if not legacy:
        print("✓ Legacy config.json is empty — nothing to migrate.")
        return

    # Build migration mapping: legacy JSON key → config.yaml dot-path
    MIGRATION_MAP = {
        "logging": None,  # nested dict — merged under "logging" key
        "request_validation": "validation.mode",
        "plugins": "plugins.enabled",
    }

    # Load existing config.yaml
    if config_path.exists() and _has_yaml:
        try:
            with open(config_path) as f:
                yaml_cfg = _yaml.safe_load(f) or {}
        except Exception:
            yaml_cfg = {}
    else:
        yaml_cfg = {}

    changes = []

    # Merge logging section
    if "logging" in legacy:
        yaml_cfg.setdefault("logging", {}).update(legacy["logging"])
        changes.append(f"  logging: {legacy['logging']}")

    # Merge request_validation → validation.mode
    if "request_validation" in legacy:
        yaml_cfg.setdefault("validation", {})["mode"] = legacy["request_validation"]
        changes.append(f"  validation.mode: {legacy['request_validation']}")

    # Merge plugins → plugins.enabled
    if "plugins" in legacy and isinstance(legacy["plugins"], list):
        yaml_cfg.setdefault("plugins", {})["enabled"] = legacy["plugins"]
        changes.append(f"  plugins.enabled: {legacy['plugins']}")

    if not changes:
        print("✓ No migratable sections found in config.json (logging/validation/plugins).")
        print("  config.json may contain agent-specific settings — those remain in place.")
        return

    print("Changes to apply to config.yaml:")
    for c in changes:
        print(c)
    print()

    if dry_run:
        print("(dry-run) — no changes written")
        return

    # Write updated config.yaml
    if _has_yaml:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            _yaml.dump(yaml_cfg, f, default_flow_style=False, allow_unicode=True)
        print("✓ config.yaml updated")
    else:
        print("✗ PyYAML not installed — cannot write config.yaml (pip install pyyaml)")
        return

    # Rename legacy config.json → config.json.bak
    bak_path = json_path.with_suffix(".json.bak")
    try:
        json_path.rename(bak_path)
        print(f"✓ {json_path.name} → {bak_path.name} (backup kept)")
    except Exception as e:
        print(f"⚠  Could not rename config.json: {e}")

    print()
    print("✓ Migration complete. Run 'tokenpak config show' to verify.")


# ── Parser builders for new commands ─────────────────────────────────────────


def _build_version_parser(sub: Subparsers) -> None:
    p = sub.add_parser("version", help="Show current versions (proxy, config, cli)")
    p.set_defaults(func=cmd_version)


def _build_update_parser(sub: Subparsers) -> None:
    p = sub.add_parser("update", help="Update TokenPak to latest from git/pypi")
    check_group = p.add_mutually_exclusive_group()
    check_group.add_argument(
        "--check", action="store_true", help="Check for updates once without installing"
    )
    check_group.add_argument(
        "--enable-checks",
        action="store_true",
        dest="enable_checks",
        help="Enable automatic daily update checks (no request now)",
    )
    check_group.add_argument(
        "--disable-checks",
        action="store_true",
        dest="disable_checks",
        help="Disable automatic update checks (no request)",
    )
    check_group.add_argument(
        "--check-status",
        action="store_true",
        dest="check_status",
        help="Show saved automatic-check state without a request",
    )
    p.add_argument("--force", action="store_true", help="Force update even if already up to date")
    p.add_argument(
        "--core-only",
        action="store_true",
        dest="core_only",
        help="Update core only, skip config merge",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Show what would change without applying",
    )
    p.set_defaults(func=cmd_update)


def _build_uninstall_parser(sub: Subparsers) -> None:
    p = sub.add_parser(
        "uninstall",
        help="Un-route (--soft) or purge state + remove package (--hard)",
    )
    p.add_argument(
        "--soft",
        action="store_true",
        help="Un-route only (reversible via `tokenpak setup`); keep config/state/package",
    )
    p.add_argument(
        "--hard",
        action="store_true",
        help="Soft + purge state (keeps journal/budget/capsules) + offer package removal",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Show the exact operations that would run, change nothing",
    )
    p.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation (required for --hard in non-interactive use)",
    )
    p.add_argument(
        "--keep-data",
        action="store_true",
        dest="keep_data",
        help="Under --hard, also retain all ~/.tpk user data (config + dbs)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit a machine-readable receipt",
    )
    p.set_defaults(func=cmd_uninstall)


def _build_config_mgmt_parser(sub: Subparsers) -> None:
    p = sub.add_parser("config", help="Config management (sync, pull, validate)")
    csub = p.add_subparsers(dest="config_cmd", required=False)

    # sync
    p_sync = csub.add_parser("sync", help="Sync config from canonical source")
    p_sync.add_argument(
        "--source", choices=["git", "url"], default="git", help="Config source: git (vault) or url"
    )
    p_sync.add_argument("--url", help="URL for source=url")
    p_sync.add_argument("--dry-run", action="store_true", dest="dry_run")
    p_sync.set_defaults(func=cmd_config_sync)

    # pull
    p_pull = csub.add_parser("pull", help="Pull config from git or URL")
    p_pull.add_argument("--source", choices=["git", "url"], default="git")
    p_pull.add_argument("--url", help="URL for source=url")
    p_pull.add_argument("--dry-run", action="store_true", dest="dry_run")
    p_pull.add_argument(
        "--merge", choices=["replace", "merge", "diff"], default="merge", help="Merge strategy"
    )
    p_pull.set_defaults(func=cmd_config_pull)

    # validate
    p_val = csub.add_parser("validate", help="Validate config against schema")
    p_val.add_argument(
        "--config",
        dest="config_file",
        metavar="FILE",
        help="Path to proxy config file (JSON/YAML) to validate against schema",
    )
    p_val.set_defaults(func=cmd_config_validate)

    # show — merged config (file + env overrides)
    p_show = csub.add_parser("show", help="Show merged config (file + env overrides)")
    p_show.add_argument("--json", action="store_true", help="Output as JSON")
    p_show.set_defaults(func=cmd_config)

    # init — create default config.yaml
    p_init = csub.add_parser("init", help="Create default config.yaml")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config")
    p_init.add_argument(
        "--with-env-stub",
        action="store_true",
        dest="with_env_stub",
        help="Also drop a placeholders-only .env.example under the TokenPak home",
    )
    p_init.set_defaults(func=cmd_config)

    # doctor — read-only config-subsystem diagnostics
    p_doctor = csub.add_parser(
        "doctor",
        help="Read-only config diagnostics (home, precedence, env vars, .env hygiene)",
    )
    p_doctor.add_argument("--json", action="store_true", help="Output as JSON")
    p_doctor.add_argument("--quiet", action="store_true", help="Print only the worst finding")
    p_doctor.add_argument("--verbose", "-v", action="store_true", help="Include per-check detail")
    p_doctor.set_defaults(func=_config_doctor_dispatch)

    # env — loaded env vars + provenance (masked by default)
    p_env = csub.add_parser(
        "env",
        help="Show loaded env vars + provenance (secret values masked by default)",
    )
    p_env.add_argument("--json", action="store_true", help="Output as JSON")
    p_env.add_argument(
        "--no-mask",
        action="store_false",
        dest="mask",
        help="Show low-class values unmasked (secret-class values are still masked)",
    )
    p_env.set_defaults(func=_config_env_dispatch, mask=True)

    # path — print config file path
    p_path = csub.add_parser("path", help="Print config file path")
    p_path.set_defaults(func=cmd_config)

    # migrate — merge config.json into config.yaml
    p_migrate = csub.add_parser(
        "migrate",
        help="Migrate legacy config.json settings into config.yaml",
    )
    p_migrate.add_argument(
        "--config-json",
        dest="config_json",
        metavar="FILE",
        default=None,
        help=(
            "Path to legacy config.json (default: resolved across the canonical and legacy homes)"
        ),
    )
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Print what would change without writing",
    )
    p_migrate.set_defaults(func=cmd_config_migrate)

    # optimize — deterministic, process-local MemoryGuard planning + state
    p_optimize = csub.add_parser(
        "optimize",
        help="Plan, apply, inspect, or roll back process-local memory optimization",
    )
    from tokenpak.services.memory_optimization import MODES as memory_modes
    from tokenpak.services.memory_optimization import PROFILES as memory_profiles

    action = p_optimize.add_mutually_exclusive_group()
    action.add_argument(
        "--plan",
        action="store_const",
        const="plan",
        dest="optimize_action",
        help="Show the deterministic plan without writing (default)",
    )
    action.add_argument(
        "--apply",
        action="store_const",
        const="apply",
        dest="optimize_action",
        help="Atomically apply the recomputed process-local plan",
    )
    action.add_argument(
        "--status",
        action="store_const",
        const="status",
        dest="optimize_action",
        help="Read managed artifacts and drift state without writing",
    )
    action.add_argument(
        "--rollback",
        action="store_const",
        const="rollback",
        dest="optimize_action",
        help="Restore the exact recorded preimage",
    )
    p_optimize.add_argument(
        "--profile",
        choices=sorted(memory_profiles),
        default="balanced",
        help="Memory budget policy",
    )
    p_optimize.add_argument(
        "--mode",
        choices=sorted(memory_modes),
        default="auto",
        help="Runtime behavior",
    )
    p_optimize.add_argument(
        "--expect-hash",
        metavar="SHA256",
        help="With --apply, refuse unless the recomputed plan has this SHA-256",
    )
    p_optimize.add_argument(
        "--force",
        action="store_true",
        help="With --rollback, restore the preimage despite external drift",
    )
    p_optimize.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_optimize.set_defaults(func=_config_optimize_dispatch)
    p.set_defaults(
        func=_bare_help(
            "config",
            "Manage configuration files",
            [
                "sync",
                "pull",
                "validate",
                "show",
                "init",
                "doctor",
                "env",
                "path",
                "migrate",
                "optimize",
            ],
            exit_nonzero=True,
        )
    )


def _config_doctor_dispatch(args: CommandArgs) -> None:
    from tokenpak.cli.commands.config_env import cmd_config_doctor

    cmd_config_doctor(args)


def _config_env_dispatch(args: CommandArgs) -> None:
    from tokenpak.cli.commands.config_env import cmd_config_env

    cmd_config_env(args)


def _config_optimize_dispatch(args: CommandArgs) -> int:
    from tokenpak.cli.commands._config_optimize import cmd_config_optimize

    return cmd_config_optimize(args)


# ── End Version Control Commands ──────────────────────────────────────────────


def _bare_help(
    name: str,
    description: str,
    subs: Sequence[str],
    exit_nonzero: bool = False,
) -> CommandHandler:
    def _help(args: CommandArgs) -> int | None:
        print(f"tokenpak {name}: {description}")
        print(f"Subcommands: {', '.join(subs)}")
        print(f"\nRun 'tokenpak {name} <subcommand> --help' for details.")
        if exit_nonzero:
            sys.exit(1)
        return None

    return _help


def main() -> None:
    global _NO_TUI_FLAG
    # Strip --no-tui from argv before any other parsing so per-subcommand
    # parsers don't need to know about it.
    if "--no-tui" in sys.argv:
        _NO_TUI_FLAG = True
        sys.argv = [a for a in sys.argv if a != "--no-tui"]

    parser = build_parser()

    # ── Intercept --version / -V ──────────────────────────────────────────────
    if len(sys.argv) >= 2 and sys.argv[1] in ("--version", "-V"):
        from tokenpak import __version__ as _ver

        print(f"tokenpak {_ver}")
        sys.exit(0)

    # ── Intercept bare `tokenpak --json`: deterministic machine-readable output ─
    # Cheap, schema-versioned status + command catalog; no slow probe (spec F3).
    if len(sys.argv) == 2 and sys.argv[1] == "--json":
        _emit_bare_json()
        sys.exit(0)

    # ── Intercept bare invocation: launch interactive menu on TTY ──────────────
    # The menu runs only when fully interactive (TTY both ends, no --no-tui, not
    # CI, TOKENPAK_NONINTERACTIVE unset, TERM != dumb; spec F1/F2). Every other
    # case prints deterministic non-interactive output and exits 0.
    if len(sys.argv) == 1:
        if _interactive_menu_allowed():
            try:
                from tokenpak.cli.commands.menu import run_menu

                run_menu()
            except Exception:
                print(f"Uptime: {_fetch_proxy_uptime()}")
                _print_quick_help()
        else:
            print(f"Uptime: {_fetch_proxy_uptime()}")
            _print_quick_help()
        sys.exit(0)

    # ── Intercept bare --help / -h for progressive disclosure ─────────────────
    if len(sys.argv) == 2 and sys.argv[1] in ("--help", "-h"):
        _print_quick_help()
        sys.exit(0)

    # ── Intercept unknown commands for typo suggestions ───────────────────────
    raw_cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    # Authoritative built-in verb set (grouped + argparse/stub commands). Plugin
    # verbs are dispatched only when raw_cmd is NOT in this set, so built-ins
    # always win on a name collision.
    known_cmds = _core_command_names()
    # ── Plugin verbs (tokenpak.commands entry-point group) ────────────────────
    # A verb that is not a built-in but is registered by an installed plugin is
    # routed straight to the plugin's own (already gated) callable. Built-ins win
    # on collision because this branch only runs for verbs not in known_cmds, and
    # discovery itself excludes any name that shadows a core command. Everything
    # after the verb is forwarded verbatim so the plugin owns its own flags
    # (including its own --help). Entitlement gating is the plugin's job.
    if raw_cmd and not raw_cmd.startswith("-") and raw_cmd not in known_cmds:
        _plugin_cmds = _discover_plugin_commands()
        if raw_cmd in _plugin_cmds:
            _verb_idx = sys.argv.index(raw_cmd)
            _rc = _dispatch_plugin_command(raw_cmd, sys.argv[_verb_idx + 1 :])
            sys.exit(_rc)

    # If user asks --help on an unrecognised command, just show that command's usage + exit 0
    if (
        raw_cmd
        and not raw_cmd.startswith("-")
        and raw_cmd not in known_cmds
        and "--help" in sys.argv
    ):
        print(f"tokenpak {raw_cmd}: no additional help available")
        print("Run `tokenpak help` for all commands.")
        sys.exit(0)

    if raw_cmd and not raw_cmd.startswith("-") and raw_cmd not in known_cmds:
        suggestion = _suggest_command(raw_cmd)
        import sys as _sys_err

        print(f"❌ Unknown command: '{raw_cmd}'", file=_sys_err.stderr)

        if suggestion:
            print(f"   Did you mean: tokenpak {suggestion}?", file=_sys_err.stderr)
        else:
            # Check for a semantically confusing command
            _COMMAND_HINTS = {
                "proxy": "→ Use `tokenpak serve` to start the proxy on localhost:8766.",
                "kill": "→ Use `tokenpak stop` to stop the running proxy.",
            }
            hint = _COMMAND_HINTS.get(raw_cmd)
            if hint:
                print(hint, file=_sys_err.stderr)
            else:
                print("\n📖 Available commands (by category):", file=_sys_err.stderr)
                for group, cmds in list(_COMMAND_GROUPS.items())[:3]:  # Show first 3 groups
                    print(f"\n   {group}:", file=_sys_err.stderr)
                    for cmd, desc in cmds[:3]:  # Show first 3 in each
                        print(f"     • {cmd:<15} {desc}", file=_sys_err.stderr)
                print("\n   (Use `tokenpak help` to see all commands)", file=_sys_err.stderr)
        sys.exit(1)

    # For companion launchers, manually split argv so *all* arguments after
    # tokenpak's own flags pass through verbatim to the underlying binary.
    # parse_args()/parse_known_args() would mishandle flags like
    # permission-bypass flags or split --model <value> pairs.
    if raw_cmd == "claude":
        claude_idx = sys.argv.index("claude")
        claude_tail = sys.argv[claude_idx + 1 :]
        # `tokenpak claude --help` asks about *this* wrapper. Verbatim
        # passthrough sent it to the client instead, so the user got the
        # client's help — or, when the client was absent, provisioning ran
        # for a binary that is not installed. Answer it here, before any
        # launch path can touch the filesystem.
        if _wants_wrapper_help(claude_tail):
            _print_companion_wrapper_help("claude")
            sys.exit(0)
        # Extract --budget (the only tokenpak-owned flag) if present
        budget = None
        passthrough = []
        i = 0
        while i < len(claude_tail):
            if claude_tail[i] == "--budget" and i + 1 < len(claude_tail):
                budget = float(claude_tail[i + 1])
                i += 2
            else:
                passthrough.append(claude_tail[i])
                i += 1
        args = argparse.Namespace(
            command="claude",
            func=cmd_claude,
            budget=budget,
            args=passthrough,
            db=".tokenpak/registry.db",
        )
    elif raw_cmd == "codex":
        codex_idx = sys.argv.index("codex")
        codex_tail = sys.argv[codex_idx + 1 :]
        if _wants_wrapper_help(codex_tail):
            _print_companion_wrapper_help("codex")
            sys.exit(0)
        args = _codex_namespace_from_tail(codex_tail)
    else:
        args = parser.parse_args()

    # No subcommand given → show smart default (savings summary)
    if not args.command:
        # Show compact savings summary instead of help
        try:
            from .telemetry.query_dsl import get_savings_report

            # Get uptime from proxy (if running)
            try:
                import urllib.request as _urlreq

                _proxy_base = os.environ.get("TOKENPAK_PROXY_URL", "http://127.0.0.1:8766")
                with _urlreq.urlopen(f"{_proxy_base}/health", timeout=3) as _r:
                    _hdata = json.loads(_r.read())
                _uptime_s = _hdata.get("uptime_seconds")
                if _uptime_s is not None:
                    _h, _rem = divmod(int(_uptime_s), 3600)
                    _m = _rem // 60
                    uptime_str = f"{_h}h {_m:02d}m" if _h else f"{_m}m"
                else:
                    uptime_str = "unknown"
            except Exception:
                uptime_str = "unknown"
            report = get_savings_report(days=1)

            # Compact summary. Bare `tokenpak` is the most-run entrypoint in
            # the product, so it is the last place that should print a number
            # nobody measured. It previously printed a hardcoded "5.6%
            # compression" regardless of the data, and attributed 95% of all
            # savings to whichever model happened to sort first.
            print(f"TokenPak — {uptime_str} uptime")

            if not report.available:
                print("💰 Savings unavailable — no readable measurement store.")
            elif report.observations == 0:
                print("💰 No measurements yet today — savings appear once traffic flows.")
            else:
                print(
                    f"💰 ${report.savings_amount:.2f} saved today "
                    f"({report.savings_pct:.0f}% reduction)"
                )

            # Get request count from recent events
            from .telemetry.query_dsl import get_recent_events

            recent = get_recent_events(limit=1000)
            req_count = len(recent) if recent else 0

            traffic = f"📊 {req_count:,} requests"
            if report.available and report.observations > 0:
                traffic += f" | {report.cache_hit_rate * 100:.0f}% cache hit"
                traffic += f" | {report.savings_pct:.1f}% reduction"
            print(traffic)

            # Top model by observed request count. Reporting which model was
            # used most is a measurement; splitting the savings total across
            # models by a fixed fraction was not.
            from .telemetry.query_dsl import get_model_usage

            usage = get_model_usage(days=1)
            if usage:
                top = usage[0]
                print(f"🔥 Most used: {top.model} across {top.request_count} requests")

            print()
            print("Run `tokenpak savings` for full breakdown.")
            sys.exit(0)
        except Exception:
            # Fallback if proxy is not running or DB unavailable
            _print_quick_help()
            sys.exit(0)

    # ── First-run welcome ──────────────────────────────────────────────────────
    machine_output = _machine_output_requested(args)
    suppress_update_nudge = _update_nudge_suppressed(args)
    if _is_first_run() and args.command not in ("help",) and not machine_output:
        print(
            "👋 Welcome to TokenPak! It looks like this is your first time.\n"
            "   First measured receipt: "
            "https://github.com/tokenpak/tokenpak/blob/main/docs/first-receipt.md\n"
            "   Offline fixture (no provider request): `tokenpak demo`\n"
            "   Run `tokenpak help` to see all available commands.\n"
        )
        _mark_intro_seen()

    # ── Smart defaults ─────────────────────────────────────────────────────────
    # `tokenpak cost` with no period flags → default to today
    if args.command == "cost":
        if not getattr(args, "week", False) and not getattr(args, "month", False):
            pass  # cmd_cost already defaults to "daily" when neither flag set

    # Honor explicit non-zero return codes from handlers. Beta-1
    # regression: handlers like cmd_pak_create and cmd_pak_import returned
    # 1 on error but the dispatcher dropped the value, so callers in
    # `set -e` scripts saw exit 0 even after a printed error. Handlers
    # that return None or 0 keep the prior fall-through behavior.
    try:
        _rc = args.func(args)
    except SystemExit as exc:
        if exc.code is None or exc.code == 0:
            _maybe_prompt_for_update(args.command, machine_output=suppress_update_nudge)
        raise
    if isinstance(_rc, int) and _rc != 0:
        sys.exit(_rc)
    _maybe_prompt_for_update(args.command, machine_output=suppress_update_nudge)


# ── Route commands ────────────────────────────────────────────────────────────


def _get_route_store(args: CommandArgs | None = None) -> "RouteStore":
    from .routing.rules import DEFAULT_ROUTES_PATH, RouteStore

    path = getattr(args, "routes", None) or DEFAULT_ROUTES_PATH
    return RouteStore(path=path)


def cmd_route_list(args: CommandArgs) -> None:
    """List all routing rules."""
    store = _get_route_store(args)
    rules = store.list()
    if not rules:
        print("No routing rules defined.")
        print(
            "Add one with: tokenpak route add --model 'gpt-4*' --target anthropic/claude-3-haiku-20240307"
        )
        return
    print(f"{'ID':<10} {'PRI':>4} {'EN':<4} {'PATTERN':<45} TARGET")
    print("-" * 90)
    for r in rules:
        pat_parts = []
        if r.pattern.model:
            pat_parts.append(f"model={r.pattern.model}")
        if r.pattern.prefix:
            pat_parts.append(f"prefix={r.pattern.prefix!r}")
        if r.pattern.min_tokens is not None:
            pat_parts.append(f"min_tokens={r.pattern.min_tokens}")
        if r.pattern.max_tokens is not None:
            pat_parts.append(f"max_tokens={r.pattern.max_tokens}")
        pat_str = ", ".join(pat_parts) or "(any)"
        enabled = "yes" if r.enabled else "no"
        desc = f"  # {r.description}" if r.description else ""
        print(f"{r.id:<10} {r.priority:>4} {enabled:<4} {pat_str:<45} {r.target}{desc}")


def cmd_route_add(args: CommandArgs) -> None:
    """Add a routing rule."""
    from .routing.rules import parse_pattern_args

    store = _get_route_store(args)
    try:
        pattern = parse_pattern_args(
            model=getattr(args, "model", None),
            prefix=getattr(args, "prefix", None),
            min_tokens=getattr(args, "min_tokens", None),
            max_tokens=getattr(args, "max_tokens", None),
        )
    except ValueError as e:
        print(f"❌ {e}")
        raise SystemExit(1)

    rule = store.add(
        pattern=pattern,
        target=args.target,
        priority=getattr(args, "priority", 100),
        description=getattr(args, "description", "") or "",
    )
    print(f"✅ Rule added: id={rule.id}  priority={rule.priority}  target={rule.target}")
    _print_rule_pattern(rule)


def _print_rule_pattern(rule: "RouteRule") -> None:
    pat = rule.pattern
    if pat.model:
        print(f"   Pattern: model glob = {pat.model!r}")
    if pat.prefix:
        print(f"   Pattern: prefix = {pat.prefix!r}")
    if pat.min_tokens is not None:
        print(f"   Pattern: min_tokens = {pat.min_tokens}")
    if pat.max_tokens is not None:
        print(f"   Pattern: max_tokens = {pat.max_tokens}")


def cmd_route_remove(args: CommandArgs) -> None:
    """Remove a routing rule by id."""
    store = _get_route_store(args)
    removed = store.remove(args.id)
    if removed:
        print(f"✅ Rule {args.id} removed.")
    else:
        print(f"⚠️  No rule found with id={args.id}")
        raise SystemExit(1)


def cmd_route_test(args: CommandArgs) -> None:
    """Show which rule would match a given prompt."""
    from .routing.rules import RouteEngine, _count_tokens_approx

    store = _get_route_store(args)
    engine = RouteEngine(store=store)

    prompt = args.prompt or ""
    model = getattr(args, "model", "") or ""
    token_count = getattr(args, "tokens", None)

    if token_count is None and prompt:
        token_count = _count_tokens_approx(prompt)

    print(
        f"Testing: model={model!r}  prompt={prompt[:60]!r}{'...' if len(prompt) > 60 else ''}  tokens≈{token_count}"
    )
    print()

    match = engine.match(model=model, prompt=prompt, token_count=token_count)
    if match:
        print(f"✅ Matched rule: id={match.id}  priority={match.priority}")
        print(f"   Target: {match.target}")
        _print_rule_pattern(match)
        if match.description:
            print(f"   Note: {match.description}")
    else:
        print("❌ No rule matched — request would use original model.")

    # Show all rules and their match status
    rules = store.list()
    if rules and getattr(args, "verbose", False):
        print()
        print("All rules evaluated:")
        for r in rules:
            from .routing.rules import RouteEngine as _RE

            did_match = _RE._matches(r.pattern, model=model, prompt=prompt, token_count=token_count)
            tag = "✓" if (did_match and r.enabled) else ("skip" if not r.enabled else "✗")
            print(f"  [{tag}] {r.id}  {r.target}")


def cmd_route_enable(args: CommandArgs) -> None:
    """Enable a routing rule."""
    store = _get_route_store(args)
    ok = store.set_enabled(args.id, True)
    print(f"✅ Rule {args.id} enabled." if ok else f"⚠️  Rule {args.id} not found.")


def cmd_route_disable(args: CommandArgs) -> None:
    """Disable a routing rule."""
    store = _get_route_store(args)
    ok = store.set_enabled(args.id, False)
    print(f"✅ Rule {args.id} disabled." if ok else f"⚠️  Rule {args.id} not found.")


def _build_route_parser(sub: Subparsers) -> None:
    p_route = sub.add_parser("route", help="Manage manual model routing rules")
    rsub = p_route.add_subparsers(dest="route_cmd", required=False)

    # Common --routes flag
    _routes_flag = dict(
        flag="--routes",
        kwargs=dict(default=None, help="Path to routes.yaml (default: ~/.tokenpak/routes.yaml)"),
    )

    # route list
    p_list = rsub.add_parser("list", help="Show all routing rules")
    p_list.add_argument("--routes", default=None, help="Path to routes.yaml")
    p_list.set_defaults(func=cmd_route_list)

    # route add
    p_add = rsub.add_parser("add", help="Add a routing rule")
    p_add.add_argument(
        "--model", default=None, help="Model glob pattern (e.g. 'gpt-4*', 'openai/*')"
    )
    p_add.add_argument("--prefix", default=None, help="Prompt prefix match (case-insensitive)")
    p_add.add_argument(
        "--min-tokens",
        dest="min_tokens",
        type=int,
        default=None,
        help="Minimum token count (inclusive)",
    )
    p_add.add_argument(
        "--max-tokens",
        dest="max_tokens",
        type=int,
        default=None,
        help="Maximum token count (inclusive)",
    )
    p_add.add_argument(
        "--target",
        required=True,
        help="Target model/provider (e.g. 'anthropic/claude-3-haiku-20240307')",
    )
    p_add.add_argument(
        "--priority",
        type=int,
        default=100,
        help="Rule priority (lower = higher priority, default 100)",
    )
    p_add.add_argument("--description", default="", help="Optional description")
    p_add.add_argument("--routes", default=None, help="Path to routes.yaml")
    p_add.set_defaults(func=cmd_route_add)

    # route remove
    p_rm = rsub.add_parser("remove", help="Remove a routing rule by id")
    p_rm.add_argument("id", help="Rule ID to remove")
    p_rm.add_argument("--routes", default=None, help="Path to routes.yaml")
    p_rm.set_defaults(func=cmd_route_remove)

    # route test
    p_test = rsub.add_parser("test", help="Show which rule matches a prompt")
    p_test.add_argument("prompt", nargs="?", default="", help="Prompt text to test")
    p_test.add_argument("--model", default="", help="Model name to test against")
    p_test.add_argument(
        "--tokens", type=int, default=None, help="Token count override (default: auto-estimated)"
    )
    p_test.add_argument(
        "--verbose", "-v", action="store_true", help="Show all rules and their match status"
    )
    p_test.add_argument("--routes", default=None, help="Path to routes.yaml")
    p_test.set_defaults(func=cmd_route_test)

    # route enable / disable
    p_en = rsub.add_parser("enable", help="Enable a routing rule")
    p_en.add_argument("id", help="Rule ID")
    p_en.add_argument("--routes", default=None, help="Path to routes.yaml")
    p_en.set_defaults(func=cmd_route_enable)

    p_dis = rsub.add_parser("disable", help="Disable a routing rule")
    p_dis.add_argument("id", help="Rule ID")
    p_dis.add_argument("--routes", default=None, help="Path to routes.yaml")
    p_dis.set_defaults(func=cmd_route_disable)
    p_route.set_defaults(
        func=_bare_help(
            "route",
            "Manage manual model routing rules",
            ["list", "add", "remove", "test", "enable", "disable"],
            exit_nonzero=True,
        )
    )


# ── Trigger commands ──────────────────────────────────────────────────────────


def _trigger_store() -> "TriggerStore":
    from tokenpak.orchestration.triggers.store import TriggerStore

    return TriggerStore()


def cmd_trigger_list(args: CommandArgs) -> None:
    import json as _json

    store = _trigger_store()
    triggers = store.list()
    if getattr(args, "json", False):
        print(
            _json.dumps(
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
        print("No triggers registered.")
        return
    print(f"{'ID':<10} {'ENABLED':<8} {'EVENT':<35} ACTION")
    print("-" * 75)
    for t in triggers:
        enabled = "yes" if t.enabled else "no"
        print(f"{t.id:<10} {enabled:<8} {t.event:<35} {t.action}")


def cmd_trigger_add(args: CommandArgs) -> None:
    import json as _json

    store = _trigger_store()
    t = store.add(event=args.event, action=args.action)
    if getattr(args, "json", False):
        print(
            _json.dumps(
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
    print(f"Trigger added: id={t.id}  event={t.event}  action={t.action}")


def cmd_trigger_remove(args: CommandArgs) -> None:
    import json as _json

    store = _trigger_store()
    removed = store.remove(args.id)
    if getattr(args, "json", False):
        print(_json.dumps({"removed": removed, "id": args.id}, indent=2))
        return
    if removed:
        print(f"Trigger {args.id} removed.")
    else:
        print(f"No trigger with id={args.id}")


def cmd_trigger_test(args: CommandArgs) -> None:
    """Dry-run: show which registered triggers would fire for a given event."""
    import json as _json

    from tokenpak.orchestration.triggers.matcher import match_event

    store = _trigger_store()
    event = args.event
    matched = [t for t in store.list() if t.enabled and match_event(t.event, event)]
    if getattr(args, "json", False):
        print(
            _json.dumps(
                [dict(id=t.id, event=t.event, action=t.action, would_fire=True) for t in matched],
                indent=2,
            )
        )
        return
    print(f"Testing event: {event}")
    if not matched:
        print("  No triggers would fire.")
    for t in matched:
        print(f"  ✓ {t.id}  {t.event}  →  {t.action}")


def cmd_trigger_log(args: CommandArgs) -> None:
    import json as _json

    store = _trigger_store()
    logs = store.list_logs(limit=args.limit)
    if getattr(args, "json", False):
        print(
            _json.dumps(
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
        print("No trigger log entries.")
        return
    for lg in logs:
        status = "✓" if lg.exit_code == 0 else "✗"
        print(f"{status} [{lg.fired_at[:19]}] {lg.trigger_id}  {lg.event}  →  {lg.action}")
        if lg.output:
            print(f"   {lg.output[:120]}")


def cmd_trigger_daemon(args: CommandArgs) -> None:
    from tokenpak.orchestration.triggers.daemon import TriggerDaemon

    store = _trigger_store()
    daemon = TriggerDaemon(store=store)
    daemon.run()


def cmd_trigger_fire(args: CommandArgs) -> None:
    """Fire an event string immediately — executes all matching enabled triggers."""
    from tokenpak.orchestration.commands import run_trigger_action
    from tokenpak.orchestration.triggers.matcher import match_event

    store = _trigger_store()
    event = args.event
    matched = [t for t in store.list() if t.enabled and match_event(t.event, event)]
    if not matched:
        print(f"No triggers matched event: {event}")
        return
    print(f"Firing event: {event} ({len(matched)} trigger(s))")
    for t in matched:
        print(f"  -> {t.id}  {t.action}")
        # Governed execution: shell=False by default; only ``shell:``-prefixed
        # actions reach the host shell, so config payloads are not shell-interpreted.
        result = run_trigger_action(t.action, timeout=30)
        store.log_fire(t, result.returncode, result.output)
        if result.timed_out:
            print("     [timeout]")
        elif result.output:
            print(f"     {result.output[:200]}")


_GIT_POST_COMMIT = """#!/bin/sh
# Installed by: tokenpak trigger hook install
tokenpak trigger fire git:commit
"""

_GIT_POST_PUSH = """#!/bin/sh
# Installed by: tokenpak trigger hook install
tokenpak trigger fire git:push
"""


def cmd_trigger_hook(args: CommandArgs) -> None:
    """Install or uninstall git hooks that emit trigger events."""
    import stat as _stat
    from pathlib import Path as _Path

    subcmd = args.hook_cmd
    git_dir = _Path(".git")
    if not git_dir.exists():
        print("Not in a git repository (no .git directory found).")
        return

    hooks_dir = git_dir / "hooks"
    hooks_dir.mkdir(exist_ok=True)

    hooks = {
        "post-commit": _GIT_POST_COMMIT,
        "post-push": _GIT_POST_PUSH,
    }

    if subcmd == "install":
        for name, body in hooks.items():
            hook_path = hooks_dir / name
            existing = hook_path.read_text() if hook_path.exists() else ""
            if "tokenpak trigger fire" in existing:
                print(f"  {name}: already installed (skip)")
            elif existing.strip():
                hook_path.write_text(existing.rstrip() + "\n\n" + body.strip() + "\n")
                print(f"  {name}: appended to existing hook")
            else:
                hook_path.write_text(body)
                hook_path.chmod(
                    hook_path.stat().st_mode | _stat.S_IEXEC | _stat.S_IXGRP | _stat.S_IXOTH
                )
                print(f"  {name}: installed")
        print("Git hooks installed. Events: git:commit, git:push")

    elif subcmd == "uninstall":
        for name in hooks:
            hook_path = hooks_dir / name
            if not hook_path.exists():
                continue
            body = hook_path.read_text()
            lines = body.splitlines(keepends=True)
            filtered = [
                l
                for l in lines
                if "tokenpak trigger fire" not in l and "Installed by: tokenpak" not in l
            ]
            new_body = "".join(filtered).strip()
            if new_body:
                hook_path.write_text(new_body + "\n")
            else:
                hook_path.unlink()
            print(f"  {name}: uninstalled")
        print("Git hooks removed.")


def _build_trigger_parser(sub: Subparsers) -> None:
    p_trig = sub.add_parser("trigger", help="Manage event triggers")
    tsub = p_trig.add_subparsers(dest="trigger_cmd", required=False)

    p_list = tsub.add_parser("list", help="List all triggers")
    p_list.add_argument(
        "--json", dest="json", action="store_true", default=False, help="Output raw JSON"
    )
    p_list.set_defaults(func=cmd_trigger_list)

    p_add = tsub.add_parser("add", help="Register a new trigger")
    p_add.add_argument(
        "--event",
        required=True,
        help="Event pattern (e.g. file:changed:*.py, git:commit, cost:daily>5)",
    )
    p_add.add_argument(
        "--action", required=True, help="Action: tokenpak sub-command or shell script path"
    )
    p_add.add_argument(
        "--json", dest="json", action="store_true", default=False, help="Output raw JSON"
    )
    p_add.set_defaults(func=cmd_trigger_add)

    p_rm = tsub.add_parser("remove", help="Remove a trigger by id")
    p_rm.add_argument("id", help="Trigger ID")
    p_rm.add_argument(
        "--json", dest="json", action="store_true", default=False, help="Output raw JSON"
    )
    p_rm.set_defaults(func=cmd_trigger_remove)

    p_test = tsub.add_parser("test", help="Dry-run: show which triggers match an event")
    p_test.add_argument("--event", required=True, help="Event string to test")
    p_test.add_argument(
        "--json", dest="json", action="store_true", default=False, help="Output raw JSON"
    )
    p_test.set_defaults(func=cmd_trigger_test)

    p_log = tsub.add_parser("log", help="Show recent trigger fire log")
    p_log.add_argument("--limit", type=int, default=20)
    p_log.add_argument(
        "--json", dest="json", action="store_true", default=False, help="Output raw JSON"
    )
    p_log.set_defaults(func=cmd_trigger_log)

    tsub.add_parser("daemon", help="Start background trigger daemon").set_defaults(
        func=cmd_trigger_daemon
    )

    p_fire = tsub.add_parser("fire", help="Fire an event string and execute matching triggers")
    p_fire.add_argument(
        "event", help="Event string to fire (e.g. git:push, agent:finished:agent-1)"
    )
    p_fire.set_defaults(func=cmd_trigger_fire)

    p_hook = tsub.add_parser("hook", help="Install/uninstall git hooks for trigger events")
    hsub = p_hook.add_subparsers(dest="hook_cmd", required=True)
    hsub.add_parser("install", help="Install post-commit and post-push git hooks").set_defaults(
        func=cmd_trigger_hook
    )
    hsub.add_parser("uninstall", help="Remove tokenpak git hooks").set_defaults(
        func=cmd_trigger_hook
    )

    p_watch = tsub.add_parser("watch", help="Start file watcher for file:changed events")
    p_watch.add_argument("paths", nargs="*", help="Paths to watch (default: .)")
    p_watch.set_defaults(func=cmd_trigger_watch)
    p_trig.set_defaults(
        func=_bare_help(
            "trigger",
            "Manage event triggers",
            ["list", "add", "remove", "test", "log", "daemon", "fire", "hook", "watch"],
            exit_nonzero=True,
        )
    )


def cmd_trigger_watch(args: CommandArgs) -> None:
    """Start file watcher for file:changed events."""
    import signal

    from tokenpak.orchestration.macros.hooks import (
        is_file_watcher_running,
        start_file_watcher,
        stop_file_watcher,
    )

    paths = args.paths if args.paths else ["."]

    if not start_file_watcher(paths):
        if is_file_watcher_running():
            print("File watcher already running.")
        else:
            print("Error: Could not start file watcher. Is 'watchdog' installed?")
            print("  pip install watchdog")
        return

    print(f"File watcher started. Watching: {', '.join(paths)}")
    print("Press Ctrl+C to stop.")

    def handle_sigint(sig: int, frame: object) -> None:
        stop_file_watcher()
        print("\nFile watcher stopped.")
        exit(0)

    signal.signal(signal.SIGINT, handle_sigint)

    import time

    while True:
        time.sleep(1)


# ── Cost / Budget commands ────────────────────────────────────────────────────


def _budget_tracker() -> "BudgetTracker":
    from tokenpak.telemetry.budget import get_budget_tracker

    return get_budget_tracker()


def cmd_cost(args: CommandArgs) -> None:
    """Show cost summary for a time period."""
    tracker = _budget_tracker()
    period = "monthly" if args.month else ("weekly" if args.week else "daily")
    as_json = getattr(args, "as_json", False)

    if args.by_model:
        rows = tracker.by_model_summary(period=period)
        if as_json:
            print(
                json.dumps(
                    {
                        "section": "cost",
                        "period": period,
                        "by_model": [dict(r) for r in rows],
                        "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
                    }
                )
            )
            return
        if not rows:
            print(f"No spend recorded for {period} period.")
            return
        print(f"{'MODEL':<30} {'REQUESTS':>9} {'INPUT':>9} {'OUTPUT':>9} {'COST':>10}")
        print("-" * 72)
        for r in rows:
            print(
                f"{(r['model'] or 'unknown'):<30} "
                f"{r['requests']:>9} "
                f"{r['tokens_input']:>9,} "
                f"{r['tokens_output']:>9,} "
                f"${r['cost_usd']:>9.4f}"
            )
        total = sum(r["cost_usd"] for r in rows)
        print(f"\nTotal: ${total:.4f}")
        return

    if args.export_csv:
        print(tracker.export_csv(period=period), end="")
        return

    # Prefer monitor.db (proxy's live database) over budget tracker
    monitor_total = _monitor_db_cost(period)
    tracked = tracker.total_spent(period)
    label = {"daily": "Today", "weekly": "This week", "monthly": "This month"}[period]

    if monitor_total is not None and monitor_total > 0:
        spent_usd, measured = monitor_total, True
    elif tracked > 0:
        spent_usd, measured = tracked, True
    elif monitor_total is None:
        # No request store at all — nothing has been measured.
        spent_usd, measured = None, False
    else:
        # The store exists and genuinely records no spend for this period.
        spent_usd, measured = 0.0, True

    # Live proxy session cost, if available.
    live_session = None
    stats = _proxy_get("/stats")
    if stats:
        session = _json_object(stats.get("session"))
        proxy_cost = _as_float(session.get("cost"))
        proxy_saved = _as_float(session.get("cost_saved"))
        saved_tokens = _as_int(session.get("saved_tokens"))
        if proxy_cost > 0 or saved_tokens > 0:
            live_session = {
                "cost_usd": proxy_cost,
                "cost_saved_usd": proxy_saved if proxy_saved > 0 else None,
                "saved_tokens": saved_tokens if saved_tokens > 0 else None,
            }

    # Budget status per period, if configured.
    budget = {}
    for p in ("daily", "monthly"):
        status = tracker.get_status(p)
        budget[p] = status.to_dict() if status else None

    if as_json:
        print(
            json.dumps(
                {
                    "section": "cost",
                    "period": period,
                    "available": measured,
                    "spent_usd": spent_usd,
                    "live_session": live_session,
                    "budget": budget,
                }
            )
        )
        return

    print(f"TokenPak Cost Summary — {label}")
    if not measured:
        print("  Spent:  no measurements yet — no request database exists")
    elif spent_usd:
        print(f"  Spent:  ${spent_usd:.4f}")
    else:
        print("  Spent:  $0.0000  (measured: no requests in this period)")

    if live_session:
        print("\n  Live session (proxy):")
        print(f"    Cost:          ${live_session['cost_usd']:.4f}")
        if live_session["cost_saved_usd"]:
            print(f"    Cost saved:    ${live_session['cost_saved_usd']:.4f}")
        if live_session["saved_tokens"]:
            print(f"    Tokens saved:  {live_session['saved_tokens']:,}")

    for p in ("daily", "monthly"):
        status_dict = budget[p]
        if status_dict:
            alert_tag = " ⚠️  ALERT" if status_dict["alert_triggered"] else ""
            print(
                f"  {p.capitalize()} budget: ${status_dict['spent_usd']:.4f} / "
                f"${status_dict['limit_usd']:.2f} ({status_dict['percent_used']:.1f}%){alert_tag}"
            )


def cmd_budget_set(args: CommandArgs) -> None:
    from tokenpak.telemetry.budget import load_budget_config, save_budget_config

    cfg = load_budget_config()
    changed = False
    if args.daily is not None:
        cfg.daily_limit_usd = args.daily
        changed = True
    if args.monthly is not None:
        cfg.monthly_limit_usd = args.monthly
        changed = True
    if args.alert_at is not None:
        cfg.alert_at_percent = args.alert_at
        changed = True
    if args.hard_stop is not None:
        cfg.hard_stop = args.hard_stop
        changed = True
    if changed:
        save_budget_config(cfg)
        print("Budget config saved.")
    print(f"  Daily limit:   {f'${cfg.daily_limit_usd:.2f}' if cfg.daily_limit_usd else 'not set'}")
    print(
        f"  Monthly limit: {f'${cfg.monthly_limit_usd:.2f}' if cfg.monthly_limit_usd else 'not set'}"
    )
    print(f"  Alert at:      {cfg.alert_at_percent:.0f}%")
    print(f"  Hard stop:     {'yes' if cfg.hard_stop else 'no'}")


def cmd_budget_status(args: CommandArgs) -> None:
    tracker = _budget_tracker()
    printed = False
    for period in ("daily", "monthly"):
        status = tracker.get_status(period)
        if status:
            bar_width = 30
            filled = int(bar_width * min(status.percent_used, 100) / 100)
            bar = "█" * filled + "░" * (bar_width - filled)
            alert_tag = " ⚠️  ALERT" if status.alert_triggered else ""
            print(f"{period.capitalize()} budget{alert_tag}")
            print(f"  [{bar}] {status.percent_used:.1f}%")
            print(
                f"  ${status.spent_usd:.4f} / ${status.limit_usd:.2f} (${status.remaining_usd:.4f} remaining)"
            )
            printed = True
    if not printed:
        print("No budget limits configured. Use `tokenpak budget set --daily N` to set one.")


def cmd_budget_history(args: CommandArgs) -> None:
    tracker = _budget_tracker()
    period = "monthly" if args.month else "daily"
    rows = tracker.list_spend(limit=args.limit, period=period)
    if not rows:
        print("No spend records found.")
        return
    print(f"{'TIMESTAMP':<22} {'MODEL':<25} {'COST':>10} {'TOKENS_IN':>10} {'TOKENS_OUT':>10}")
    print("-" * 82)
    for r in rows:
        print(
            f"{r['timestamp'][:19]:<22} "
            f"{(r['model'] or 'unknown'):<25} "
            f"${r['cost_usd']:>9.4f} "
            f"{r['tokens_input']:>10,} "
            f"{r['tokens_output']:>10,}"
        )


# ── Forecast (Burn Rate & Cost Projections) ──────────────────────────────────


def cmd_forecast(args: CommandArgs) -> None:
    """Show cost burn rate analysis and projections."""
    from .cli.forecast import format_burn_rate_display, get_burn_rate

    tracker = _budget_tracker()

    # Get window size from args
    period = getattr(args, "period", "7d")
    if period == "7d":
        window_days = 7
    elif period == "30d":
        window_days = 30
    elif period == "90d":
        window_days = 90
    else:
        window_days = 7

    # Get threshold if set
    threshold = getattr(args, "alert", None)
    if threshold is not None:
        try:
            threshold = float(threshold)
        except (ValueError, TypeError):
            print(f"Invalid threshold: {threshold}")
            return

    # Calculate burn rate
    analysis = get_burn_rate(tracker, window_days=window_days)

    # Display
    output = format_burn_rate_display(analysis, threshold=threshold)
    print(output)

    # Check threshold and alert if needed
    if threshold and analysis.monthly_projection > threshold:
        print()
        print(
            f"⚠️  Alert: Projected monthly spend ${analysis.monthly_projection:.2f} exceeds threshold ${threshold:.2f}"
        )


# ── Goals (Savings Targets & Progress Tracking) ────────────────────────────────


def _get_goal_manager() -> "GoalManager":
    from .cli.goals import GoalManager

    return GoalManager()


def cmd_goals_list(args: CommandArgs) -> None:
    """List all savings goals with progress."""
    manager = _get_goal_manager()
    goals_list = manager.list_goals()

    if not goals_list:
        print("No goals defined. Create one with: tokenpak goals add")
        return

    print(f"\n{'GOAL NAME':<30} {'TYPE':<12} {'PROGRESS':<30} {'STATUS':<12}")
    print("-" * 90)

    for goal in goals_list:
        progress = manager.get_progress(goal.goal_id)
        if not progress:
            continue

        # Create progress bar
        bar_width = 20
        filled = int(bar_width * min(progress.progress_percent, 100) / 100)
        bar = "█" * filled + "░" * (bar_width - filled)

        # Status indicator
        if progress.progress_percent >= 100:
            status = "✅ DONE"
        elif progress.pace_status == "behind":
            status = "⚠️  BEHIND"
        elif progress.pace_status == "ahead":
            status = "🚀 AHEAD"
        else:
            status = "▶️  ON TRACK"

        print(
            f"{goal.name:<30} {goal.goal_type:<12} "
            f"[{bar}] {progress.progress_percent:>5.1f}%  {status:<12}"
        )

        # Show additional details
        if goal.goal_type == "savings":
            print(f"  └─ ${progress.current_value:.2f} / ${progress.target_value:.2f}")
        else:
            print(f"  └─ {progress.current_value:.1f} / {progress.target_value:.1f}")


def cmd_goals_detail(args: CommandArgs) -> None:
    """Show detailed info for a specific goal."""
    manager = _get_goal_manager()
    goal = manager.get_goal(args.goal_id)

    if not goal:
        print(f"Goal '{args.goal_id}' not found.")
        return

    progress = manager.get_progress(goal.goal_id)
    if not progress:
        print(f"No progress data for goal '{args.goal_id}'.")
        return

    print(f"\n📊 Goal: {goal.name}")
    print(f"{'─' * 60}")
    print(f"ID:              {goal.goal_id}")
    print(f"Type:            {goal.goal_type}")
    print(f"Description:     {goal.description or '(none)'}")
    print(f"Start Date:      {goal.start_date}")
    print(f"End Date:        {goal.end_date}")
    print(f"Days Elapsed:    {goal.days_elapsed()} / {goal.total_days()}")
    print(f"Days Remaining:  {goal.days_remaining()}")
    print()

    # Progress bar
    bar_width = 30
    filled = int(bar_width * min(progress.progress_percent, 100) / 100)
    bar = "█" * filled + "░" * (bar_width - filled)
    print(f"Progress:        [{bar}] {progress.progress_percent:.1f}%")

    if goal.goal_type == "savings":
        print(f"Current:         ${progress.current_value:.2f}")
        print(f"Target:          ${progress.target_value:.2f}")
        print(f"Remaining:       ${max(0, progress.target_value - progress.current_value):.2f}")
    else:
        print(f"Current:         {progress.current_value:.1f}")
        print(f"Target:          {progress.target_value:.1f}")

    print()
    print(f"Pace Status:     {progress.pace_status.upper()}")
    expected = goal.expected_progress_percent()
    print(f"Expected:        {expected:.1f}% (based on time)")
    print(f"Actual:          {progress.progress_percent:.1f}%")

    # Milestone status
    print()
    print("Milestones:")
    milestones = [25, 50, 75, 100]
    for m in milestones:
        fired = getattr(progress, f"milestone_{m}_fired", False)
        status = "✅" if fired else "⭕"
        print(f"  {status} {m}%")


def cmd_goals_add(args: CommandArgs) -> None:
    """Add a new savings goal."""
    manager = _get_goal_manager()

    goal = manager.add_goal(
        name=args.name,
        goal_type=args.type,
        target_value=args.target,
        start_date=args.start,
        end_date=args.end,
        description=args.description or "",
        metric_name=args.metric or "",
        rolling_window=args.rolling_window,
    )

    print(f"✅ Goal created: {goal.goal_id}")
    print(f"   Name: {goal.name}")
    print(f"   Type: {goal.goal_type}")
    print(f"   Target: {goal.target_value}")
    print(f"   Period: {goal.start_date} → {goal.end_date}")


def cmd_goals_edit(args: CommandArgs) -> None:
    """Edit an existing goal."""
    manager = _get_goal_manager()

    # Build update dict from provided args
    updates = {}
    if args.name:
        updates["name"] = args.name
    if args.target is not None:
        updates["target_value"] = args.target
    if args.description:
        updates["description"] = args.description
    if args.end:
        updates["end_date"] = args.end

    if not updates:
        print("No updates provided. Use --name, --target, --description, or --end.")
        return

    goal = manager.edit_goal(args.goal_id, **updates)
    if not goal:
        print(f"Goal '{args.goal_id}' not found.")
        return

    print(f"✅ Goal updated: {goal.goal_id}")
    for key, val in updates.items():
        print(f"   {key}: {val}")


def cmd_goals_delete(args: CommandArgs) -> None:
    """Delete a goal."""
    manager = _get_goal_manager()

    if not manager.delete_goal(args.goal_id):
        print(f"Goal '{args.goal_id}' not found.")
        return

    print(f"✅ Goal deleted: {args.goal_id}")


def cmd_goals_update(args: CommandArgs) -> None:
    """Update goal progress."""
    manager = _get_goal_manager()

    progress = manager.update_progress(args.goal_id, args.value)
    if not progress:
        print(f"Goal '{args.goal_id}' not found.")
        return

    goal = manager.get_goal(args.goal_id)
    if goal is None:
        print(f"Goal '{args.goal_id}' not found.")
        return
    print(f"✅ Progress updated for {goal.name}")
    print(f"   Current: {progress.current_value}")
    print(f"   Target: {progress.target_value}")
    print(f"   Progress: {progress.progress_percent:.1f}%")
    print(f"   Pace: {progress.pace_status.upper()}")

    # Check milestones
    milestones = manager.check_milestones(args.goal_id)
    for m in milestones:
        print(f"   {m['message']}")


def cmd_goals_export(args: CommandArgs) -> None:
    """Export goals to JSON."""
    import json
    from pathlib import Path

    manager = _get_goal_manager()
    goals_list = manager.list_goals()

    export = {
        "goals": [g.to_dict() for g in goals_list],
        "progress": {gid: p.to_dict() for gid, p in manager.progress.items()},
        "summary": manager.get_summary_stats(),
    }

    if args.output:
        path = Path(args.output)
        with open(path, "w") as f:
            json.dump(export, f, indent=2)
        print(f"✅ Exported to {path}")
    else:
        print(json.dumps(export, indent=2))


def cmd_goals_history(args: CommandArgs) -> None:
    """Show goal history and milestones."""
    manager = _get_goal_manager()
    goals_list = manager.list_goals()

    if not goals_list:
        print("No goals defined.")
        return

    print(f"\n{'GOAL':<30} {'MILESTONE':<12} {'ACHIEVED':<20}")
    print("-" * 65)

    for goal in goals_list:
        progress = manager.get_progress(goal.goal_id)
        if not progress:
            continue

        milestones = []
        if progress.milestone_25_fired:
            milestones.append("25%")
        if progress.milestone_50_fired:
            milestones.append("50%")
        if progress.milestone_75_fired:
            milestones.append("75%")
        if progress.milestone_100_fired:
            milestones.append("100%")

        if milestones:
            for i, m in enumerate(milestones):
                prefix = goal.name if i == 0 else ""
                print(f"{prefix:<30} {m:<12}")


def cmd_goals_compare(args: CommandArgs) -> None:
    """Compare goal progress."""
    manager = _get_goal_manager()
    goals_list = manager.list_goals()

    if len(goals_list) < 2:
        print("Need at least 2 goals to compare.")
        return

    print(f"\n{'GOAL':<30} {'PROGRESS':<12} {'PACE':<12} {'DAYS LEFT':<12}")
    print("-" * 70)

    for goal in goals_list:
        progress = manager.get_progress(goal.goal_id)
        if not progress:
            continue

        print(
            f"{goal.name:<30} {progress.progress_percent:>10.1f}%  "
            f"{progress.pace_status:<12} {goal.days_remaining():>10}"
        )


def _build_goals_parser(sub: Subparsers) -> None:
    """Add goals subparser."""
    p_goals = sub.add_parser("goals", help="Manage savings goals and track progress")
    gsub = p_goals.add_subparsers(dest="goals_cmd", required=False)

    # List goals (default)
    p_list = gsub.add_parser("list", help="List all goals")
    p_list.set_defaults(func=cmd_goals_list)

    # Detail
    p_detail = gsub.add_parser("detail", help="Show details for a specific goal")
    p_detail.add_argument("goal_id", help="Goal ID")
    p_detail.set_defaults(func=cmd_goals_detail)

    # Add goal
    p_add = gsub.add_parser("add", help="Create a new goal")
    p_add.add_argument("--name", required=True, help="Goal name")
    p_add.add_argument(
        "--type",
        required=True,
        choices=["savings", "compression", "cache", "metric"],
        help="Goal type",
    )
    p_add.add_argument("--target", required=True, type=float, help="Target value")
    p_add.add_argument("--start", help="Start date (YYYY-MM-DD, default: today)")
    p_add.add_argument("--end", help="End date (YYYY-MM-DD, default: 30 days from start)")
    p_add.add_argument("--description", help="Goal description")
    p_add.add_argument("--metric", help="Custom metric name (for metric type)")
    p_add.add_argument("--rolling-window", action="store_true", help="Enable weekly pace tracking")
    p_add.set_defaults(func=cmd_goals_add)

    # Edit goal
    p_edit = gsub.add_parser("edit", help="Edit an existing goal")
    p_edit.add_argument("goal_id", help="Goal ID to edit")
    p_edit.add_argument("--name", help="New goal name")
    p_edit.add_argument("--target", type=float, help="New target value")
    p_edit.add_argument("--description", help="New description")
    p_edit.add_argument("--end", help="New end date (YYYY-MM-DD)")
    p_edit.set_defaults(func=cmd_goals_edit)

    # Delete goal
    p_delete = gsub.add_parser("delete", help="Delete a goal")
    p_delete.add_argument("goal_id", help="Goal ID to delete")
    p_delete.set_defaults(func=cmd_goals_delete)

    # Update progress
    p_update = gsub.add_parser("update", help="Update goal progress")
    p_update.add_argument("goal_id", help="Goal ID")
    p_update.add_argument("value", type=float, help="New current value")
    p_update.set_defaults(func=cmd_goals_update)

    # Export
    p_export = gsub.add_parser("export", help="Export goals to JSON")
    p_export.add_argument("--output", "-o", help="Output file (default: stdout)")
    p_export.set_defaults(func=cmd_goals_export, json_output=True)

    # History
    p_history = gsub.add_parser("history", help="Show milestone history")
    p_history.set_defaults(func=cmd_goals_history)

    # Compare
    p_compare = gsub.add_parser("compare", help="Compare goal progress")
    p_compare.set_defaults(func=cmd_goals_compare)

    # Default to list if no subcommand
    p_goals.set_defaults(func=cmd_goals_list)


def _build_cost_parser(sub: Subparsers) -> None:
    p_cost = sub.add_parser("cost", help="Show API cost summary")
    p_cost.add_argument("--week", action="store_true", help="Show weekly totals")
    p_cost.add_argument("--month", action="store_true", help="Show monthly totals")
    p_cost.add_argument("--by-model", action="store_true", help="Break down by model")
    cost_output_group = p_cost.add_mutually_exclusive_group()
    cost_output_group.add_argument("--export-csv", action="store_true", help="Export as CSV")
    cost_output_group.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit machine-readable JSON output",
    )
    p_cost.set_defaults(func=cmd_cost)

    # Subcommands for cost
    cost_sub = p_cost.add_subparsers(dest="cost_subcmd")
    p_show_budget = cost_sub.add_parser("show-budget", help="Show budget status and alerts")
    p_show_budget.add_argument("--config", help="Path to tokenpak config file")
    p_show_budget.set_defaults(func=cmd_cost_show_budget)


def _build_budget_parser(sub: Subparsers) -> None:
    p_budget = sub.add_parser("budget", help="Manage budget limits")
    bsub = p_budget.add_subparsers(dest="budget_cmd", required=False)

    p_set = bsub.add_parser("set", help="Configure budget limits")
    p_set.add_argument("--daily", type=float, metavar="USD", help="Daily spend limit in USD")
    p_set.add_argument("--monthly", type=float, metavar="USD", help="Monthly spend limit in USD")
    p_set.add_argument(
        "--alert-at", type=float, metavar="PCT", help="Alert threshold %% (default 80)"
    )
    p_set.add_argument(
        "--hard-stop", action="store_true", default=None, help="Block requests when limit exceeded"
    )
    p_set.set_defaults(func=cmd_budget_set)

    bsub.add_parser("status", help="Show current budget status").set_defaults(
        func=cmd_budget_status
    )
    bsub.add_parser("show", help="Alias for status — show current budget status").set_defaults(
        func=cmd_budget_status
    )

    p_hist = bsub.add_parser("history", help="Show recent spend records")
    p_hist.add_argument("--limit", type=int, default=20)
    p_hist.add_argument("--month", action="store_true", help="Show this month")
    p_hist.set_defaults(func=cmd_budget_history)
    p_budget.set_defaults(func=lambda a: p_budget.print_help())


def _build_forecast_parser(sub: Subparsers) -> None:
    p_forecast = sub.add_parser("forecast", help="Cost burn rate & projections")
    p_forecast.add_argument(
        "--period", choices=["7d", "30d", "90d"], default="7d", help="Analysis window (default: 7d)"
    )
    p_forecast.add_argument(
        "--alert",
        type=float,
        metavar="USD",
        help="Alert if monthly projection exceeds this USD amount",
    )
    p_forecast.set_defaults(func=cmd_forecast)


# ── top-level lock subcommand ─────────────────────────────────────────────────


def cmd_lock_claim(args: CommandArgs) -> None:
    import time as _time

    from tokenpak.orchestration.locks import FileLockManager, LockConflictError

    mgr = FileLockManager(agent_id=args.agent or None, timeout_s=args.timeout)
    try:
        record = mgr.claim(args.path, timeout_s=args.timeout)
        print(f"✅ Lock claimed: {record['path']}")
        print(f"   Agent:      {record['agent']}")
        exp = record["expires"]
        print(f"   Expires in: {exp - _time.time():.0f}s  (at epoch {exp:.0f})")
    except LockConflictError as e:
        print(f"❌ {e}")
        raise SystemExit(1)


def cmd_lock_release(args: CommandArgs) -> None:
    from tokenpak.orchestration.locks import FileLockManager

    mgr = FileLockManager(agent_id=args.agent or None)
    released = mgr.release(args.path)
    if released:
        print(f"✅ Released: {args.path}")
    else:
        print(f"⚠️  No lock held by this agent on: {args.path}")


def cmd_lock_query(args: CommandArgs) -> None:
    import time as _time

    from tokenpak.orchestration.locks import FileLockManager

    mgr = FileLockManager(agent_id=args.agent or None)
    record = mgr.query(args.path)
    if record is None:
        print(f"🔓 Unlocked: {args.path}")
    else:
        remaining = max(0, record.get("expires", 0) - _time.time())
        print(f"🔒 Locked:   {record['path']}")
        print(f"   Agent:      {record['agent']}")
        print(f"   PID:        {record.get('pid', '?')}")
        print(f"   Expires in: {remaining:.0f}s")


def cmd_lock_list(args: CommandArgs) -> None:
    import time as _time

    from tokenpak.orchestration.locks import FileLockManager

    mgr = FileLockManager(agent_id=args.agent or None)
    mgr.prune_expired()
    locks = mgr.locks()
    if not locks:
        print("No active locks.")
        return
    now = _time.time()
    print(f"{'Path':<50} {'Agent':<15} {'Expires In':>12}")
    print("-" * 80)
    for lock in locks:
        remaining = max(0, lock.get("expires", 0) - now)
        path = lock.get("path", "?")
        if len(path) > 49:
            path = "…" + path[-48:]
        print(f"{path:<50} {lock.get('agent', '?'):<15} {remaining:>10.0f}s")


def cmd_lock_renew(args: CommandArgs) -> None:
    import time as _time

    from tokenpak.orchestration.locks import FileLockManager, LockConflictError, LockExpiredError

    mgr = FileLockManager(agent_id=args.agent or None, timeout_s=args.timeout)
    try:
        record = mgr.renew(args.path, timeout_s=args.timeout)
        exp = record["expires"]
        print(f"🔄 Renewed: {record['path']}")
        print(f"   Agent:      {record['agent']}")
        print(f"   Expires in: {exp - _time.time():.0f}s")
    except LockExpiredError as e:
        print(f"⚠️  {e}")
        raise SystemExit(1)
    except LockConflictError as e:
        print(f"❌ {e}")
        raise SystemExit(1)


def _build_pak_parser(sub: Subparsers) -> None:
    """Register the ``tokenpak pak`` subcommand (MultiPak Pro Phase 1).

    Implementation lives in :mod:`tokenpak.cli.commands.pak` to keep the
    handler module isolated and grow naturally as Phase 2+ adds
    ``recall|hydrate|promote|prune`` actions. Lazy import keeps
    ``tokenpak --help`` fast.
    """
    from tokenpak.cli.commands.pak import build_pak_parser

    build_pak_parser(sub)


def _build_tip_parser(sub: Subparsers) -> None:
    """Register the ``tokenpak tip`` subcommand (Beta 1 regression recovery).

    Restores the v1.3.7 doctor-conformance surface as a dedicated verb
    family. Implementation lives in :mod:`tokenpak.cli.commands.tip`.
    """
    from tokenpak.cli.commands.tip import build_tip_parser

    build_tip_parser(sub)


def _build_features_parser(sub: Subparsers) -> None:
    """Register the ``tokenpak features`` subcommand (Beta 1, Packet G)."""
    from tokenpak.cli.commands.features import build_features_parser

    build_features_parser(sub)


def _build_pakplan_parser(sub: Subparsers) -> None:
    """Register the ``tokenpak pakplan`` subcommand (Beta 1 consumer surface).

    The PAKPlan foundation (recall schema + reason/risk registries +
    ordering hints) shipped at PR #184 / ``43bfb58e2c``. Beta 1 OSS
    surface is preview/explain/report only — scoring + capture pipeline
    remain Pro.
    """
    from tokenpak.cli.commands.pakplan import build_pakplan_parser

    build_pakplan_parser(sub)


def _build_dispatch_parser(sub: Subparsers) -> None:
    """Register the ``tokenpak dispatch`` command group (Dispatch v0.1-alpha).

    TokenPak Dispatch is the OSS workflow-control layer:
    Decision Inbox + ``run|status|inspect|decisions|approve|reject|pause|resume|
    cancel|discard-late|delivery|receipt`` verbs over the Run Ledger.
    Implementation lives in :mod:`tokenpak.cli.commands.dispatch_cmd`; lazy
    import keeps ``tokenpak --help`` fast.
    """
    # Dispatch runtime is excluded from the released wheel (preview / main-only).
    # Register the command group only when the orchestration package is present
    # in this build; in the slim released package it is cleanly absent.
    import importlib.util

    if importlib.util.find_spec("tokenpak.orchestration.dispatch") is None:
        return
    from tokenpak.cli.commands.dispatch_cmd import build_dispatch_parser

    build_dispatch_parser(sub)


def _build_home_parser(sub: Subparsers) -> None:
    """Register the ``tokenpak home`` subcommand (Beta 1).

    Subcommands: ``path | init | validate | explain | migrate``.
    Implementation lives in :mod:`tokenpak.cli.commands.home_cmd`. The
    verb is ``home`` rather than ``config`` because the existing
    ``config`` parser owns proxy config.yaml lifecycle commands; this
    family owns the TokenPak home directory.
    """
    from tokenpak.cli.commands.home_cmd import build_home_parser

    build_home_parser(sub)


def _build_lock_parser(sub: Subparsers) -> None:
    p_lock = sub.add_parser("lock", help="File lock management for multi-agent coordination")
    lsub = p_lock.add_subparsers(dest="lock_cmd", required=False)

    # claim
    p_claim = lsub.add_parser("claim", help="Claim a lock on a file or directory")
    p_claim.add_argument("path", help="File or directory path to lock")
    p_claim.add_argument(
        "--timeout",
        type=int,
        default=1800,
        metavar="SECONDS",
        help="Lock TTL in seconds (default 1800 = 30 min)",
    )
    p_claim.add_argument("--agent", default=None, help="Agent id override")
    p_claim.set_defaults(func=cmd_lock_claim)

    # release
    p_release = lsub.add_parser("release", help="Release a held lock")
    p_release.add_argument("path", help="File or directory path to release")
    p_release.add_argument("--agent", default=None, help="Agent id override")
    p_release.set_defaults(func=cmd_lock_release)

    # query
    p_query = lsub.add_parser("query", help="Query who holds a lock on a path")
    p_query.add_argument("path", help="File or directory path to query")
    p_query.add_argument("--agent", default=None, help="Agent id override (for manager context)")
    p_query.set_defaults(func=cmd_lock_query)

    # list
    p_list = lsub.add_parser("list", help="List all active locks")
    p_list.add_argument("--agent", default=None, help="Filter by agent id (display context only)")
    p_list.set_defaults(func=cmd_lock_list)

    # renew (heartbeat)
    p_renew = lsub.add_parser("renew", help="Renew (heartbeat) a held lock to extend its TTL")
    p_renew.add_argument("path", help="File or directory path to renew")
    p_renew.add_argument(
        "--timeout",
        type=int,
        default=1800,
        metavar="SECONDS",
        help="New TTL in seconds (default 1800 = 30 min)",
    )
    p_renew.add_argument("--agent", default=None, help="Agent id override")
    p_renew.set_defaults(func=cmd_lock_renew)
    p_lock.set_defaults(func=lambda a: p_lock.print_help())


# ── agent lock/unlock/locks commands ─────────────────────────────────────────


def cmd_agent_lock(args: CommandArgs) -> None:
    from tokenpak.orchestration.locks import FileLockManager, LockConflictError

    mgr = FileLockManager(agent_id=args.agent or None)
    try:
        record = mgr.claim(args.path, timeout_s=args.timeout)
        print(f"✅ Lock acquired: {record['path']}")
        print(f"   Agent:   {record['agent']}")
        print(
            f"   Expires: {record['expires']:.0f} (in {record['expires'] - __import__('time').time():.0f}s)"
        )
    except LockConflictError as e:
        print(f"❌ {e}")
        raise SystemExit(1)


def cmd_agent_unlock(args: CommandArgs) -> None:
    from tokenpak.orchestration.locks import FileLockManager

    mgr = FileLockManager(agent_id=args.agent or None)
    released = mgr.release(args.path)
    if released:
        print(f"✅ Lock released: {args.path}")
    else:
        print(f"⚠️  No lock held by this agent on: {args.path}")


def cmd_agent_locks(args: CommandArgs) -> None:
    import time

    from tokenpak.orchestration.locks import FileLockManager

    mgr = FileLockManager(agent_id=args.agent or None)
    mgr.prune_expired()
    locks = mgr.locks()
    if not locks:
        print("No active locks.")
        return
    print(f"{'Path':<50} {'Agent':<15} {'Expires In':>12}")
    print("-" * 80)
    now = time.time()
    for lock in locks:
        remaining = max(0, lock.get("expires", 0) - now)
        path = lock.get("path", "?")
        if len(path) > 49:
            path = "…" + path[-48:]
        print(f"{path:<50} {lock.get('agent', '?'):<15} {remaining:>10.0f}s")


def cmd_agent_list(args: CommandArgs) -> None:
    """List registered agents."""
    import json as json_mod

    from tokenpak.orchestration.registry import AgentRegistry

    registry = AgentRegistry()
    if args.all:
        agents = registry.list_all()
    else:
        agents = registry.list_active()

    if args.json:
        print(json_mod.dumps([a.to_dict() for a in agents], indent=2))
        return

    if not agents:
        print("No registered agents.")
        return

    print(f"{'ID':<10} {'Name':<12} {'Hostname':<15} {'Status':<10} {'Heartbeat':<12}")
    print("-" * 65)
    for a in agents:
        age = a.heartbeat_age_seconds()
        if age < 60:
            hb = f"{age:.0f}s ago"
        elif age < 3600:
            hb = f"{age / 60:.0f}m ago"
        else:
            hb = f"{age / 3600:.1f}h ago"
        stale = " (stale)" if a.is_stale() else ""
        print(f"{a.agent_id:<10} {a.name:<12} {a.hostname:<15} {a.status:<10} {hb}{stale}")


def cmd_agent_register(args: CommandArgs) -> None:
    """Register this agent."""
    import json as json_mod

    from tokenpak.orchestration.registry import AgentRegistry

    hostname = args.hostname or socket.gethostname()
    capabilities = {
        "gpu": args.gpu,
        "memory_gb": args.memory,
        "specialties": args.specialties,
        "provider_access": args.providers,
        "max_concurrent": 1,
    }

    registry = AgentRegistry()
    agent_id = registry.register(args.name, hostname, capabilities)

    if args.json:
        agent = registry.get(agent_id)
        if agent is None:
            raise RuntimeError(f"registered agent {agent_id} could not be reloaded")
        print(json_mod.dumps(agent.to_dict(), indent=2))
    else:
        print(f"✅ Registered: {args.name} @ {hostname} (id: {agent_id})")


def cmd_agent_deregister(args: CommandArgs) -> None:
    """Remove agent from registry."""
    from tokenpak.orchestration.registry import AgentRegistry

    registry = AgentRegistry()
    if registry.deregister(args.agent_id):
        print(f"✅ Deregistered: {args.agent_id}")
    else:
        print(f"⚠️  Agent not found: {args.agent_id}")


def cmd_agent_heartbeat(args: CommandArgs) -> None:
    """Send heartbeat for agent."""
    from tokenpak.orchestration.registry import AgentRegistry

    registry = AgentRegistry()
    if registry.heartbeat(args.agent_id, status=args.status, current_task=args.task):
        print(f"✅ Heartbeat: {args.agent_id}")
    else:
        print(f"⚠️  Agent not found: {args.agent_id}")


def cmd_agent_match(args: CommandArgs) -> None:
    """Find agents matching requirements."""
    import json as json_mod

    from tokenpak.orchestration.capabilities import CapabilityMatcher, TaskRequirements

    requirements = TaskRequirements(
        requires_gpu=True if args.gpu else None,
        min_memory_gb=args.memory,
        required_specialties=args.specialty or [],
        required_providers=args.provider or [],
    )

    matcher = CapabilityMatcher()
    matches = matcher.match(requirements)

    if args.json:
        print(json_mod.dumps([m.to_dict() for m in matches], indent=2))
        return

    if not matches:
        print("No matching agents found.")
        return

    print(f"{'Score':<8} {'ID':<10} {'Name':<12} {'Reasons'}")
    print("-" * 60)
    for m in matches:
        reasons = ", ".join(m.reasons[:3]) if m.reasons else "-"
        print(f"{m.score:<8.1f} {m.agent.agent_id:<10} {m.agent.name:<12} {reasons}")


def cmd_agent_prune(args: CommandArgs) -> None:
    """Remove stale agents."""
    from tokenpak.orchestration.registry import AgentRegistry

    registry = AgentRegistry()
    count = registry.prune_stale()
    if count:
        print(f"✅ Pruned {count} stale agent(s)")
    else:
        print("No stale agents to prune.")


def cmd_agent_handoff(args: CommandArgs) -> None:
    """Dispatch to handoff command handler."""
    from .cli.commands.handoff import handoff_cmd

    handoff_cmd(args)


def _build_agent_parser(sub: Subparsers) -> None:
    p_agent = sub.add_parser("agent", help="Agent coordination (locks, registry, capabilities)")
    asub = p_agent.add_subparsers(dest="agent_cmd", required=False)

    # --- Lock commands ---
    p_lock = asub.add_parser("lock", help="Claim a file lock")
    p_lock.add_argument("path", help="File path to lock")
    p_lock.add_argument(
        "--timeout",
        type=int,
        default=600,
        metavar="SECONDS",
        help="Lock TTL in seconds (default 600)",
    )
    p_lock.add_argument("--agent", default=None, help="Agent id override")
    p_lock.set_defaults(func=cmd_agent_lock)

    p_unlock = asub.add_parser("unlock", help="Release a file lock")
    p_unlock.add_argument("path", help="File path to unlock")
    p_unlock.add_argument("--agent", default=None, help="Agent id override")
    p_unlock.set_defaults(func=cmd_agent_unlock)

    p_locks = asub.add_parser("locks", help="List all active locks")
    p_locks.add_argument("--agent", default=None, help="Filter by agent id")
    p_locks.set_defaults(func=cmd_agent_locks)

    # --- Registry commands ---
    p_list = asub.add_parser("list", help="List registered agents")
    p_list.add_argument("--all", action="store_true", help="Include stale agents")
    p_list.add_argument("--json", action="store_true", help="JSON output")
    p_list.set_defaults(func=cmd_agent_list)

    p_register = asub.add_parser("register", help="Register this agent")
    p_register.add_argument("name", help="Agent name (e.g., agent-1, agent-2)")
    p_register.add_argument("--hostname", default=None, help="Hostname (default: auto-detect)")
    p_register.add_argument("--gpu", action="store_true", help="Has GPU")
    p_register.add_argument("--memory", type=float, default=4.0, help="Memory in GB")
    p_register.add_argument(
        "--specialties", nargs="*", default=[], help="Specialties (e.g., code research)"
    )
    p_register.add_argument("--providers", nargs="*", default=["anthropic"], help="Provider access")
    p_register.add_argument("--json", action="store_true", help="JSON output")
    p_register.set_defaults(func=cmd_agent_register)

    p_deregister = asub.add_parser("deregister", help="Remove an agent from registry")
    p_deregister.add_argument("agent_id", help="Agent ID to remove")
    p_deregister.set_defaults(func=cmd_agent_deregister)

    p_heartbeat = asub.add_parser("heartbeat", help="Send heartbeat for an agent")
    p_heartbeat.add_argument("agent_id", help="Agent ID")
    p_heartbeat.add_argument(
        "--status", choices=["active", "busy", "draining"], help="Update status"
    )
    p_heartbeat.add_argument("--task", default=None, help="Current task name")
    p_heartbeat.set_defaults(func=cmd_agent_heartbeat)

    p_match = asub.add_parser("match", help="Find agents matching requirements")
    p_match.add_argument("--gpu", action="store_true", help="Require GPU")
    p_match.add_argument("--memory", type=float, default=None, help="Minimum memory GB")
    p_match.add_argument("--specialty", nargs="*", default=[], help="Required specialties")
    p_match.add_argument("--provider", nargs="*", default=[], help="Required providers")
    p_match.add_argument("--json", action="store_true", help="JSON output")
    p_match.set_defaults(func=cmd_agent_match)

    p_prune = asub.add_parser("prune", help="Remove stale agents")
    p_prune.set_defaults(func=cmd_agent_prune)

    # handoff subcommand
    p_handoff = asub.add_parser("handoff", help="Context handoff between agents")
    hsub = p_handoff.add_subparsers(dest="handoff_cmd", required=True)

    hc = hsub.add_parser("create", help="Create a context handoff")
    hc.add_argument("--from", dest="handoff_from", required=True, help="Sending agent")
    hc.add_argument("--to", dest="handoff_to", required=True, help="Receiving agent")
    hc.add_argument(
        "--ref", action="append", metavar="TYPE:PATH[:DESC]", help="Context ref (repeatable)"
    )
    hc.add_argument("--done", metavar="TEXT", help="What was done")
    hc.add_argument("--next", dest="whats_next", metavar="TEXT", help="What comes next")
    hc.add_argument("--file", action="append", metavar="PATH", help="Relevant file (repeatable)")
    hc.add_argument(
        "--ttl", type=float, default=24.0, metavar="HOURS", help="TTL in hours (default 24)"
    )
    hc.set_defaults(func=cmd_agent_handoff)

    hr = hsub.add_parser("receive", help="Receive and validate a handoff")
    hr.add_argument("handoff_id", help="Handoff ID")
    hr.set_defaults(func=cmd_agent_handoff)

    ha = hsub.add_parser("apply", help="Apply a handoff (load context)")
    ha.add_argument("handoff_id", help="Handoff ID")
    ha.set_defaults(func=cmd_agent_handoff)

    hl = hsub.add_parser("list", help="List handoffs")
    hl.add_argument("--to", dest="handoff_to", metavar="AGENT", help="Filter by recipient")
    hl.add_argument("--from", dest="handoff_from", metavar="AGENT", help="Filter by sender")
    hl.add_argument("--status", metavar="STATUS", help="Filter by status")
    hl.set_defaults(func=cmd_agent_handoff)

    hs = hsub.add_parser("show", help="Show handoff details")
    hs.add_argument("handoff_id", help="Handoff ID")
    hs.set_defaults(func=cmd_agent_handoff)

    he = hsub.add_parser("expire", help="Expire stale handoffs")
    he.set_defaults(func=cmd_agent_handoff)
    p_agent.set_defaults(
        func=_bare_help(
            "agent",
            "Agent coordination (locks, registry, capabilities)",
            [
                "lock",
                "unlock",
                "locks",
                "list",
                "register",
                "deregister",
                "heartbeat",
                "match",
                "prune",
                "handoff",
            ],
        )
    )


# ── Replay commands ───────────────────────────────────────────────────────────


def _replay_store_path() -> str:
    """Return the default replay store path (honouring XDG convention)."""
    return str(Path.home() / ".tokenpak" / "replay.db")


def _get_replay_store() -> "ReplayStore":
    from tokenpak.telemetry.replay import get_replay_store

    return get_replay_store(_replay_store_path())


def cmd_replay_list(args: CommandArgs) -> None:
    """List recent replay entries."""
    store = _get_replay_store()
    entries = store.list(limit=args.limit, provider=args.provider or None)
    if not entries:
        print("No replay entries found.  Run tokenpak via the proxy to capture sessions.")
        return
    print(f"{'':2} {'ID':<10} {'TIMESTAMP':<20} {'PROVIDER/MODEL':<30} {'TOKENS':>12} {'SAVED':>7}")
    print("-" * 88)
    for e in entries:
        has_content = "📦" if e.messages is not None else "  "
        ts = e.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        pm = f"{e.provider}/{e.model}"
        if len(pm) > 29:
            pm = pm[:26] + "..."
        tokens_str = f"{e.input_tokens_raw}→{e.input_tokens_sent}"
        print(
            f"{has_content} {e.replay_id:<10} {ts:<20} {pm:<30} {tokens_str:>12} {e.savings_pct:>6.1f}%"
        )
    print(
        f"\n{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}  (📦 = content captured, eligible for replay)"
    )


def cmd_replay_show(args: CommandArgs) -> None:
    """Show details of a single replay entry."""
    store = _get_replay_store()
    e = store.get(args.id)
    if e is None:
        print(f"No entry found with id: {args.id}")
        raise SystemExit(1)
    ts = e.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    savings_tok = e.tokens_saved
    print(f"Replay Entry: {e.replay_id}")
    print(f"  Timestamp : {ts}")
    print(f"  Provider  : {e.provider}")
    print(f"  Model     : {e.model}")
    print(f"  Tokens raw: {e.input_tokens_raw:,}")
    print(f"  Tokens sent:{e.input_tokens_sent:,}")
    print(f"  Saved     : {savings_tok:,} ({e.savings_pct}%)")
    print(f"  Cost      : ${e.cost_usd:.6f}")
    if e.metadata:
        print(f"  Metadata  : {json.dumps(e.metadata)}")
    if e.messages is not None:
        print(f"\n  Messages  : {len(e.messages)} message(s) captured")
        if getattr(args, "show_messages", False):
            print(json.dumps(e.messages, indent=2))
    else:
        print("\n  Messages  : not captured (content capture was disabled)")
    if e.response is not None and getattr(args, "show_messages", False):
        print(f"\n  Response:\n{json.dumps(e.response, indent=2)}")


def _compress_messages(messages: Sequence[JsonObject], aggressive: bool = False) -> tuple[str, int]:
    """Compress message content and return (compressed_text, token_count)."""
    from .compression.processors.text import TextProcessor
    from .telemetry.tokens import count_tokens

    proc = TextProcessor(aggressive=aggressive)
    parts: list[JsonObject] = []
    for msg in messages:
        role_value = msg.get("role", "unknown")
        role = role_value if isinstance(role_value, str) else str(role_value)
        content = msg.get("content", "")
        if isinstance(content, list):
            # multi-part content (vision etc.)
            text_parts = [
                c.get("text", "")
                for c in content
                if isinstance(c, dict) and c.get("type") == "text"
            ]
            content = "\n".join(text_parts)
        if not isinstance(content, str):
            content = str(content)
        compressed = proc.process(content) if content else ""
        parts.append({"role": role, "content": compressed})

    combined = json.dumps(parts)
    return combined, count_tokens(combined)


def cmd_replay_run(args: CommandArgs) -> None:
    """Re-run a captured session with different settings (zero API cost)."""
    from .telemetry.tokens import count_tokens

    store = _get_replay_store()
    e = store.get(args.id)
    if e is None:
        print(f"No entry found with id: {args.id}")
        raise SystemExit(1)

    if e.messages is None:
        print(f"Entry {args.id} has no captured messages — cannot replay.")
        print("Enable content capture (proxy content-capture=true) to record messages.")
        raise SystemExit(1)

    model_label = args.model or e.model
    aggressive = getattr(args, "aggressive", False)
    no_compress = getattr(args, "no_compress", False)
    show_diff = getattr(args, "diff", False)

    replay_messages: list[JsonObject] = []
    for message in e.messages:
        if isinstance(message, dict):
            replay_messages.append(_json_object(message))
        else:
            replay_messages.append({"role": "unknown", "content": str(message)})

    raw_combined = json.dumps(replay_messages)
    raw_tokens = count_tokens(raw_combined)

    print(f"Replaying [{e.replay_id}] — original: {e.provider}/{e.model}")
    print(f"  Re-running as: {model_label}")
    print()

    if no_compress:
        result_tokens = raw_tokens
        mode_label = "no compression"
        compressed_messages = replay_messages
    else:
        _compressed, result_tokens = _compress_messages(replay_messages, aggressive=aggressive)
        try:
            parsed_messages = json.loads(_compressed)
            compressed_messages = (
                [_json_object(message) for message in parsed_messages if isinstance(message, dict)]
                if isinstance(parsed_messages, list)
                else replay_messages
            )
        except Exception:
            compressed_messages = replay_messages
        mode_label = "aggressive compression" if aggressive else "standard compression"

    saved = raw_tokens - result_tokens
    pct = round(saved / max(raw_tokens, 1) * 100, 1)
    orig_saved_pct = e.savings_pct

    print(f"  Mode          : {mode_label}")
    print(f"  Raw tokens    : {raw_tokens:,}")
    print(f"  Result tokens : {result_tokens:,}")
    print(f"  Saved         : {saved:,} ({pct}%)")
    print()
    print(
        f"  Original run  : {e.input_tokens_raw:,} → {e.input_tokens_sent:,} (-{orig_saved_pct}%)"
    )

    delta = e.input_tokens_sent - result_tokens
    if delta > 0:
        print(f"  Improvement   : -{delta:,} tokens vs original run ✓")
    elif delta < 0:
        print(f"  Delta vs orig : +{abs(delta):,} tokens (original was more compressed)")
    else:
        print("  Delta vs orig : no change")

    if show_diff and not no_compress:
        print()
        print("─── Diff (first message) ───")
        orig_first = replay_messages[0].get("content", "") if replay_messages else ""
        comp_first = compressed_messages[0].get("content", "") if compressed_messages else ""
        if isinstance(orig_first, list):
            orig_first = " ".join(c.get("text", "") for c in orig_first if isinstance(c, dict))
        if isinstance(comp_first, list):
            comp_first = " ".join(c.get("text", "") for c in comp_first if isinstance(c, dict))
        if not isinstance(orig_first, str):
            orig_first = str(orig_first)
        if not isinstance(comp_first, str):
            comp_first = str(comp_first)
        orig_lines = orig_first.splitlines()
        comp_lines = comp_first.splitlines()
        import difflib

        diff = list(
            difflib.unified_diff(
                orig_lines, comp_lines, fromfile="original", tofile="compressed", lineterm=""
            )
        )
        if diff:
            for line in diff[:60]:
                print(line)
            if len(diff) > 60:
                print(f"... ({len(diff) - 60} more diff lines)")
        else:
            print("(no textual diff — content identical)")


def cmd_replay_clear(args: CommandArgs) -> None:
    """Clear all entries from the replay store."""
    store = _get_replay_store()
    n = store.clear()
    print(f"Cleared {n} replay entr{'y' if n == 1 else 'ies'} from store.")


def _build_replay_parser(sub: Subparsers) -> None:
    p_replay = sub.add_parser("replay", help="List, inspect, and re-run captured sessions")
    rsub = p_replay.add_subparsers(dest="replay_cmd")

    # list
    p_list = rsub.add_parser("list", help="List recent captured sessions")
    p_list.add_argument("--limit", type=int, default=20, help="Max entries to show (default 20)")
    p_list.add_argument("--provider", default=None, help="Filter by provider")
    p_list.set_defaults(func=cmd_replay_list)

    # show
    p_show = rsub.add_parser("show", help="Show full details of a captured session")
    p_show.add_argument("id", help="Replay entry ID")
    p_show.add_argument(
        "--messages",
        dest="show_messages",
        action="store_true",
        help="Print captured message content",
    )
    p_show.set_defaults(func=cmd_replay_show)

    # run (default when an id is passed directly to 'replay')
    p_run = rsub.add_parser("run", help="Re-run a session with different settings (zero API cost)")
    p_run.add_argument("id", help="Replay entry ID")
    p_run.add_argument("--model", default=None, help="Label as a different model")
    p_run.add_argument(
        "--no-compress",
        dest="no_compress",
        action="store_true",
        help="Simulate sending uncompressed",
    )
    p_run.add_argument(
        "--aggressive", action="store_true", help="Apply aggressive compression mode"
    )
    p_run.add_argument(
        "--diff", action="store_true", help="Show unified diff of original vs compressed messages"
    )
    p_run.set_defaults(func=cmd_replay_run)

    # clear
    p_clear = rsub.add_parser("clear", help="Remove all entries from the replay store")
    p_clear.set_defaults(func=cmd_replay_clear)

    def _replay_dispatch(args: CommandArgs) -> None:
        # Default action when no subcommand given: show list
        args.limit = 20
        args.provider = None
        cmd_replay_list(args)

    p_replay.set_defaults(func=_replay_dispatch)


# ── Demo command ──────────────────────────────────────────────────────────────


def _build_demo_parser(sub: Subparsers) -> None:
    # ── Recipe SDK ─────────────────────────────────────────────────────────────
    p_recipe = sub.add_parser(
        "recipe", help="Custom recipe development tooling (create/test/validate/benchmark)"
    )
    rsub2 = p_recipe.add_subparsers(dest="recipe_cmd", required=False)

    # recipe list
    p_rlist = rsub2.add_parser("list", help="List baked-in OSS compression recipes")
    p_rlist.add_argument(
        "--category",
        default=None,
        help="Filter by category (general, python, javascript, markdown, config, common_patterns)",
    )
    p_rlist.set_defaults(func=cmd_recipe_list)

    # recipe create
    p_rcreate = rsub2.add_parser("create", help="Scaffold a new custom recipe YAML file")
    p_rcreate.add_argument("name", help="Recipe name (e.g. my-legal-cleanup)")
    p_rcreate.add_argument(
        "--output-dir",
        default=".",
        metavar="DIR",
        help="Directory to write the recipe file (default: current dir)",
    )
    p_rcreate.add_argument(
        "--category",
        default="general",
        help="Recipe category: python, markdown, legal, medical, etc.",
    )
    p_rcreate.add_argument("--description", default="", help="Short description")
    p_rcreate.add_argument(
        "--match-mode",
        default="extension",
        help="Pattern match mode: any|extension|filename|content|path_pattern",
    )
    p_rcreate.add_argument(
        "--ext", default="txt", help="File extension hint (for extension match mode)"
    )
    p_rcreate.add_argument(
        "--domain-example",
        default=None,
        metavar="DOMAIN",
        help="Use a domain-specific template: legal | medical",
    )
    p_rcreate.set_defaults(func=cmd_recipe_create)

    # recipe validate
    p_rvalidate = rsub2.add_parser("validate", help="Validate a recipe YAML against the schema")
    p_rvalidate.add_argument("file", help="Path to recipe YAML file")
    p_rvalidate.set_defaults(func=cmd_recipe_validate)

    # recipe test
    p_rtest = rsub2.add_parser("test", help="Test a recipe against sample input")
    p_rtest.add_argument("file", help="Path to recipe YAML file")
    p_rtest.add_argument("--input-text", default=None, help="Raw text to test against")
    p_rtest.add_argument(
        "--input-file", default=None, metavar="FILE", help="Path to a file to use as test input"
    )
    p_rtest.add_argument(
        "--filename-hint",
        default="",
        metavar="FILENAME",
        help="Filename to check pattern matching against (e.g. script.py)",
    )
    p_rtest.set_defaults(func=cmd_recipe_test)

    # recipe benchmark
    p_rbench = rsub2.add_parser(
        "benchmark", help="Benchmark compression ratio and speed for a recipe"
    )
    p_rbench.add_argument("file", help="Path to recipe YAML file")
    p_rbench.add_argument(
        "--samples-file",
        default=None,
        metavar="FILE",
        help="JSON file with list of sample strings (default: auto-generated)",
    )
    p_rbench.add_argument(
        "--runs", type=int, default=5, help="Repetitions per sample for timing (default: 5)"
    )
    p_rbench.set_defaults(func=cmd_recipe_benchmark)
    p_recipe.set_defaults(
        func=_bare_help(
            "recipe",
            "Custom recipe development tooling",
            ["list", "create", "validate", "test", "benchmark"],
        )
    )

    # ── Demo ───────────────────────────────────────────────────────────────────
    p_demo = sub.add_parser("demo", help="Show OSS compression recipes and apply to sample input")
    p_demo.add_argument("--list", action="store_true", help="List all 50 baked-in recipes")
    p_demo.add_argument(
        "--category",
        default=None,
        help="Filter by category (general, python, javascript, markdown, config, common_patterns)",
    )
    p_demo.add_argument("--recipe", default=None, help="Show details for a specific recipe by name")
    p_demo.add_argument("--file", default=None, help="Show which recipes match a given file path")
    p_demo.add_argument(
        "--seed",
        action="store_true",
        help="Populate dashboard with 500 realistic demo events (24h window)",
    )
    p_demo.add_argument(
        "--seed-count",
        type=int,
        default=500,
        metavar="N",
        help="Number of demo events to generate (default: 500)",
    )
    p_demo.add_argument(
        "--seed-hours", type=int, default=24, metavar="H", help="Time window in hours (default: 24)"
    )
    p_demo.add_argument(
        "--clear", action="store_true", help="Remove all demo data from telemetry storage"
    )
    p_demo.set_defaults(func=cmd_demo)


def _run_compression_demo() -> None:
    """Show offline compression on a realistic DevOps agent conversation fixture."""
    from tokenpak.compression.pipeline import CompressionPipeline

    # Fixture: DevOps agent diagnosing a startup failure.
    # Savings drivers: dedup (config file read twice), alias (long hostnames / paths 3+ times).
    # Content is original, written for this demo — no third-party rights.
    _CONFIG_YAML = (
        "# /etc/myapp/config.yaml\n"
        "database:\n"
        "  host: postgres-primary.internal.company.com\n"
        "  port: 5432\n"
        "  name: myapp_production\n"
        "  pool_size: 20\n"
        "  connection_timeout: 30\n"
        "cache:\n"
        "  host: redis-cluster.internal.company.com\n"
        "  port: 6379\n"
        "  ttl: 3600\n"
        "  max_connections: 50\n"
        "logging:\n"
        "  level: INFO\n"
        "  file: /var/log/myapp/application.log\n"
        "  max_size: 100MB\n"
        "  rotate: daily\n"
        "app:\n"
        "  port: 8080\n"
        "  workers: 4\n"
        "  timeout: 60\n"
        "  environment: production"
    )

    _LOG_LINES = (
        "2026-04-17 06:01:02 INFO  myapp v2.4.1 starting up\n"
        "2026-04-17 06:01:02 INFO  Loading /etc/myapp/config.yaml\n"
        "2026-04-17 06:01:02 INFO  Config loaded: database.host=postgres-primary.internal.company.com\n"
        "2026-04-17 06:01:02 INFO  Config loaded: cache.host=redis-cluster.internal.company.com\n"
        "2026-04-17 06:01:03 INFO  Connecting to postgres-primary.internal.company.com:5432\n"
        "2026-04-17 06:01:03 ERROR Connection refused: postgres-primary.internal.company.com:5432\n"
        "2026-04-17 06:01:03 ERROR DB init failed — pool_size=20 pool could not be established\n"
        "2026-04-17 06:01:03 FATAL Startup aborted: see /var/log/myapp/application.log for details"
    )

    MESSAGES = [
        {
            "role": "system",
            "content": (
                "You are a DevOps support agent for myapp. Diagnose and resolve startup failures.\n"
                "When a service fails, always:\n"
                "1. Read /etc/myapp/config.yaml for configuration\n"
                "2. Check /var/log/myapp/application.log for errors\n"
                "3. Verify connectivity to postgres-primary.internal.company.com\n"
                "4. Verify connectivity to redis-cluster.internal.company.com\n"
                "Never restart without identifying root cause first."
            ),
        },
        {"role": "user", "content": "myapp failed to start this morning. Please diagnose."},
        {
            "role": "assistant",
            "content": (
                "I'll diagnose the startup failure. Reading /etc/myapp/config.yaml first.\n"
                '<tool_call>read_file("/etc/myapp/config.yaml")</tool_call>'
            ),
        },
        {"role": "tool", "content": _CONFIG_YAML},
        {
            "role": "assistant",
            "content": (
                "Config shows database.host=postgres-primary.internal.company.com and "
                "cache.host=redis-cluster.internal.company.com. Now checking "
                "/var/log/myapp/application.log.\n"
                '<tool_call>read_file("/var/log/myapp/application.log", tail=50)</tool_call>'
            ),
        },
        {"role": "tool", "content": _LOG_LINES},
        {
            "role": "assistant",
            "content": (
                "Root cause: /var/log/myapp/application.log shows connection refused to "
                "postgres-primary.internal.company.com:5432. The config at "
                "/etc/myapp/config.yaml looks correct. Check that the host is reachable "
                "and port 5432 is open."
            ),
        },
        {
            "role": "user",
            "content": "The host is reachable. Could the config be wrong? Please re-read /etc/myapp/config.yaml.",
        },
        {
            "role": "assistant",
            "content": (
                "Re-reading /etc/myapp/config.yaml to verify.\n"
                '<tool_call>read_file("/etc/myapp/config.yaml")</tool_call>'
            ),
        },
        {"role": "tool", "content": _CONFIG_YAML},  # second read → dedup
        {
            "role": "assistant",
            "content": (
                "/etc/myapp/config.yaml is unchanged. The connection_timeout is 30s and pool_size "
                "is 20. If postgres-primary.internal.company.com is reachable, verify that port "
                "5432 is not blocked and the myapp_production database exists. Also confirm "
                "redis-cluster.internal.company.com is accessible — startup requires both."
            ),
        },
    ]

    pipeline = CompressionPipeline(enable_instruction_table=False)
    result = pipeline.run(MESSAGES)

    saved = result.tokens_saved
    raw = result.tokens_raw
    after = result.tokens_after
    pct = result.savings_pct

    # Cost at Claude claude-sonnet-4-6 input list price ($3.00 / 1M tokens)
    cost_per_token = 3.00 / 1_000_000
    cost_saved = saved * cost_per_token

    W = 56

    def _row(label: str, value: str) -> None:
        # W chars total: │(1) + 2 spaces + label + pad + value + 2 spaces + │(1) = W
        pad = W - 6 - len(label) - len(value)
        pad = max(pad, 1)
        print(f"│  {label}{' ' * pad}{value}  │")

    print()
    print("┌" + "─" * (W - 2) + "┐")
    _row("TokenPak — Offline Fixture Demo", "")
    print("├" + "─" * (W - 2) + "┤")
    _row("Scenario", "DevOps agent (config + logs)")
    _row("Data source", "built-in sample fixture")
    _row("Savings drivers", "dedup + alias")
    print("├" + "─" * (W - 2) + "┤")
    _row("Original", f"{raw:,} tokens")
    _row("Compressed", f"{after:,} tokens")
    _row("Fixture delta", f"{saved:,} tokens  ({pct:.1f}%)")
    _row("Fixture cost delta", f"${cost_saved:.5f} per fixture")
    _row("Receipt status", "not a savings receipt")
    print("├" + "─" * (W - 2) + "┤")
    stages_str = ", ".join(result.stages_run)
    print("│  Stages: " + stages_str + " " * (W - 12 - len(stages_str)) + "│")
    print("└" + "─" * (W - 2) + "┘")
    print()
    # These used to open with `tokenpak serve` described as "zero-config",
    # which contradicted both setup's own instructions and doctor's
    # "no config → Run: tokenpak setup". The real first step is setup.
    print("  Measure this on your own traffic:")
    print("    tokenpak setup        → choose a profile and write config")
    print("    tokenpak start        → run the proxy")
    print("    tokenpak integrate    → point your client at it")
    print("    tokenpak savings      → your measured numbers, once traffic flows")
    print("    tokenpak demo --list  → browse built-in compression recipes")
    print()


def _print_oss_recipe_catalog(
    engine: "CompressionRecipeEngine", category: str | None = None
) -> None:
    """Print the baked-in OSS recipe catalog, optionally filtered by category."""
    summary = engine.summary()
    print("TokenPak — Baked-in Compression Recipes")
    print("=" * 50)
    print(f"Total recipes: {summary['total']}")
    print()

    categories = [category] if category else engine.categories()

    for cat in categories:
        recipes = engine.by_category(cat)
        if not recipes:
            print(f"  [no recipes in category '{cat}']")
            continue
        print(f"  ── {cat} ({len(recipes)}) ──")
        for r in recipes:
            hint = f"~{int(r.compression_hint * 100)}%" if r.compression_hint > 0 else "   "
            print(f"    {r.name:<45}  {hint}  {r.description[:60]}")
        print()

    print(
        "Use `tokenpak demo --recipe <name>` for details, "
        "or `tokenpak demo --file <path>` to see applicable recipes."
    )


def cmd_demo(args: CommandArgs) -> None:
    """Show OSS compression recipes and demonstrate recipe selection."""
    from tokenpak.compression.recipes import get_oss_engine

    engine = get_oss_engine()

    # ── Demo data seeding
    if args.seed:
        from tokenpak.telemetry.demo import seed_demo_data

        result = seed_demo_data(count=args.seed_count, hours=args.seed_hours)
        print(f"✅ Seeded {result['events']} demo events")
        print(f"   Cache hit rate: {result['cache_hit_rate'] * 100:.1f}%")
        print(f"   Total events now: {result['total_events']}")
        print(f"   Total cache-read: {result['cache_read_total']:,}")
        print()
        print("Dashboard should now show demo data with realistic patterns.")
        return

    if args.clear:
        """Clear all demo data from telemetry storage."""
        from tokenpak.telemetry.demo import clear_demo_data

        result = clear_demo_data()
        print(f"✅ Cleared {result['deleted_events']} demo events")
        print(f"   Remaining events: {result['remaining_events']}")
        if result["remaining_events"] == 0:
            print("   Dashboard is now empty (ready for real traffic)")
        return

    # ── Default: offline compression demo on sample prompt
    if (
        not getattr(args, "list", False)
        and not getattr(args, "category", None)
        and not getattr(args, "recipe", None)
        and not getattr(args, "file", None)
    ):
        _run_compression_demo()
        return

    # ── Single recipe detail
    if args.recipe:
        recipe = engine.get_recipe(args.recipe)
        if recipe is None:
            print(f"Recipe '{args.recipe}' not found.")
            print(f"Available: {', '.join(engine.list_recipes()[:5])} ...")
            return
        print(f"┌─ Recipe: {recipe.name}")
        print(f"│  Category   : {recipe.category}")
        print(f"│  Description: {recipe.description}")
        print(f"│  Match mode : {recipe.match_mode}")
        print(f"│  Compression: ~{int(recipe.compression_hint * 100)}% reduction expected")
        print("│  Operations :")
        for op in recipe.operations:
            op_type = op.get("type", "?")
            params = {k: v for k, v in op.items() if k != "type"}
            param_str = ", ".join(f"{k}={v!r}" for k, v in list(params.items())[:3])
            print(f"│    [{op_type}]  {param_str}")
        print("└──")
        return

    # ── File matching
    if args.file:
        print(f"Recipes applicable to: {args.file}")
        matches = engine.recipes_for_file(args.file)
        if not matches:
            print("  (none)")
        for r in matches:
            print(f"  {r.name:<45} [{r.category}]  ~{int(r.compression_hint * 100)}% savings")
        return

    # ── List all (optionally filtered by category)
    _print_oss_recipe_catalog(engine, category=args.category)


# ── Recipe SDK CLI commands ────────────────────────────────────────────────────


def cmd_recipe_list(args: CommandArgs) -> None:
    """List baked-in OSS compression recipes."""
    from tokenpak.compression.recipes import get_oss_engine

    engine = get_oss_engine()
    _print_oss_recipe_catalog(engine, category=args.category)


def cmd_recipe_create(args: CommandArgs) -> None:
    """Scaffold a new custom recipe file."""
    from tokenpak.compression.recipe_sdk import RecipeSDK

    sdk = RecipeSDK()
    out = sdk.create(
        args.name,
        output_dir=args.output_dir,
        category=args.category or "general",
        description=args.description or "",
        match_mode=args.match_mode or "extension",
        ext=args.ext or "txt",
        domain_example=args.domain_example,
    )
    print(f"✅ Recipe scaffolded: {out}")
    print(f"   Next: tokenpak recipe validate {out}")
    print(f"         tokenpak recipe test {out}")


def cmd_recipe_validate(args: CommandArgs) -> None:
    """Validate a recipe YAML file against the schema."""
    from tokenpak.compression.recipe_sdk import RecipeSDK, RecipeValidationError

    sdk = RecipeSDK()
    try:
        warnings = sdk.validate(args.file)
    except RecipeValidationError as exc:
        print(f"❌ Validation FAILED: {exc}")
        raise SystemExit(1)
    if warnings:
        print(f"⚠️  Validation passed with {len(warnings)} warning(s):")
        for w in warnings:
            print(f"   • {w}")
    else:
        print(f"✅ Recipe '{args.file}' is valid — no issues found.")


def cmd_recipe_test(args: CommandArgs) -> None:
    """Test a recipe against sample input and show compression result."""
    from tokenpak.compression.recipe_sdk import RecipeSDK, RecipeValidationError

    sdk = RecipeSDK()
    try:
        result = sdk.test(
            args.file,
            input_text=args.input_text,
            input_file=args.input_file,
            filename_hint=args.filename_hint or "",
        )
    except RecipeValidationError as exc:
        print(f"❌ Recipe validation error: {exc}")
        raise SystemExit(1)

    print(f"Recipe test: {args.file}")
    print("─" * 50)
    if result["warnings"]:
        for w in result["warnings"]:
            print(f"  ⚠️  {w}")
    print(
        f"  Pattern match  : {'✅ yes' if result['pattern_match'] else '❌ no (check pattern settings)'}"
    )
    print(f"  Filename hint  : {result['filename_hint']}")
    print(f"  Ops applied    : {', '.join(result['ops_applied']) or '(none)'}")
    print(f"  Input chars    : {result['input_chars']}")
    print(f"  Output chars   : {result['output_chars']}")
    ratio_pct = round(result["compression_ratio"] * 100, 1)
    print(f"  Compression    : {ratio_pct}% removed")
    if result.get("compression_hint") is not None:
        hint_pct = round(result["compression_hint"] * 100, 1)
        print(f"  Hint vs actual : {hint_pct}% expected → {ratio_pct}% actual")
    print()
    print("Output preview:")
    print("─" * 50)
    print(result["output_preview"])


def cmd_recipe_benchmark(args: CommandArgs) -> None:
    """Benchmark a recipe's compression ratio and throughput."""
    from tokenpak.compression.recipe_sdk import RecipeSDK, RecipeValidationError

    sdk = RecipeSDK()

    samples = None
    if args.samples_file:
        import json as _json

        raw = open(args.samples_file).read()
        try:
            loaded = _json.loads(raw)
            if isinstance(loaded, list):
                samples = [str(s) for s in loaded]
            else:
                samples = [raw]
        except Exception:
            samples = [raw]

    try:
        result = sdk.benchmark(args.file, samples=samples, runs=args.runs)
    except RecipeValidationError as exc:
        print(f"❌ Recipe validation error: {exc}")
        raise SystemExit(1)

    print(f"Benchmark: {result['recipe']}  [{result['category']}]")
    print("─" * 50)
    print(f"  Samples tested        : {result['samples_tested']}")
    print(f"  Runs per sample       : {result['runs_per_sample']}")
    print(f"  Total chars processed : {result['total_chars_processed']:,}")
    print()
    c = result["compression"]
    print(
        f"  Compression (mean)    : {round(c['mean'] * 100, 1)}%  "
        f"[min {round(c['min'] * 100, 1)}% – max {round(c['max'] * 100, 1)}%]"
    )
    if result["hint_vs_actual"]["hint"] is not None:
        hint_pct = round(result["hint_vs_actual"]["hint"] * 100, 1)
        actual_pct = round(result["hint_vs_actual"]["actual_mean"] * 100, 1)
        delta = actual_pct - hint_pct
        sign = "+" if delta >= 0 else ""
        print(f"  Hint vs actual        : {hint_pct}% → {actual_pct}%  ({sign}{delta:.1f}% delta)")
    t = result["timing_ms"]
    print(
        f"  Timing ms (mean)      : {t['mean']:.3f} ms  [min {t['min']:.3f} – max {t['max']:.3f}]"
    )


# ── run: Macro scheduler CLI ──────────────────────────────────────────────────


def cmd_run_cron(args: CommandArgs) -> None:
    """Schedule a macro to run on a cron expression."""
    from tokenpak.orchestration.macros.scheduler import schedule_cron

    scheduled = schedule_cron(
        name=args.name,
        cron_expr=args.cron,
        description=getattr(args, "description", ""),
    )
    print(f"✅ Scheduled '{args.name}' [id: {scheduled.id}]")
    print(f"   Cron:    {scheduled.schedule}")
    print(f"   Command: {scheduled.command}")


def cmd_run_at(args: CommandArgs) -> None:
    """Schedule a macro to run once at a given time."""
    from tokenpak.orchestration.macros.scheduler import schedule_at

    scheduled = schedule_at(
        name=args.name,
        run_at=args.at,
        description=getattr(args, "description", ""),
    )
    print(f"✅ Scheduled '{args.name}' [id: {scheduled.id}]")
    print(f"   At:      {scheduled.schedule}")
    print(f"   Command: {scheduled.command}")


def cmd_run_list_scheduled(args: CommandArgs) -> None:
    """List all scheduled macro runs."""
    from tokenpak.orchestration.macros.scheduler import list_scheduled

    schedules = list_scheduled()
    if not schedules:
        print("No scheduled macros.")
        return
    print(f"{'ID':<10} {'NAME':<25} {'TYPE':<6} {'SCHEDULE':<25} {'COMMAND'}")
    print("-" * 90)
    for s in schedules:
        print(f"{s.id:<10} {s.name:<25} {s.schedule_type:<6} {s.schedule:<25} {s.command}")


def cmd_run_cancel(args: CommandArgs) -> None:
    """Cancel a scheduled macro run."""
    from tokenpak.orchestration.macros.scheduler import cancel_schedule

    ok = cancel_schedule(args.id)
    if ok:
        print(f"✅ Cancelled scheduled run: {args.id}")
    else:
        print(f"❌ No scheduled run found with id: {args.id}")


# ── diff: Context diff visualization ─────────────────────────────────────────


def cmd_diff(args: CommandArgs) -> int:
    """Show context diff: removed, compressed, retained blocks."""
    from tokenpak.cli.commands.diff import run_diff_cmd

    # Propagate the exit code: "unavailable" must not exit 0, or scripts read
    # the absence of a diff as a successful empty diff.
    return run_diff_cmd(args)


def _build_diff_parser(sub: Subparsers) -> None:
    p_diff = sub.add_parser(
        "diff", help="Show context changes (removed/compressed/retained blocks)"
    )
    p_diff.add_argument("--verbose", "-v", action="store_true", help="Show token counts per block")
    p_diff.add_argument("--json", action="store_true", help="Output as JSON")
    p_diff.add_argument(
        "--since", default=None, metavar="TIMESTAMP", help="Diff from specific time"
    )
    p_diff.set_defaults(func=cmd_diff)


# ── run: Macro scheduler CLI ──────────────────────────────────────────────────


def _build_run_parser(sub: Subparsers) -> None:
    p_run = sub.add_parser("run", help="Schedule and manage macro runs")
    rsub = p_run.add_subparsers(dest="run_cmd", required=False)

    # run <name> --cron "<expr>"
    p_cron = rsub.add_parser("cron", help="Schedule a macro on a cron expression")
    p_cron.add_argument("name", help="Macro name")
    p_cron.add_argument(
        "--cron", required=True, metavar="EXPR", help='Cron expression e.g. "0 9 * * 1-5"'
    )
    p_cron.add_argument("--description", default="", help="Optional description")
    p_cron.set_defaults(func=cmd_run_cron)

    # run <name> --at "<time>"
    p_at = rsub.add_parser("at", help="Schedule a one-shot macro run at a specific time")
    p_at.add_argument("name", help="Macro name")
    p_at.add_argument(
        "--at",
        required=True,
        metavar="TIME",
        help='Time string e.g. "2026-03-06 09:00" or "now + 1 hour"',
    )
    p_at.add_argument("--description", default="", help="Optional description")
    p_at.set_defaults(func=cmd_run_at)

    # run list --scheduled
    p_list = rsub.add_parser("list", help="List all scheduled macro runs")
    p_list.set_defaults(func=cmd_run_list_scheduled)

    # run cancel <id>
    p_cancel = rsub.add_parser("cancel", help="Cancel a scheduled macro run")
    p_cancel.add_argument("id", help="Schedule ID to cancel")
    p_cancel.set_defaults(func=cmd_run_cancel)
    p_run.set_defaults(func=lambda a: p_run.print_help())


# ── macro: Premade macro CLI ──────────────────────────────────────────────────


def cmd_macro_install(args: CommandArgs) -> None:
    """Install a premade macro."""
    from tokenpak.orchestration.macros.premade_macros import install_macro

    try:
        path = install_macro(args.name)
        print(f"✅ Installed macro '{args.name}' → {path}")
    except ValueError as e:
        print(f"❌ {e}")


def cmd_macro_run(args: CommandArgs) -> None:
    """Run a user-defined YAML macro or a premade macro."""
    import json as _json

    from tokenpak.orchestration.macros.engine import MacroEngine
    from tokenpak.orchestration.macros.premade_macros import (
        PREMADE_MACROS,
        format_macro_output,
        run_macro,
    )

    name = args.name
    dry_run = getattr(args, "dry_run", False)
    continue_on_error = getattr(args, "continue_on_error", False)
    raw_vars = getattr(args, "var", []) or []

    # Parse --var KEY=VALUE overrides
    runtime_vars: dict[str, str] = {}
    for kv in raw_vars:
        if "=" in kv:
            k, v = kv.split("=", 1)
            runtime_vars[k.strip()] = v.strip()
        else:
            print(f"⚠️  Ignoring malformed --var (expected KEY=VALUE): {kv}")

    # Try user-defined YAML macro first
    engine = MacroEngine()
    if engine.exists(name):
        result = engine.run(
            name,
            variables=runtime_vars or None,
            dry_run=dry_run,
            continue_on_error=continue_on_error,
        )
        if getattr(args, "json", False):
            print(_json.dumps(result.to_dict(), indent=2))
        else:
            print(result.format())
        return

    # Fall back to premade macros
    if name in PREMADE_MACROS:
        if dry_run:
            print(
                f"[DRY RUN] Would run premade macro '{name}' ({len(PREMADE_MACROS[name]['steps'])} steps)"
            )
            for step in PREMADE_MACROS[name]["steps"]:
                print(f"  🔍 {step['label']}: {step['cmd']}")
            return
        result_dict = run_macro(name)
        if getattr(args, "json", False):
            print(_json.dumps(result_dict, indent=2))
        else:
            print(format_macro_output(result_dict))
        return

    # Nothing found
    engine_macros = [m.name for m in engine.list()]
    premade = list(PREMADE_MACROS.keys())
    all_names = sorted(set(engine_macros + premade))
    print(f"❌ Unknown macro: '{name}'.")
    if all_names:
        print(f"   Available: {', '.join(all_names)}")


def cmd_macro_list(args: CommandArgs) -> None:
    """List all available macros (premade + user-defined YAML)."""
    from tokenpak.orchestration.macros.engine import MacroEngine
    from tokenpak.orchestration.macros.premade_macros import list_macros

    print(f"{'NAME':<25} {'TYPE':<10} DESCRIPTION")
    print("-" * 75)

    # Premade macros
    for premade_macro in list_macros():
        print(f"{premade_macro['name']:<25} {'premade':<10} {premade_macro['description']}")

    # User-defined YAML macros
    engine = MacroEngine()
    user_macros = engine.list()
    for user_macro in user_macros:
        print(f"{user_macro.name:<25} {'yaml':<10} {user_macro.description}")

    if not user_macros:
        print("  (no user macros — use `tokenpak macro create` to add one)")


def cmd_macro_create(args: CommandArgs) -> None:
    """Create a user-defined YAML macro."""
    from pathlib import Path as _Path

    from tokenpak.orchestration.macros.engine import MacroEngine

    engine = MacroEngine()

    # If --file provided, load YAML from file
    if getattr(args, "file", None):
        yaml_text = _Path(args.file).read_text()
        try:
            path = engine.create_from_yaml(yaml_text, overwrite=getattr(args, "overwrite", False))
            print(f"✅ Created macro from file → {path}")
        except Exception as e:
            print(f"❌ {e}")
        return

    # Build from CLI args
    name = getattr(args, "name", None)
    if not name:
        print("❌ --name is required (or use --file to load from YAML)")
        return

    # Parse --step "label:cmd" pairs
    raw_steps = getattr(args, "step", []) or []
    steps = []
    for i, s in enumerate(raw_steps, 1):
        if ":" in s:
            label, cmd = s.split(":", 1)
            steps.append({"name": f"step{i}", "label": label.strip(), "cmd": cmd.strip()})
        else:
            steps.append({"name": f"step{i}", "label": f"Step {i}", "cmd": s.strip()})

    if not steps:
        print("❌ At least one --step is required (e.g., --step 'Check status:tokenpak status')")
        return

    # Parse --var KEY=VALUE defaults
    raw_vars = getattr(args, "var", []) or []
    variables: dict[str, str] = {}
    for kv in raw_vars:
        if "=" in kv:
            k, v = kv.split("=", 1)
            variables[k.strip()] = v.strip()

    try:
        path = engine.create(
            name=name,
            steps=steps,
            description=getattr(args, "description", "") or "",
            variables=variables or None,
            continue_on_error=getattr(args, "continue_on_error", False),
            overwrite=getattr(args, "overwrite", False),
        )
        print(f"✅ Created macro '{name}' → {path}")
        print(f"   Run it with: tokenpak macro run {name}")
    except Exception as e:
        print(f"❌ {e}")


def cmd_macro_show(args: CommandArgs) -> None:
    """Show a macro definition."""
    import json as _json

    from tokenpak.orchestration.macros.engine import MacroEngine
    from tokenpak.orchestration.macros.premade_macros import PREMADE_MACROS

    name = args.name
    engine = MacroEngine()

    if engine.exists(name):
        macro = engine.show(name)
        if getattr(args, "json", False):
            print(_json.dumps(macro.to_dict(), indent=2))
        else:
            print(f"Name:         {macro.name}")
            print(f"Description:  {macro.description or '(none)'}")
            print(
                f"Fail mode:    {'continue-on-error' if macro.continue_on_error else 'fail-fast'}"
            )
            if macro.variables:
                print("Variables:")
                for k, v in macro.variables.items():
                    print(f"  {k} = {v}")
            print(f"Steps ({len(macro.steps)}):")
            for i, step in enumerate(macro.steps, 1):
                print(f"  {i}. [{step.name}] {step.label}")
                print(f"       $ {step.cmd}")
        return

    if name in PREMADE_MACROS:
        macro_data = PREMADE_MACROS[name]
        if getattr(args, "json", False):
            print(_json.dumps({"name": name, **macro_data}, indent=2))
        else:
            print(f"Name:        {name}  (premade)")
            print(f"Description: {macro_data['description']}")
            print(f"Steps ({len(macro_data['steps'])}):")
            for i, step in enumerate(macro_data["steps"], 1):
                print(f"  {i}. [{step['name']}] {step['label']}")
                print(f"       $ {step['cmd']}")
        return

    print(f"❌ Macro '{name}' not found.")


def cmd_macro_delete(args: CommandArgs) -> None:
    """Delete a user-defined YAML macro."""
    from tokenpak.orchestration.macros.engine import MacroEngine

    name = args.name
    engine = MacroEngine()

    if not engine.exists(name):
        print(f"❌ Macro '{name}' not found.")
        return

    if not getattr(args, "yes", False):
        confirm = input(f"Delete macro '{name}'? [y/N] ").strip().lower()
        if confirm != "y":
            print("Aborted.")
            return

    if engine.delete(name):
        print(f"✅ Deleted macro '{name}'.")
    else:
        print(f"❌ Failed to delete macro '{name}'.")


def cmd_macro_hooks(args: CommandArgs) -> None:
    """List, install, or check hook scripts."""
    from tokenpak.orchestration.macros.script_hooks import install_hook, list_hooks

    if args.hook_action == "list":
        hooks = list_hooks()
        print(f"{'HOOK':<20} {'EXISTS':<8} {'EXEC':<8} PATH")
        print("-" * 80)
        for name, info in hooks.items():
            exists = "✅" if info["exists"] else "—"
            executable = "✅" if info["executable"] else "—"
            print(f"{name:<20} {exists:<8} {executable:<8} {info['path']}")
    elif args.hook_action == "install":
        try:
            path = install_hook(args.hook_name)
            print(f"✅ Installed hook stub: {path}")
            print("   Edit this file to customize the hook behavior.")
        except ValueError as e:
            print(f"❌ {e}")


def _build_macro_parser(sub: Subparsers) -> None:
    p_macro = sub.add_parser(
        "macro", help="Premade macros, user-defined YAML macros, and script hooks"
    )
    msub = p_macro.add_subparsers(dest="macro_cmd", required=False)

    # macro list
    msub.add_parser("list", help="List all macros (premade + user-defined)").set_defaults(
        func=cmd_macro_list
    )

    # macro create
    p_create = msub.add_parser("create", help="Create a user-defined YAML macro")
    p_create.add_argument("--name", help="Macro name (e.g., my-deploy)")
    p_create.add_argument("--description", default="", help="Short description")
    p_create.add_argument(
        "--step",
        action="append",
        metavar="LABEL:CMD",
        help="Add a step (repeatable). Format: 'Label:command'",
    )
    p_create.add_argument(
        "--var",
        action="append",
        metavar="KEY=VALUE",
        help="Default variable (repeatable). Format: KEY=VALUE",
    )
    p_create.add_argument(
        "--continue-on-error",
        action="store_true",
        default=False,
        help="Keep running if a step fails (default: fail-fast)",
    )
    p_create.add_argument("--file", help="Load macro definition from a YAML file")
    p_create.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help="Overwrite an existing macro with the same name",
    )
    p_create.set_defaults(func=cmd_macro_create)

    # macro run <name>
    p_run = msub.add_parser("run", help="Run a macro (YAML or premade)")
    p_run.add_argument("name", help="Macro name")
    p_run.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print commands without executing them",
    )
    p_run.add_argument(
        "--continue-on-error",
        action="store_true",
        default=False,
        help="Keep running if a step fails",
    )
    p_run.add_argument(
        "--var", action="append", metavar="KEY=VALUE", help="Runtime variable override (repeatable)"
    )
    p_run.add_argument("--json", action="store_true", help="Output raw JSON")
    p_run.set_defaults(func=cmd_macro_run)

    # macro show <name>
    p_show = msub.add_parser("show", help="Show a macro definition")
    p_show.add_argument("name", help="Macro name")
    p_show.add_argument("--json", action="store_true", help="Output raw JSON")
    p_show.set_defaults(func=cmd_macro_show)

    # macro delete <name>
    p_delete = msub.add_parser("delete", help="Delete a user-defined YAML macro")
    p_delete.add_argument("name", help="Macro name")
    p_delete.add_argument(
        "--yes", "-y", action="store_true", default=False, help="Skip confirmation prompt"
    )
    p_delete.set_defaults(func=cmd_macro_delete)

    # macro install <name>  (premade shortcut)
    p_install = msub.add_parser("install", help="Install a premade macro as a local file")
    p_install.add_argument("name", help="Macro name (morning-standup, pre-deploy, weekly-report)")
    p_install.set_defaults(func=cmd_macro_install)

    # macro hooks list / install <name>
    p_hooks = msub.add_parser("hooks", help="Manage proxy lifecycle script hooks")
    hsub = p_hooks.add_subparsers(dest="hook_action", required=True)
    hsub.add_parser("list", help="List all hook scripts and their status").set_defaults(
        func=cmd_macro_hooks
    )
    p_hook_install = hsub.add_parser("install", help="Install a hook stub script")
    p_hook_install.add_argument(
        "hook_name", help="Hook name (on_request, on_response, on_error, on_budget_alert)"
    )
    p_hook_install.set_defaults(func=cmd_macro_hooks)
    p_macro.set_defaults(
        func=_bare_help(
            "macro",
            "Premade macros, user-defined YAML macros, and script hooks",
            ["list", "create", "run", "show", "delete", "install", "hooks"],
        )
    )


# ── Fingerprint commands ──────────────────────────────────────────────────────


def _build_fingerprint_parser(sub: Subparsers) -> None:
    p_fp = sub.add_parser("fingerprint", help="Fingerprint sync and cache management")
    fpsub = p_fp.add_subparsers(dest="fingerprint_cmd", required=False)

    # fingerprint sync
    p_sync = fpsub.add_parser("sync", help="Generate and sync a fingerprint, receive directives")
    p_sync.add_argument("text", nargs="?", help="Prompt text (or omit to read from stdin)")
    p_sync.add_argument("--file", "-f", dest="input_file", help="Read prompt from file")
    p_sync.add_argument("--messages", dest="messages_file", help="OpenAI messages JSON file")
    p_sync.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Show what would be sent without transmitting",
    )
    p_sync.add_argument("--privacy", choices=["minimal", "standard", "full"], default="standard")
    p_sync.add_argument("--ttl", type=int, default=3600, help="Cache TTL in seconds (default 3600)")
    p_sync.add_argument("--skip-cache", action="store_true", default=False)
    p_sync.add_argument("--json", dest="output_json", action="store_true", default=False)
    p_sync.set_defaults(func=cmd_fingerprint_sync)

    # fingerprint cache
    p_cache = fpsub.add_parser("cache", help="Show local directive cache status")
    p_cache.add_argument("--json", dest="output_json", action="store_true", default=False)
    p_cache.set_defaults(func=cmd_fingerprint_cache)

    # fingerprint clear-cache
    p_clear = fpsub.add_parser("clear-cache", help="Clear cached directives")
    p_clear.add_argument(
        "--id", dest="fp_id", default=None, help="Clear only this fingerprint ID (default: all)"
    )
    p_clear.add_argument(
        "--yes", "-y", action="store_true", default=False, help="Skip confirmation prompt"
    )
    p_clear.set_defaults(func=cmd_fingerprint_clear_cache)
    p_fp.set_defaults(func=lambda a: p_fp.print_help())


def cmd_fingerprint_sync(args: CommandArgs) -> None:
    import json as _json
    import sys as _sys
    from pathlib import Path as _Path

    from tokenpak.compression.fingerprinting.generator import FingerprintGenerator
    from tokenpak.compression.fingerprinting.privacy import PrivacyLevel, apply_privacy
    from tokenpak.compression.fingerprinting.sync import FingerprintSync

    gen = FingerprintGenerator()

    if getattr(args, "messages_file", None):
        with open(args.messages_file) as f:
            messages = _json.load(f)
        fingerprint = gen.generate_from_messages(messages)
    elif getattr(args, "input_file", None):
        content = _Path(args.input_file).read_text()
        fingerprint = gen.generate(content)
    elif getattr(args, "text", None):
        fingerprint = gen.generate(args.text)
    elif not _sys.stdin.isatty():
        content = _sys.stdin.read()
        fingerprint = gen.generate(content)
    else:
        print("Error: provide TEXT, --file, --messages, or pipe stdin.", file=_sys.stderr)
        _sys.exit(1)

    privacy_level = PrivacyLevel(args.privacy)
    client = FingerprintSync(ttl=args.ttl, privacy_level=privacy_level)

    if args.dry_run:
        payload = apply_privacy(fingerprint.to_dict(), privacy_level)
        if args.output_json:
            print(
                _json.dumps(
                    {
                        "dry_run": True,
                        "fingerprint_id": fingerprint.fingerprint_id,
                        "payload_preview": payload,
                    },
                    indent=2,
                )
            )
        else:
            print("── Dry Run ─────────────────────────────────")
            print(f"  Fingerprint ID : {fingerprint.fingerprint_id}")
            print(f"  Total tokens   : {fingerprint.total_tokens:,}")
            print(f"  Segments       : {fingerprint.segment_count}")
            print(f"  Privacy level  : {args.privacy}")
            print()
            print("  Payload that would be sent:")
            print(_json.dumps(payload, indent=4))
        return

    try:
        result = client.sync(fingerprint, dry_run=False, skip_cache=args.skip_cache)
    except PermissionError as e:
        print(f"✗ {e}", file=_sys.stderr)
        _sys.exit(1)

    if args.output_json:
        print(
            _json.dumps(
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

    print(f"{status_icon} Fingerprint synced  [{source_label}]")
    print(f"  ID         : {fingerprint.fingerprint_id}")
    print(f"  Tokens     : {fingerprint.total_tokens:,}")
    print(f"  Directives : {len(result.directives)}")

    if result.error:
        print(f"  Warning    : {result.error}", file=_sys.stderr)

    if result.directives:
        print()
        print("  Directives received:")
        for d in result.directives:
            print(f"    [{d.priority}] {d.action}  — {d.description or d.directive_id}")


def cmd_fingerprint_cache(args: CommandArgs) -> None:
    import json as _json

    from tokenpak.compression.fingerprinting.sync import FingerprintSync

    client = FingerprintSync()
    status = client.cache_status()

    if getattr(args, "output_json", False):
        print(_json.dumps(status, indent=2))
        return

    print("── Fingerprint Cache ────────────────────────")
    print(f"  Cache dir  : {status['cache_dir']}")
    print(f"  TTL        : {status['ttl_seconds']}s")
    print(f"  Entries    : {status['entries']}")
    print(f"  Valid      : {status.get('valid', 0)}")
    print(f"  Expired    : {status.get('expired', 0)}")


def cmd_fingerprint_clear_cache(args: CommandArgs) -> None:
    import sys as _sys

    from tokenpak.compression.fingerprinting.sync import FingerprintSync

    client = FingerprintSync()

    fp_id = getattr(args, "fp_id", None)
    yes = getattr(args, "yes", False)
    scope = f"fingerprint {fp_id}" if fp_id else "ALL cached directives"

    if not yes:
        confirm = input(f"Clear {scope}? [y/N] ").strip().lower()
        if confirm not in ("y", "yes"):
            print("Aborted.")
            _sys.exit(0)

    deleted = client.clear_cache(fingerprint_id=fp_id)
    print(f"✓ Cleared {deleted} cache file(s).")


# ── Validate command ──────────────────────────────────────────────────────────


def cmd_validate(args: CommandArgs) -> None:
    """Validate a TokenPak JSON file against the v1.0 protocol schema."""
    import json as _json
    import sys as _sys

    from tokenpak.core.validator import TokenPakValidator

    validator = TokenPakValidator()
    result = validator.validate_file(args.file, verbose=getattr(args, "verbose", False))

    if getattr(args, "json_output", False):
        print(_json.dumps(result.to_dict(), indent=2))
        _sys.exit(0 if result.valid else 1)

    # Human-readable output
    print("\nTokenPak Validator v1.0")
    print(f"File : {args.file}")
    print("─" * 50)

    if not result.issues:
        print("  ✓ No issues found.")
    else:
        for issue in result.issues:
            print(str(issue))

    print("─" * 50)
    print(f"{result.summary()}\n")

    if not result.valid:
        _sys.exit(1)


def _build_validate_parser(sub: Subparsers) -> argparse.ArgumentParser:
    p = sub.add_parser("validate", help="Validate a TokenPak JSON file against the v1.0 schema")
    p.add_argument("file", help="Path to the .json TokenPak file")
    p.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show quality hints in addition to errors/warnings",
    )
    p.add_argument(
        "--json", dest="json_output", action="store_true", help="Output validation result as JSON"
    )
    p.set_defaults(func=cmd_validate)
    return p


def _build_config_check_parser(sub: Subparsers) -> argparse.ArgumentParser:
    """Register 'tokenpak config-check' command."""
    p = sub.add_parser("config-check", help="Validate a proxy config file (JSON)")
    p.add_argument("file", help="Path to config file (JSON)")
    p.set_defaults(func=cmd_config_check)
    return p


def _build_validate_config_parser(sub: Subparsers) -> argparse.ArgumentParser:
    """Register 'tokenpak validate-config' command."""
    p = sub.add_parser(
        "validate-config",
        help="Validate TokenPak config file (YAML or JSON) with detailed checks",
    )
    p.add_argument("file", help="Path to config file (YAML or JSON)")
    p.set_defaults(func=cmd_validate_config_cli)
    return p


# ── Vault Health Management ───────────────────────────────────────────────────


def cmd_vault_health(args: CommandArgs) -> None:
    """Manage vault index health."""
    from .vault.vault_health import VaultHealth

    subcmd = getattr(args, "vault_health_cmd", None)

    if subcmd == "repair":
        try:
            health = VaultHealth()

            # Check if index exists
            if not health.index_path.exists():
                print(f"❌ Index not found: {health.index_path}")
                sys.exit(2)

            print("\nTOKENPAK  |  Vault Health")
            print("──────────────────────────────\n")

            # Get initial status
            status = health.get_status()
            print(f"Index: {health.index_path}")
            print(f"Status: {status}\n")

            # Check if stale
            is_stale = health.check_index_staleness()

            if not is_stale:
                print("✅ Index is current (no rebuild needed)")
                print("Exit code: 0\n")
                sys.exit(0)

            # Rebuild needed
            block_count = len(list(health.blocks_dir.iterdir()))
            print(f"Index is stale: {block_count} blocks found, index mismatch detected\n")
            print("Rebuilding index from blocks...")

            metrics = health.rebuild_index()

            print(f"✅ Rebuilt in {metrics['rebuild_time_seconds']:.2f} seconds")
            print(f"Entries: {metrics['index_entries']:,}")
            print(f"  Added: {metrics['entries_added']}")
            print(f"  Removed: {metrics['entries_removed']}")
            print(f"Index size: {metrics['index_size_bytes']:,} bytes")
            print("\nExit code: 1 (rebuilt)\n")
            sys.exit(1)

        except FileNotFoundError as e:
            print(f"❌ Error: {e}")
            sys.exit(2)
        except Exception as e:
            print(f"❌ Error during rebuild: {e}")
            sys.exit(2)

    else:
        print("Unknown vault subcommand. Use 'repair'.")
        sys.exit(1)


# ── Fleet Management ──────────────────────────────────────────────────────


def cmd_validate_config_cli(args: CommandArgs) -> None:
    """CLI wrapper for tokenpak validate-config."""
    from .cli.cli_validate_config import cmd_validate_config

    exit_code = cmd_validate_config(args)
    sys.exit(exit_code)


def cmd_config_check(args: CommandArgs) -> None:
    """Validate a proxy config file (JSON)."""
    import json

    from tokenpak.core.config_validator import ConfigValidator

    config_path = Path(args.file).expanduser()

    if not config_path.exists():
        print(f"ERROR: Config file not found: {config_path}")
        sys.exit(2)

    # Load JSON
    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in {config_path}: {e}")
        sys.exit(2)

    # Validate
    validator_factory = cast(Callable[[], ConfigValidator], ConfigValidator)
    validator = validator_factory()
    errors = validator.validate(config)

    if not errors:
        print(f"✓ Config is valid: {config_path}")
        sys.exit(0)

    # Print errors
    print(f"✗ Config validation failed ({len(errors)} error(s)):\n")
    for error in errors:
        print(f"  Field: {error.field}")
        print(f"    Expected: {error.expected}")
        print(f"    Actual: {error.actual}")
        print(f"    Message: {error.message}")
        print(f"    Fix: {error.suggestion}")
        print()

    sys.exit(1)


def cmd_fleet(args: CommandArgs) -> None:
    """Query and manage a fleet of TokenPak proxy instances."""
    from .cli.fleet import (
        interactive_add_machine,
        load_fleet_config,
        query_fleet,
        query_fleet_agent_rows,
        render_fleet_agent_table,
        render_fleet_json,
        render_fleet_table,
        save_fleet_config,
    )

    subcmd = getattr(args, "fleet_cmd", None)

    if subcmd == "init":
        # Interactive setup
        machines = load_fleet_config()
        print("╔═══════════════════════════════════════════════╗")
        print("║  TokenPak Fleet Configuration                 ║")
        print("╚═══════════════════════════════════════════════╝")

        if machines:
            print(f"\nCurrent fleet ({len(machines)} machine(s)):")
            for m in machines:
                print(f"  • {m.name} @ {m.host}:{m.port}")
            print()

        new_machine = interactive_add_machine(machines)
        if new_machine:
            machines.append(new_machine)
            save_fleet_config(machines)
            print("\n✅ Saved fleet config to ~/.tokenpak/fleet.yaml")

    else:
        # Default: show status table
        machines = load_fleet_config()

        if not machines:
            print("❌ No machines configured in fleet.")
            print("   Run: tokenpak fleet init")
            sys.exit(1)

        # Query all machines
        stats = query_fleet(machines)
        agent_rows, errors = query_fleet_agent_rows(machines)

        # Render output
        if getattr(args, "json", False):
            print(render_fleet_json(stats))
        elif getattr(args, "compact", False):
            print(render_fleet_table(stats, compact=True))
        else:
            if agent_rows:
                print(render_fleet_agent_table(agent_rows))
                if errors:
                    print("\n⚠️  Offline machines:")
                    for err in errors:
                        print(f"  - {err}")
            else:
                print(render_fleet_table(stats, compact=False))


def _build_vault_health_parser(sub: Subparsers) -> argparse.ArgumentParser:
    """Build the vault/vault-health command parser (vault-health is an alias for vault)."""
    p_vault = sub.add_parser(
        "vault",
        aliases=["vault-health"],
        help="Vault index health diagnostic and repair",
        description=(
            "Check the health of your vault index and repair stale or corrupted entries.\n"
            "The vault index stores compressed context blocks and metadata about requests.\n\n"
            "Subcommands:\n"
            "  repair     Check and rebuild stale vault index entries\n\n"
            "Example:\n"
            "  tokenpak vault repair    # Auto-fix corrupted entries\n"
            "  tokenpak vault-health repair  # Same via alias"
        ),
    )
    vaultsub = p_vault.add_subparsers(dest="vault_health_cmd", required=False)
    p_repair = vaultsub.add_parser("repair", help="Check and rebuild stale vault index")
    p_repair.set_defaults(func=cmd_vault_health)
    p_vault.set_defaults(func=cmd_vault_health)
    return p_vault


def _build_fleet_parser(sub: Subparsers) -> argparse.ArgumentParser:
    """Build the fleet command parser."""
    p_fleet = sub.add_parser("fleet", help="Manage and query multi-machine proxy fleet")

    p_fleet.add_argument("--json", action="store_true", help="Output as JSON")
    p_fleet.add_argument("--compact", action="store_true", help="Compact one-line output")

    fsub = p_fleet.add_subparsers(dest="fleet_cmd", required=False)

    # fleet init
    p_init = fsub.add_parser("init", help="Interactively configure fleet")
    p_init.set_defaults(func=cmd_fleet)

    p_fleet.set_defaults(func=cmd_fleet)
    return p_fleet


def _build_compress_parser(sub: Subparsers) -> argparse.ArgumentParser:
    """Build the compress command parser."""
    p_compress = sub.add_parser(
        "compress",
        help="Compress text/JSON/code content",
        description=(
            "Compress a piece of text, JSON, or code using TokenPak's compression.\n"
            "Shows token savings and compressed output.\n\n"
            "The default HTTP proxy does not invoke this body-compaction path.\n"
            "Use this command explicitly to compress arbitrary content.\n\n"
            "Example:\n"
            "  tokenpak compress < myfile.json\n"
            '  echo \'{"data": "...large JSON..."}\' | tokenpak compress --verbose'
        ),
    )
    p_compress.add_argument("--file", "-f", help="Input file path (reads from stdin if omitted)")
    p_compress.add_argument("--verbose", "-v", action="store_true", help="Show compression blocks")
    p_compress.add_argument("--json", action="store_true", help="Output as machine-readable JSON")

    def _compress_dispatch(args: CommandArgs) -> object:
        from tokenpak.cli.commands.compress_cmd import run_compress

        return run_compress(args)

    p_compress.set_defaults(func=_compress_dispatch)
    return p_compress


def _build_optimize_parser(sub: Subparsers) -> argparse.ArgumentParser:
    """Build the optimize command parser."""
    p_optimize = sub.add_parser(
        "optimize",
        help="Optimize prompts for better Prompt Packing efficiency",
        description=(
            "Analyze and optimize a prompt for better Prompt Packing efficiency.\n"
            "Suggests rewording and restructuring to reduce compressed token count.\n\n"
            "Example:\n"
            "  tokenpak optimize < myprompt.txt\n"
            "  tokenpak optimize --strategy aggressive myfile.txt"
        ),
    )
    p_optimize.add_argument("--file", "-f", help="Input file path (reads from stdin if omitted)")
    p_optimize.add_argument(
        "--strategy",
        choices=["conservative", "balanced", "aggressive"],
        default="balanced",
        help="Optimization aggressiveness (default: balanced)",
    )
    p_optimize.add_argument(
        "--show-diff", action="store_true", help="Show before/after token counts"
    )
    p_optimize.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Machine-readable JSON output",
    )

    def _optimize_dispatch(args: CommandArgs) -> object:
        # File-mode or stdin-mode → prompt analyzer.
        # No file and no stdin → delegate to the session-level analyzer
        # in tokenpak.cli.commands.optimize.
        import sys

        from tokenpak.cli.commands.optimize_prompt import run_optimize_prompt

        if getattr(args, "file", None) or not sys.stdin.isatty():
            return run_optimize_prompt(args)
        try:
            from tokenpak.cli.commands.optimize import run_optimize as _session

            _session(
                verbose=getattr(args, "verbose", False),
                as_json=getattr(args, "as_json", False),
                apply=False,
            )
            return 0
        except Exception as exc:  # pragma: no cover — fallback path
            print(f"optimize: session analyzer unavailable ({exc})", file=sys.stderr)
            print(
                "Tip: pass --file <path> to analyze a prompt file instead.",
                file=sys.stderr,
            )
            return 1

    p_optimize.set_defaults(func=_optimize_dispatch)
    return p_optimize


def _build_last_parser(sub: Subparsers) -> argparse.ArgumentParser:
    """Build the last command parser."""
    p_last = sub.add_parser(
        "last",
        help="Show details of last compressed request",
        description=(
            "Display details about the most recent request processed by the proxy.\n"
            "Includes compression ratio, token savings, latency, and provider info.\n\n"
            "Example:\n"
            "  tokenpak last                    # Show last request\n"
            "  tokenpak last --json             # Export as JSON\n"
            "  tokenpak last --limit 5          # Show last 5 requests"
        ),
    )
    p_last.add_argument("--limit", type=int, default=1, help="Show last N requests (default: 1)")
    p_last.add_argument("--json", action="store_true", help="Output as JSON")
    p_last.add_argument(
        "--verbose", "-v", action="store_true", help="Show full request/response bodies"
    )
    p_last.set_defaults(
        func=lambda args: print(
            "Use: tokenpak status to check proxy health and recent requests.\n"
            "Or: tokenpak dashboard for interactive monitoring."
        )
    )
    return p_last


def _build_prune_parser(sub: Subparsers) -> argparse.ArgumentParser:
    """Build the prune command parser."""
    p_prune = sub.add_parser(
        "prune",
        help="Prune old audit log entries",
        description=(
            "Remove low-priority blocks from the compression store.\n"
            "Blocks below the quality threshold are listed and optionally deleted.\n\n"
            "Example:\n"
            "  tokenpak prune                     # interactive review\n"
            "  tokenpak prune --dry-run           # preview without changes\n"
            "  tokenpak prune --auto              # prune without confirmation\n"
            "  tokenpak prune --threshold 0.3     # custom quality threshold"
        ),
    )
    p_prune.add_argument("--auto", action="store_true", help="Auto-prune without confirmation")
    p_prune.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="Show what would be pruned (no changes made)",
    )
    p_prune.add_argument(
        "--threshold",
        type=float,
        default=0.4,
        help="Quality score below which blocks are pruned (default: 0.4)",
    )
    p_prune.add_argument("--json", dest="as_json", action="store_true", help="Output raw JSON")

    def _cmd_prune(args: CommandArgs) -> None:
        from tokenpak.cli.commands.prune import run_prune

        run_prune(
            auto=args.auto,
            dry_run=args.dry_run,
            threshold=args.threshold,
            as_json=args.as_json,
        )

    p_prune.set_defaults(func=_cmd_prune)
    return p_prune


# --- Merged from cli.py (2026-03-25) ---


def cmd_monitor(args: CommandArgs) -> None:
    """Start the live monitor dashboard."""
    from tokenpak.telemetry.monitoring.server import run

    port = getattr(args, "port", 8767)
    run(port=port)


def cmd_cost_show_budget(args: CommandArgs) -> int:
    """Show budget status and spending progress."""
    import json

    try:
        from tokenpak.telemetry.costs.budget_tracker import BudgetTracker
    except ImportError:
        print("Budget tracking module not available.")
        return 1

    config_path = getattr(args, "config", None)
    from tokenpak.telemetry.costs.budget_tracker import BudgetConfigInput

    config = BudgetConfigInput()
    if config_path:
        try:
            with open(config_path) as f:
                config_data = json.load(f)
                raw_config = (
                    config_data.get("cost_budget") if isinstance(config_data, dict) else None
                )
                if isinstance(raw_config, dict):
                    daily_limit = raw_config.get("daily_limit")
                    weekly_limit = raw_config.get("weekly_limit")
                    enabled = raw_config.get("enabled")
                    if isinstance(daily_limit, (int, float)) and not isinstance(daily_limit, bool):
                        config["daily_limit"] = float(daily_limit)
                    if isinstance(weekly_limit, (int, float)) and not isinstance(
                        weekly_limit, bool
                    ):
                        config["weekly_limit"] = float(weekly_limit)
                    if isinstance(enabled, bool):
                        config["enabled"] = enabled
        except Exception as e:
            print(f"Error loading config: {e}")
            return 1

    tracker = BudgetTracker(config)
    summary = tracker.get_budget_summary()

    print("\n📊 TokenPak Budget Status\n" + "=" * 40)
    if not summary.get("enabled"):
        print("Budget tracking: DISABLED")
        print("  Set 'cost_budget.daily_limit' in tokenpak.json to enable.")
    else:
        if summary.get("daily_limit"):
            print(f"Daily limit:  ${summary['daily_limit']:.2f}")
        if summary.get("weekly_limit"):
            print(f"Weekly limit: ${summary['weekly_limit']:.2f}")
        print(f"Alert cooldown: {summary.get('alert_cooldown_minutes', 5):.0f} minutes")
        alerts = summary.get("last_alerts", {})
        if alerts:
            print("\nRecent Alerts:")
            for k, v in alerts.items():
                print(f"  • {k}: {v}")
        else:
            print("\nNo alerts triggered yet.")
    print("=" * 40 + "\n")
    return 0


# ── Retrieval CLI ─────────────────────────────────────────────────────────────


def _bm25_doc_count(bm25: object) -> int:
    """Get BM25 doc count without async."""
    try:
        blocks = getattr(bm25, "_blocks", [])
        return len(blocks) if isinstance(blocks, Sized) else 0
    except Exception:
        return 0


def _vec_doc_count(vec: object) -> int:
    """Get vector index doc count."""
    try:
        idx = getattr(vec, "_index", None)
        if idx is None:
            return 0
        count = getattr(idx, "ntotal", None)
        if isinstance(count, int):
            return count
        ids = getattr(idx, "_ids", [])
        return len(ids) if isinstance(ids, Sized) else 0
    except Exception:
        return 0


def cmd_retrieval_status(args: CommandArgs) -> None:
    """Show retrieval configuration and index stats."""
    from .vault.retrieval.base import HybridSearchConfig
    from .vault.retrieval.bm25 import BM25Retriever
    from .vault.retrieval.vector_local import LocalVectorRetriever

    cfg = HybridSearchConfig.from_env()
    json_out = getattr(args, "json", False)

    bm25 = BM25Retriever(vault_index_path=cfg.vault_index_path)
    vec = LocalVectorRetriever(
        model_name=cfg.vector_model,
        index_path=cfg.vector_index_path,
    )

    bm25_ok = bm25.is_available()
    bm25_count = _bm25_doc_count(bm25)
    vec_ok = vec.is_available()
    vec_count = _vec_doc_count(vec)
    status: JsonObject = {
        "config": {
            "bm25_weight": cfg.bm25_weight,
            "vector_weight": cfg.vector_weight,
            "vector_model": cfg.vector_model,
            "rrf_k": cfg.rrf_k,
            "top_k": cfg.top_k,
            "vault_index_path": cfg.vault_index_path,
            "vector_index_path": cfg.vector_index_path,
        },
        "bm25": {
            "available": bm25_ok,
            "doc_count": bm25_count,
        },
        "vector": {
            "available": vec_ok,
            "doc_count": vec_count,
        },
    }

    if json_out:
        import json as _json

        print(_json.dumps(status, indent=2))
        return

    # Human output
    print("🔍 TokenPak Retrieval Status")
    print()
    print("  Configuration:")
    print(f"    BM25 weight:      {cfg.bm25_weight}")
    print(f"    Vector weight:    {cfg.vector_weight}")
    print(f"    Vector model:     {cfg.vector_model}")
    print(f"    RRF k:            {cfg.rrf_k}")
    print(f"    Top-K:            {cfg.top_k}")
    if cfg.vault_index_path:
        print(f"    Vault index:      {cfg.vault_index_path}")
    if cfg.vector_index_path:
        print(f"    Vector index:     {cfg.vector_index_path}")
    print()
    print("  Retrievers:")
    print(f"    BM25:    {'✅ available' if bm25_ok else '❌ unavailable'}  ({bm25_count} docs)")
    print(
        f"    Vector:  {'✅ available' if vec_ok else '⚠️  unavailable (sentence-transformers not installed or no index)'}  ({vec_count} docs)"
    )
    print()
    if bm25_ok:
        mode = "hybrid" if vec_ok else "bm25-only"
    elif vec_ok:
        mode = "vector-only"
    else:
        mode = "⛔ no retrievers available"
    print(f"  Active mode: {mode}")


def cmd_retrieval_test(args: CommandArgs) -> None:
    """Test a query through all enabled retrievers."""
    import asyncio

    from .vault.retrieval.base import FusedResult, HybridSearchConfig
    from .vault.retrieval.hybrid import HybridRetriever

    cfg = HybridSearchConfig.from_env()
    query_text = args.query
    top_k = getattr(args, "top_k", cfg.top_k)
    json_out = getattr(args, "json", False)

    retriever = HybridRetriever(cfg)

    async def _run() -> list[FusedResult]:
        return await retriever.search(query_text, top_k=top_k)

    import time

    t0 = time.perf_counter()
    results = asyncio.run(_run())
    elapsed_ms = (time.perf_counter() - t0) * 1000

    if json_out:
        import json as _json

        out = {
            "query": query_text,
            "elapsed_ms": round(elapsed_ms, 2),
            "results": [
                {
                    "doc_id": r.doc_id,
                    "fused_score": r.fused_score,
                    "sources": list(r.source_results.keys()),
                    "content_preview": r.content[:200] if r.content else "",
                }
                for r in results
            ],
        }
        print(_json.dumps(out, indent=2))
        return

    print(f"🔍 Query: {query_text!r}")
    print(f"⏱  Elapsed: {elapsed_ms:.1f}ms  |  Results: {len(results)}")
    print()
    if not results:
        print("  (no results)")
        return
    for i, r in enumerate(results, 1):
        sources = ", ".join(r.source_results.keys()) if r.source_results else "bm25"
        preview = (r.content[:120] + "…") if len(r.content) > 120 else r.content
        print(f"  {i}. [{r.fused_score:.4f}] {r.doc_id}  ({sources})")
        if preview:
            print(f"     {preview}")
        print()


def _build_retrieval_parser(sub: Subparsers) -> argparse.ArgumentParser:
    """Build the retrieval command parser."""
    p_ret = sub.add_parser("retrieval", help="Inspect and test the hybrid retrieval system")
    p_ret.add_argument("--json", action="store_true", help="Output as JSON")
    rsub = p_ret.add_subparsers(dest="retrieval_cmd", required=False)

    # retrieval status
    p_status = rsub.add_parser("status", help="Show retrieval config and index stats")
    p_status.add_argument("--json", action="store_true", help="Output as JSON")
    p_status.set_defaults(func=cmd_retrieval_status)

    # retrieval test
    p_test = rsub.add_parser("test", help="Run a test query through all enabled retrievers")
    p_test.add_argument("query", help="Query string to test")
    p_test.add_argument(
        "--top-k", type=int, default=5, dest="top_k", help="Number of results (default: 5)"
    )
    p_test.add_argument("--json", action="store_true", help="Output as JSON")
    p_test.set_defaults(func=cmd_retrieval_test)

    p_ret.set_defaults(func=lambda a: p_ret.print_help())
    return p_ret


# ---------------------------------------------------------------------------
# Telemetry export command
# ---------------------------------------------------------------------------


def _build_telemetry_parser(sub: Subparsers) -> argparse.ArgumentParser:
    """Build the telemetry command parser with export subcommand."""
    p = sub.add_parser("telemetry", help="Telemetry data tools")
    tsub = p.add_subparsers(dest="telemetry_cmd", required=True)

    p_export = tsub.add_parser("export", help="Export telemetry event data to JSON or CSV")
    p_export.add_argument(
        "--format",
        dest="format",
        choices=["json", "csv"],
        default="json",
        help="Output format (default: json)",
    )
    p_export.add_argument(
        "--since",
        dest="since",
        default=None,
        metavar="YYYY-MM-DD",
        help="Only include events on or after this date",
    )
    p_export.add_argument(
        "--until",
        dest="until",
        default=None,
        metavar="YYYY-MM-DD",
        help="Only include events on or before this date",
    )
    p_export.add_argument(
        "--provider",
        dest="provider",
        default=None,
        help="Filter to a specific provider name",
    )
    p_export.set_defaults(func=_cmd_telemetry_export)

    p.set_defaults(func=lambda a: p.print_help())
    return p


def _cmd_telemetry_export(args: CommandArgs) -> None:
    from tokenpak.cli.commands.telemetry import cmd_telemetry_export

    cmd_telemetry_export(args)


if __name__ == "__main__":
    main()
