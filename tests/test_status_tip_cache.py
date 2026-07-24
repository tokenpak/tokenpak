"""Tests for TIP cache attribution in `tokenpak status`.

Regression: TCM-09 requires platform cache, TokenPak compression,
TokenPak managed-cache, and companion enrichment to render as distinct
status lanes without hardcoding provider/model/platform names.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tokenpak.cli.commands import status


def _create_monitor_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE requests (
                timestamp TEXT,
                model TEXT,
                cache_origin TEXT,
                cache_read_tokens INTEGER,
                compressed_tokens INTEGER
            )
            """
        )
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn.executemany(
            "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
            [
                (now, "claude-sonnet-4-6", "client", 100_000, 0),
                (now, "claude-sonnet-4-6", "proxy", 50_000, 20_000),
                (now, "unknown-future-model", "unknown", 7_000, 0),
            ],
        )
        conn.commit()
    finally:
        conn.close()


def _create_companion_journal(home: Path) -> None:
    journal = home / ".tokenpak" / "companion" / "journal.db"
    journal.parent.mkdir(parents=True)
    conn = sqlite3.connect(journal)
    try:
        conn.execute(
            """
            CREATE TABLE entries (
                timestamp REAL,
                entry_type TEXT,
                metadata_json TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO entries VALUES (?, ?, ?)",
            (
                time.time(),
                "companion_savings",
                json.dumps({"tokens_avoided": 30_000, "cost_avoided_usd": 0.09}),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_tip_cache_json_has_four_distinct_lanes(tmp_path, monkeypatch):
    """`--json` exposes the TCM-09 attribution lanes with independent values."""
    db = tmp_path / "monitor.db"
    _create_monitor_db(db)
    monkeypatch.setenv("HOME", str(tmp_path))
    _create_companion_journal(tmp_path)

    captured = StringIO()
    with (
        patch("tokenpak.cli.commands.status._fetch", return_value=None),
        patch("sys.stdout", captured),
    ):
        status.run(as_json=True, db_path=str(db))

    payload = json.loads(captured.getvalue())
    tip_cache = payload["tip_cache"]

    assert tip_cache["source"] == "monitor_db+companion_journal"
    assert tip_cache["unknown_cache_tokens"] == 7_000
    assert tip_cache["lines"]["platform_cache"]["tokens"] == 100_000
    assert tip_cache["lines"]["tokenpak_compression"]["tokens"] == 20_000
    assert tip_cache["lines"]["tokenpak_managed_cache"]["tokens"] == 50_000
    assert tip_cache["lines"]["companion_enrichment"]["tokens"] == 30_000
    rates = status.get_rates("claude-sonnet-4-6")
    cache_delta = rates["input"] - rates["cached"]
    assert tip_cache["lines"]["platform_cache"]["usd"] == round(
        100_000 / 1_000_000 * cache_delta, 6
    )
    assert tip_cache["lines"]["tokenpak_managed_cache"]["usd"] == round(
        50_000 / 1_000_000 * cache_delta, 6
    )
    assert tip_cache["lines"]["tokenpak_compression"]["usd"] == round(
        20_000 / 1_000_000 * rates["input"], 6
    )
    assert tip_cache["lines"]["companion_enrichment"]["usd"] == 0.09


def test_tip_cache_compact_output_renders_four_lanes(tmp_path, monkeypatch):
    """`tokenpak status --tip-cache` renders a compact human proof surface."""
    db = tmp_path / "monitor.db"
    _create_monitor_db(db)
    monkeypatch.setenv("HOME", str(tmp_path))
    _create_companion_journal(tmp_path)

    captured = StringIO()
    with (
        patch("tokenpak.cli.commands.status._fetch", return_value=None),
        patch("sys.stdout", captured),
    ):
        status.run(tip_cache=True, db_path=str(db), no_meme=True)

    out = captured.getvalue()
    assert "TIP cache attribution" in out
    assert "Platform cache" in out
    assert "TokenPak compression" in out
    assert "TokenPak managed-cache" in out
    assert "Companion enrichment" in out
    assert "Unattributed cache" in out
    assert "monitor_db+companion_journal" in out


def test_tip_cache_prefers_rollup_daily_when_available(tmp_path, monkeypatch):
    """FTA-06-style rollup_daily is used when it exposes TIP lane columns."""
    db = tmp_path / "monitor.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            """
            CREATE TABLE rollup_daily (
                date TEXT,
                requests INTEGER,
                platform_cache_tokens INTEGER,
                platform_cache_savings_usd REAL,
                tokenpak_compression_tokens INTEGER,
                tokenpak_compression_savings_usd REAL,
                tokenpak_managed_cache_tokens INTEGER,
                tokenpak_managed_cache_savings_usd REAL,
                companion_enrichment_tokens INTEGER,
                companion_enrichment_savings_usd REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO rollup_daily VALUES (datetime('now'), 2, 10, 0.01, 20, 0.02, 30, 0.03, 40, 0.04)"
        )
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setenv("HOME", str(tmp_path))

    result = status._query_tip_cache_attribution(db_path=str(db))

    assert result["source"] == "rollup_daily"
    assert result["requests"] == 2
    assert result["lines"]["platform_cache"]["tokens"] == 10
    assert result["lines"]["tokenpak_compression"]["usd"] == 0.02
    assert result["lines"]["tokenpak_managed_cache"]["tokens"] == 30
    assert result["lines"]["companion_enrichment"]["tokens"] == 40
