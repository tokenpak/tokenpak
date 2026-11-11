# SPDX-License-Identifier: Apache-2.0
"""Native Windows support for the existing Claude companion launcher."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.request import urlopen

from tokenpak import _paths
from tokenpak.companion._proxy_endpoint import (
    PROXY_URL_ENV,
    bounded_call,
    probe_timeout,
    proxy_base_url,
    proxy_port,
)
from tokenpak.companion._python_spawn import python_spawn_prefix
from tokenpak.companion.config import CompanionConfig

_PROMPT_MODULE = "tokenpak.companion.hooks.pre_send"
_NATIVE_MODULE = "tokenpak.companion.hooks.windows_native"
# Seconds one proxy health probe may take, unless
# TOKENPAK_PROXY_PROBE_TIMEOUT_SECONDS sets another value at launch.
DEFAULT_PROBE_TIMEOUT_S = 3.0


def prepare_environment() -> None:
    """Load the local path profile, preserving caller-selected companion options."""
    profile = _paths.home() / "windows-env.json"
    if profile.is_file():
        values = json.loads(profile.read_text(encoding="utf-8"))
        allowed = {"PYTHONUTF8", "PYTHONDONTWRITEBYTECODE", "TIKTOKEN_CACHE_DIR", "TEMP", "TMP"}
        for key, value in values.items():
            if isinstance(value, str) and (key.startswith("TOKENPAK_") or key in allowed):
                os.environ.setdefault(key, value)
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("ENABLE_TOOL_SEARCH", "true")
    # Pin the address every TokenPak launcher resolves (a configured URL
    # wins, normalized), so the hooks, MCP server and status line started
    # under this session use the same proxy.
    os.environ[PROXY_URL_ENV] = proxy_base_url()


def native_hook(action: str) -> dict:
    # Claude's exec-form args avoid Git Bash/PowerShell quoting and WSL bash.exe.
    # The interpreter flags come from the shared version-aware helper, so a
    # Python 3.10 interpreter never receives the 3.11-only ``-P`` flag.
    module = _PROMPT_MODULE if action == "prompt" else _NATIVE_MODULE
    executable, *flags = python_spawn_prefix()
    args = ["-X", "utf8", *flags, "-m", module]
    if action != "prompt":
        args.append(action)
    return {"type": "command", "command": executable, "args": args, "timeout": 10}


def _owned_native_hook(hook: dict) -> bool:
    """True for an exec-form hook this module generated on an earlier merge."""
    args = hook.get("args")
    if hook.get("type") != "command" or not isinstance(args, list):
        return False
    return any(
        args[index] == "-m" and args[index + 1] in (_PROMPT_MODULE, _NATIVE_MODULE)
        for index in range(len(args) - 1)
    )


def status_command() -> str:
    # statusLine lacks exec-form args. Encode a constant PowerShell invocation
    # so an enclosing Git Bash cannot reinterpret Windows paths or punctuation.
    executable, *flags = python_spawn_prefix()
    exe = executable.replace("'", "''")
    switches = " ".join(["-X", "utf8", *flags])
    script = (
        "$OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        f"$input | & '{exe}' {switches} -m {_NATIVE_MODULE} status; exit $LASTEXITCODE"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand " + encoded


def configure_native_settings(
    settings: dict,
    config: CompanionConfig,
    run_dir: Path | None,
    *,
    status_line: bool,
    replaced_commands: set,
) -> None:
    hooks = settings.setdefault("hooks", {})
    # Drop the POSIX shell hooks this overlay replaces and any native hooks a
    # previous merge generated, so repeated merges produce identical settings.
    # User-defined hooks, including custom clear-title hooks, are preserved.
    for entries in hooks.values():
        for entry in entries:
            entry["hooks"] = [
                h
                for h in entry.get("hooks", [])
                if not (
                    h.get("type") == "command"
                    and h.get("command") in replaced_commands
                    and "args" not in h
                )
                and not _owned_native_hook(h)
            ]
    if config.hooks_enabled:
        hooks.setdefault("UserPromptSubmit", []).append({"hooks": [native_hook("prompt")]})
        if run_dir is not None:
            hooks.setdefault("SessionStart", []).append({"hooks": [native_hook("bind")]})
        # Title restoration remains cosmetic; preserve any pre-existing custom title.
        if not any(
            e.get("hooks") for e in hooks.get("SessionStart", []) if e.get("matcher") == "clear"
        ):
            hooks.setdefault("SessionStart", []).append(
                {"matcher": "clear", "hooks": [native_hook("title")]}
            )
    settings["hooks"] = {
        event: [e for e in entries if e.get("hooks")] for event, entries in hooks.items()
    }
    if status_line:
        settings["statusLine"] = {"type": "command", "command": status_command()}


def main() -> int:
    prepare_environment()
    args = sys.argv[1:]
    if args not in (["--help"], ["-h"], ["--version"], ["-v"]):
        url = proxy_base_url()
        timeout = probe_timeout(DEFAULT_PROBE_TIMEOUT_S)
        try:
            port = proxy_port()
        except ValueError as exc:
            print(f"tokenpak: {exc}", file=sys.stderr)
            return 1

        def answered() -> bool:
            with urlopen(url + "/health", timeout=timeout) as response:
                data = json.load(response)
            return data.get("status") == "ok" and bool(data.get("version"))

        def healthy() -> bool:
            # Bounded in wall-clock time, not only per network operation.
            return bool(bounded_call(answered, timeout))

        if not healthy():
            executable, *flags = python_spawn_prefix()
            result = subprocess.call(
                [executable, "-X", "utf8", *flags, "-m", "tokenpak", "start", "--port", str(port)],
                cwd=_paths.home(),
            )
            if result or not healthy():
                print("tokenpak: local proxy did not become healthy", file=sys.stderr)
                return 1
    from tokenpak.companion.launcher import main as launch

    return launch(args)


if __name__ == "__main__":
    raise SystemExit(main())
