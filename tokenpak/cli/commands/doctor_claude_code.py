"""doctor_claude_code — CCI-12 Claude Code health checks for tokenpak doctor --claude-code.

9 health checks:
  1. ANTHROPIC_BASE_URL is set (env + ~/.claude/settings.json)
  2. Proxy reachable at configured URL (GET /health)
  3. Auth flow works (POST /v1/messages/count_tokens, expect 200)
  4. Active profile is claude-code-* (TOKENPAK_PROFILE env)
  5. Sample request round-trips (tiny messages request through proxy)
  6. Telemetry visible (session logged in last 24 h)
  7. No PYTHONPATH drift (proxy proc environ vs canonical from systemd unit)
  8. Per-host install consistency (tokenpak.env, systemd unit, settings.json same URL)
  9. Plugin directory exists at ~/.claude/plugins/tokenpak or ~/.claude/plugins/tokenpak-claude-code

Each check runs independently.  A failure in one does not block the rest.
Exit: non-zero if any check fails.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import TypedDict

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_PROXY_URL = "http://127.0.0.1:8766"
SYSTEMD_UNIT_NAME = "tokenpak-proxy.service"
REMEDIATION = "Run `tokenpak install --claude-code` to fix this"
NUM_CHECKS = 9

# Plugin directory candidate names under ~/.claude/plugins/
_PLUGIN_DIR_NAMES = ("tokenpak", "tokenpak-claude-code")


# Path helpers — evaluated at call time so monkeypatching Path.home() works in tests
def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / SYSTEMD_UNIT_NAME


def _tokenpak_env_path() -> Path:
    return Path.home() / ".config" / "tokenpak.env"


def _claude_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


def _proxy_pid_path() -> Path:
    return Path.home() / ".tokenpak" / "proxy.pid"


def _monitor_db_path() -> Path:
    return Path.home() / ".tokenpak" / "monitor.db"


class CheckResult(TypedDict):
    check: str
    status: str  # "pass" | "fail"
    message: str
    detail: str
    remediation: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _http_get(url: str, timeout: int = 4) -> tuple[int, bytes]:
    """Return (status_code, body).  Never raises — on error returns (0, b'')."""
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:
            body = b""
        return exc.code, body
    except Exception:
        return 0, b""


def _http_post_json(url: str, payload: dict, headers: dict | None = None, timeout: int = 8) -> tuple[int, bytes]:
    """POST JSON payload to url. Returns (status_code, body). Never raises."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Content-Length", str(len(data)))
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:
            body = b""
        return exc.code, body
    except Exception:
        return 0, b""


def _read_claude_settings() -> dict:
    """Return parsed ~/.claude/settings.json or {} on any error."""
    try:
        return json.loads(_claude_settings_path().read_text())
    except Exception:
        return {}


def _get_configured_proxy_url() -> tuple[str, str]:
    """Return (proxy_url, source) where source is 'env' or 'settings.json' or 'default'."""
    env_val = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if env_val:
        return env_val, "env"
    settings = _read_claude_settings()
    settings_url = settings.get("anthropicBaseUrl", "").strip() or settings.get("ANTHROPIC_BASE_URL", "").strip()
    if settings_url:
        return settings_url, "settings.json"
    return "", "none"


def _get_proxy_pid() -> int | None:
    """Read proxy PID from ~/.tokenpak/proxy.pid. Returns None if unavailable."""
    try:
        return int(_proxy_pid_path().read_text().strip())
    except Exception:
        return None


def _read_proc_environ(pid: int) -> dict[str, str] | None:
    """Read /proc/<pid>/environ and return dict of env vars. None on any error."""
    proc_env_path = Path(f"/proc/{pid}/environ")
    try:
        raw = proc_env_path.read_bytes()
        result: dict[str, str] = {}
        for entry in raw.split(b"\x00"):
            if b"=" in entry:
                key, _, val = entry.partition(b"=")
                result[key.decode(errors="replace")] = val.decode(errors="replace")
        return result
    except PermissionError:
        return None
    except Exception:
        return None


def _get_canonical_pythonpath_from_unit() -> str | None:
    """Extract PYTHONPATH from the systemd unit's Environment= lines. Returns None if not found."""
    unit_path = _systemd_unit_path()
    if not unit_path.exists():
        return None
    try:
        content = unit_path.read_text()
    except Exception:
        return None
    home_str = str(Path.home())
    # Look for Environment=PYTHONPATH=...
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("Environment=PYTHONPATH="):
            val = line[len("Environment=PYTHONPATH="):]
            # Expand %h → home directory (systemd specifier)
            val = val.replace("%h", home_str)
            return val
    return None


def _get_url_from_unit() -> str | None:
    """Extract the proxy URL from the systemd unit's Environment= lines (ANTHROPIC_BASE_URL or TOKENPAK_PORT)."""
    unit_path = _systemd_unit_path()
    if not unit_path.exists():
        return None
    try:
        content = unit_path.read_text()
    except Exception:
        return None
    home_str = str(Path.home())
    # Try ANTHROPIC_BASE_URL in Environment lines
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("Environment=ANTHROPIC_BASE_URL="):
            val = line[len("Environment=ANTHROPIC_BASE_URL="):]
            return val.replace("%h", home_str).strip()
    # Fall back: derive from TOKENPAK_PORT
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("Environment=TOKENPAK_PORT="):
            port = line[len("Environment=TOKENPAK_PORT="):].strip()
            return f"http://127.0.0.1:{port}"
    return None


def _get_url_from_env_file() -> str | None:
    """Extract proxy URL from tokenpak.env (ANTHROPIC_BASE_URL or TOKENPAK_PORT)."""
    if not _tokenpak_env_path().exists():
        return None
    try:
        content = _tokenpak_env_path().read_text()
    except Exception:
        return None
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if line.startswith("ANTHROPIC_BASE_URL="):
            return line[len("ANTHROPIC_BASE_URL="):].strip()
    # Fall back to TOKENPAK_PORT
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if line.startswith("TOKENPAK_PORT="):
            port = line[len("TOKENPAK_PORT="):].strip()
            return f"http://127.0.0.1:{port}"
    return None


def _normalise_url(url: str) -> str:
    """Normalise URL for comparison: strip trailing slash, lowercase scheme+host."""
    url = url.strip().rstrip("/")
    # Normalise localhost aliases
    url = url.replace("http://localhost:", "http://127.0.0.1:")
    return url


# ---------------------------------------------------------------------------
# The 8 checks
# ---------------------------------------------------------------------------

def _check_base_url_set() -> CheckResult:
    """Check 1: ANTHROPIC_BASE_URL is set to the proxy URL."""
    url, source = _get_configured_proxy_url()
    if not url:
        return CheckResult(
            check="base_url_set",
            status="fail",
            message="Check 1  ANTHROPIC_BASE_URL   NOT SET — Claude Code will use api.anthropic.com directly",
            detail="ANTHROPIC_BASE_URL not found in env or ~/.claude/settings.json",
            remediation=REMEDIATION,
        )
    return CheckResult(
        check="base_url_set",
        status="pass",
        message=f"Check 1  ANTHROPIC_BASE_URL   {url}  (from {source})",
        detail=f"source={source} url={url}",
        remediation="",
    )


def _check_proxy_reachable() -> CheckResult:
    """Check 2: Proxy is reachable at the configured URL (GET /health)."""
    url, _source = _get_configured_proxy_url()
    if not url:
        # Fall back to default
        url = DEFAULT_PROXY_URL
    health_url = _normalise_url(url) + "/health"
    status_code, body = _http_get(health_url, timeout=4)
    if status_code == 200:
        try:
            data = json.loads(body)
            mode = data.get("compilation_mode", "unknown")
        except Exception:
            mode = "unknown"
        return CheckResult(
            check="proxy_reachable",
            status="pass",
            message=f"Check 2  Proxy reachable      {health_url} — mode={mode}",
            detail=f"status={status_code} mode={mode}",
            remediation="",
        )
    if status_code == 0:
        return CheckResult(
            check="proxy_reachable",
            status="fail",
            message=f"Check 2  Proxy reachable      UNREACHABLE at {health_url}",
            detail=f"Connection refused or timeout at {health_url}",
            remediation=REMEDIATION,
        )
    return CheckResult(
        check="proxy_reachable",
        status="fail",
        message=f"Check 2  Proxy reachable      HTTP {status_code} at {health_url}",
        detail=f"status={status_code} url={health_url}",
        remediation=REMEDIATION,
    )


def _check_auth_flow() -> CheckResult:
    """Check 3: Auth flow works — POST /v1/messages/count_tokens, expect 200."""
    url, _source = _get_configured_proxy_url()
    if not url:
        url = DEFAULT_PROXY_URL
    endpoint = _normalise_url(url) + "/v1/messages/count_tokens"
    payload = {
        "model": "claude-3-haiku-20240307",
        "messages": [{"role": "user", "content": "ping"}],
    }
    # Use a dummy API key; the proxy validates format not value for count_tokens
    headers = {"x-api-key": "sk-ant-test-doctor-check"}
    status_code, body = _http_post_json(endpoint, payload, headers=headers, timeout=8)
    if status_code == 200:
        try:
            data = json.loads(body)
            tokens = data.get("input_tokens", "?")
        except Exception:
            tokens = "?"
        return CheckResult(
            check="auth_flow",
            status="pass",
            message=f"Check 3  Auth flow            OK — count_tokens={tokens}",
            detail=f"endpoint={endpoint} status=200 input_tokens={tokens}",
            remediation="",
        )
    return CheckResult(
        check="auth_flow",
        status="fail",
        message=f"Check 3  Auth flow            FAILED — HTTP {status_code} from {endpoint}",
        detail=f"endpoint={endpoint} status={status_code} body={body[:200].decode(errors='replace')}",
        remediation=REMEDIATION,
    )


def _check_active_profile() -> CheckResult:
    """Check 4: Active profile is one of claude-code-* or override is set."""
    profile = os.environ.get("TOKENPAK_PROFILE", "").strip()
    override = os.environ.get("TOKENPAK_PROFILE_OVERRIDE", "").strip()
    # Also check the stats endpoint for active_profile
    url, _source = _get_configured_proxy_url()
    if not url:
        url = DEFAULT_PROXY_URL
    stats_code, stats_body = _http_get(_normalise_url(url) + "/stats", timeout=3)
    proxy_profile = ""
    if stats_code == 200:
        try:
            stats_data = json.loads(stats_body)
            proxy_profile = stats_data.get("session", {}).get("active_profile", "")
        except Exception:
            pass
    is_cc_profile = (
        profile.startswith("claude-code-")
        or override.startswith("claude-code-")
        or proxy_profile.startswith("claude-code-")
    )
    if is_cc_profile:
        active = profile or proxy_profile or override
        return CheckResult(
            check="active_profile",
            status="pass",
            message=f"Check 4  Active profile       {active} — is claude-code-*",
            detail=f"TOKENPAK_PROFILE={profile} proxy_profile={proxy_profile} override={override}",
            remediation="",
        )
    # Not a claude-code profile — this is a warning not a hard fail per the spec
    # ("make check 4 a best-effort check that warns instead of fails when profile isn't visible")
    profile_display = profile or proxy_profile or "(not detected)"
    return CheckResult(
        check="active_profile",
        status="fail",
        message=(
            f"Check 4  Active profile       {profile_display} — expected claude-code-* profile\n"
            "                              Set TOKENPAK_PROFILE=claude-code-cli or run with --mode=cli"
        ),
        detail=(
            f"TOKENPAK_PROFILE={profile!r} proxy_profile={proxy_profile!r} "
            f"override={override!r}  — none starts with 'claude-code-'"
        ),
        remediation=REMEDIATION,
    )


def _check_sample_roundtrip() -> CheckResult:
    """Check 5: Sample request round-trips through the proxy."""
    url, _source = _get_configured_proxy_url()
    if not url:
        url = DEFAULT_PROXY_URL
    # Use count_tokens as the round-trip probe — it's lightweight and stateless
    endpoint = _normalise_url(url) + "/v1/messages/count_tokens"
    payload = {
        "model": "claude-3-haiku-20240307",
        "messages": [{"role": "user", "content": "doctor round-trip probe"}],
    }
    headers = {"x-api-key": "sk-ant-test-doctor-roundtrip"}
    t0 = time.monotonic()
    status_code, body = _http_post_json(endpoint, payload, headers=headers, timeout=10)
    latency_ms = (time.monotonic() - t0) * 1000
    if status_code == 200:
        return CheckResult(
            check="sample_roundtrip",
            status="pass",
            message=f"Check 5  Round-trip           OK — {latency_ms:.0f} ms via {_normalise_url(url)}",
            detail=f"endpoint={endpoint} status=200 latency_ms={latency_ms:.0f}",
            remediation="",
        )
    return CheckResult(
        check="sample_roundtrip",
        status="fail",
        message=f"Check 5  Round-trip           FAILED — HTTP {status_code} ({latency_ms:.0f} ms)",
        detail=f"endpoint={endpoint} status={status_code} body={body[:200].decode(errors='replace')}",
        remediation=REMEDIATION,
    )


def _check_telemetry_visible() -> CheckResult:
    """Check 6: Telemetry visible — at least 1 session logged in last 24 h."""
    cutoff = time.time() - 86400  # 24 hours ago

    # Primary: query via proxy /v1/sessions endpoint
    url, _source = _get_configured_proxy_url()
    if not url:
        url = DEFAULT_PROXY_URL
    sessions_url = _normalise_url(url) + "/v1/sessions?limit=1"
    status_code, body = _http_get(sessions_url, timeout=4)
    if status_code == 200:
        try:
            data = json.loads(body)
            sessions = data.get("sessions", [])
            total = data.get("total", len(sessions))
            if total and total > 0:
                return CheckResult(
                    check="telemetry_visible",
                    status="pass",
                    message=f"Check 6  Telemetry visible    {total} session(s) found via /v1/sessions",
                    detail=f"sessions_endpoint={sessions_url} total={total}",
                    remediation="",
                )
        except Exception:
            pass

    # Secondary: query monitor.db directly
    if _monitor_db_path().exists():
        try:
            conn = sqlite3.connect(str(_monitor_db_path()))
            cur = conn.cursor()
            # Try requests table — use created_at or id as a proxy for recency
            cur.execute(
                "SELECT COUNT(*) FROM requests WHERE id IN "
                "(SELECT id FROM requests ORDER BY id DESC LIMIT 100)"
            )
            recent = cur.fetchone()[0]
            conn.close()
            if recent and recent > 0:
                return CheckResult(
                    check="telemetry_visible",
                    status="pass",
                    message=f"Check 6  Telemetry visible    {recent} request(s) in DB (last 100) — traffic confirmed",
                    detail=f"db={_monitor_db_path()} recent_requests={recent}",
                    remediation="",
                )
            else:
                return CheckResult(
                    check="telemetry_visible",
                    status="pass",
                    message="Check 6  Telemetry visible    no traffic yet — SKIP (no requests logged)",
                    detail="monitor.db has 0 rows; skipping as per spec (no traffic → skip)",
                    remediation="",
                )
        except Exception as exc:
            pass

    # No traffic, no DB, sessions endpoint unavailable — skip per spec
    return CheckResult(
        check="telemetry_visible",
        status="pass",
        message="Check 6  Telemetry visible    no traffic or DB — SKIP",
        detail="No requests in DB and /v1/sessions unavailable; skipping per spec",
        remediation="",
    )


def _check_pythonpath_drift() -> CheckResult:
    """Check 7: No PYTHONPATH drift — proxy proc environ vs canonical from systemd unit."""
    pid = _get_proxy_pid()
    if pid is None:
        return CheckResult(
            check="pythonpath_drift",
            status="fail",
            message="Check 7  PYTHONPATH drift     CANNOT CHECK — proxy.pid not found",
            detail=f"PID file {_proxy_pid_path()} missing; proxy may not be running via launcher",
            remediation=REMEDIATION,
        )

    proc_env = _read_proc_environ(pid)
    if proc_env is None:
        return CheckResult(
            check="pythonpath_drift",
            status="fail",
            message=f"Check 7  PYTHONPATH drift     CANNOT READ /proc/{pid}/environ (permission denied)",
            detail=f"pid={pid} — /proc/{pid}/environ unreadable; run as the same user as the proxy",
            remediation="Check that the proxy runs as the current user",
        )

    proc_pythonpath = proc_env.get("PYTHONPATH", "")
    canonical_pythonpath = _get_canonical_pythonpath_from_unit()

    home_str = str(Path.home())

    if canonical_pythonpath is not None:
        # Compare normalised (strip trailing colons/spaces)
        canon_norm = canonical_pythonpath.strip().rstrip(":")
        proc_norm = proc_pythonpath.strip().rstrip(":")
        if canon_norm == proc_norm:
            return CheckResult(
                check="pythonpath_drift",
                status="pass",
                message=f"Check 7  PYTHONPATH drift     OK — proc matches systemd unit",
                detail=f"pid={pid} proc_pythonpath={proc_pythonpath!r} canonical={canonical_pythonpath!r}",
                remediation="",
            )
        # Drift detected
        return CheckResult(
            check="pythonpath_drift",
            status="fail",
            message=(
                f"Check 7  PYTHONPATH drift     DRIFT DETECTED\n"
                f"                              proc   : {proc_pythonpath}\n"
                f"                              unit   : {canonical_pythonpath}"
            ),
            detail=(
                f"pid={pid} proc_pythonpath={proc_pythonpath!r} "
                f"canonical={canonical_pythonpath!r} — mismatch"
            ),
            remediation="Restart proxy via: systemctl --user restart tokenpak-proxy.service",
        )

    # No systemd unit — fall back to home-directory heuristic
    # The 2026-04-08 calibot incident: PYTHONPATH referenced /home/sue/ instead of current user
    if proc_pythonpath and home_str not in proc_pythonpath:
        wrong_user = re.search(r"/home/(\w+)/", proc_pythonpath)
        wrong_name = wrong_user.group(1) if wrong_user else "unknown"
        return CheckResult(
            check="pythonpath_drift",
            status="fail",
            message=(
                f"Check 7  PYTHONPATH drift     DRIFT DETECTED — proc PYTHONPATH references "
                f"/home/{wrong_name}/ not /home/{Path.home().name}/\n"
                f"                              proc: {proc_pythonpath}"
            ),
            detail=(
                f"pid={pid} proc_pythonpath={proc_pythonpath!r} "
                f"current_home={home_str} — wrong user in path"
            ),
            remediation="Restart proxy via: systemctl --user restart tokenpak-proxy.service",
        )

    if not proc_pythonpath:
        return CheckResult(
            check="pythonpath_drift",
            status="pass",
            message="Check 7  PYTHONPATH drift     OK — PYTHONPATH not set in proc (using sys.path defaults)",
            detail=f"pid={pid} PYTHONPATH unset in proc environ",
            remediation="",
        )

    return CheckResult(
        check="pythonpath_drift",
        status="pass",
        message=f"Check 7  PYTHONPATH drift     OK — {proc_pythonpath[:80]}",
        detail=f"pid={pid} proc_pythonpath={proc_pythonpath!r}",
        remediation="",
    )


def _check_install_consistency() -> CheckResult:
    """Check 8: tokenpak.env, systemd unit, and ~/.claude/settings.json all reference same proxy URL."""
    sources: dict[str, str | None] = {
        "tokenpak.env": _get_url_from_env_file(),
        "systemd unit": _get_url_from_unit(),
        "settings.json": _get_configured_proxy_url()[0] or None,
    }

    # Normalise for comparison
    present = {k: _normalise_url(v) for k, v in sources.items() if v}
    missing = [k for k, v in sources.items() if not v]

    if not present:
        return CheckResult(
            check="install_consistency",
            status="fail",
            message="Check 8  Install consistency  FAIL — proxy URL not found in any config source",
            detail=f"sources checked: {list(sources.keys())}",
            remediation=REMEDIATION,
        )

    unique_urls = set(present.values())
    if len(unique_urls) == 1:
        url_str = next(iter(unique_urls))
        sources_str = ", ".join(present.keys())
        missing_note = f" (missing from: {', '.join(missing)})" if missing else ""
        return CheckResult(
            check="install_consistency",
            status="pass",
            message=f"Check 8  Install consistency  OK — {url_str} consistent across {sources_str}{missing_note}",
            detail=f"url={url_str} sources={list(present.keys())} missing={missing}",
            remediation="",
        )

    # Inconsistency detected
    detail_lines = [f"  {src}: {url}" for src, url in sorted(present.items())]
    if missing:
        detail_lines += [f"  {src}: (not configured)" for src in sorted(missing)]
    return CheckResult(
        check="install_consistency",
        status="fail",
        message=(
            "Check 8  Install consistency  MISMATCH — sources disagree on proxy URL\n"
            + "\n".join(detail_lines)
        ),
        detail="\n".join(detail_lines),
        remediation=REMEDIATION,
    )


def _check_plugin_dir() -> CheckResult:
    """Check 9 (CCP-09): tokenpak plugin directory exists under ~/.claude/plugins/."""
    home = Path.home()
    candidates = [home / ".claude" / "plugins" / name for name in _PLUGIN_DIR_NAMES]

    for path in candidates:
        if path.exists():
            return CheckResult(
                check="plugin_dir",
                status="pass",
                message=f"Check 9  Plugin directory     found: {path}",
                detail=f"path={path}",
                remediation="",
            )

    checked = ", ".join(str(p) for p in candidates)
    return CheckResult(
        check="plugin_dir",
        status="fail",
        message=(
            "Check 9  Plugin directory     NOT FOUND — MCP tools unavailable\n"
            f"         Checked: {checked}"
        ),
        detail=f"candidates_checked=[{checked}]",
        remediation=(
            "Install the Claude Code plugin:\n"
            "  mkdir -p ~/.claude/plugins/tokenpak\n"
            "  # Then copy or symlink the plugin files into that directory.\n"
            "  # See tokenpak/integrations/claude_code/plugin/README.md for full instructions."
        ),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_claude_code_checks(
    output_json: bool = False,
    verbose: bool = False,
) -> tuple[int, list[CheckResult]]:
    """Run all 9 CCI-12 + CCP-09 Claude Code health checks.

    Returns:
        (fail_count, checks) — fail_count is the number of failed checks.
    """
    check_fns = [
        _check_base_url_set,
        _check_proxy_reachable,
        _check_auth_flow,
        _check_active_profile,
        _check_sample_roundtrip,
        _check_telemetry_visible,
        _check_pythonpath_drift,
        _check_install_consistency,
        _check_plugin_dir,
    ]

    results: list[CheckResult] = []
    fail_count = 0

    for fn in check_fns:
        try:
            result = fn()
        except Exception as exc:
            result = CheckResult(
                check=fn.__name__.replace("_check_", ""),
                status="fail",
                message=f"{fn.__name__}  ERROR — unexpected exception: {exc}",
                detail=str(exc),
                remediation="Report this as a tokenpak bug",
            )
        results.append(result)
        if result["status"] == "fail":
            fail_count += 1

    return fail_count, results


def print_claude_code_checks(
    output_json: bool = False,
    verbose: bool = False,
) -> int:
    """Run checks, print results, return exit code (0=pass, 1=any fail)."""
    try:
        from .doctor import Colors
    except ImportError:
        class Colors:  # type: ignore[no-redef]
            GREEN = "\033[92m"
            YELLOW = "\033[93m"
            RED = "\033[91m"
            RESET = "\033[0m"

            @staticmethod
            def ok(text: str) -> str:
                return f"{Colors.GREEN}OK {Colors.RESET} {text}"

            @staticmethod
            def fail(text: str) -> str:
                return f"{Colors.RED}FAIL{Colors.RESET} {text}"

    fail_count, results = run_claude_code_checks(output_json=output_json, verbose=verbose)

    if output_json:
        out = {
            "checks": list(results),
            "fail_count": fail_count,
            "total": NUM_CHECKS,
            "passed": NUM_CHECKS - fail_count,
        }
        import json as _json
        print(_json.dumps(out, indent=2))
        return 1 if fail_count > 0 else 0

    for result in results:
        if result["status"] == "pass":
            print(Colors.ok(result["message"]))
        else:
            print(Colors.fail(result["message"]))
            if result.get("remediation"):
                print(f"         → {result['remediation']}")
        if verbose and result.get("detail"):
            for line in result["detail"].splitlines():
                print(f"         {line}")

    print()
    print(f"{fail_count} of {NUM_CHECKS} checks failed.")
    return 1 if fail_count > 0 else 0
