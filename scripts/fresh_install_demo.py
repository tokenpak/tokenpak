#!/usr/bin/env python3
"""Validate a clean release-candidate install through the offline demo."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import venv
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
DEMO_MARKERS = ("TokenPak — Offline Fixture Demo", "Receipt status", "not a savings receipt")


class FreshInstallError(RuntimeError):
    """Raised when the clean-install contract fails."""


def validate_demo_output(output: str) -> None:
    missing = [marker for marker in DEMO_MARKERS if marker not in output]
    if missing:
        raise FreshInstallError("demo output is missing: " + ", ".join(missing))


def _run(command: list[str], *, environment: dict[str, str], cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode:
        raise FreshInstallError(
            f"command failed ({completed.returncode}): {' '.join(command)}\n{completed.stdout}"
        )
    return completed.stdout


def run_fresh_install(repo: Path, max_seconds: float) -> float:
    preparation_start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="tokenpak-fresh-install-") as directory:
        root = Path(directory)
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        environment["HOME"] = str(root / "home")
        environment["USERPROFILE"] = environment["HOME"]
        environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
        Path(environment["HOME"]).mkdir()

        wheelhouse = root / "wheelhouse"
        wheelhouse.mkdir()
        _run(
            [sys.executable, "-m", "build", "--wheel", "--outdir", str(wheelhouse)],
            environment=environment,
            cwd=repo,
        )
        wheels = sorted(wheelhouse.glob("tokenpak-*.whl"))
        if len(wheels) != 1:
            raise FreshInstallError(f"expected one candidate wheel, found {len(wheels)}")

        env_dir = root / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(env_dir)
        scripts_dir = "Scripts" if os.name == "nt" else "bin"
        python = env_dir / scripts_dir / ("python.exe" if os.name == "nt" else "python")
        install_start = time.monotonic()
        _run(
            [str(python), "-m", "pip", "install", "--quiet", str(wheels[0])],
            environment=environment,
            cwd=repo,
        )
        tokenpak = env_dir / scripts_dir / ("tokenpak.exe" if os.name == "nt" else "tokenpak")
        if not tokenpak.is_file():
            raise FreshInstallError(
                f"installed tokenpak console entry point is missing: {tokenpak}"
            )
        _run([str(tokenpak), "--help"], environment=environment, cwd=root)
        output = _run(
            [str(tokenpak), "demo"],
            environment=environment,
            cwd=root,
        )
        validate_demo_output(output)

    elapsed = time.monotonic() - install_start
    preparation = install_start - preparation_start
    if elapsed > max_seconds:
        raise FreshInstallError(
            f"fresh install-to-demo took {elapsed:.2f}s (limit {max_seconds:.2f}s)"
        )
    print(output, end="" if output.endswith("\n") else "\n")
    print(f"A5 clean wheel/environment preparation: {preparation:.2f}s (outside gate clock)")
    print(f"A5 fresh install-to-demo: PASS in {elapsed:.2f}s (limit {max_seconds:.2f}s)")
    return elapsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--max-seconds", type=float, default=60.0)
    args = parser.parse_args(argv)
    try:
        run_fresh_install(args.repo.resolve(), args.max_seconds)
    except (FreshInstallError, OSError) as exc:
        print(f"A5 fresh install-to-demo FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
