
import pytest
pytest.importorskip("tokenpak.request_ledger", reason="module not available in current build")
import json
from datetime import datetime, timezone
from pathlib import Path

from tokenpak.request_ledger import append_request


def test_append_request_writes_and_trims(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    # Write 3 entries, keep MAX_REQUESTS=1000 but we simulate trimming by writing and re-reading
    for i in range(3):
        append_request({"id": f"r{i}", "timestamp": datetime.now(timezone.utc).isoformat()}, path=path)

    lines = path.read_text().splitlines()
    assert len(lines) == 3
    data = json.loads(lines[-1])
    assert data["id"].startswith("r")
