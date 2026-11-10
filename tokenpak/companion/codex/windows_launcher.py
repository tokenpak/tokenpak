# SPDX-License-Identifier: Apache-2.0
"""Launch an already-provisioned native Windows Codex companion session.

Uses the user's existing Codex home and permissions. Does not provision or
pretend to support the POSIX-only isolated-home lease/retention implementation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.request import urlopen

from tokenpak import _paths
from tokenpak.companion._python_spawn import python_spawn_prefix
from tokenpak.companion.codex.mcp_config import SERVER_NAME
from tokenpak.status.binding import ENV, create_launch_dir

# Name of the model provider this launcher defines for each session through
# ``-c`` overrides; it routes Codex to the local TokenPak proxy.
MODEL_PROVIDER = "tokenpak_local"


def split_overrides(args: list[str]) -> tuple[list[str], list[str]]:
    overrides, forwarded = [], []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            forwarded.extend(args[index:])
            break
        if arg in {"-c", "--config"}:
            if index + 1 >= len(args):
                raise ValueError(f"{arg} requires a value")
            overrides.extend([arg, args[index + 1]])
            index += 2
        else:
            (overrides if arg.startswith(("-c=", "--config=")) else forwarded).append(arg)
            index += 1
    return overrides, forwarded


def codex_binary(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    explicit = env.get("TOKENPAK_CODEX_BINARY")
    candidates = [explicit, shutil.which("codex.exe")]
    appdata = env.get("APPDATA")
    if appdata:
        candidates.append(
            str(
                Path(appdata)
                / "npm/node_modules/@openai/codex/node_modules/@openai/codex-win32-x64"
                / "vendor/x86_64-pc-windows-msvc/bin/codex.exe"
            )
        )
    for path in candidates:
        if path and Path(path).is_file():
            return path
    raise FileNotFoundError(
        "Native codex.exe not found; set TOKENPAK_CODEX_BINARY to its absolute path"
    )


def launch_environment() -> dict[str, str]:
    """Return the child environment, including the local path profile."""
    env = os.environ.copy()
    # The installed local profile is data, not executable code, and contains
    # only the explicitly configured path/profile variables.
    environment_file = _paths.home() / "windows-env.json"
    if environment_file.is_file():
        values = json.loads(environment_file.read_text(encoding="utf-8"))
        allowed = {"PYTHONUTF8", "PYTHONDONTWRITEBYTECODE", "TIKTOKEN_CACHE_DIR", "TEMP", "TMP"}
        for key, value in values.items():
            if isinstance(value, str) and (key.startswith("TOKENPAK_") or key in allowed):
                env[key] = value
    return env


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        # Resolve the profile before discovery so a profile-only
        # TOKENPAK_CODEX_BINARY can locate a custom installation.
        env = launch_environment()
        binary = codex_binary(env)
        if args in (["--version"], ["-V"], ["--help"], ["-h"]):
            return subprocess.call([binary, *args])
        home = Path(env.get("TOKENPAK_HOME", str(_paths.home())))
        env["TOKENPAK_HOME"] = str(home)
        env["PYTHONUTF8"] = "1"
        port = int(env.get("TOKENPAK_PORT", "8766"))
        base_url = f"http://127.0.0.1:{port}"

        def healthy() -> bool:
            try:
                with urlopen(base_url + "/health", timeout=3) as response:
                    health = json.load(response)
                return health.get("status") == "ok" and bool(health.get("version"))
            except (OSError, ValueError):
                return False

        if not healthy():
            executable, *flags = python_spawn_prefix()
            started = subprocess.call(
                [executable, "-X", "utf8", *flags, "-m", "tokenpak", "start", "--port", str(port)],
                env=env,
                cwd=home,
            )
            if started or not healthy():
                raise RuntimeError("TokenPak proxy did not become healthy")
        journal = Path(env.get("TOKENPAK_COMPANION_JOURNAL_DIR", str(home / "companion")))
        directory = create_launch_dir(journal / "run")
        env[ENV] = str(directory)
        env["TOKENPAK_COMPANION_PYTHON"] = sys.executable
        overrides, forwarded = split_overrides(args)
        route = ["--no-daemon", "-c", f"model_provider={json.dumps(MODEL_PROVIDER)}"]
        provider = f"model_providers.{MODEL_PROVIDER}"
        settings = {
            f"{provider}.name": "TokenPak local proxy",
            f"{provider}.base_url": base_url + "/v1",
            f"{provider}.wire_api": "responses",
            f"{provider}.requires_openai_auth": True,
            f"{provider}.supports_websockets": False,
            f"mcp_servers.{SERVER_NAME}.env.{ENV}": str(directory),
        }
        for key, value in settings.items():
            route.extend(["-c", f"{key}={json.dumps(value)}"])
        # No approval, sandbox, or hook-trust bypass flags are added.
        return subprocess.call([binary, *overrides, *route, *forwarded], env=env)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"tokenpak: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
