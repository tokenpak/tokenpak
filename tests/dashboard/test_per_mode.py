"""PerModePanel — ε acceptance."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tokenpak.dashboard.panels.per_mode import PerModePanel, PerModeRow


def _seed_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            model TEXT,
            request_type TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            estimated_cost REAL DEFAULT 0.0,
            latency_ms INTEGER DEFAULT 0,
            status_code INTEGER DEFAULT 200,
            endpoint TEXT,
            compilation_mode TEXT,
            protected_tokens INTEGER DEFAULT 0,
            compressed_tokens INTEGER DEFAULT 0,
            injected_tokens INTEGER DEFAULT 0,
            injected_sources TEXT,
            cache_read_tokens INTEGER DEFAULT 0
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO requests
        (timestamp, model, input_tokens, output_tokens, estimated_cost,
         status_code, endpoint, cache_read_tokens)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            ("2026-04-22T10:00:00", "claude-haiku", 100, 50, 0.01, 200, "https://api.anthropic.com/v1/messages", 20),
            ("2026-04-22T10:05:00", "claude-haiku", 200, 80, 0.02, 200, "https://api.anthropic.com/v1/messages", 40),
            ("2026-04-22T10:10:00", "gpt-4o", 500, 200, 0.05, 500, "https://api.openai.com/v1/chat/completions", 0),
            ("2026-04-22T10:15:00", "claude-opus", 1000, 400, 0.20, 200, "https://api.anthropic.com/v1/messages?beta=true", 300),
        ],
    )
    conn.commit()
    conn.close()


def test_load_groups_by_endpoint_family(tmp_path):
    db = tmp_path / "monitor.db"
    _seed_db(db)
    panel = PerModePanel.load(db_path=db)
    labels = {r.label for r in panel.rows}
    assert "anthropic" in labels
    assert "openai" in labels


def test_load_aggregates_requests_and_errors(tmp_path):
    db = tmp_path / "monitor.db"
    _seed_db(db)
    panel = PerModePanel.load(db_path=db)
    ant_row = next(r for r in panel.rows if r.label == "anthropic")
    assert ant_row.requests == 3
    assert ant_row.errors == 0
    openai_row = next(r for r in panel.rows if r.label == "openai")
    assert openai_row.requests == 1
    assert openai_row.errors == 1
    assert openai_row.error_rate == 1.0


def test_load_empty_when_db_missing(tmp_path):
    panel = PerModePanel.load(db_path=tmp_path / "missing.db")
    assert panel.rows == []


def test_as_dict_is_serializable(tmp_path):
    import json
    db = tmp_path / "monitor.db"
    _seed_db(db)
    panel = PerModePanel.load(db_path=db)
    d = panel.as_dict()
    assert "rows" in d
    # Round-trip through json.
    json.dumps(d)


def test_per_mode_row_error_rate_zero_requests():
    r = PerModeRow(label="x")
    assert r.error_rate == 0.0
