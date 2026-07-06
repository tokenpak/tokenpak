from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from tokenpak.proxy import vault_bridge


def _write_index(root: Path) -> None:
    blocks = root / "blocks"
    blocks.mkdir()
    (blocks / "alpha.txt").write_text("alpha beta gamma\n", encoding="utf-8")
    (root / "index.json").write_text(
        json.dumps(
            {
                "blocks": {
                    "alpha": {
                        "source_path": "alpha.md",
                        "raw_tokens": 3,
                    }
                }
            }
        ),
        encoding="utf-8",
    )


def test_maybe_reload_serializes_concurrent_builds(tmp_path, monkeypatch):
    monkeypatch.setattr(vault_bridge, "VAULT_INDEX_RELOAD_INTERVAL", 0)
    _write_index(tmp_path)

    class CountingVaultIndex(vault_bridge.VaultIndex):
        def __init__(self, tokenpak_dir: str):
            super().__init__(tokenpak_dir)
            self.load_calls = 0
            self.load_call_lock = threading.Lock()

        def _load(self, index_path: Path, mtime: float):
            with self.load_call_lock:
                self.load_calls += 1
            time.sleep(0.05)
            return super()._load(index_path, mtime)

    idx = CountingVaultIndex(str(tmp_path))
    start = threading.Barrier(8)
    errors: list[BaseException] = []

    def reload_once() -> None:
        try:
            start.wait(timeout=2)
            idx.maybe_reload()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=reload_once) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert idx.load_calls == 1
    assert idx.available
