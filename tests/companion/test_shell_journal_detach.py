# SPDX-License-Identifier: Apache-2.0
"""A pending journal write must not keep the prompt's response pipes open."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOOK = Path(__file__).resolve().parents[2] / "tokenpak/companion/hooks/pre_send.sh"


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 5
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists(), path.name


def test_pending_sqlite_writer_does_not_hold_prompt_response(tmp_path):
    binaries = tmp_path / "bin"
    binaries.mkdir()
    started, release, finished = (tmp_path / name for name in ("started", "release", "finished"))
    sqlite = binaries / "sqlite3"
    sqlite.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "root = Path(os.environ['TOKENPAK_COMPANION_JOURNAL_DIR'])\n"
        "(root / 'started').touch()\n"
        "deadline = time.monotonic() + 10\n"
        "while not (root / 'release').exists():\n"
        "    if time.monotonic() >= deadline: raise SystemExit(73)\n"
        "    time.sleep(0.01)\n"
        "if sys.argv[-1].startswith('INSERT'):\n"
        "    print('writer-only stdout')\n"
        "    print('writer-only stderr', file=sys.stderr)\n"
        "    (root / 'finished').touch()\n"
    )
    sqlite.chmod(0o700)
    (tmp_path / "journal.db").touch()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"type":"user","content":"fixture"}\n' * 100)
    env = {
        **{key: value for key, value in os.environ.items() if not key.startswith("TOKENPAK_")},
        "HOME": str(tmp_path),
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "TOKENPAK_COMPANION_JOURNAL_DIR": str(tmp_path),
        "TOKENPAK_COMPANION_ENABLED": "1",
        "TOKENPAK_COMPANION_SHOW_COST": "0",
    }
    process = subprocess.Popen(
        ["bash", str(HOOK)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=tmp_path,
        env=env,
    )
    try:
        stdout, stderr = process.communicate(
            json.dumps({"session_id": "pending-writer", "transcript_path": str(transcript)}),
            timeout=2,
        )
        assert process.returncode == 0
        assert stdout == stderr == ""
        _wait_for(started)
        assert not finished.exists()
    finally:
        # Release and reap this fixture's pending work even when the old hook
        # times out. Never leave detached writers racing temporary cleanup.
        release.touch()
        process.communicate(timeout=10)
        _wait_for(finished)
