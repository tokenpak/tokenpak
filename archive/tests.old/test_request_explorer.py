from datetime import datetime, timedelta, timezone
from pathlib import Path

import sys
import types

if "watchdog" not in sys.modules:
    watchdog = types.ModuleType("watchdog")
    events = types.ModuleType("watchdog.events")
    observers = types.ModuleType("watchdog.observers")

    class FileSystemEventHandler:  # pragma: no cover - import shim
        pass

    class Observer:  # pragma: no cover - import shim
        def schedule(self, *args, **kwargs):
            return None

        def start(self):
            return None

        def stop(self):
            return None

        def join(self):
            return None

    events.FileSystemEventHandler = FileSystemEventHandler
    observers.Observer = Observer
    sys.modules["watchdog"] = watchdog
    sys.modules["watchdog.events"] = events
    sys.modules["watchdog.observers"] = observers

from tokenpak.request_explorer import (
    age_label,
    cache_pct,
    get_request_by_id,
    load_requests,
    status_label,
    to_view,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(__import__("json").dumps(row) + "\n")


def test_load_requests_skips_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text("{bad json}\n{\"id\": \"ok\"}\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "ok"


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "r1"}, {"id": "r2"}])
    row = get_request_by_id("r2", path=path)
    assert row is not None
    assert row["id"] == "r2"


def test_cache_pct_and_status():
    view = to_view({"id": "r", "model": "m", "input_tokens": 100, "output_tokens": 10, "cache_read": 25})
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"


def test_status_error():
    view = to_view({"id": "r", "model": "m", "status": "error"})
    assert status_label(view) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=12)).isoformat()
    assert age_label(ts).endswith("s")


def test_age_label_minutes():
    ts = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    assert age_label(ts).endswith("m")
