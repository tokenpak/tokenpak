# SPDX-License-Identifier: Apache-2.0
"""`tokenpak integrate` — point LLM clients at the tokenpak proxy.

Free-tier GTM helper. Shows users the exact env var or config snippet to
paste so their existing tool (Claude Code, Cursor, Cline, Continue.dev,
Aider, raw OpenAI/Anthropic SDK, LiteLLM, Codex CLI) routes through
tokenpak.

Default is PRINT mode (read-only). `--apply` is reserved for future
auto-config writing with backups — unset today so we never touch a user's
config file without explicit opt-in and per-client write logic.

Design goals:
    - Zero runtime dependency on the proxy (works before it's started).
    - Detection is best-effort and never fails the command.
    - Adding a new client = one Integration entry (stay dynamic, per
      feedback_always_dynamic memory).
"""

from __future__ import annotations

import argparse
import importlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


DEFAULT_PROXY_URL = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")


# ---------------------------------------------------------------------------
# Detectors — each returns a short human-readable location or None.
# ---------------------------------------------------------------------------


def _detect_binary(name: str) -> Optional[str]:
    path = shutil.which(name)
    return path if path else None


def _detect_module(name: str) -> Optional[str]:
    try:
        m = importlib.import_module(name)
        ver = getattr(m, "__version__", "")
        loc = getattr(m, "__file__", None) or "<installed>"
        return f"{loc} (v{ver})" if ver else loc
    except Exception:
        return None


def _detect_vscode_extension(prefix: str) -> Optional[str]:
    """Look in standard VS Code extensions dirs for an extension matching prefix."""
    candidates = [
        Path.home() / ".vscode" / "extensions",
        Path.home() / ".vscode-server" / "extensions",
        Path.home() / ".cursor" / "extensions",
    ]
    for root in candidates:
        if not root.exists():
            continue
        for child in root.iterdir():
            if child.is_dir() and child.name.startswith(prefix):
                return str(child)
    return None


def _detect_cursor_app() -> Optional[str]:
    # macOS, Linux (deb/rpm install), Windows (%LOCALAPPDATA%)
    for p in (
        "/Applications/Cursor.app",
        "/usr/bin/cursor",
        "/usr/local/bin/cursor",
        str(Path.home() / ".local" / "bin" / "cursor"),
    ):
        if Path(p).exists():
            return p
    return _detect_binary("cursor")


def _detect_claude_cli() -> Optional[str]:
    return _detect_binary("claude") or _detect_binary("claude-code")


def _detect_codex_cli() -> Optional[str]:
    loc = _detect_binary("codex")
    if loc:
        return loc
    if (Path.home() / ".codex").exists():
        return str(Path.home() / ".codex")
    return None


def _detect_aider() -> Optional[str]:
    return _detect_binary("aider")


# ---------------------------------------------------------------------------
# Integration record
# ---------------------------------------------------------------------------


@dataclass
class Integration:
    key: str                # CLI arg: "cursor", "cline", etc.
    label: str              # Human-readable: "Cursor"
    kind: str               # "client" (binary/app) | "sdk" (python lib)
    detector: Callable[[], Optional[str]]
    instructions: Callable[[str], str]  # proxy_url -> multi-line instructions
    notes: list[str] = field(default_factory=list)


def _instr_claude_code(proxy_url: str) -> str:
    return (
        f"Set before launching Claude Code (or export permanently):\n"
        f"    export ANTHROPIC_BASE_URL={proxy_url}\n\n"
        f"Claude Code reads OAuth creds from ~/.claude/.credentials.json — the\n"
        f"proxy forwards byte-preserved, so subscription billing is untouched.\n\n"
        f"Verify:\n"
        f"    tokenpak status   # monitor.db should show rows after your next Claude Code turn"
    )


def _instr_cursor(proxy_url: str) -> str:
    return (
        f"Cursor settings (Cmd+, / Ctrl+, → search \"Base URL\"):\n"
        f"    OpenAI Base URL:      {proxy_url}/v1\n"
        f"    Anthropic Base URL:   {proxy_url}\n\n"
        f"Or edit settings.json directly:\n"
        f"    \"cursor.general.openaiApiKey\":  \"<your key>\",\n"
        f"    \"cursor.general.openaiBaseUrl\": \"{proxy_url}/v1\"\n\n"
        f"Cursor re-reads settings on save — no restart needed."
    )


def _instr_cline(proxy_url: str) -> str:
    return (
        f"Cline uses VS Code settings. In the Cline panel (gear icon):\n"
        f"  1. Provider: \"Anthropic\" (or \"OpenAI Compatible\")\n"
        f"  2. Base URL: {proxy_url}\n"
        f"  3. API Key: your existing Anthropic/OpenAI key\n\n"
        f"Or in settings.json:\n"
        f"    \"cline.apiProvider\": \"anthropic\",\n"
        f"    \"cline.apiBaseUrl\": \"{proxy_url}\"\n\n"
        f"Saving reloads Cline automatically."
    )


def _instr_continue(proxy_url: str) -> str:
    return (
        f"Continue.dev config — edit ~/.continue/config.yaml or config.json:\n\n"
        f"  models:\n"
        f"    - name: tokenpak-claude\n"
        f"      provider: anthropic\n"
        f"      model: claude-sonnet-4-6\n"
        f"      apiBase: {proxy_url}\n"
        f"    - name: tokenpak-openai\n"
        f"      provider: openai\n"
        f"      model: gpt-4o\n"
        f"      apiBase: {proxy_url}/v1\n\n"
        f"Continue auto-reloads on save. Pick a tokenpak-* model from the picker."
    )


def _instr_aider(proxy_url: str) -> str:
    return (
        f"Point Aider at tokenpak via env vars:\n\n"
        f"  # Anthropic models\n"
        f"  export ANTHROPIC_API_BASE={proxy_url}\n"
        f"  aider --model anthropic/claude-sonnet-4-6\n\n"
        f"  # OpenAI models\n"
        f"  export OPENAI_API_BASE={proxy_url}/v1\n"
        f"  aider --model gpt-4o"
    )


def _instr_openai_sdk(proxy_url: str) -> str:
    return (
        f"Python OpenAI SDK — override the base_url:\n\n"
        f"    from openai import OpenAI\n"
        f"    client = OpenAI(base_url=\"{proxy_url}/v1\", api_key=\"<your key>\")\n\n"
        f"Or env var (picked up automatically):\n"
        f"    export OPENAI_BASE_URL={proxy_url}/v1"
    )


def _instr_anthropic_sdk(proxy_url: str) -> str:
    return (
        f"Python Anthropic SDK — override base_url:\n\n"
        f"    from anthropic import Anthropic\n"
        f"    client = Anthropic(base_url=\"{proxy_url}\", api_key=\"<your key>\")\n\n"
        f"Or env var:\n"
        f"    export ANTHROPIC_BASE_URL={proxy_url}"
    )


def _instr_litellm(proxy_url: str) -> str:
    return (
        f"LiteLLM config — edit your litellm config.yaml:\n\n"
        f"  model_list:\n"
        f"    - model_name: tokenpak-sonnet\n"
        f"      litellm_params:\n"
        f"        model: anthropic/claude-sonnet-4-6\n"
        f"        api_base: {proxy_url}\n\n"
        f"Or per-call: litellm.completion(model=..., api_base=\"{proxy_url}\")"
    )


def _instr_codex(proxy_url: str) -> str:
    return (
        f"Codex CLI reads OpenAI creds from ~/.codex/auth.json.\n"
        f"Point it at tokenpak with:\n\n"
        f"    export OPENAI_BASE_URL={proxy_url}/v1\n"
        f"    codex exec \"your prompt\"\n\n"
        f"tokenpak's Codex adapter handles the OAuth credential injection;\n"
        f"see project_tokenpak_codex_three_paths memory for path choice."
    )


# Dynamic registry — add a new client by appending one Integration here.
INTEGRATIONS: list[Integration] = [
    Integration(
        key="claude-code",
        label="Claude Code",
        kind="client",
        detector=_detect_claude_cli,
        instructions=_instr_claude_code,
    ),
    Integration(
        key="cursor",
        label="Cursor",
        kind="client",
        detector=_detect_cursor_app,
        instructions=_instr_cursor,
    ),
    Integration(
        key="cline",
        label="Cline (VS Code extension)",
        kind="client",
        detector=lambda: _detect_vscode_extension("saoudrizwan.claude-dev"),
        instructions=_instr_cline,
    ),
    Integration(
        key="continue",
        label="Continue.dev",
        kind="client",
        detector=lambda: _detect_vscode_extension("continue.continue"),
        instructions=_instr_continue,
    ),
    Integration(
        key="aider",
        label="Aider",
        kind="client",
        detector=_detect_aider,
        instructions=_instr_aider,
    ),
    Integration(
        key="codex",
        label="Codex CLI",
        kind="client",
        detector=_detect_codex_cli,
        instructions=_instr_codex,
    ),
    Integration(
        key="openai-sdk",
        label="OpenAI Python SDK",
        kind="sdk",
        detector=lambda: _detect_module("openai"),
        instructions=_instr_openai_sdk,
    ),
    Integration(
        key="anthropic-sdk",
        label="Anthropic Python SDK",
        kind="sdk",
        detector=lambda: _detect_module("anthropic"),
        instructions=_instr_anthropic_sdk,
    ),
    Integration(
        key="litellm",
        label="LiteLLM",
        kind="sdk",
        detector=lambda: _detect_module("litellm"),
        instructions=_instr_litellm,
    ),
]


def _find(key: str) -> Optional[Integration]:
    for i in INTEGRATIONS:
        if i.key == key:
            return i
    return None


def _render_listing(proxy_url: str) -> str:
    """List every supported client with detection status."""
    lines: list[str] = [""]
    lines.append("  TOKENPAK integrate")
    lines.append("  " + "─" * 40)
    lines.append(f"  Proxy URL  {proxy_url}")
    lines.append("")

    clients = [i for i in INTEGRATIONS if i.kind == "client"]
    sdks = [i for i in INTEGRATIONS if i.kind == "sdk"]

    def _row(integration: Integration) -> str:
        try:
            loc = integration.detector()
        except Exception:
            loc = None
        badge = "✓" if loc else "✗"
        suffix = f"  ({loc})" if loc else "  (not detected)"
        return f"    {badge} {integration.key:14s} {integration.label}{suffix}"

    lines.append("  Clients:")
    for i in clients:
        lines.append(_row(i))
    lines.append("")
    lines.append("  SDKs:")
    for i in sdks:
        lines.append(_row(i))
    lines.append("")
    lines.append("  Next step:")
    lines.append("    tokenpak integrate <client>      # show setup instructions")
    lines.append("    tokenpak integrate --all         # show instructions for every supported client")
    lines.append("")
    return "\n".join(lines)


def _render_one(integration: Integration, proxy_url: str) -> str:
    lines: list[str] = [""]
    lines.append(f"  TOKENPAK integrate — {integration.label}")
    lines.append("  " + "─" * 40)
    try:
        loc = integration.detector()
    except Exception:
        loc = None
    if loc:
        lines.append(f"  Detected   {loc}")
    else:
        lines.append(f"  Detected   (not installed on this host — instructions below still apply)")
    lines.append("")
    for ln in integration.instructions(proxy_url).splitlines():
        lines.append("  " + ln)
    lines.append("")
    lines.append("  After setup, verify with:  tokenpak status")
    lines.append("")
    return "\n".join(lines)


def run_integrate(args: argparse.Namespace) -> int:
    """CLI handler for `tokenpak integrate`."""
    proxy_url = getattr(args, "proxy_url", None) or DEFAULT_PROXY_URL

    if getattr(args, "apply", False):
        print(
            "integrate: --apply is not yet implemented — "
            "instructions below are safe to paste manually."
        )
        print()

    client = getattr(args, "client", None)
    show_all = getattr(args, "all", False)

    if show_all:
        for integration in INTEGRATIONS:
            print(_render_one(integration, proxy_url))
        return 0

    if not client:
        print(_render_listing(proxy_url))
        return 0

    integration = _find(client)
    if integration is None:
        known = ", ".join(i.key for i in INTEGRATIONS)
        print(
            f"integrate: unknown client '{client}'. "
            f"Known clients: {known}",
        )
        return 2

    print(_render_one(integration, proxy_url))
    return 0
