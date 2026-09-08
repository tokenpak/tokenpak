# SPDX-License-Identifier: Apache-2.0
"""Explicit private tmux launcher. Auto mode never starts a multiplexer."""

from __future__ import annotations

import argparse
import contextlib
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from tokenpak.companion._python_spawn import python_spawn_prefix
from tokenpak.status.binding import create_launch_dir


def launch(args: list[str], parent: Path, *, receipt_out=None, run_id=None) -> int:
    if shutil.which("tmux") is None:
        print(
            "tokenpak: --status-surface=tmux requires tmux; install it or use auto/off",
            file=sys.stderr,
        )
        return 2
    directory = create_launch_dir(parent)
    result_path = directory / "exit-code"
    socket = "tokenpak-" + uuid.uuid4().hex
    base = ["tmux", "-L", socket, "-f", os.devnull]
    command = [*python_spawn_prefix(), "-m", __name__, "--exit-file", str(result_path)]
    if receipt_out:
        command += ["--receipt-out", receipt_out, "--run-id", run_id]
    command += ["--", "--status-surface=tmux", *args]
    size = shutil.get_terminal_size((80, 24))
    created = False
    try:
        subprocess.run(
            [
                *base,
                "new-session",
                "-d",
                "-s",
                "companion",
                "-x",
                str(size.columns),
                "-y",
                str(size.lines),
                "-c",
                str(Path.cwd()),
                shlex.join(command),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            timeout=5,
        )
        created = True
        subprocess.run(
            [*base, "set-option", "-t", "companion", "status", "off"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        subprocess.call([*base, "attach-session", "-t", "companion"])
        if result_path.exists():
            code = int(result_path.read_text())
            result_path.unlink()
            directory.rmdir()
            return code
        # A user-requested detach preserves the managed child and its normal lease.
        print(f"tokenpak: panel detached; resume with tmux -L {socket} attach", file=sys.stderr)
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"tokenpak: could not open terminal panel: {exc}", file=sys.stderr)
        if created:
            print(
                f"tokenpak: session may still be running; resume with tmux -L {socket} attach",
                file=sys.stderr,
            )
        else:
            with contextlib.suppress(OSError):
                directory.rmdir()
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exit-file", required=True)
    parser.add_argument("--receipt-out")
    parser.add_argument("--run-id")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    from tokenpak.companion.codex.launcher import main as codex_main
    from tokenpak.status.worker import atomic_write

    code = 1
    try:
        forwarded = args.args[1:] if args.args[:1] == ["--"] else args.args
        code = codex_main(forwarded, receipt_out=args.receipt_out, run_id=args.run_id)
        return code
    finally:
        with contextlib.suppress(OSError):
            atomic_write(Path(args.exit_file), str(code))


if __name__ == "__main__":
    raise SystemExit(main())
