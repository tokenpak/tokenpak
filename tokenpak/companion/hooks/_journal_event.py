# SPDX-License-Identifier: Apache-2.0
"""Queue content-free shell-hook metadata without the sqlite3 executable.

This records a prompt submission, never a completed turn or provider usage.
SQLite materialisation remains outside the prompt's synchronous path.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Shell hooks use the launcher's interpreter and this exact packaged file.
# A repository in the caller's cwd must not shadow the selected installation.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    # This detached writer must yield CPU to foreground prompts during bursts.
    if hasattr(os, "nice"):
        try:
            os.nice(10)
        except OSError:
            pass

from tokenpak.companion import _sqlite
from tokenpak.companion.config import journal_write_dir
from tokenpak.status.binding import valid_session


def _main(argv: list[str]) -> int:
    try:
        session_id, tokens_text, micro_text, model = argv
        if not valid_session(session_id):
            raise ValueError("invalid session identity")
        if not all(value.isascii() and value.isdecimal() for value in (tokens_text, micro_text)):
            raise ValueError("invalid estimate")
        tokens, micro = int(tokens_text), int(micro_text)
        if max(tokens, micro) > 2**63 - 1 or len(model) > 256 or any(ord(c) < 32 for c in model):
            raise ValueError("invalid metadata")
        stamp = time.time()
        directory = journal_write_dir()
        content = (
            f"prompt submitted (~{tokens:,} tokens, "
            f"est ${micro // 1_000_000}.{micro % 1_000_000:06d}, model: {model or 'unknown'})"
        )
        _sqlite.queue_pre_send_event(
            directory,
            session_id=session_id,
            timestamp=stamp,
            date=datetime.fromtimestamp(stamp).date().isoformat(),
            entry_type="auto",
            content=content,
            metadata_json="{}",
            tokens_est=None,
            cost_est=None,
        )
        # The shell has already detached this helper. Materialise here rather
        # than starting a second interpreter for every prompt. Failed writes
        # remain queued for the next reader or worker to replay.
        if os.environ.get("TOKENPAK_COMPANION_ASYNC_FLUSH", "1").lower() not in {
            "0",
            "false",
            "no",
        }:
            _sqlite.flush_pre_send_events(directory)
        return 0
    except Exception as exc:
        # Model and other input values may contain private content.
        print(f"tokenpak: prompt journal unavailable ({type(exc).__name__})", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
