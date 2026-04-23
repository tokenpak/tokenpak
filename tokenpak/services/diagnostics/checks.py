"""Shared diagnostic checks — consumed by ``tokenpak doctor`` + CI probes.

Each check returns a :class:`CheckResult`. The caller (CLI, doctor,
installer, CI) decides presentation + whether to fail on findings.

Core checks run for every invocation of ``tokenpak doctor``. The
Claude-Code-specific checks only run under ``doctor --claude-code``
(or on installer verify).
"""

from __future__ import annotations

import enum
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from tokenpak.services.diagnostics.drift import detect_install_drift


class CheckStatus(enum.Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(slots=True)
class CheckResult:
    name: str
    status: CheckStatus
    summary: str
    details: list[str] = field(default_factory=list)


def _ok(name: str, summary: str, *details: str) -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.OK, summary=summary, details=list(details))


def _warn(name: str, summary: str, *details: str) -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.WARN, summary=summary, details=list(details))


def _fail(name: str, summary: str, *details: str) -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.FAIL, summary=summary, details=list(details))


# ── Core checks ──────────────────────────────────────────────────────────

def _check_version_consistency() -> CheckResult:
    # Read the version via importlib.metadata instead of `import tokenpak`
    # so services/diagnostics doesn't import the whole package (that
    # would round-trip through tokenpak.proxy and break the
    # services → entrypoint layering rule).
    try:
        from importlib.metadata import PackageNotFoundError, version

        v = version("tokenpak")
    except (PackageNotFoundError, Exception) as exc:  # noqa: BLE001
        return _fail("version", f"pkg metadata missing: {exc}")
    if not v:
        return _fail(
            "version",
            "tokenpak version metadata missing",
            "Namespace-package shadow? Run `tokenpak doctor --claude-code` for drift detail.",
        )
    return _ok("version", f"tokenpak {v} present in package metadata")


def _check_install_drift() -> CheckResult:
    report = detect_install_drift()
    if report.has_shadow:
        return _fail(
            "install-drift",
            "shadow install detected",
            *report.messages,
        )
    if report.messages:
        return _warn("install-drift", "potential install drift", *report.messages)
    return _ok(
        "install-drift",
        f"{len(report.locations)} tokenpak location(s), {len(report.dist_infos)} dist-info entry",
    )


def _check_proxy_reachable() -> CheckResult:
    port = int(os.environ.get("TOKENPAK_PORT", "8766"))
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/healthz", timeout=1.5
        ) as resp:
            if resp.status == 200:
                return _ok("proxy", f"proxy reachable on 127.0.0.1:{port}")
    except Exception:
        pass
    # /health is the proxy's actual endpoint name — try that as fallback.
    try:
        import urllib.request

        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/health", timeout=1.5
        ) as resp:
            if resp.status == 200:
                return _ok("proxy", f"proxy reachable on 127.0.0.1:{port}")
    except Exception:
        pass
    return _warn(
        "proxy",
        f"proxy not responding on 127.0.0.1:{port}",
        "Run `tokenpak start` if you expected it to be up.",
    )


def run_core_checks() -> list[CheckResult]:
    """Return the standard `tokenpak doctor` check suite."""
    return [
        _check_version_consistency(),
        _check_install_drift(),
        _check_proxy_reachable(),
    ]


# ── Claude Code checks ─────────────────────────────────────────────────

def _check_claude_binary() -> CheckResult:
    binary = shutil.which("claude")
    if not binary:
        return _fail(
            "claude-binary",
            "`claude` CLI not found on PATH",
            "Install Claude Code from https://claude.com/docs/claude-code",
        )
    return _ok("claude-binary", f"claude CLI present at {binary}")


def _check_companion_settings() -> CheckResult:
    settings = Path.home() / ".tokenpak" / "companion" / "run" / "settings.json"
    if not settings.exists():
        return _warn(
            "companion-settings",
            "companion settings.json not found",
            f"expected at {settings}",
            "Run `tokenpak claude` once to generate it.",
        )
    try:
        import json

        with open(settings) as f:
            data = json.load(f)
    except Exception as exc:
        return _fail("companion-settings", f"settings.json unparseable: {exc}")
    hook_cmd = None
    try:
        hook_cmd = data["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
    except (KeyError, IndexError, TypeError):
        pass
    if not hook_cmd:
        return _fail("companion-settings", "UserPromptSubmit hook missing from settings.json")
    if " -P " not in hook_cmd and "-P" not in hook_cmd.split():
        return _warn(
            "companion-settings",
            "hook missing `-P` flag (1.2.7+)",
            f"current: {hook_cmd}",
            "Run `tokenpak claude` to regenerate.",
        )
    return _ok("companion-settings", "settings.json + hook cmd valid")


def _check_anthropic_base_url_routing() -> CheckResult:
    """When tokenpak claude is active, ANTHROPIC_BASE_URL should route
    to the local proxy."""
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    if not base_url:
        return _warn(
            "base-url-routing",
            "ANTHROPIC_BASE_URL not set",
            "Claude Code traffic will go direct to api.anthropic.com.",
            "`tokenpak claude` sets this automatically when you launch via it.",
        )
    if "127.0.0.1" in base_url or "localhost" in base_url:
        return _ok("base-url-routing", f"ANTHROPIC_BASE_URL={base_url}")
    return _warn(
        "base-url-routing",
        f"ANTHROPIC_BASE_URL={base_url} is not local",
        "Traffic won't traverse the local tokenpak proxy.",
    )


def run_claude_code_checks() -> list[CheckResult]:
    """Claude-Code-specific diagnostics — invoked by `doctor --claude-code`."""
    return [
        _check_claude_binary(),
        _check_companion_settings(),
        _check_anthropic_base_url_routing(),
    ]


__all__ = [
    "CheckResult",
    "CheckStatus",
    "run_core_checks",
    "run_claude_code_checks",
]
