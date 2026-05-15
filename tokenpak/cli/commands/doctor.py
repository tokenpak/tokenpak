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


def run_doctor(
    fix: bool = False,
    output_json: bool = False,
    verbose: bool = False,
    claude_code: bool = False,
) -> int:
    """Run all diagnostic checks. Returns exit code (0=pass, 1=warn, 2=errors).

    Args:
        fix: Auto-fix issues where possible.
        output_json: Output results as machine-readable JSON instead of human text.
        verbose: Show extra detail for each check.
        claude_code: Run Claude Code integration checks (ENABLE_TOOL_SEARCH, mode, IDE).
    """
    if not output_json:
        print("\nTOKENPAK  |  Doctor")
        print("──────────────────────────────\n")

    counts = {"pass": 0, "warn": 0, "fail": 0}
    fixes: list[tuple[str, Path]] = []
    checks: list[dict] = []
    # Std 33: resolve through _paths so doctor reports the canonical
    # home (~/.tpk/) when present, and surfaces the legacy fallback
    # when the user hasn't run `tokenpak home migrate` yet.
    from tokenpak import _paths

    tokenpak_dir = _paths.home()

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

    # === Check 0: Std 33 home boundary ==========================================
    # Reports the resolved TokenPak home + flags legacy paths that should
    # be migrated. Cheap, side-effect-free, runs before everything else
    # so the operator sees the boundary state up front.
    if _paths.is_legacy_active():
        _record(
            "home_boundary",
            "warn",
            f"~/.tpk/ boundary    legacy: {_paths.legacy_home()}",
            detail=(
                f"Using legacy ~/.tokenpak/ — canonical ~/.tpk/ is "
                f"absent. Run `tokenpak home migrate` to copy your "
                f"state to ~/.tpk/ (non-destructive, backup-first)."
            ),
        )
    elif _paths.has_legacy() and _paths.has_canonical():
        _record(
            "home_boundary",
            "warn",
            f"~/.tpk/ boundary    canonical + legacy both present",
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
                    f"Proxy not running   port {proxy_port} — start with: tokenpak proxy restart",
                )
        except Exception:
            _record(
                "proxy_health",
                "warn",
                f"Proxy not reachable port {proxy_port} — check failed",
            )

    # === Check 2: DB path and row count =========================================
    db_path = tokenpak_dir / "monitor.db"
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

    # === Check 7b: Registered vault paths staleness (VDS-03) ====================
    # Reads ~/.tokenpak/vault.yaml + per-path index health (VDS-01) and warns
    # when a registered directory's last rebuild is older than expected
    # interval × 2, when the path is missing, when metadata is corrupt, or
    # when the previous reindex failed. Manual schedules don't warn solely on
    # age. Per VDS-03 spec — does not fail unrelated checks.
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
            total_bytes = sum(
                f.stat().st_size for f in tokenpak_dir.rglob("*") if f.is_file()
            )
            size_mb = total_bytes / (1024 * 1024)
            if size_mb < 500:
                _record("disk_usage", "pass", f"Disk usage          {size_mb:.1f} MB — OK")
            else:
                _record(
                    "disk_usage",
                    "warn",
                    f"Disk usage          {size_mb:.1f} MB — consider cleanup "
                    "(tokenpak maintenance)",
                )
        else:
            _record("disk_usage", "warn", "Disk usage          ~/.tokenpak not found")
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
                detail="Set spend_guard.enabled=true in ~/.tokenpak/config.yaml or unset TOKENPAK_SPEND_GUARD_ENABLED=0",
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

    # === Claude Code integration checks (--claude-code) =========================
    if claude_code:
        if not output_json:
            print()
            print("── Claude Code checks (CCP-09) ─────────────────")

        # CCP-09: ENABLE_TOOL_SEARCH check
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

        # CCP-09 (2026-04-08 amendment): Active consumption mode detection
        # Ref: CCP-22 mode matrix is the source of truth for mode→behavior mappings.

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
        # Ref: CCP-22 mode matrix — Cursor/Windsurf do not load Claude Code plugins.
        term_program = os.environ.get("TERM_PROGRAM", "").strip()
        if term_program.lower() in ("cursor", "windsurf"):
            _record(
                "cc_mode_ide",
                "fail",
                f"IDE detection       {term_program} detected — Claude Code plugins do NOT load in "
                f"{term_program}\n"
                "                    Workaround: use the tokenpak proxy directly "
                "(ANTHROPIC_BASE_URL=http://localhost:8766) or the CCP-23 SDK helpers",
                detail=(
                    f"TERM_PROGRAM={term_program}. Cursor and Windsurf use Claude Code's API "
                    "but do not load plugins from --plugin-dir. "
                    "Use the proxy endpoint or CCP-23 SDK helpers instead. "
                    "Ref: CCP-22 mode matrix."
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
                "(check CCP-22 mode matrix)",
                detail=f"TERM_PROGRAM={term_program}; consult CCP-22 mode matrix for this terminal.",
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
                "TMUX session        detected — ensure vault index uses shared file locks "
                "(CCP-06 amendment: concurrent pane access may contend on ~/.tokenpak/index.json)",
                detail=(
                    f"TMUX={tmux_val}. Multiple Claude Code panes in the same TMUX session may "
                    "concurrently read/write the vault index. "
                    "The tokenpak plugin uses advisory file locks (CCP-06) to coordinate access."
                ),
            )
        else:
            _record(
                "cc_mode_tmux",
                "pass",
                "TMUX session        not detected",
                detail="TMUX env var not set; no concurrent-access advisory needed.",
            )

        # === CCI-12: 8-point Claude Code operational health checks ================
        if not output_json:
            print()
            print("── Claude Code operational checks (CCI-12) ─────")
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
            print(f"{cc_fail_count} of 8 checks failed.")

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


try:
    import click

    @click.command("doctor")
    @click.option("--fix", is_flag=True, help="Auto-fix issues where possible")
    @click.option("--fleet", is_flag=True, help="Check all agents in ~/.tokenpak/fleet.yaml")
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
    def doctor_cmd(
        fix: bool,
        fleet: bool,
        deploy: bool,
        verbose: bool,
        output_json: bool,
        claude_code: bool,
    ) -> None:
        """Run diagnostics on your TokenPak installation.

        Checks proxy health, DB state, token savings, error rate, vault index
        freshness, and env var conflicts. Each check reports ✅/⚠️/❌ with
        an actionable fix suggestion.

        Exit codes: 0=all pass, 1=warnings only, 2=one or more errors.

        Fleet mode: run doctor on all registered agents in ~/.tokenpak/fleet.yaml.

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
        if fleet:
            rc = run_fleet_doctor(fix=fix, deploy=deploy)
        else:
            rc = run_doctor(fix=fix, output_json=output_json, verbose=verbose, claude_code=claude_code)
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
        {"name": "trix", "host": "agent-2", "user": "trix"},
        {"name": "cali", "host": "agent-3", "user": "cali"},
        {"name": "sue", "host": "agent-1", "user": "sue"},
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
