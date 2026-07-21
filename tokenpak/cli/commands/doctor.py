"""doctor command — diagnose common TokenPak issues."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sqlite3
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


class Colors:
    """ANSI color codes + emoji markers."""

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


def _proxy_get(path: str, port: int | None = None, timeout: int = 3) -> dict | None:
    """Fetch JSON from running proxy. Returns None if unreachable."""
    port = port or int(os.environ.get("TOKENPAK_PORT", "8766"))
    try:
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{port}{path}", timeout=timeout
        )
        return json.loads(resp.read())
    except Exception:
        return None


# Canonical proxy URL the routed Claude Code config is expected to point at.
# Mirrors install.PROXY_URL so the route check compares against the same value
# `tokenpak setup` writes. Overridable via TOKENPAK_PROXY_URL for non-default ports.
CANONICAL_PROXY_URL = os.environ.get("TOKENPAK_PROXY_URL", "http://127.0.0.1:8766")
_DISK_USAGE_MAX_ENTRIES = 5000
_DISK_USAGE_TIMEOUT_SECONDS = 0.25


@dataclass(frozen=True)
class _DiskUsageResult:
    total_bytes: int
    files: int
    entries: int
    truncated: bool = False
    reason: str = ""


def _measure_disk_usage(
    root: Path,
    *,
    max_entries: int = _DISK_USAGE_MAX_ENTRIES,
    timeout_s: float = _DISK_USAGE_TIMEOUT_SECONDS,
) -> _DiskUsageResult:
    """Return a bounded size estimate for TokenPak state."""
    deadline = time.monotonic() + max(0.0, timeout_s)
    pending = [root]
    total_bytes = 0
    files = 0
    entries = 0

    def _truncated(reason: str) -> _DiskUsageResult:
        return _DiskUsageResult(
            total_bytes=total_bytes,
            files=files,
            entries=entries,
            truncated=True,
            reason=reason,
        )

    while pending:
        if entries >= max_entries:
            return _truncated(f"entry limit {max_entries}")
        if time.monotonic() >= deadline:
            return _truncated(f"timeout {timeout_s:.2f}s")

        current = pending.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    entries += 1
                    if entries >= max_entries:
                        return _truncated(f"entry limit {max_entries}")
                    if time.monotonic() >= deadline:
                        return _truncated(f"timeout {timeout_s:.2f}s")
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total_bytes += entry.stat(follow_symlinks=False).st_size
                            files += 1
                        elif entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue

    return _DiskUsageResult(total_bytes=total_bytes, files=files, entries=entries)


def _claude_settings_path() -> Path:
    """Path to Claude Code's settings.json (~/.claude/settings.json)."""
    return Path.home() / ".claude" / "settings.json"


def _api_key_setup_detail() -> str:
    """Detailed no-key setup guidance shared by default and verbose output."""
    try:
        from tokenpak.cli.commands.setup import env_var_help

        examples = env_var_help("ANTHROPIC_API_KEY", "sk-...")
    except Exception:
        examples = "    export ANTHROPIC_API_KEY=sk-..."
    if "setx ANTHROPIC_API_KEY" not in examples:
        examples = "\n".join(
            [
                examples.rstrip(),
                '    setx ANTHROPIC_API_KEY "sk-..."',
                "    set ANTHROPIC_API_KEY=sk-...",
            ]
        )
    return (
        "Claude Code OAuth/session auth can use the local proxy with no direct API key.\n"
        "To add a direct provider key, set one before launching TokenPak:\n"
        f"{examples}"
    )


def _route_state() -> tuple[str, str | None]:
    """Resolve Claude Code → TokenPak proxy routing state, honestly.

    Reads ``~/.claude/settings.json`` ``env.ANTHROPIC_BASE_URL`` and compares it
    to the canonical proxy URL. Returns ``(state, base_url)`` where ``state`` is:

    - ``"active"``      — base URL points at the canonical TokenPak proxy.
    - ``"other"``       — base URL is set but points elsewhere (a non-TokenPak gateway).
    - ``"not routed"``  — no settings file / no ANTHROPIC_BASE_URL key.
    - ``"unknown"``     — settings file present but unreadable (never fabricate).

    Never raises. A corrupt/unreadable settings file yields ``"unknown"`` rather
    than a made-up state (truth-over-polish).
    """
    settings = _claude_settings_path()
    if not settings.exists():
        return ("not routed", None)
    try:
        data = json.loads(settings.read_text())
    except Exception:
        # File exists but is unreadable/corrupt — honest "unknown", not "not routed".
        return ("unknown", None)
    base_url = ""
    if isinstance(data, dict):
        env = data.get("env")
        if isinstance(env, dict):
            base_url = str(env.get("ANTHROPIC_BASE_URL", "") or "").strip()
    if not base_url:
        return ("not routed", None)

    def _norm(u: str) -> str:
        # Treat the loopback aliases as one host: a proxy at localhost:8766 and
        # 127.0.0.1:8766 is the same TokenPak proxy, so don't report a textual
        # mismatch as "other gateway".
        u = u.rstrip("/").lower()
        return u.replace("://localhost", "://127.0.0.1")

    if _norm(base_url) == _norm(CANONICAL_PROXY_URL):
        return ("active", base_url)
    return ("other", base_url)


def _update_state() -> tuple[str, str | None]:
    """Resolve "is an update available?" from the CACHED L1 check only.

    Reuses the L1 update-check cache (``_cli_core._read_update_cache``) — it does
    NOT issue a fresh blocking network probe (truth-over-polish + zero added
    latency in doctor). Returns ``(state, latest)`` where ``state`` is:

    - ``"available"`` — cached PyPI version is newer than the running version.
    - ``"current"``   — cached version is <= running version (up to date).
    - ``"unknown"``   — no usable cache (never checked yet, opted out, or the
                        cache is stale/empty); doctor reports ``Unknown`` rather
                        than forcing a network call.

    Never raises.
    """
    try:
        from tokenpak import _cli_core

        if _cli_core._update_nudge_opted_out():
            return ("unknown", None)
        _checked_at, cached_latest = _cli_core._read_update_cache()
        if not cached_latest:
            # No cached value (never run a launcher, or last check failed).
            return ("unknown", None)
        from packaging.version import Version as _PV

        from tokenpak import __version__ as current_ver

        if _PV(cached_latest) > _PV(current_ver):
            return ("available", cached_latest)
        return ("current", cached_latest)
    except Exception:
        return ("unknown", None)


def _proxy_state() -> str:
    """Resolve proxy run-state honestly, reusing the scope#1 cached status source.

    Delegates to ``menu_status.snapshot()`` — the cached, non-blocking,
    backoff-protected probe introduced by the menu-renderer foundation — so
    doctor and the interactive menu agree on proxy state and neither fabricates.
    Returns one of ``running`` / ``stopped`` / ``starting`` / ``unknown``.
    Falls back to ``unknown`` if that module is unavailable.
    """
    try:
        from tokenpak.cli.commands import menu_status

        return menu_status.snapshot(probe=True).state
    except Exception:
        return "unknown"


# Lifecycle-summary glyphs (within the doctor 5-emoji allow-list: ✅ ⚠️ ❌).
_GLYPH = {"green": "✅", "yellow": "⚠️ ", "red": "❌"}

# stdlib box-drawing (no Rich/new deps) — matches the menu receipt-card charset.
_BOX = {
    "tl": "┌", "tr": "┐", "bl": "└", "br": "┘",
    "h": "─", "v": "│", "ml": "├", "mr": "┤",
}


def build_lifecycle_summary(
    *,
    version: str,
    setup_present: bool,
    route_state: str,
    proxy_state: str,
    update_state: str,
    update_latest: str | None = None,
) -> str:
    """Build the compact lifecycle panel as a plain string (snapshot-testable).

    Pure string builder — takes already-resolved, honest values and renders a
    stdlib box-drawing panel. Each row carries a green/yellow/red glyph plus a
    single next-step hint. Unknown probes render ``Unknown`` (never fabricated).

    The five rows model the install → setup → route → proxy → update lifecycle:
      Installed · Setup · Routed · Proxy · Update
    """
    # (label, color, value, hint) — value/hint already honest.
    rows: list[tuple[str, str, str, str]] = []

    # Installed — the package is importable, so always green with the version.
    rows.append(("Installed", "green", f"v{version}", ""))

    # Setup — config.json present under the resolved home?
    if setup_present:
        rows.append(("Setup", "green", "config present", ""))
    else:
        rows.append(("Setup", "yellow", "no config", "Run: tokenpak setup"))

    # Routed — Claude Code → TokenPak proxy.
    if route_state == "active":
        rows.append(("Routed", "green", "active", ""))
    elif route_state == "other":
        rows.append(("Routed", "yellow", "other gateway", "Run: tokenpak setup"))
    elif route_state == "not routed":
        rows.append(("Routed", "yellow", "not routed", "Run: tokenpak setup"))
    else:  # unknown
        rows.append(("Routed", "yellow", "Unknown", "Check ~/.claude/settings.json"))

    # Proxy — running / stopped / starting / unknown.
    if proxy_state == "running":
        rows.append(("Proxy", "green", "running", ""))
    elif proxy_state == "starting":
        rows.append(("Proxy", "yellow", "starting", "wait for boot to finish"))
    elif proxy_state == "stopped":
        rows.append(("Proxy", "yellow", "stopped", "Run: tokenpak restart"))
    else:  # unknown
        rows.append(("Proxy", "yellow", "Unknown", "Run: tokenpak restart"))

    # Update — from the cached L1 check only.
    if update_state == "available":
        rows.append((
            "Update",
            "yellow",
            f"{update_latest} available" if update_latest else "available",
            "Run: tokenpak update",
        ))
    elif update_state == "current":
        rows.append(("Update", "green", "up to date", ""))
    else:  # unknown
        rows.append(("Update", "green", "Unknown", ""))

    # --- render ---------------------------------------------------------------
    # The status glyphs (✅/⚠️/❌) and the arrow (→) occupy 2 terminal columns
    # each while ``len()`` counts them as 1. Measure *display* width so the right
    # border lines up regardless of how many wide glyphs a row carries.
    _wide = set(_GLYPH.values()) | {"✅", "⚠️", "⚠️ ", "❌", "→"}

    def _disp_width(text: str) -> int:
        w = 0
        for ch in text:
            o = ord(ch)
            if o == 0xFE0F:
                # VARIATION SELECTOR-16: zero-width on its own; it promotes the
                # preceding base symbol to emoji (already counted as 2 below).
                continue
            if (
                0x1F300 <= o <= 0x1FAFF  # emoji blocks (✅ ❌ etc.)
                or 0x2600 <= o <= 0x27BF  # misc symbols + dingbats (⚠ ✅)
                or o == 0x2192  # → rightwards arrow renders 2-wide in most terms
            ):
                w += 2
            else:
                w += 1
        return w

    title = "TokenPak lifecycle"
    body_texts: list[str] = []
    for label, color, value, hint in rows:
        glyph = _GLYPH[color]
        text = f" {glyph} {label:<10} {value}"
        if hint:
            text += f"  →  {hint}"
        body_texts.append(text)

    widest = max([_disp_width(title) + 1] + [_disp_width(t) for t in body_texts])
    inner = max(widest + 2, 40)

    def _line(text: str) -> str:
        pad = inner - _disp_width(text)
        if pad < 0:
            pad = 0
        return f"{_BOX['v']}{text}{' ' * pad}{_BOX['v']}"

    out: list[str] = []
    out.append(f"{_BOX['tl']}{_BOX['h'] * inner}{_BOX['tr']}")
    out.append(_line(f" {title}"))
    out.append(f"{_BOX['ml']}{_BOX['h'] * inner}{_BOX['mr']}")
    for text in body_texts:
        out.append(_line(text))
    out.append(f"{_BOX['bl']}{_BOX['h'] * inner}{_BOX['br']}")
    return "\n".join(out)


def verify_integration_target(key: str, proxy_url: str) -> tuple[bool, str]:
    """Lightweight post-apply check for a named integration target.

    Called from ``tokenpak integrate`` guided form after --apply succeeds.
    Returns (ok, human_message). Does NOT run the full doctor suite.
    Unknown keys return (True, "no verify available") rather than raising.
    """
    from tokenpak.cli.commands.integrate import _find as _integrate_find

    integration = _integrate_find(key)
    if integration is None:
        return (True, f"no verify available for unknown key {key!r}")
    if integration.verify_fn is None:
        return (True, "no verify available — run tokenpak status to confirm")
    try:
        return integration.verify_fn(proxy_url)
    except Exception as exc:
        return (False, f"verify raised: {exc}")


def attribution_coverage(db_path) -> "tuple[int, int, float | None]":
    """Return ``(known, total, pct)`` attribution coverage over the requests
    ledger — the share of rows whose origin is genuinely known (non-empty
    ``attribution_source``). Internal gate measure only; NOT a public number.
    Degrades to ``(0, 0, None)`` when the db / table / column is absent."""
    import sqlite3 as _sq

    try:
        conn = _sq.connect(str(db_path), timeout=2.0)
    except Exception:
        return (0, 0, None)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(requests)")]
        if "attribution_source" not in cols:
            return (0, 0, None)
        total = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] or 0
        known = conn.execute(
            "SELECT COUNT(*) FROM requests "
            "WHERE attribution_source IS NOT NULL AND attribution_source != ''"
        ).fetchone()[0] or 0
        pct = (100.0 * known / total) if total else None
        return (known, total, pct)
    except Exception:
        return (0, 0, None)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def companion_hook_integrity() -> "list[tuple[str, str, str]]":
    """Inspect installed companion hook configs for silent-failure hazards.

    Returns ``(status, message, detail)`` tuples for _record(). Two hazards
    are checked across Claude Code (``~/.claude/settings.json``) and Codex
    (``~/.codex/hooks.json``) hook configs:

    - The bash hook variants shell out to the ``sqlite3`` CLI for their
      journal/budget writes and silently no-op when the binary is missing —
      evidence loss with no error surfaced anywhere. WARN when bash hooks
      are installed but the CLI is absent.
    - Hook commands referencing script paths that no longer exist (e.g. a
      relocated or partially removed install) fail on every prompt. WARN
      listing the missing paths.
    """
    import shutil as _shutil

    results: "list[tuple[str, str, str]]" = []
    hook_cmds: "list[str]" = []

    def _collect(hook_config) -> None:
        if not isinstance(hook_config, dict):
            return
        for groups in hook_config.values():
            if not isinstance(groups, list):
                continue
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for h in group.get("hooks", []) or []:
                    if isinstance(h, dict):
                        cmd = str(h.get("command", "") or "")
                        if "tokenpak" in cmd.lower():
                            hook_cmds.append(cmd)

    for cfg_path in (
        _claude_settings_path(),
        Path.home() / ".codex" / "hooks.json",
    ):
        try:
            if not cfg_path.exists():
                continue
            data = json.loads(cfg_path.read_text())
        except Exception:
            continue
        if isinstance(data, dict):
            _collect(data.get("hooks", {}))

    if not hook_cmds:
        results.append((
            "pass",
            "Companion hooks     not installed (no hook commands found)",
            "",
        ))
        return results

    missing: "list[str]" = []
    uses_bash_scripts = False
    for cmd in hook_cmds:
        for token in cmd.split():
            if token.endswith((".sh", ".py")) and ("/" in token or "\\" in token):
                if token.endswith(".sh"):
                    uses_bash_scripts = True
                if not Path(token).exists():
                    missing.append(token)

    healthy = True
    if missing:
        healthy = False
        results.append((
            "warn",
            f"Companion hooks     {len(missing)} installed hook script path(s) missing",
            "Missing: " + ", ".join(sorted(set(missing)))
            + " — these hooks fail on every prompt. Re-run the companion "
            "launcher (or reinstall) to repair the hook config.",
        ))

    if uses_bash_scripts and _shutil.which("sqlite3") is None:
        healthy = False
        results.append((
            "warn",
            "Companion hooks     sqlite3 CLI not found — bash hooks silently "
            "skip journal/budget writes",
            "The installed bash hook variants depend on the sqlite3 "
            "command-line tool for journal and budget writes and no-op "
            "without it. Install it (e.g. apt install sqlite3 / brew "
            "install sqlite) or switch to the python hook variant.",
        ))

    if healthy:
        results.append((
            "pass",
            f"Companion hooks     {len(hook_cmds)} hook command(s) installed — "
            "scripts present"
            + (", sqlite3 CLI available" if uses_bash_scripts else ""),
            "",
        ))
    return results


def run_doctor(
    fix: bool = False,
    output_json: bool = False,
    verbose: bool = False,
    claude_code: bool = False,
    lifecycle: bool = False,
) -> int:
    """Run all diagnostic checks. Returns exit code (0=pass, 1=warn, 2=errors).

    Args:
        fix: Auto-fix issues where possible.
        output_json: Output results as machine-readable JSON instead of human text.
        verbose: Show extra detail for each check.
        claude_code: Run Claude Code integration checks (ENABLE_TOOL_SEARCH, mode, IDE).
        lifecycle: Render only the compact lifecycle summary panel and exit.
    """
    from tokenpak import _paths

    # Resolve through _paths so doctor reports the canonical home (~/.tpk/) when
    # present, and surfaces the legacy fallback when the user hasn't migrated.
    tokenpak_dir = _paths.home()

    # --- Lifecycle summary (default-visible; --lifecycle = only this) ---------
    # Built from honest, already-resolved probes: route state, the cached L1
    # update check (no fresh network call), the scope#1 cached proxy probe, and
    # config presence. Unknown probes render "Unknown", never a fabricated state.
    def _lifecycle_panel() -> str:
        from tokenpak import __version__ as _ver

        route_st, _ = _route_state()
        upd_st, upd_latest = _update_state()
        return build_lifecycle_summary(
            version=_ver,
            setup_present=(tokenpak_dir / "config.json").exists(),
            route_state=route_st,
            proxy_state=_proxy_state(),
            update_state=upd_st,
            update_latest=upd_latest,
        )

    if lifecycle and not output_json:
        # --lifecycle: render the panel alone and exit (no full check suite).
        print()
        print(_lifecycle_panel())
        print()
        return 0

    if not output_json:
        print("\nTOKENPAK  |  Doctor")
        print("──────────────────────────────\n")
        # Default doctor run leads with the lifecycle summary so the operator
        # sees the install→setup→route→proxy→update story up front.
        print(_lifecycle_panel())
        print()

    counts = {"pass": 0, "warn": 0, "fail": 0}
    fixes: list[tuple[str, Path]] = []
    checks: list[dict] = []

    def _record(
        name: str,
        status: str,
        message: str,
        detail: str = "",
    ) -> None:
        """Record a check result and optionally print it."""
        checks.append(
            {"check": name, "status": status, "message": message, "detail": detail}
        )
        counts[status] += 1
        if not output_json:
            if status == "pass":
                print(Colors.ok(message))
            elif status == "warn":
                print(Colors.warn(message))
            else:
                print(Colors.fail(message))
            if verbose and detail:
                for line in detail.splitlines():
                    print(f"         {line}")

    # === Check 0: home-directory boundary ==========================================
    # Reports the resolved TokenPak home + flags legacy paths that should
    # be migrated. Cheap, side-effect-free, runs before everything else
    # so the operator sees the boundary state up front.
    if _paths.is_legacy_active():
        _record(
            "home_boundary",
            "warn",
            f"~/.tpk/ boundary    legacy: {_paths.legacy_home()}",
            detail=(
                "Using legacy ~/.tokenpak/ — canonical ~/.tpk/ is "
                "absent. Run `tokenpak home migrate` to copy your "
                "state to ~/.tpk/ (non-destructive, backup-first)."
            ),
        )
    elif _paths.has_legacy() and _paths.has_canonical():
        _record(
            "home_boundary",
            "warn",
            "~/.tpk/ boundary    canonical + legacy both present",
            detail=(
                f"Both {_paths.canonical_home()} and "
                f"{_paths.legacy_home()} exist. Canonical wins. "
                f"Once you've confirmed ~/.tpk/ is working, you can "
                f"remove the legacy directory manually."
            ),
        )
    else:
        _record(
            "home_boundary",
            "pass",
            f"~/.tpk/ boundary    {tokenpak_dir}",
        )

    # === Check 1: Proxy health with latency =====================================
    proxy_port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    health = _proxy_get("/health", port=proxy_port)
    if health is not None:
        latency = health.get("latency", {})
        p50 = latency.get("p50_latency_ms")
        p95 = latency.get("p95_latency_ms")
        p99 = latency.get("p99_latency_ms")
        samples = latency.get("samples", 0)
        outlier = latency.get("outlier_detected", False)
        mode = health.get("compilation_mode", "unknown")
        requests_total = health.get("stats", {}).get("requests", 0)

        if p50 is not None:
            if p95 is not None:
                latency_str = f"p50={p50:.0f}ms p95={p95:.0f}ms p99={p99:.0f}ms ({samples} samples)"
            else:
                latency_str = f"p50={p50:.0f}ms p99={p99:.0f}ms ({samples} samples)"
            if outlier:
                latency_str += " ⚠️ outlier detected (API congestion likely, not proxy)"
        else:
            latency_str = "no latency data"

        _record(
            "proxy_health",
            "pass",
            f"Proxy reachable     port {proxy_port} — {mode} mode, "
            f"{requests_total} reqs, {latency_str}",
            detail=f"compilation_mode={mode} requests={requests_total} p95={p95} p99={p99} outlier={outlier}",
        )

        # Attribution coverage — % of ledger rows with a genuinely known origin
        # (non-empty attribution_source). Internal gate measure; NOT a public
        # number. Informational only — does not fail/penalize the doctor exit.
        try:
            _mon_db = _paths.monitor_db("read")
        except Exception:
            _mon_db = None
        if _mon_db is not None:
            _cov_known, _cov_total, _cov_pct = attribution_coverage(_mon_db)
            if _cov_total > 0 and _cov_pct is not None:
                _record(
                    "attribution_coverage",
                    "pass",
                    f"Attribution         {_cov_pct:.0f}% known origin "
                    f"({_cov_known}/{_cov_total} reqs)",
                    detail=f"non-empty attribution_source on {_cov_known}/{_cov_total} requests rows",
                )
    else:
        # Fall back to TCP check
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1)
            rc = sock.connect_ex(("127.0.0.1", proxy_port))
            sock.close()
            if rc == 0:
                _record(
                    "proxy_health",
                    "warn",
                    f"Proxy port open     port {proxy_port} — /health unreachable",
                )
            else:
                _record(
                    "proxy_health",
                    "warn",
                    f"Proxy not running   port {proxy_port} — start with: tokenpak restart",
                )
        except Exception:
            _record(
                "proxy_health",
                "warn",
                f"Proxy not reachable port {proxy_port} — check failed",
            )

    # === Check 1b: Routing state (Claude Code → TokenPak proxy) ==================
    # Promoted into the DEFAULT run (previously only surfaced under --claude-code).
    # Derived from ~/.claude/settings.json env.ANTHROPIC_BASE_URL vs the canonical
    # proxy URL. Honest: a corrupt/unreadable settings file reports "unknown",
    # never a fabricated routed/not-routed verdict.
    _route_st, _route_url = _route_state()
    if _route_st == "active":
        _record(
            "routing",
            "pass",
            f"Routing             Claude Code → TokenPak proxy (active) — {_route_url}",
            detail=f"ANTHROPIC_BASE_URL={_route_url} matches canonical {CANONICAL_PROXY_URL}",
        )
    elif _route_st == "other":
        _record(
            "routing",
            "warn",
            f"Routing             Claude Code → other gateway (not TokenPak) — {_route_url}\n"
            "                    Fix: tokenpak setup",
            detail=f"ANTHROPIC_BASE_URL={_route_url} (not the canonical {CANONICAL_PROXY_URL})",
        )
    elif _route_st == "not routed":
        _record(
            "routing",
            "warn",
            "Routing             Claude Code → TokenPak proxy (not routed) — run: tokenpak setup",
            detail="No ANTHROPIC_BASE_URL in ~/.claude/settings.json (or file absent)",
        )
    else:  # unknown
        _record(
            "routing",
            "warn",
            "Routing             Unknown — ~/.claude/settings.json present but unreadable",
            detail="settings.json exists but could not be parsed; not fabricating a route verdict",
        )

    # === Check 2: DB path and row count =========================================
    # D5 (feed normalization): resolve via the canonical candidate chain
    # (_paths.monitor_db) instead of home()/monitor.db, so doctor reads the SAME
    # DB as status, _cli_core, and the proxy writer. home() alone bypasses the
    # chain — once ~/.tpk/ exists without a monitor.db, home() points there and
    # doctor would report "not found" while the proxy writes ~/.tokenpak/ or
    # ~/tokenpak/ (the latent split-brain). Falls back to home()/monitor.db only
    # if the resolver finds nothing (preserves the prior "not found" path).
    _resolved_db = _paths.monitor_db(mode="read")
    db_path = _resolved_db if _resolved_db is not None else (tokenpak_dir / "monitor.db")
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM requests")
            row_count = cur.fetchone()[0]
            conn.close()
            _record(
                "db_rowcount",
                "pass",
                f"Monitor DB          {db_path} — {row_count:,} rows",
                detail=f"path={db_path} rows={row_count}",
            )
        except Exception as exc:
            _record(
                "db_rowcount",
                "warn",
                f"Monitor DB          {db_path} — could not query: {exc}",
            )
    else:
        _record(
            "db_rowcount",
            "warn",
            f"Monitor DB          not found — {db_path} (starts populating on first request)",
        )

    # === Check 3: DB schema version =============================================
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            )
            has_table = cur.fetchone() is not None
            if has_table:
                cur.execute(
                    "SELECT version, applied_at, description FROM schema_version "
                    "ORDER BY version DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    ver, applied_at, desc = row
                    _record(
                        "db_schema_version",
                        "pass",
                        f"DB schema version   v{ver} — applied {applied_at[:10]}",
                        detail=f"version={ver} applied_at={applied_at} desc={desc}",
                    )
                else:
                    _record(
                        "db_schema_version",
                        "warn",
                        "DB schema version   schema_version table empty",
                    )
            else:
                _record(
                    "db_schema_version",
                    "warn",
                    "DB schema version   no schema_version table (legacy DB)",
                )
            conn.close()
        except Exception as exc:
            _record(
                "db_schema_version",
                "warn",
                f"DB schema version   could not check: {exc}",
            )

    # === Check 4: Budget controller row count ===================================
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='budget_alerts'"
            )
            has_budget = cur.fetchone() is not None
            if has_budget:
                cur.execute("SELECT COUNT(*) FROM budget_alerts")
                alert_count = cur.fetchone()[0]
                cur.execute(
                    "SELECT COUNT(*) FROM budget_alerts WHERE triggered = 1"
                )
                triggered_count = cur.fetchone()[0]
                if triggered_count > 0:
                    _record(
                        "budget_controller",
                        "warn",
                        f"Budget controller   {alert_count} alert rows, "
                        f"{triggered_count} triggered",
                        detail=f"total_alerts={alert_count} triggered={triggered_count}",
                    )
                else:
                    _record(
                        "budget_controller",
                        "pass",
                        f"Budget controller   {alert_count} alert rows, 0 triggered",
                        detail=f"total_alerts={alert_count} triggered=0",
                    )
            else:
                _record(
                    "budget_controller",
                    "warn",
                    "Budget controller   budget_alerts table not found (legacy DB)",
                )
            conn.close()
        except Exception as exc:
            _record(
                "budget_controller",
                "warn",
                f"Budget controller   could not check: {exc}",
            )

    # === Check 5: Shadow reader status/failures =================================
    if health is not None:
        sr = health.get("shadow_reader", {})
        sr_enabled = sr.get("enabled", False)
        sr_failures = sr.get("failures", 0)
        if sr_enabled:
            if sr_failures and sr_failures > 0:
                _record(
                    "shadow_reader",
                    "warn",
                    f"Shadow reader       enabled — {sr_failures} failure(s) recorded",
                    detail=str(sr),
                )
            else:
                _record(
                    "shadow_reader",
                    "pass",
                    "Shadow reader       enabled — no failures",
                    detail=str(sr),
                )
        else:
            _record(
                "shadow_reader",
                "warn",
                "Shadow reader       disabled (set TOKENPAK_SHADOW_READER=1)",
                detail=str(sr),
            )
    else:
        _record(
            "shadow_reader",
            "warn",
            "Shadow reader       unknown — proxy not reachable",
        )

    # === Check 6: Trace enabled =================================================
    trace_enabled = bool(
        os.environ.get("TOKENPAK_TRACE", "0").strip() not in ("0", "", "false", "False")
    )
    if trace_enabled:
        _record(
            "trace_enabled",
            "pass",
            "Trace mode          enabled (TOKENPAK_TRACE=1)",
        )
    else:
        _record(
            "trace_enabled",
            "pass",
            "Trace mode          disabled (TOKENPAK_TRACE not set — normal)",
        )

    # === Check 7: Vault index freshness =========================================
    # Primary: check from health endpoint, secondary: file mtime
    if health is not None:
        vi = health.get("vault_index", {})
        vi_available = vi.get("available", False)
        vi_blocks = vi.get("blocks", 0)
        vi_path = vi.get("path", "")

        if vi_available and vi_blocks > 0:
            # Also check file mtime
            index_path = tokenpak_dir / "index.json"
            if index_path.exists():
                age_hours = (time.time() - os.path.getmtime(index_path)) / 3600
                if age_hours > 24:
                    _record(
                        "vault_index",
                        "warn",
                        f"Vault index         {vi_blocks:,} blocks — "
                        f"stale ({age_hours:.1f}h old, run: tokenpak index)",
                        detail=f"path={vi_path} blocks={vi_blocks} age_hours={age_hours:.1f}",
                    )
                else:
                    _record(
                        "vault_index",
                        "pass",
                        f"Vault index         {vi_blocks:,} blocks — "
                        f"fresh ({age_hours:.1f}h old)",
                        detail=f"path={vi_path} blocks={vi_blocks} age_hours={age_hours:.1f}",
                    )
            else:
                _record(
                    "vault_index",
                    "pass",
                    f"Vault index         {vi_blocks:,} blocks — loaded from {vi_path}",
                    detail=f"path={vi_path} blocks={vi_blocks}",
                )
        elif not vi_available:
            _record(
                "vault_index",
                "warn",
                "Vault index         not available — run: tokenpak index <path>",
            )
        else:
            _record(
                "vault_index",
                "warn",
                "Vault index         0 blocks — run: tokenpak index <path>",
            )
    else:
        # Fallback: check file on disk
        index_path = tokenpak_dir / "index.json"
        if index_path.exists():
            try:
                data = json.loads(index_path.read_text())
                block_count = len(data.get("blocks", []))
                age_hours = (time.time() - os.path.getmtime(index_path)) / 3600
                if block_count > 0:
                    status = "warn" if age_hours > 24 else "pass"
                    age_note = f"stale ({age_hours:.1f}h)" if age_hours > 24 else f"{age_hours:.1f}h old"
                    _record(
                        "vault_index",
                        status,
                        f"Vault index         {index_path} — {block_count:,} blocks, {age_note}",
                    )
                else:
                    _record(
                        "vault_index",
                        "warn",
                        f"Vault index         {index_path} — 0 blocks (run: tokenpak index)",
                    )
            except json.JSONDecodeError:
                _record(
                    "vault_index",
                    "fail",
                    f"Vault index         {index_path} — invalid JSON",
                )
        else:
            _record(
                "vault_index",
                "warn",
                "Vault index         not found (run: tokenpak index <path>)",
            )

    # === Check 7b: Registered vault paths staleness ============================
    # Reads vault.yaml under the resolved TokenPak home + per-path index
    # health and warns when a registered directory's last rebuild is older
    # than expected interval × 2, when the path is missing, when metadata
    # is corrupt, or when the previous reindex failed. Manual schedules
    # don't warn solely on age. Does not fail unrelated checks.
    try:
        from tokenpak.vault import doctor_check as _vds03

        _vds03_findings, _vds03_err = _vds03.load_and_check()
        if _vds03_err is not None:
            _record(
                "vault_paths_staleness",
                "warn",
                f"Vault paths        {_vds03_err}",
                detail=_vds03_err,
            )
        elif not _vds03_findings:
            _record(
                "vault_paths_staleness",
                "pass",
                "Vault paths        no registered paths "
                "(register with: tokenpak vault add <path>)",
            )
        else:
            summary = _vds03.summarize(_vds03_findings)
            for f in _vds03_findings:
                _record(
                    f"vault_path:{f.status}",
                    f.severity,
                    f"Vault path         {f.message}",
                    detail=(
                        f"path={f.path} status={f.status} schedule={f.schedule} "
                        f"age_seconds={f.age_seconds} threshold_seconds={f.threshold_seconds} "
                        f"last_indexed={f.last_indexed} last_index_status={f.last_index_status}"
                    ),
                )
            warn_total = sum(
                summary.get(k, 0) for k in ("stale", "missing", "never", "corrupt", "failed")
            )
            if warn_total == 0:
                _record(
                    "vault_paths_summary",
                    "pass",
                    f"Vault paths        all {summary.get('ok', 0)} registered path(s) fresh",
                    detail=str(summary),
                )
            else:
                _record(
                    "vault_paths_summary",
                    "warn",
                    (
                        f"Vault paths        "
                        f"{summary.get('stale', 0)} stale / "
                        f"{summary.get('missing', 0)} missing / "
                        f"{summary.get('never', 0)} never / "
                        f"{summary.get('corrupt', 0)} corrupt / "
                        f"{summary.get('failed', 0)} failed "
                        f"(of {sum(summary.values())} registered)"
                    ),
                    detail=str(summary),
                )
    except Exception as exc:
        # Per spec: must not fail unrelated doctor checks. Bubble as a warn.
        _record(
            "vault_paths_staleness",
            "warn",
            f"Vault paths        check failed: {exc}",
            detail=str(exc),
        )

    # === Check 8: Recent error rate from last 100 requests ======================
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM ("
                "  SELECT id FROM requests ORDER BY id DESC LIMIT 100"
                ")"
            )
            total_recent = cur.fetchone()[0]
            if total_recent > 0:
                cur.execute(
                    "SELECT COUNT(*) FROM requests WHERE id IN ("
                    "  SELECT id FROM requests ORDER BY id DESC LIMIT 100"
                    ") AND (status_code >= 400 OR status_code = 0)"
                )
                error_count = cur.fetchone()[0]
                error_rate = (error_count / total_recent) * 100
                if error_count == 0:
                    _record(
                        "recent_error_rate",
                        "pass",
                        f"Recent error rate   0/{total_recent} errors (0.0%) — last 100 requests",
                        detail=f"errors={error_count} total={total_recent} rate=0.0%",
                    )
                elif error_rate < 5:
                    _record(
                        "recent_error_rate",
                        "warn",
                        f"Recent error rate   {error_count}/{total_recent} errors "
                        f"({error_rate:.1f}%) — last 100 requests",
                        detail=f"errors={error_count} total={total_recent} rate={error_rate:.1f}%",
                    )
                else:
                    _record(
                        "recent_error_rate",
                        "fail",
                        f"Recent error rate   {error_count}/{total_recent} errors "
                        f"({error_rate:.1f}%) — last 100 requests (HIGH)",
                        detail=f"errors={error_count} total={total_recent} rate={error_rate:.1f}%",
                    )
            else:
                _record(
                    "recent_error_rate",
                    "warn",
                    "Recent error rate   no requests in DB yet",
                )
            conn.close()
        except Exception as exc:
            _record(
                "recent_error_rate",
                "warn",
                f"Recent error rate   could not compute: {exc}",
            )
    else:
        _record(
            "recent_error_rate",
            "warn",
            "Recent error rate   monitor.db not found",
        )

    # === Check 9: Token savings summary from last 100 requests ==================
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            cur = conn.cursor()
            cur.execute(
                "SELECT "
                "  COUNT(*) as total, "
                "  SUM(input_tokens) as total_input, "
                "  SUM(compressed_tokens) as total_sent, "
                "  SUM(input_tokens - compressed_tokens) as tokens_saved, "
                "  SUM(estimated_cost) as total_cost "
                "FROM ("
                "  SELECT input_tokens, compressed_tokens, estimated_cost "
                "  FROM requests "
                "  WHERE compressed_tokens IS NOT NULL AND input_tokens > 0 "
                "  ORDER BY id DESC LIMIT 100"
                ")"
            )
            row = cur.fetchone()
            conn.close()
            if row and row[0] and row[0] > 0:
                total, total_input, total_sent, tokens_saved, total_cost = row
                tokens_saved = tokens_saved or 0
                total_input = total_input or 0
                pct_saved = (tokens_saved / total_input * 100) if total_input > 0 else 0
                cost_str = f"${total_cost:.4f}" if total_cost else "$0.0000"
                _record(
                    "token_savings",
                    "pass",
                    f"Token savings       {tokens_saved:,} saved ({pct_saved:.1f}%) "
                    f"— {total} reqs, cost {cost_str}",
                    detail=(
                        f"total_input={total_input:,} sent={total_sent:,} "
                        f"saved={tokens_saved:,} ({pct_saved:.1f}%) cost={cost_str}"
                    ),
                )
            else:
                _record(
                    "token_savings",
                    "warn",
                    "Token savings       no compressed request data yet",
                )
        except Exception as exc:
            _record(
                "token_savings",
                "warn",
                f"Token savings       could not compute: {exc}",
            )
    else:
        _record(
            "token_savings",
            "warn",
            "Token savings       monitor.db not found",
        )

    # === Check 10: Env var conflicts ============================================
    conflict_pairs = [
        ("ANTHROPIC_API_KEY", "ANTHROPIC_OAUTH_TOKEN"),
        ("TOKENPAK_PORT", None),  # single: check if overriding default
    ]
    env_issues: list[str] = []

    # Check for both proxy port env vs default
    port_env = os.environ.get("TOKENPAK_PORT", "")
    if port_env and port_env != "8766":
        env_issues.append(f"TOKENPAK_PORT={port_env} (non-default; expected 8766)")

    # Check known conflict combos
    anth_key = os.environ.get("ANTHROPIC_API_KEY", "")
    anth_oauth = os.environ.get("ANTHROPIC_OAUTH_TOKEN", "")
    anth_oauth2 = os.environ.get("ANTHROPIC_OAUTH_TOKEN2", "")
    if anth_key and (anth_oauth or anth_oauth2):
        env_issues.append(
            "ANTHROPIC_API_KEY set alongside ANTHROPIC_OAUTH_TOKEN — "
            "may cause auth conflicts"
        )

    # Check for conflicting upstream overrides
    if os.environ.get("TOKENPAK_BASE_URL") and os.environ.get("ANTHROPIC_BASE_URL"):
        env_issues.append(
            "Both TOKENPAK_BASE_URL and ANTHROPIC_BASE_URL set — potential routing conflict"
        )

    if env_issues:
        issues_str = "; ".join(env_issues)
        _record(
            "env_conflicts",
            "warn",
            f"Env var conflicts   {len(env_issues)} issue(s) detected",
            detail="\n".join(env_issues),
        )
        if verbose and not output_json:
            for issue in env_issues:
                print(f"         → {issue}")
    else:
        _record(
            "env_conflicts",
            "pass",
            "Env var conflicts   none detected",
        )

    # === Legacy checks (kept for compatibility) =================================

    # Python version
    v = sys.version_info
    py_version = f"{v.major}.{v.minor}.{v.micro}"
    if v >= (3, 10):
        _record("python_version", "pass", f"Python version      {py_version} — OK")
    else:
        _record(
            "python_version",
            "fail",
            f"Python version      {py_version} — requires ≥3.10",
        )

    # Config file
    config_path = tokenpak_dir / "config.json"
    if config_path.exists():
        try:
            with open(config_path) as f:
                json.load(f)
            _record("config_file", "pass", f"Config file         {config_path} — valid")
        except json.JSONDecodeError:
            _record(
                "config_file",
                "fail",
                f"Config file         {config_path} — invalid JSON",
            )
            fixes.append(("reset config", config_path))
    else:
        _record(
            "config_file",
            "warn",
            f"Config file         {config_path} — not found",
        )
        fixes.append(("create config", config_path))

    # Disk usage
    try:
        if tokenpak_dir.exists():
            usage = _measure_disk_usage(tokenpak_dir)
            size_mb = usage.total_bytes / (1024 * 1024)
            if usage.truncated:
                _record(
                    "disk_usage",
                    "warn",
                    f"Disk usage          at least {size_mb:.1f} MB — bounded after "
                    f"{usage.entries} entries ({usage.reason}); run: tokenpak maintenance",
                    detail=(
                        f"bytes_partial={usage.total_bytes} files_partial={usage.files} "
                        f"entries={usage.entries} reason={usage.reason}"
                    ),
                )
            elif size_mb < 500:
                _record("disk_usage", "pass", f"Disk usage          {size_mb:.1f} MB — OK")
            else:
                _record(
                    "disk_usage",
                    "warn",
                    f"Disk usage          {size_mb:.1f} MB — consider cleanup "
                    "(tokenpak maintenance)",
                )
        else:
            _record("disk_usage", "warn", f"Disk usage          {tokenpak_dir} not found")
    except Exception:
        _record("disk_usage", "warn", "Disk usage          could not measure")

    # Python deps
    required_deps = ["click", "yaml", "httpx"]
    optional_deps = ["aiohttp", "fastapi", "uvicorn"]
    missing_required: list[str] = []
    missing_optional: list[str] = []
    for pkg in required_deps:
        if importlib.util.find_spec(pkg) is None:
            missing_required.append(pkg)
    for pkg in optional_deps:
        if importlib.util.find_spec(pkg) is None:
            missing_optional.append(pkg)
    if missing_required:
        _record(
            "dependencies",
            "fail",
            f"Dependencies        missing required: {', '.join(missing_required)} "
            "(run: pip install tokenpak)",
        )
    elif missing_optional:
        _record(
            "dependencies",
            "warn",
            f"Dependencies        missing optional: {', '.join(missing_optional)} "
            "(install for full features)",
        )
    else:
        _record("dependencies", "pass", "Dependencies        all core packages present")

    # Log file
    log_path = tokenpak_dir / "debug.log"
    if log_path.exists():
        log_mb = log_path.stat().st_size / (1024 * 1024)
        _record("debug_log", "pass", f"Debug log           {log_path} — {log_mb:.2f} MB")
    else:
        _record("debug_log", "pass", "Debug log           (not present)")

    # API key env vars
    api_key_checks = [
        ("ANTHROPIC_API_KEY", "Anthropic"),
        ("OPENAI_API_KEY", "OpenAI"),
        ("GOOGLE_API_KEY", "Google"),
    ]
    found_keys = [p for env_var, p in api_key_checks if os.environ.get(env_var, "").strip()]
    if found_keys:
        _record(
            "api_keys",
            "pass",
            f"API keys            {', '.join(found_keys)} — env vars set",
        )
    else:
        _record(
            "api_keys",
            "warn",
            "API keys            none found — set ANTHROPIC_API_KEY, OPENAI_API_KEY, "
            "or GOOGLE_API_KEY",
            detail=_api_key_setup_detail(),
        )

    # Proxy degradation check
    try:
        deg = _proxy_get("/degradation")
        if deg is not None:
            if deg.get("is_degraded"):
                recent = deg.get("recent_events", [])
                detail = recent[0].get("detail", "") if recent else ""
                _record(
                    "proxy_degradation",
                    "warn",
                    f"Proxy degradation   running in degraded mode — "
                    f"{detail[:60] or 'see tokenpak status'}",
                )
            else:
                _record(
                    "proxy_degradation",
                    "pass",
                    "Proxy degradation   not degraded — no recent issues",
                )
    except Exception:
        pass

    # Failover config
    failover_cfg_path = tokenpak_dir / "config.yaml"
    if failover_cfg_path.exists():
        try:
            import yaml

            with open(failover_cfg_path) as _f:
                _fc = yaml.safe_load(_f) or {}
            fo = _fc.get("failover", {})
            if fo.get("enabled") and fo.get("chain"):
                _record(
                    "failover_config",
                    "pass",
                    f"Failover config     {failover_cfg_path} — "
                    f"{len(fo['chain'])} provider(s)",
                )
            elif fo.get("enabled"):
                _record(
                    "failover_config",
                    "warn",
                    "Failover config     enabled but no providers in chain",
                )
            else:
                _record(
                    "failover_config",
                    "pass",
                    f"Failover config     {failover_cfg_path} — disabled (no failover)",
                )
        except Exception as _e:
            _record(
                "failover_config",
                "warn",
                f"Failover config     could not parse config.yaml: {_e}",
            )
    else:
        _record("failover_config", "pass", "Failover config     not configured (optional)")

    # Spend Guard (TIP Spend Guard — proxy-side circuit breaker)
    # Ref: standards/29-spend-guard-agent-contract.md, docs/spend-guard.md
    try:
        from tokenpak.proxy.spend_guard.policy import load_config as _load_sg_cfg
        sg = _load_sg_cfg()
        if sg.enabled:
            sg_audit = Path(os.path.expanduser(sg.audit_db_path))
            audit_present = "audit-db present" if sg_audit.exists() else "audit-db not yet created"
            _record(
                "spend_guard",
                "pass",
                (
                    f"Spend Guard         enabled — block=${sg.block_cost_usd:g}/"
                    f"{sg.block_tokens // 1000}K, hard=${sg.hard_block_cost_usd:g}/"
                    f"{sg.hard_block_tokens // 1000_000}M, "
                    f"session=${sg.session_block_cost_usd:g}/"
                    f"{sg.session_window_seconds // 60}min ({audit_present})"
                ),
                detail=(
                    f"warn={sg.warn_tokens}/${sg.warn_cost_usd}, "
                    f"block={sg.block_tokens}/${sg.block_cost_usd}, "
                    f"hard_block={sg.hard_block_tokens}/${sg.hard_block_cost_usd}, "
                    f"session_block_cost_usd=${sg.session_block_cost_usd} "
                    f"window={sg.session_window_seconds}s, "
                    f"pending_ttl={sg.pending_ttl_seconds}s, "
                    f"audit_db={sg.audit_db_path}"
                ),
            )
        else:
            _record(
                "spend_guard",
                "warn",
                "Spend Guard         disabled — runaway requests will not be blocked pre-send",
                detail=f"Set spend_guard.enabled=true in {tokenpak_dir}/config.yaml or unset TOKENPAK_SPEND_GUARD_ENABLED=0",
            )
    except ImportError:
        _record(
            "spend_guard",
            "warn",
            "Spend Guard         module not available — upgrade to TokenPak v1.5.1+",
        )
    except Exception as _sg_e:
        _record(
            "spend_guard",
            "warn",
            f"Spend Guard         could not load config: {type(_sg_e).__name__}",
            detail=str(_sg_e),
        )

    # Required dirs
    required_dirs = [tokenpak_dir / "cache"]
    missing_dirs = [d for d in required_dirs if not d.exists()]
    if missing_dirs:
        missing_names = ", ".join(str(d.name) for d in missing_dirs)
        _record("required_dirs", "warn", f"Required dirs       missing: {missing_names}")
        if fix:
            for d in missing_dirs:
                d.mkdir(parents=True, exist_ok=True)
                if not output_json:
                    print(f"  ✓ Created {d}")
    else:
        _record("required_dirs", "pass", "Required dirs       all present")

    # === Companion hook integrity (script paths + sqlite3 CLI) ==================
    # The bash hook variants no-op silently without the sqlite3 CLI, and a
    # hook command pointing at a missing script fails on every prompt —
    # both are invisible without a doctor check.
    try:
        for _ch_status, _ch_msg, _ch_detail in companion_hook_integrity():
            _record("companion_hooks", _ch_status, _ch_msg, detail=_ch_detail)
    except Exception as _ch_e:  # pragma: no cover — must never crash doctor
        _record(
            "companion_hooks",
            "warn",
            f"Companion hooks     could not inspect hook configs: {type(_ch_e).__name__}",
            detail=str(_ch_e),
        )

    # === Permission tiers + launcher defaults ===================================
    # Persistent-tier rows can only read strict/standard/auto/custom. Launcher
    # defaults are separate per-client rows; malformed launcher state fails
    # closed to inherit and is reported as an error with reset guidance.
    try:
        from tokenpak.cli.commands.permissions import doctor_rows as _perm_rows

        _tier_rows, _tier_drift = _perm_rows()
        _drift_guidance = (
            "\n         → A client config was modified outside TokenPak. Run "
            "`tokenpak permissions set <tier>` to re-apply or "
            "`tokenpak permissions reset` to clear the managed keys."
        )
        _launcher_guidance = (
            "\n         → Launcher state is invalid and was ignored. Run "
            "`tokenpak permissions launcher inherit --client both` to restore "
            "safe inherit defaults."
        )
        for _row in _tier_rows:
            _tier_row_drift = (
                _row.startswith(("Claude Code persistent tier", "Codex persistent tier"))
                and "custom" in _row
            )
            _launcher_row_drift = "launcher default" in _row and "(" in _row
            _launcher_active = "launcher default" in _row and any(
                mode in _row
                for mode in ("approval-bypass", "sandbox-bypass", "full-bypass")
            )
            _guidance = ""
            if _tier_row_drift:
                _guidance = _drift_guidance
            elif _launcher_row_drift:
                _guidance = _launcher_guidance
            _record(
                "permission_tier",
                (
                    "fail"
                    if _tier_row_drift or _launcher_row_drift
                    else "warn" if _launcher_active else "pass"
                ),
                _row + _guidance,
            )
    except Exception as _pt_e:  # pragma: no cover — display must never crash doctor
        _record(
            "permission_tier",
            "warn",
            f"Permission tiers    could not read tier state: {type(_pt_e).__name__}",
            detail=str(_pt_e),
        )

    # === Claude Code integration checks (--claude-code) =========================
    if claude_code:
        if not output_json:
            print()
            print("── Claude Code checks ─────────────────")

        # ENABLE_TOOL_SEARCH check
        # Required for MCP tool-use when ANTHROPIC_BASE_URL points at a non-first-party gateway.
        # Ref: code.claude.com/docs/en/env-vars — ENABLE_TOOL_SEARCH entry
        base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
        tool_search_val = os.environ.get("ENABLE_TOOL_SEARCH", "").strip().lower()
        is_tool_search_on = tool_search_val == "true"

        if not base_url:
            _record(
                "enable_tool_search",
                "warn",
                "ENABLE_TOOL_SEARCH   ANTHROPIC_BASE_URL not set — check skipped "
                "(set it to your proxy URL to use the tokenpak gateway)",
                detail="ANTHROPIC_BASE_URL is unset; user may not yet be routing through a gateway",
            )
        elif "anthropic.com" in base_url:
            _record(
                "enable_tool_search",
                "pass",
                f"ENABLE_TOOL_SEARCH   first-party gateway ({base_url}) — no override required",
                detail=f"ANTHROPIC_BASE_URL={base_url}; Anthropic gateway does not require ENABLE_TOOL_SEARCH",
            )
        elif not is_tool_search_on:
            _record(
                "enable_tool_search",
                "fail",
                f"ENABLE_TOOL_SEARCH   NOT SET — MCP tool-use will silently fail on {base_url}\n"
                "                     Fix: export ENABLE_TOOL_SEARCH=true",
                detail=(
                    f"ANTHROPIC_BASE_URL={base_url} (non-Anthropic gateway) "
                    "but ENABLE_TOOL_SEARCH is not 'true'. "
                    "MCP tool-use requests will be rejected silently."
                ),
            )
        else:
            _record(
                "enable_tool_search",
                "pass",
                f"ENABLE_TOOL_SEARCH   true — MCP tool-use enabled on {base_url}",
                detail=f"ANTHROPIC_BASE_URL={base_url} ENABLE_TOOL_SEARCH=true",
            )

        # Active consumption mode detection
        # The mode matrix is the source of truth for mode→behavior mappings.

        # TTY / interactive mode
        is_tty = sys.stdin.isatty()
        _record(
            "cc_mode_tty",
            "pass",
            f"Invocation mode     {'interactive (TTY)' if is_tty else 'non-interactive (-p / piped)'}",
            detail=f"sys.stdin.isatty()={is_tty}",
        )

        # --bare heuristic: CLAUDE_PLUGIN_DATA is set by the plugin loader; absence means bare.
        plugin_data = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
        if not plugin_data:
            _record(
                "cc_mode_bare",
                "fail",
                "Plugin load         CLAUDE_PLUGIN_DATA not set — likely running with --bare "
                "or plugin not discovered\n"
                "                    Fix: ensure --plugin-dir points at the built plugin directory",
                detail=(
                    "CLAUDE_PLUGIN_DATA is unset. Claude Code sets this when a plugin loads. "
                    "If you launched with --bare, the tokenpak plugin is not active. "
                    "See: tokenpak claude-code build-plugin"
                ),
            )
        else:
            _record(
                "cc_mode_bare",
                "pass",
                f"Plugin load         CLAUDE_PLUGIN_DATA={plugin_data} — plugin active",
                detail=f"CLAUDE_PLUGIN_DATA={plugin_data}",
            )

        # $TERM_PROGRAM: IDE detection
        # Mode matrix — Cursor/Windsurf do not load Claude Code plugins.
        term_program = os.environ.get("TERM_PROGRAM", "").strip()
        if term_program.lower() in ("cursor", "windsurf"):
            _record(
                "cc_mode_ide",
                "fail",
                f"IDE detection       {term_program} detected — Claude Code plugins do NOT load in "
                f"{term_program}\n"
                "                    Workaround: use the tokenpak proxy directly "
                "(ANTHROPIC_BASE_URL=http://localhost:8766) or the SDK helpers",
                detail=(
                    f"TERM_PROGRAM={term_program}. Cursor and Windsurf use Claude Code's API "
                    "but do not load plugins from --plugin-dir. "
                    "Use the proxy endpoint or SDK helpers instead. "
                    "See the mode matrix."
                ),
            )
        elif term_program.lower() == "vscode":
            _record(
                "cc_mode_ide",
                "pass",
                "IDE detection       vscode — Claude Code VSCode extension loads plugins",
                detail="TERM_PROGRAM=vscode; plugin discovery is active in this IDE.",
            )
        elif term_program:
            _record(
                "cc_mode_ide",
                "pass",
                f"IDE detection       TERM_PROGRAM={term_program} — plugin load not verified "
                "(check the mode matrix)",
                detail=f"TERM_PROGRAM={term_program}; consult the mode matrix for this terminal.",
            )
        else:
            _record(
                "cc_mode_ide",
                "pass",
                "IDE detection       TERM_PROGRAM not set — running in terminal/script context",
                detail="No TERM_PROGRAM; direct terminal invocation or script.",
            )

        # $TMUX: TMUX session detection
        tmux_val = os.environ.get("TMUX", "").strip()
        if tmux_val:
            _record(
                "cc_mode_tmux",
                "pass",
                f"TMUX session        detected — ensure vault index uses shared file locks "
                f"(concurrent pane access may contend on {tokenpak_dir}/index.json)",
                detail=(
                    f"TMUX={tmux_val}. Multiple Claude Code panes in the same TMUX session may "
                    "concurrently read/write the vault index. "
                    "The tokenpak plugin uses advisory file locks to coordinate access."
                ),
            )
        else:
            _record(
                "cc_mode_tmux",
                "pass",
                "TMUX session        not detected",
                detail="TMUX env var not set; no concurrent-access advisory needed.",
            )

        # === Claude Code operational health checks ==============================
        if not output_json:
            print()
            print("── Claude Code operational checks ─────")
        from .doctor_claude_code import NUM_CHECKS as _CC_NUM_CHECKS
        from .doctor_claude_code import run_claude_code_checks
        cc_fail_count, cc_results = run_claude_code_checks(output_json=output_json, verbose=verbose)
        for result in cc_results:
            if result["status"] == "pass":
                if not output_json:
                    print(Colors.ok(result["message"]))
            else:
                if not output_json:
                    print(Colors.fail(result["message"]))
                    if result.get("remediation"):
                        print(f"         → {result['remediation']}")
                counts["fail"] += 1
            if verbose and result.get("detail") and not output_json:
                for line in result["detail"].splitlines():
                    print(f"         {line}")
            if output_json:
                checks.append({
                    "check": result["check"],
                    "status": result["status"],
                    "message": result["message"],
                    "detail": result.get("detail", ""),
                })
        if not output_json:
            print()
            print(f"{cc_fail_count} of {_CC_NUM_CHECKS} checks failed.")

    # === JSON output ============================================================
    if output_json:
        exit_code = 2 if counts["fail"] > 0 else (1 if counts["warn"] > 0 else 0)
        output = {
            "summary": counts,
            "checks": checks,
            "exit_code": exit_code,
        }
        print(json.dumps(output, indent=2))
        return exit_code

    # === Summary (human) ========================================================
    print("\n──────────────────────────────")
    err_s = "s" if counts["fail"] != 1 else ""
    warn_s = "s" if counts["warn"] != 1 else ""
    print(f"{counts['fail']} error{err_s}, {counts['warn']} warning{warn_s}.")

    # === Auto-fix ===============================================================
    if fix and fixes:
        print("\nAuto-fix requested. Fixing issues...")
        for fix_type, fix_path in fixes:
            if fix_type in ("create config", "reset config"):
                if fix_type == "reset config" and fix_path.exists():
                    backup = Path(str(fix_path) + ".backup")
                    fix_path.rename(backup)
                    print(f"  ✓ Backed up invalid config → {backup}")
                tokenpak_dir.mkdir(parents=True, exist_ok=True)
                default_cfg = {"version": "1.0", "port": 8766, "compress": True}
                with open(fix_path, "w") as f:
                    json.dump(default_cfg, f, indent=2)
                print(f"  ✓ Created {fix_path}")

    # Exit code: 2=errors, 1=warnings, 0=all pass
    if counts["fail"] > 0:
        return 2
    if counts["warn"] > 0:
        return 1
    return 0


def run_stream_check(output_json: bool = False) -> int:
    """Exercise the companion truncated-stream guard. Returns exit code.

    Drives the defensive stream reader over a fake provider that closes the
    connection mid-chunk (no terminal ``message_stop``). Exits 0 when the
    guard flags the truncation with the stable ``TPK_STREAM_TRUNCATED`` code
    and writes the structured ``provider.error`` telemetry event.
    """
    from tokenpak.companion import stream as _stream

    result = _stream.self_check()
    passed = bool(result.get("passed"))

    if output_json:
        print(json.dumps(result, indent=2))
        return 0 if passed else 2

    print("\nTOKENPAK  |  Doctor — stream guard")
    print("──────────────────────────────\n")
    if passed:
        print(Colors.ok(
            f"Stream guard        truncation flagged ({result.get('code')}); "
            f"provider.error event written"
        ))
        print("\n──────────────────────────────")
        print("0 errors, 0 warnings.")
        return 0
    print(Colors.fail(
        "Stream guard        FAILED to flag truncated stream — "
        f"flagged={result.get('flagged')} code={result.get('code')} "
        f"event_written={result.get('event_written')}"
    ))
    print("\n──────────────────────────────")
    print("1 error, 0 warnings.")
    return 2


try:
    import click

    @click.command("doctor")
    @click.option("--fix", is_flag=True, help="Auto-fix issues where possible")
    @click.option("--fleet", is_flag=True, help="Check all agents listed in fleet.yaml under the resolved TokenPak home")
    @click.option(
        "--deploy", is_flag=True, help="Push latest doctor to all agents (use with --fleet)"
    )
    @click.option(
        "--verbose", "-v", is_flag=True, help="Show extra detail for each check"
    )
    @click.option(
        "--json", "output_json", is_flag=True, help="Output results as JSON"
    )
    @click.option(
        "--claude-code", "claude_code", is_flag=True,
        help="Run Claude Code integration checks (ENABLE_TOOL_SEARCH, mode, IDE detection)",
    )
    @click.option(
        "--stream", "stream", is_flag=True,
        help="Exercise the truncated-stream guard via a fake provider that closes mid-chunk",
    )
    @click.option(
        "--lifecycle", "lifecycle", is_flag=True,
        help="Show only the compact lifecycle summary (installed/setup/routed/proxy/update)",
    )
    def doctor_cmd(
        fix: bool,
        fleet: bool,
        deploy: bool,
        verbose: bool,
        output_json: bool,
        claude_code: bool,
        stream: bool,
        lifecycle: bool,
    ) -> None:
        """Run diagnostics on your TokenPak installation.

        Checks proxy health, DB state, token savings, error rate, vault index
        freshness, and env var conflicts. Each check reports ✅/⚠️/❌ with
        an actionable fix suggestion.

        Exit codes: 0=all pass, 1=warnings only, 2=one or more errors.

        Fleet mode: run doctor on all registered agents in fleet.yaml under the resolved TokenPak home (see ``tokenpak home path``).

        Examples:

        \b
          tokenpak doctor                   # run all checks locally
          tokenpak doctor --verbose         # show extra detail per check
          tokenpak doctor --fix             # run checks and auto-fix where possible
          tokenpak doctor --json            # machine-readable JSON output
          tokenpak doctor --claude-code     # Claude Code integration checks
          tokenpak doctor --fleet           # check all agents in fleet
          tokenpak doctor --fleet --fix     # check + fix all agents
          tokenpak doctor --fleet --deploy  # push latest doctor to all agents first
        """
        if stream:
            rc = run_stream_check(output_json=output_json)
        elif fleet:
            rc = run_fleet_doctor(fix=fix, deploy=deploy)
        else:
            rc = run_doctor(
                fix=fix,
                output_json=output_json,
                verbose=verbose,
                claude_code=claude_code,
                lifecycle=lifecycle,
            )
        sys.exit(rc)

except ImportError:

    def doctor_cmd(*args, **kwargs):  # type: ignore
        print("click not installed; doctor command unavailable")


# ===========================================================================
# Fleet Doctor — tokenpak doctor --fleet
# ===========================================================================

import concurrent.futures
import subprocess

import yaml

FLEET_CONFIG_FILE = Path.home() / ".tokenpak" / "fleet.yaml"

DEFAULT_FLEET_CONFIG = {
    "agents": [
        {"name": "agent-2", "host": "agent-2", "user": "agent-2"},
        {"name": "agent-3", "host": "agent-3", "user": "agent-3"},
        {"name": "agent-1", "host": "agent-1", "user": "agent-1"},
    ]
}


def load_fleet_config() -> dict:
    """Load fleet.yaml; create with defaults if missing."""
    if FLEET_CONFIG_FILE.exists():
        try:
            with open(FLEET_CONFIG_FILE) as f:
                data = yaml.safe_load(f)
            if data and "agents" in data:
                return data
        except Exception:
            pass
    # Create default
    FLEET_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FLEET_CONFIG_FILE, "w") as f:
        yaml.safe_dump(DEFAULT_FLEET_CONFIG, f, default_flow_style=False)
    return DEFAULT_FLEET_CONFIG


def _run_remote_doctor(agent: dict, fix: bool = False, timeout: int = 30) -> dict:
    """SSH into an agent and run tokenpak doctor. Returns result dict."""
    name = agent.get("name", "?")
    host = agent.get("host", "")
    user = agent.get("user", "")

    cmd_parts = ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes"]
    if user:
        cmd_parts += [f"{user}@{host}"]
    else:
        cmd_parts += [host]

    remote_cmd = "tokenpak doctor"
    if fix:
        remote_cmd += " --fix"
    cmd_parts.append(remote_cmd)

    try:
        result = subprocess.run(
            cmd_parts,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        errors = 0
        warnings = 0
        for line in output.splitlines():
            if "error" in line and "warning" in line:
                parts = line.strip().split()
                try:
                    errors = int(parts[0])
                    warnings = int(parts[2])
                except (ValueError, IndexError):
                    pass
        # Map exit codes: 0=pass, 1=warn, 2=errors
        if result.returncode == 2:
            errors = max(errors, 1)
        return {
            "name": name,
            "host": host,
            "success": result.returncode == 0,
            "output": output,
            "errors": errors,
            "warnings": warnings,
        }
    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "host": host,
            "success": False,
            "output": f"[timeout after {timeout}s]",
            "errors": 1,
            "warnings": 0,
        }
    except Exception as exc:
        return {
            "name": name,
            "host": host,
            "success": False,
            "output": str(exc),
            "errors": 1,
            "warnings": 0,
        }


def _deploy_doctor(agent: dict, timeout: int = 30) -> dict:
    """SCP the latest doctor.py to an agent's tokenpak installation."""
    name = agent.get("name", "?")
    host = agent.get("host", "")
    user = agent.get("user", "")

    local_doctor = Path(__file__)
    remote_target = (
        f"{user}@{host}:~/.local/lib/python3/dist-packages/tokenpak/agent/cli/commands/doctor.py"
    )
    if not user:
        remote_target = (
            f"{host}:~/.local/lib/python3/dist-packages/tokenpak/agent/cli/commands/doctor.py"
        )

    cmd = [
        "scp",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "BatchMode=yes",
        str(local_doctor),
        remote_target,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return {
            "name": name,
            "host": host,
            "success": result.returncode == 0,
            "output": result.stdout + result.stderr,
        }
    except subprocess.TimeoutExpired:
        return {
            "name": name,
            "host": host,
            "success": False,
            "output": f"[scp timeout after {timeout}s]",
        }
    except Exception as exc:
        return {"name": name, "host": host, "success": False, "output": str(exc)}


def run_fleet_doctor(fix: bool = False, deploy: bool = False) -> int:
    """Run fleet-wide doctor checks. Returns 0 if all pass, 1 if any warn, 2 if any fail."""
    fleet_cfg = load_fleet_config()
    agents = fleet_cfg.get("agents", [])

    if not agents:
        print(Colors.warn("Fleet config has no agents defined"))
        return 2

    print("\nTOKENPAK  |  Fleet Doctor")
    print(f"Checking {len(agents)} agent(s) in parallel...\n")

    if deploy:
        print("Deploying latest doctor to all agents...")
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(agents)) as ex:
            deploy_futures = {ex.submit(_deploy_doctor, a): a for a in agents}
            for fut in concurrent.futures.as_completed(deploy_futures):
                r = fut.result()
                status = (
                    Colors.ok(f"  Deployed to {r['name']} ({r['host']})")
                    if r["success"]
                    else Colors.fail(
                        f"  Deploy failed on {r['name']} ({r['host']}): {r['output'][:60]}"
                    )
                )
                print(status)
        print()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(agents)) as ex:
        futures = {ex.submit(_run_remote_doctor, a, fix): a for a in agents}
        results = []
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: r["name"])

    total_errors = 0
    total_warnings = 0
    all_ok = True

    print("──────────────────────────────────────────────────────")
    for r in results:
        icon = "✅" if r["errors"] == 0 else "❌"
        warn_note = f", {r['warnings']}w" if r["warnings"] else ""
        print(
            f"  {icon}  {r['name']:10s}  ({r['host']})  "
            f"— {r['errors']} errors{warn_note}"
        )
        total_errors += r["errors"]
        total_warnings += r["warnings"]
        if r["errors"] > 0:
            all_ok = False
            for line in r["output"].splitlines():
                print(f"     {line}")

    print("──────────────────────────────────────────────────────")
    print(
        f"Fleet: {total_errors} total error(s), {total_warnings} total warning(s) "
        f"across {len(agents)} agents"
    )
    if total_errors > 0:
        return 2
    if total_warnings > 0:
        return 1
    return 0
