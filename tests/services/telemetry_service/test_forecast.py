"""CostForecast — ε acceptance."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from tokenpak.services.telemetry_service.forecast import forecast


def _seed_forecast_db(path: Path, daily_costs: list[float]) -> None:
    """Build a monitor.db with one row per day carrying the given cost."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            model TEXT,
            estimated_cost REAL DEFAULT 0.0,
            status_code INTEGER DEFAULT 200
        )
        """
    )
    now = datetime.utcnow()
    for offset_days, cost in enumerate(reversed(daily_costs)):
        ts = (now - timedelta(days=offset_days)).isoformat()
        conn.execute(
            "INSERT INTO requests (timestamp, model, estimated_cost) VALUES (?, ?, ?)",
            (ts, "test", cost),
        )
    conn.commit()
    conn.close()


def test_empty_db_returns_insufficient_data(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_DB", str(tmp_path / "none.db"))
    result = forecast()
    assert result.days_of_history == 0
    assert result.trend == "insufficient_data"


def test_flat_spend(tmp_path, monkeypatch):
    db = tmp_path / "flat.db"
    _seed_forecast_db(db, daily_costs=[1.0] * 14)
    monkeypatch.setenv("TOKENPAK_DB", str(db))
    result = forecast(days_of_history=14)
    assert result.days_of_history == 14
    assert result.avg_daily_usd == 1.0
    assert result.projected_next_30_days_usd == 30.0
    assert result.trend == "flat"


def test_rising_trend(tmp_path, monkeypatch):
    db = tmp_path / "rising.db"
    # early 1s, late 5s — clearly rising.
    _seed_forecast_db(db, daily_costs=[1, 1, 1, 2, 3, 4, 5, 5, 5])
    monkeypatch.setenv("TOKENPAK_DB", str(db))
    result = forecast(days_of_history=14)
    assert result.trend == "rising"


def test_falling_trend(tmp_path, monkeypatch):
    db = tmp_path / "falling.db"
    _seed_forecast_db(db, daily_costs=[5, 5, 5, 4, 3, 2, 1, 1, 1])
    monkeypatch.setenv("TOKENPAK_DB", str(db))
    result = forecast(days_of_history=14)
    assert result.trend == "falling"


def test_as_dict_is_serialisable(tmp_path, monkeypatch):
    import json
    db = tmp_path / "t.db"
    _seed_forecast_db(db, daily_costs=[0.5] * 10)
    monkeypatch.setenv("TOKENPAK_DB", str(db))
    result = forecast(days_of_history=14)
    json.dumps(result.as_dict())
