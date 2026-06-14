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
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from tokenpak._formatting.shell_detect import render_env_var as _env

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
    applier: Optional[Callable[[str], "ApplyResult"]] = None  # None = print-only
    backup_locator: Optional[Callable[[], Optional[Path]]] = None  # for --revert
    preview_fn: Optional[Callable[[str], str]] = None  # human diff preview
    verify_fn: Optional[Callable[[str], "tuple[bool, str]"]] = None  # post-apply check
    notes: list[str] = field(default_factory=list)


@dataclass
class ApplyResult:
    """Outcome of an --apply run for a single client."""
    ok: bool
    summary: str                              # one-line human summary
    changes: list[str] = field(default_factory=list)
    backup_path: Optional[str] = None         # where the old config was preserved
    error: Optional[str] = None               # populated when ok=False
    rollback_cmd: Optional[str] = None        # if ok=True but user wants to revert


def _instr_claude_code(proxy_url: str) -> str:
    return (
        f"No API key required — Claude Code reads your OAuth credentials\n"
        f"from ~/.claude/.credentials.json, and the proxy forwards them\n"
        f"byte-preserved so your subscription billing is untouched.\n\n"
        f"Set before launching Claude Code (or persist in your shell rc):\n"
        f"    {_env('ANTHROPIC_BASE_URL', proxy_url)}\n\n"
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
        f"  {_env('ANTHROPIC_API_BASE', proxy_url)}\n"
        f"  aider --model anthropic/claude-sonnet-4-6\n\n"
        f"  # OpenAI models\n"
        f"  {_env('OPENAI_API_BASE', proxy_url + '/v1')}\n"
        f"  aider --model gpt-4o"
    )


def _instr_openai_sdk(proxy_url: str) -> str:
    return (
        f"Python OpenAI SDK — override the base_url:\n\n"
        f"    from openai import OpenAI\n"
        f"    client = OpenAI(base_url=\"{proxy_url}/v1\", api_key=\"<your key>\")\n\n"
        f"Or env var (picked up automatically):\n"
        f"    {_env('OPENAI_BASE_URL', proxy_url + '/v1')}"
    )


def _instr_anthropic_sdk(proxy_url: str) -> str:
    return (
        f"Python Anthropic SDK — override base_url:\n\n"
        f"    from anthropic import Anthropic\n"
        f"    client = Anthropic(base_url=\"{proxy_url}\", api_key=\"<your key>\")\n\n"
        f"Or env var:\n"
        f"    {_env('ANTHROPIC_BASE_URL', proxy_url)}"
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
        f"    {_env('OPENAI_BASE_URL', proxy_url + '/v1')}\n"
        f"    codex exec \"your prompt\"\n\n"
        f"tokenpak's Codex adapter handles the OAuth credential injection;\n"
        f"see project_tokenpak_codex_three_paths memory for path choice."
    )


# ---------------------------------------------------------------------------
# Appliers — per-client logic to actually modify config. Each takes the
# proxy_url and returns an ApplyResult. Clients without an applier will
# fall through to a print-only response when --apply is used.
# ---------------------------------------------------------------------------


def _apply_claude_code(proxy_url: str) -> ApplyResult:
    """Write ANTHROPIC_BASE_URL + TOKENPAK_PROFILE into Claude Code settings.json.

    Cribs from tokenpak/cli/commands/install.py which already has atomic-write
    + backup logic. Always backs up the existing settings.json first so users
    can revert with `cp ~/.claude/settings.json.bak ~/.claude/settings.json`.
    """
    try:
        from tokenpak.cli.commands.install import (
            MODE_PROFILE_MAP,
            _atomic_write_settings,
            _backup_settings,
            _read_settings,
            _settings_path,
            auto_detect_mode,
        )
    except Exception as exc:  # pragma: no cover — import failure
        return ApplyResult(
            ok=False,
            summary="Claude Code install helpers unavailable",
            error=str(exc),
        )

    bak: Optional[Path] = None
    try:
        bak = _backup_settings()
        settings = _read_settings()
        env = settings.setdefault("env", {})
        prev_base = env.get("ANTHROPIC_BASE_URL")
        prev_profile = env.get("TOKENPAK_PROFILE")

        mode = auto_detect_mode()
        profile = MODE_PROFILE_MAP.get(mode, "balanced")

        env["ANTHROPIC_BASE_URL"] = proxy_url
        env["TOKENPAK_PROFILE"] = profile
        _atomic_write_settings(settings)

        changes: list[str] = []
        if prev_base != proxy_url:
            changes.append(f"env.ANTHROPIC_BASE_URL: {prev_base or '(unset)'} → {proxy_url}")
        if prev_profile != profile:
            changes.append(
                f"env.TOKENPAK_PROFILE: {prev_profile or '(unset)'} → {profile} "
                f"(detected mode: {mode})"
            )
        if not changes:
            return ApplyResult(
                ok=True,
                summary="Claude Code already configured — no changes.",
                backup_path=str(bak) if bak else None,
            )

        settings_p = _settings_path()
        rollback = f"cp {bak} {settings_p}" if bak else f"edit {settings_p} manually"
        return ApplyResult(
            ok=True,
            summary=f"Updated {settings_p} ({len(changes)} change{'s' if len(changes) != 1 else ''}).",
            changes=changes,
            backup_path=str(bak) if bak else None,
            rollback_cmd=rollback,
        )
    except Exception as exc:
        # Best-effort rollback if we have a backup
        try:
            if bak is not None:
                from tokenpak.cli.commands.install import restore_backup
                restore_backup(bak)
        except Exception:
            pass
        return ApplyResult(
            ok=False,
            summary="Claude Code apply failed (rollback attempted).",
            error=str(exc),
            backup_path=str(bak) if bak else None,
        )


def _cursor_settings_path() -> Optional[Path]:
    """Return the platform's Cursor user settings.json path IF Cursor is installed.

    Detection requires the Cursor User/ directory to already exist — Cursor
    creates it on first launch. Returning None means "Cursor not installed
    here"; callers should refuse to apply rather than creating config files
    for an app that isn't present.
    """
    import sys as _sys
    home = Path.home()
    candidates: list[Path] = []
    if _sys.platform == "darwin":
        candidates.append(home / "Library/Application Support/Cursor/User/settings.json")
    elif _sys.platform == "win32":
        appdata = os.environ.get("APPDATA", "")
        if appdata:
            candidates.append(Path(appdata) / "Cursor" / "User" / "settings.json")
    else:  # linux + everything else
        candidates.append(home / ".config/Cursor/User/settings.json")
        candidates.append(home / ".config/cursor/User/settings.json")
    # Only return a path where the Cursor User/ dir already exists — that's
    # proof Cursor has been launched at least once on this host.
    for p in candidates:
        if p.parent.exists():
            return p
    return None


def _apply_cursor(proxy_url: str) -> ApplyResult:
    """Write cursor.general.openaiBaseUrl + anthropic equivalents into Cursor settings.json.

    Cursor stores user settings in a VS Code-style settings.json. Keys vary
    across Cursor versions — we only touch the documented widely-supported
    ones (`cursor.general.*`). We never touch the user's API key; the user
    must set that themselves through Cursor's UI.
    """
    import shutil as _shutil

    settings_path = _cursor_settings_path()
    if settings_path is None:
        return ApplyResult(
            ok=False,
            summary="Cursor install not detected (no User/settings.json path found).",
            error="cursor_not_found",
        )

    bak: Optional[Path] = None
    try:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        if settings_path.exists():
            bak = settings_path.with_suffix(".json.bak")
            _shutil.copy2(settings_path, bak)
            import json as _json
            try:
                config = _json.loads(settings_path.read_text(encoding="utf-8"))
                if not isinstance(config, dict):
                    config = {}
            except Exception as exc:
                return ApplyResult(
                    ok=False,
                    summary="Could not parse existing Cursor settings.json.",
                    error=str(exc),
                    backup_path=str(bak),
                )
        else:
            config = {}

        # Keys Cursor respects at time of writing. Harmless if the user's
        # Cursor version ignores one — they'll still be in the file.
        new_keys = {
            "cursor.general.openaiBaseUrl": proxy_url.rstrip("/") + "/v1",
            "cursor.general.anthropicBaseUrl": proxy_url,
        }
        changes: list[str] = []
        for k, v in new_keys.items():
            prev = config.get(k)
            if prev != v:
                config[k] = v
                changes.append(f"{k}: {prev or '(unset)'} → {v}")

        if not changes:
            return ApplyResult(
                ok=True,
                summary="Cursor already configured — no changes.",
                backup_path=str(bak) if bak else None,
            )

        import json as _json2
        tmp = settings_path.with_suffix(".json.tmp")
        tmp.write_text(_json2.dumps(config, indent=2), encoding="utf-8")
        os.replace(tmp, settings_path)

        rollback = (
            f"cp {bak} {settings_path}" if bak
            else f"remove cursor.general.openaiBaseUrl / anthropicBaseUrl from {settings_path}"
        )
        return ApplyResult(
            ok=True,
            summary=f"Updated {settings_path} ({len(changes)} key{'s' if len(changes) != 1 else ''}). "
                    f"Your API key must still be set in Cursor's UI.",
            changes=changes,
            backup_path=str(bak) if bak else None,
            rollback_cmd=rollback,
        )
    except Exception as exc:
        if bak is not None and settings_path.exists():
            try:
                _shutil.copy2(bak, settings_path)
            except Exception:
                pass
        return ApplyResult(
            ok=False,
            summary="Cursor apply failed (rollback attempted).",
            error=str(exc),
            backup_path=str(bak) if bak else None,
        )


def _apply_cline(proxy_url: str) -> ApplyResult:
    """Cline stores API config in VS Code globalState (LevelDB).

    We can't reliably read/write that from a CLI without opening a lock on
    the user's live VS Code instance. This applier reports that clearly
    rather than silently pretending to succeed.
    """
    return ApplyResult(
        ok=False,
        summary="Cline stores API config in VS Code globalState (not a JSON file).",
        error="cline_globalstate_not_writable_externally",
        rollback_cmd=(
            "Set manually: open the Cline panel in VS Code (gear icon) →"
            f" Provider: Anthropic → Base URL: {proxy_url} → API Key: your own"
        ),
    )


def _apply_aider(proxy_url: str) -> ApplyResult:
    """Write Aider config to ~/.aider.conf.yml (simple key:value YAML).

    Aider accepts a yaml config with api-base entries per provider. We only
    set the base URLs — the user keeps their own API keys in ~/.aider.conf.yml
    or env vars.
    """
    import shutil as _shutil

    conf = Path.home() / ".aider.conf.yml"
    openai_base = proxy_url.rstrip("/") + "/v1"

    bak: Optional[Path] = None
    try:
        existing = ""
        if conf.exists():
            bak = conf.with_suffix(".yml.bak")
            _shutil.copy2(conf, bak)
            existing = conf.read_text(encoding="utf-8")

        # Simple key-rewrite over the YAML text. Aider's config is flat, so we
        # can safely do line-level matching for the two keys we manage without
        # needing a YAML parser. Any key we didn't write gets preserved.
        lines = existing.splitlines() if existing else []
        managed_keys = {
            "openai-api-base": openai_base,
            "anthropic-api-base": proxy_url,
        }
        seen: dict[str, int] = {}
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            for key in managed_keys:
                if stripped.startswith(f"{key}:"):
                    lines[i] = f"{key}: {managed_keys[key]}"
                    seen[key] = i
                    break

        changes: list[str] = []
        for key, val in managed_keys.items():
            if key not in seen:
                lines.append(f"{key}: {val}")
                changes.append(f"added {key}: {val}")
            else:
                changes.append(f"set {key}: {val}")

        new_text = "\n".join(lines).strip() + "\n"
        if new_text == existing:
            return ApplyResult(
                ok=True,
                summary="Aider already configured — no changes.",
                backup_path=str(bak) if bak else None,
            )

        tmp = conf.with_suffix(".yml.tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, conf)

        rollback = f"cp {bak} {conf}" if bak else f"remove openai-api-base / anthropic-api-base from {conf}"
        return ApplyResult(
            ok=True,
            summary=f"Updated {conf} ({len(changes)} change{'s' if len(changes) != 1 else ''}). "
                    f"Your API key must still be set (env var or --api-key).",
            changes=changes,
            backup_path=str(bak) if bak else None,
            rollback_cmd=rollback,
        )
    except Exception as exc:
        if bak is not None:
            try:
                _shutil.copy2(bak, conf)
            except Exception:
                pass
        return ApplyResult(
            ok=False,
            summary="Aider apply failed (rollback attempted).",
            error=str(exc),
            backup_path=str(bak) if bak else None,
        )


def _apply_continue(proxy_url: str) -> ApplyResult:
    """Add tokenpak-* model entries to ~/.continue/config.json.

    Continue.dev reads its model list from ~/.continue/config.{json,yaml}.
    We target config.json because it doesn't require a YAML parser in the
    stdlib-only companion. If only config.yaml exists we fall back to
    print-only with a helpful error rather than clobber the user's YAML.
    """
    import shutil as _shutil

    continue_dir = Path.home() / ".continue"
    config_json = continue_dir / "config.json"
    config_yaml = continue_dir / "config.yaml"

    # If user only has YAML, don't clobber it — prompt to convert or edit manually.
    if not config_json.exists() and config_yaml.exists():
        return ApplyResult(
            ok=False,
            summary="Found config.yaml; tokenpak auto-apply only writes config.json.",
            error="yaml_config_present",
            rollback_cmd=f"edit {config_yaml} manually using the printed snippet",
        )

    bak: Optional[Path] = None
    try:
        continue_dir.mkdir(parents=True, exist_ok=True)
        if config_json.exists():
            bak = config_json.with_suffix(".json.bak")
            _shutil.copy2(config_json, bak)
            import json as _json
            try:
                config = _json.loads(config_json.read_text(encoding="utf-8"))
            except Exception as exc:
                return ApplyResult(
                    ok=False,
                    summary="Could not parse existing config.json.",
                    error=str(exc),
                    backup_path=str(bak),
                )
        else:
            config = {}

        models = config.setdefault("models", [])
        if not isinstance(models, list):
            return ApplyResult(
                ok=False,
                summary="config.json 'models' field is not a list — refusing to touch.",
                error="models_not_list",
                backup_path=str(bak) if bak else None,
            )

        openai_base = proxy_url.rstrip("/") + "/v1"
        tp_entries = [
            {
                "title": "tokenpak-sonnet",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "apiBase": proxy_url,
            },
            {
                "title": "tokenpak-opus",
                "provider": "anthropic",
                "model": "claude-opus-4-7",
                "apiBase": proxy_url,
            },
            {
                "title": "tokenpak-gpt4o",
                "provider": "openai",
                "model": "gpt-4o",
                "apiBase": openai_base,
            },
        ]

        changes: list[str] = []
        existing_titles = {m.get("title") for m in models if isinstance(m, dict)}
        for entry in tp_entries:
            title = entry["title"]
            # If a previous tokenpak-* entry exists, replace in place
            replaced = False
            for i, m in enumerate(models):
                if isinstance(m, dict) and m.get("title") == title:
                    if m == entry:
                        break  # already identical
                    models[i] = entry
                    changes.append(f"updated model: {title}")
                    replaced = True
                    break
            if title not in existing_titles and not replaced:
                models.append(entry)
                changes.append(f"added model: {title}")

        if not changes:
            return ApplyResult(
                ok=True,
                summary="Continue.dev already configured — no changes.",
                backup_path=str(bak) if bak else None,
            )

        # Atomic write
        import json as _json2
        tmp = config_json.with_suffix(".json.tmp")
        tmp.write_text(_json2.dumps(config, indent=2), encoding="utf-8")
        os.replace(tmp, config_json)

        rollback = (
            f"cp {bak} {config_json}" if bak
            else f"remove the tokenpak-* entries from {config_json}"
        )
        return ApplyResult(
            ok=True,
            summary=f"Updated {config_json} ({len(changes)} change{'s' if len(changes) != 1 else ''}).",
            changes=changes,
            backup_path=str(bak) if bak else None,
            rollback_cmd=rollback,
        )
    except Exception as exc:
        if bak is not None and config_json.exists():
            try:
                _shutil.copy2(bak, config_json)
            except Exception:
                pass
        return ApplyResult(
            ok=False,
            summary="Continue.dev apply failed (rollback attempted).",
            error=str(exc),
            backup_path=str(bak) if bak else None,
        )


# ---------------------------------------------------------------------------
# Backup locators — return existing .bak path for --revert, or None.
# Convention: all appliers write <original_path>.bak (e.g. settings.json.bak).
# ---------------------------------------------------------------------------


def _bak_claude_code() -> Optional[Path]:
    try:
        from tokenpak.cli.commands.install import _settings_path
        bak = _settings_path().with_suffix(".json.bak")
        return bak if bak.exists() else None
    except Exception:
        return None


def _bak_cursor() -> Optional[Path]:
    p = _cursor_settings_path()
    if p is None:
        return None
    bak = p.with_suffix(".json.bak")
    return bak if bak.exists() else None


def _bak_aider() -> Optional[Path]:
    bak = Path.home() / ".aider.conf.yml.bak"
    return bak if bak.exists() else None


def _bak_continue() -> Optional[Path]:
    bak = Path.home() / ".continue" / "config.json.bak"
    return bak if bak.exists() else None


# ---------------------------------------------------------------------------
# Preview functions — human-readable diff of intended change (for guided form)
# ---------------------------------------------------------------------------


def _preview_claude_code(proxy_url: str) -> str:
    try:
        from tokenpak.cli.commands.install import _read_settings, _settings_path
        settings = _read_settings()
        current = settings.get("env", {}).get("ANTHROPIC_BASE_URL", "(unset)")
        return (
            f"  File:  {_settings_path()}\n"
            f"  env.ANTHROPIC_BASE_URL: {current!r} → {proxy_url!r}"
        )
    except Exception:
        return f"  Will set env.ANTHROPIC_BASE_URL={proxy_url} in Claude Code settings.json"


def _preview_cursor(proxy_url: str) -> str:
    p = _cursor_settings_path()
    if p is None:
        return "  Cursor settings not found — will create a new settings.json"
    try:
        import json as _json
        config = _json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        current = config.get("cursor.general.anthropicBaseUrl", "(unset)")
        return (
            f"  File:  {p}\n"
            f"  cursor.general.anthropicBaseUrl: {current!r} → {proxy_url!r}"
        )
    except Exception:
        return f"  Will update cursor.general.anthropicBaseUrl in {p}"


def _preview_aider(proxy_url: str) -> str:
    conf = Path.home() / ".aider.conf.yml"
    if not conf.exists():
        return f"  Will create {conf} with anthropic-api-base: {proxy_url}"
    try:
        for line in conf.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("anthropic-api-base:"):
                current = line.split(":", 1)[1].strip()
                return f"  File:  {conf}\n  anthropic-api-base: {current!r} → {proxy_url!r}"
        return f"  File:  {conf}\n  Will add anthropic-api-base: {proxy_url}"
    except Exception:
        return f"  Will update anthropic-api-base in {conf}"


def _preview_continue(proxy_url: str) -> str:
    config_json = Path.home() / ".continue" / "config.json"
    if not config_json.exists():
        return f"  Will create {config_json} with tokenpak-* model entries"
    return (
        f"  File:  {config_json}\n"
        f"  Will add/update tokenpak-sonnet, tokenpak-opus, tokenpak-gpt4o entries"
    )


# ---------------------------------------------------------------------------
# Verify functions — post-apply quick check (returns (ok, message))
# ---------------------------------------------------------------------------


def _verify_claude_code(proxy_url: str) -> tuple[bool, str]:
    try:
        from tokenpak.cli.commands.install import _read_settings
        settings = _read_settings()
        got = settings.get("env", {}).get("ANTHROPIC_BASE_URL", "")
        if got == proxy_url:
            return (True, f"ANTHROPIC_BASE_URL={got} — proxy route active")
        return (False, f"ANTHROPIC_BASE_URL={got!r} (expected {proxy_url!r})")
    except Exception as exc:
        return (False, f"verify read failed: {exc}")


def _verify_cursor(proxy_url: str) -> tuple[bool, str]:
    p = _cursor_settings_path()
    if p is None or not p.exists():
        return (False, "Cursor settings.json not found after apply")
    try:
        import json as _json
        config = _json.loads(p.read_text(encoding="utf-8"))
        got = config.get("cursor.general.anthropicBaseUrl", "")
        if got == proxy_url:
            return (True, f"cursor.general.anthropicBaseUrl={got} — proxy route active")
        return (False, f"cursor.general.anthropicBaseUrl={got!r} (expected {proxy_url!r})")
    except Exception as exc:
        return (False, f"verify read failed: {exc}")


def _verify_aider(proxy_url: str) -> tuple[bool, str]:
    conf = Path.home() / ".aider.conf.yml"
    if not conf.exists():
        return (False, ".aider.conf.yml not found after apply")
    try:
        for line in conf.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("anthropic-api-base:"):
                got = line.split(":", 1)[1].strip()
                if got == proxy_url:
                    return (True, f"anthropic-api-base={got} — proxy route active")
                return (False, f"anthropic-api-base={got!r} (expected {proxy_url!r})")
        return (False, "anthropic-api-base key not found in .aider.conf.yml")
    except Exception as exc:
        return (False, f"verify read failed: {exc}")


def _verify_continue(proxy_url: str) -> tuple[bool, str]:
    config_json = Path.home() / ".continue" / "config.json"
    if not config_json.exists():
        return (False, "Continue config.json not found after apply")
    try:
        import json as _json
        config = _json.loads(config_json.read_text(encoding="utf-8"))
        tp_models = [
            m for m in config.get("models", [])
            if isinstance(m, dict) and str(m.get("title", "")).startswith("tokenpak-")
        ]
        if tp_models:
            return (True, f"{len(tp_models)} tokenpak-* model entries present — proxy route active")
        return (False, "No tokenpak-* model entries found in config.json")
    except Exception as exc:
        return (False, f"verify read failed: {exc}")


# ---------------------------------------------------------------------------
# Revert — restore most recent backup atomically (write-temp + rename)
# ---------------------------------------------------------------------------


def _revert_integration(integration: Integration) -> ApplyResult:
    """Restore the most recent .bak for the given integration target."""
    if integration.backup_locator is None:
        return ApplyResult(
            ok=False,
            summary=f"--revert not supported for {integration.label} (no backup convention).",
            error="no_backup_locator",
        )
    bak = integration.backup_locator()
    if bak is None:
        return ApplyResult(
            ok=True,
            summary=f"Nothing to revert — no backup found for {integration.label}.",
        )
    # Target: strip trailing .bak from the backup filename
    # e.g. settings.json.bak → settings.json  (.stem of a path whose suffix is .bak)
    target = bak.parent / bak.stem
    try:
        tmp = target.with_name(target.name + ".revert_tmp")
        shutil.copy2(bak, tmp)
        os.replace(tmp, target)
        return ApplyResult(
            ok=True,
            summary=f"Reverted {target} from {bak.name}.",
            changes=[f"restored {target} from {bak}"],
            backup_path=str(bak),
        )
    except Exception as exc:
        return ApplyResult(
            ok=False,
            summary=f"Revert failed for {integration.label}.",
            error=str(exc),
            backup_path=str(bak),
        )


# ---------------------------------------------------------------------------
# Guided interactive form (TTY + no --apply + no --no-tui)
# ---------------------------------------------------------------------------

# Keys whose applier is None or whose config is not directly writable.
_PRINT_ONLY_KEYS: frozenset[str] = frozenset(
    {"cline", "codex", "openai-sdk", "anthropic-sdk", "litellm"}
)


def _tty_confirm(prompt: str, default: bool = True) -> bool:
    """Readline-style [Y/n] prompt. Returns default on empty input."""
    hint = "[Y/n]" if default else "[y/N]"
    sys.stdout.write(f"\n  {prompt} {hint}: ")
    sys.stdout.flush()
    try:
        line = sys.stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if not line:
        return default
    return line in ("y", "yes")


def _run_guided_form(integration: Integration, proxy_url: str) -> int:
    """Interactive guided integration form (TTY path, no --apply, no --no-tui)."""
    print()
    print(f"  TOKENPAK integrate — {integration.label}  (guided)")
    print("  " + "─" * 42)

    try:
        loc = integration.detector()
    except Exception:
        loc = None
    if loc:
        print(f"  Detected   {loc}")
    else:
        print("  Detected   (not installed on this host — instructions still apply)")

    is_print_only = integration.key in _PRINT_ONLY_KEYS or integration.applier is None

    if is_print_only:
        print()
        print(f"  ⚠  {integration.label} needs a manual step (auto-apply not available).")
        print()
        for ln in integration.instructions(proxy_url).splitlines():
            print("  " + ln)
        print()
        print("  Tip: run  tokenpak status  after following the steps above.")
        return 0

    # Preview intended change
    if integration.preview_fn:
        print()
        print("  Intended change:")
        for ln in integration.preview_fn(proxy_url).splitlines():
            print(ln)

    if not _tty_confirm(f"Apply configuration for {integration.label}?"):
        print("  Cancelled — no changes made.")
        return 0

    # Apply
    try:
        result = integration.applier(proxy_url)  # type: ignore[misc]
    except Exception as exc:  # pragma: no cover
        result = ApplyResult(ok=False, summary="applier raised unexpectedly", error=str(exc))

    print(_render_apply(integration, result))
    if not result.ok:
        return 1

    # Post-apply verify
    if integration.verify_fn:
        ok, msg = integration.verify_fn(proxy_url)
        badge = "✅" if ok else "✖"
        print(f"  Verify     {badge} {msg}")
    else:
        print("  Verify     run  tokenpak status  to confirm proxy traffic is flowing")

    if result.backup_path:
        print()
        print(f"  To revert:  tokenpak integrate {integration.key} --revert")

    print()
    return 0


# ---------------------------------------------------------------------------
# Permission tiers (claude-code / codex only)
#
# Persistent trust level (strict/standard/auto → client config) vs runtime
# unattended bypass (fleet → TokenPak launcher state only). See
# tokenpak/cli/commands/permissions.py for the canonical mapping + write
# discipline. Tier handling only engages when the invoking argparse
# namespace carries a ``tier`` attribute — the real CLI parser always sets
# it; legacy programmatic callers without the attribute keep the exact
# pre-tier behavior.
# ---------------------------------------------------------------------------

_TIER_CLIENTS: frozenset[str] = frozenset({"claude-code", "codex"})


def _prompt_tier() -> Optional[str]:
    """Interactive tier picker (TTY path). Returns None on cancel."""
    from tokenpak.cli.commands.permissions import (
        ALL_TIERS,
        DEFAULT_TIER,
        TIER_DESCRIPTIONS,
    )

    print()
    print("  Permission tier:")
    for i, t in enumerate(ALL_TIERS, 1):
        marker = " (default)" if t == DEFAULT_TIER else ""
        print(f"    {i}. {t:<8}{marker} — {TIER_DESCRIPTIONS[t]}")
    sys.stdout.write(f"  Choose [1-{len(ALL_TIERS)}] (Enter = {DEFAULT_TIER}): ")
    sys.stdout.flush()
    try:
        line = sys.stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    if not line:
        return DEFAULT_TIER
    if line in ALL_TIERS:
        return line
    if line.isdigit() and 1 <= int(line) <= len(ALL_TIERS):
        return ALL_TIERS[int(line) - 1]
    print(f"  Unrecognized choice {line!r} — cancelled, no tier applied.")
    return None


def _resolve_apply_tier(args: argparse.Namespace) -> Optional[str]:
    """Resolve the tier for an --apply run. Returns None when aborted.

    Flag wins; otherwise prompt on a TTY; otherwise silently default to
    ``standard``. ``fleet`` always requires explicit confirmation (TTY
    prompt or --yes) and prints a warning first.
    """
    tier = getattr(args, "tier", None)
    interactive = _is_interactive() and not _is_no_tui()
    if tier is None:
        if interactive:
            tier = _prompt_tier()
            if tier is None:
                return None
        else:
            tier = "standard"
    if tier == "fleet":
        from tokenpak.cli.commands.permissions import _FLEET_WARNING

        print()
        print(f"  ⚠  {_FLEET_WARNING}")
        if getattr(args, "yes", False):
            return "fleet"
        if interactive:
            if _tty_confirm("Enable fleet mode (launcher bypass flags)?", default=False):
                return "fleet"
            print("  Cancelled — fleet mode unchanged.")
            return None
        print(
            "integrate: --tier fleet requires --yes in non-interactive mode "
            "(explicit opt-in)."
        )
        return None
    return tier


def _render_tier_result(client_key: str, title: str, result) -> str:
    """Compact display block for a tier apply outcome."""
    lines: list[str] = [""]
    lines.append(f"  Permission tier — {client_key}  ({title})")
    lines.append("  " + "─" * 40)
    badge = "✅ Applied" if result.ok else "✖ Failed"
    lines.append(f"  {badge}: {result.summary}")
    for c in result.changes:
        lines.append(f"    • {c}")
    if result.backup_path:
        lines.append(f"  Backup    {result.backup_path}")
    if result.rollback_cmd:
        lines.append(f"  Rollback  {result.rollback_cmd}")
    if result.error:
        lines.append(f"  Error     {result.error}")
    return "\n".join(lines)


def _apply_tier_and_render(client_key: str, tier: str, backup: bool) -> int:
    """Apply tier (or fleet launcher state) for one client + print outcome."""
    from tokenpak.cli.commands import permissions as _perms

    if tier == "fleet":
        # Fleet never persists into client config. The persistent tier is
        # left exactly as-is when one is already applied; on a fresh config
        # the default tier is applied so the client has a defined baseline.
        applied = _perms.applied_tier(client_key)
        if applied is None:
            result = _perms.apply_tier(client_key, _perms.DEFAULT_TIER, backup=backup)
            print(_render_tier_result(client_key, f"persistent: {_perms.DEFAULT_TIER}", result))
            if not result.ok:
                return 1
        else:
            print(f"\n  Persistent tier unchanged ({applied}) — fleet never persists.")
        _perms.set_fleet_mode(
            True, f"tokenpak integrate {client_key} --apply --tier fleet"
        )
        print("  ✅ Launcher fleet mode: enabled (TokenPak-owned state only).")
        print(
            "     `tokenpak claude` / `tokenpak codex` will inject bypass flags "
            "and print a banner."
        )
        return 0

    result = _perms.apply_tier(client_key, tier, backup=backup)
    print(_render_tier_result(client_key, f"tier: {tier}", result))
    return 0 if result.ok else 1


# Dynamic registry — add a new client by appending one Integration here.
INTEGRATIONS: list[Integration] = [
    Integration(
        key="claude-code",
        label="Claude Code",
        kind="client",
        detector=_detect_claude_cli,
        instructions=_instr_claude_code,
        applier=_apply_claude_code,
        backup_locator=_bak_claude_code,
        preview_fn=_preview_claude_code,
        verify_fn=_verify_claude_code,
    ),
    Integration(
        key="cursor",
        label="Cursor",
        kind="client",
        detector=_detect_cursor_app,
        instructions=_instr_cursor,
        applier=_apply_cursor,
        backup_locator=_bak_cursor,
        preview_fn=_preview_cursor,
        verify_fn=_verify_cursor,
    ),
    Integration(
        key="cline",
        label="Cline (VS Code extension)",
        kind="client",
        detector=lambda: _detect_vscode_extension("saoudrizwan.claude-dev"),
        instructions=_instr_cline,
        applier=_apply_cline,
    ),
    Integration(
        key="continue",
        label="Continue.dev",
        kind="client",
        detector=lambda: _detect_vscode_extension("continue.continue"),
        instructions=_instr_continue,
        applier=_apply_continue,
        backup_locator=_bak_continue,
        preview_fn=_preview_continue,
        verify_fn=_verify_continue,
    ),
    Integration(
        key="aider",
        label="Aider",
        kind="client",
        detector=_detect_aider,
        instructions=_instr_aider,
        applier=_apply_aider,
        backup_locator=_bak_aider,
        preview_fn=_preview_aider,
        verify_fn=_verify_aider,
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
        lines.append("  Detected   (not installed on this host — instructions below still apply)")
    lines.append("")
    for ln in integration.instructions(proxy_url).splitlines():
        lines.append("  " + ln)
    lines.append("")
    lines.append("  After setup, verify with:  tokenpak status")
    lines.append("")
    return "\n".join(lines)


def _render_apply(integration: Integration, result: ApplyResult) -> str:
    """Format an ApplyResult for human display."""
    lines: list[str] = [""]
    lines.append(f"  TOKENPAK integrate — {integration.label}  (--apply)")
    lines.append("  " + "─" * 40)
    badge = "✅ Applied" if result.ok else "✖ Failed"
    lines.append(f"  {badge}: {result.summary}")
    if result.changes:
        lines.append("")
        lines.append("  Changes:")
        for c in result.changes:
            lines.append(f"    • {c}")
    if result.backup_path:
        lines.append("")
        lines.append(f"  Backup    {result.backup_path}")
    if result.rollback_cmd:
        lines.append(f"  Rollback  {result.rollback_cmd}")
    if result.error:
        lines.append("")
        lines.append(f"  Error     {result.error}")
    lines.append("")
    return "\n".join(lines)


def run_integrate(args: argparse.Namespace) -> int:
    """CLI handler for `tokenpak integrate`."""
    proxy_url = getattr(args, "proxy_url", None) or DEFAULT_PROXY_URL
    apply_mode = bool(getattr(args, "apply", False))
    revert_mode = bool(getattr(args, "revert", False))
    client = getattr(args, "client", None)
    show_all = getattr(args, "all", False)
    no_tui = _is_no_tui()

    # --revert requires a specific client.
    if revert_mode:
        if not client:
            print("integrate: --revert requires a specific client (e.g. `tokenpak integrate claude-code --revert`).")
            print()
            print(_render_listing(proxy_url))
            return 2
        integration = _find(client)
        if integration is None:
            known = ", ".join(i.key for i in INTEGRATIONS)
            print(f"integrate: unknown client '{client}'. Known clients: {known}")
            return 2
        result = _revert_integration(integration)
        print(_render_apply(integration, result))
        if result.ok and integration.verify_fn:
            ok, msg = integration.verify_fn(proxy_url)
            badge = "✅" if ok else "✖"
            print(f"  Verify     {badge} {msg}")
        print()
        return 0 if result.ok else 1

    # --apply without a specific client is a no-op (ambiguous) — treat as list.
    if apply_mode and not client and not show_all:
        print("integrate: --apply requires a specific client (e.g. `tokenpak integrate claude-code --apply`).")
        print()
        print(_render_listing(proxy_url))
        return 2

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

    if apply_mode:
        # Permission-tier handling (claude-code / codex only; engaged only
        # when the namespace carries a `tier` attribute — see the tier
        # section above for the back-compat contract).
        tier_engaged = integration.key in _TIER_CLIENTS and hasattr(args, "tier")
        tier: Optional[str] = None
        if tier_engaged:
            tier = _resolve_apply_tier(args)
            if tier is None:
                return 1

        if integration.applier is None:
            if tier_engaged and tier is not None:
                # No base-config applier (codex): apply the tier, then print
                # the manual base-URL instructions.
                rc = _apply_tier_and_render(integration.key, tier, backup=True)
                print(_render_one(integration, proxy_url))
                return rc
            # Graceful fallback — print instructions + a note that auto-apply
            # isn't available for this client yet.
            print(_render_one(integration, proxy_url))
            kind_note = (
                "SDKs don't have a config file to write — use the snippet above."
                if integration.kind == "sdk"
                else "Auto-apply not supported for this client yet — paste the "
                     "instructions above manually."
            )
            print(f"  (--apply: {kind_note})")
            print()
            return 0
        try:
            result = integration.applier(proxy_url)
        except Exception as exc:  # pragma: no cover — applier should never raise
            result = ApplyResult(
                ok=False, summary="applier raised unexpectedly", error=str(exc),
            )
        print(_render_apply(integration, result))
        if not result.ok:
            return 1
        if tier_engaged and tier is not None:
            # The base applier already backed up the pre-apply file state;
            # a second backup here would overwrite that .bak with a
            # post-apply copy, so the tier write reuses the existing one.
            return _apply_tier_and_render(integration.key, tier, backup=False)
        return 0

    # Default path — no flags.
    # TTY + not --no-tui → guided interactive form.
    # Non-TTY OR --no-tui → print-only (existing behavior).
    if _is_interactive() and not no_tui:
        return _run_guided_form(integration, proxy_url)

    print(_render_one(integration, proxy_url))
    return 0


def _is_no_tui() -> bool:
    """Return True when --no-tui was stripped from argv."""
    try:
        from tokenpak._cli_core import _no_tui
        return _no_tui()
    except Exception:
        return False


def _is_interactive() -> bool:
    """Return True when both stdin and stdout are TTYs (guided form is possible)."""
    return sys.stdin.isatty() and sys.stdout.isatty()
