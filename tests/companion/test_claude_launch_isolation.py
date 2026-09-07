# SPDX-License-Identifier: Apache-2.0
"""Concurrent Claude launches keep generated identity/config files isolated."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx

from tokenpak.companion import launcher


def test_concurrent_launches_do_not_replace_each_others_generated_files(
    monkeypatch, tmp_path: Path
) -> None:
    journal_dir = tmp_path / "companion"
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(journal_dir))
    monkeypatch.setattr(launcher, "_launcher_mode_state", lambda: ("inherit", None))

    def no_proxy(*_args, **_kwargs):
        raise httpx.ConnectError("offline fixture")

    monkeypatch.setattr(httpx, "get", no_proxy)
    barrier = threading.Barrier(2)
    records: list[tuple[str, Path, str]] = []
    errors: list[BaseException] = []

    def fake_exec(_file: str, argv: list[str], _env: dict[str, str]) -> None:
        barrier.wait(timeout=5)
        settings_path = Path(argv[argv.index("--settings") + 1])
        label = argv[argv.index("--name") + 1]
        title_path = settings_path.parent / "session_title.json"
        title = json.loads(title_path.read_text())["hookSpecificOutput"]["sessionTitle"]
        records.append((label, settings_path.parent, title))

    monkeypatch.setattr(launcher.os, "execvpe", fake_exec)

    def run(label: str) -> None:
        try:
            launcher.main(["--name", label])
        except BaseException as exc:  # noqa: BLE001 - surfaced by assertion
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(label,)) for label in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors
    assert len(records) == 2
    assert len({run_dir for _, run_dir, _ in records}) == 2
    assert all(label == title for label, _, title in records)
