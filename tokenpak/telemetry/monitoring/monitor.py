"""
Monitor — standalone SQLite-backed request logger and telemetry store.

Extracted from proxy.py (was class Monitor at line ~2635).
This module is importable independently of the proxy runtime.

Usage:
    from tokenpak.telemetry.monitoring.monitor import Monitor
    m = Monitor(db_path="~/.tokenpak/monitor.db")
    m.log(model="claude-3", ...)
"""

from __future__ import annotations

import datetime as _dt
import os
import sqlite3
import sys
import threading
from queue import Empty, Queue
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Budget constants (mirrored from proxy.py config)
# ---------------------------------------------------------------------------

BUDGET_DAILY_LIMIT_USD: float = float(
    os.environ.get("TOKENPAK_BUDGET_DAILY_LIMIT_USD", "0")
)
BUDGET_ALERT_THRESHOLD_PCT: float = float(
    os.environ.get("TOKENPAK_BUDGET_ALERT_PCT", "80")
)

# ---------------------------------------------------------------------------
# DB write queue (background async writes)
# ---------------------------------------------------------------------------

_DB_CONNECTION = None
_DB_LOCK = threading.Lock()
_DB_WRITE_QUEUE: Optional[Queue] = None
_DB_QUEUE_LOCK = threading.Lock()
_DB_QUEUE_MAX_SIZE = 1000
_DB_BACKGROUND_THREAD = None
_DB_BACKGROUND_STOP = threading.Event()


def _init_db_write_queue() -> None:
    """Initialize the database write queue and background thread."""
    global _DB_WRITE_QUEUE, _DB_BACKGROUND_THREAD
    with _DB_QUEUE_LOCK:
        if _DB_WRITE_QUEUE is None:
            _DB_WRITE_QUEUE = Queue(maxsize=_DB_QUEUE_MAX_SIZE)
            _DB_BACKGROUND_STOP.clear()
            _DB_BACKGROUND_THREAD = threading.Thread(
                target=_db_writer_worker,
                daemon=True,
                name="TokenPak-DB-Writer",
            )
            # TOKENPAK_NO_THREADS: skip background threads in test environments
            # to avoid "OSError: Bad file descriptor" in pytest fd-level capture.
            if not os.environ.get("TOKENPAK_NO_THREADS"):
                _DB_BACKGROUND_THREAD.start()


def _db_writer_worker() -> None:
    """Background worker thread that drains the DB write queue."""
    while not _DB_BACKGROUND_STOP.is_set():
        try:
            work_item = _DB_WRITE_QUEUE.get(timeout=1.0)  # type: ignore[union-attr]
            if work_item is None:
                break
            db_path, insert_params = work_item
            try:
                with _DB_LOCK:
                    conn = _get_db_connection(db_path)
                    conn.execute(
                        """INSERT INTO requests
                           (timestamp,model,request_type,input_tokens,output_tokens,estimated_cost,
                            latency_ms,status_code,endpoint,compilation_mode,protected_tokens,
                            compressed_tokens,injected_tokens,injected_sources,cache_read_tokens,
                            cache_creation_tokens,would_have_saved,route)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        insert_params,
                    )
                    conn.commit()
            except Exception as e:
                print(f"[TokenPak] DB write error: {e}", file=sys.stderr)
            finally:
                _DB_WRITE_QUEUE.task_done()  # type: ignore[union-attr]
        except Empty:
            continue
        except Exception as e:
            print(f"[TokenPak] DB worker error: {e}", file=sys.stderr)


def _get_db_connection(db_path: str) -> sqlite3.Connection:
    """Get or create persistent SQLite connection with WAL mode enabled."""
    global _DB_CONNECTION
    if _DB_CONNECTION is None:
        _DB_CONNECTION = sqlite3.connect(db_path, check_same_thread=False)
        _DB_CONNECTION.execute("PRAGMA journal_mode=WAL")
        _DB_CONNECTION.execute("PRAGMA synchronous=NORMAL")
        _DB_CONNECTION.execute("PRAGMA busy_timeout=5000")
    return _DB_CONNECTION


# ---------------------------------------------------------------------------
# Monitor class
# ---------------------------------------------------------------------------

class Monitor:
    """Thread-safe SQLite-backed telemetry store for TokenPak proxy."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
        try:
            _init_db_write_queue()
        except NameError:
            pass

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                request_type TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                estimated_cost REAL,
                latency_ms INTEGER,
                status_code INTEGER,
                endpoint TEXT,
                compilation_mode TEXT,
                protected_tokens INTEGER,
                compressed_tokens INTEGER,
                injected_tokens INTEGER DEFAULT 0,
                injected_sources TEXT DEFAULT '',
                cache_read_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                would_have_saved INTEGER DEFAULT 0,
                route TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON requests(timestamp)")
        # Upgrade columns for older DBs
        for col, typedef in [
            ("injected_tokens", "INTEGER DEFAULT 0"),
            ("injected_sources", "TEXT DEFAULT ''"),
            ("cache_read_tokens", "INTEGER DEFAULT 0"),
            ("cache_creation_tokens", "INTEGER DEFAULT 0"),
            ("would_have_saved", "INTEGER DEFAULT 0"),
            ("route", "TEXT DEFAULT ''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE requests ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass
        conn.commit()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS budget_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                daily_limit REAL,
                total_spent REAL,
                pct_used REAL,
                notified INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def log(
        self,
        model: str,
        request_type: Optional[str] = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        estimated_cost: float = 0.0,
        latency_ms: int = 0,
        status_code: int = 200,
        endpoint: Optional[str] = None,
        compilation_mode: Optional[str] = None,
        protected_tokens: int = 0,
        compressed_tokens: int = 0,
        injected_tokens: int = 0,
        injected_sources: str = "",
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        would_have_saved: int = 0,
        route: str = "",
    ) -> None:
        """Log a single request observation."""
        params = (
            _dt.datetime.now().isoformat(),
            model,
            request_type,
            input_tokens,
            output_tokens,
            estimated_cost,
            latency_ms,
            status_code,
            endpoint,
            compilation_mode,
            protected_tokens,
            compressed_tokens,
            injected_tokens,
            injected_sources,
            cache_read_tokens,
            cache_creation_tokens,
            would_have_saved,
            route or "",
        )
        if _DB_WRITE_QUEUE is not None and not _DB_WRITE_QUEUE.full():
            _DB_WRITE_QUEUE.put((self.db_path, params))
        else:
            _conn = sqlite3.connect(str(self.db_path))
            _conn.execute(
                """INSERT INTO requests
                   (timestamp,model,request_type,input_tokens,output_tokens,estimated_cost,
                    latency_ms,status_code,endpoint,compilation_mode,protected_tokens,
                    compressed_tokens,injected_tokens,injected_sources,cache_read_tokens,
                    cache_creation_tokens,would_have_saved,route)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                params,
            )
            _conn.commit()
            _conn.close()
        # Budget check (fail-open)
        try:
            self._check_budget_alert(0.0)
        except Exception:
            pass

    def get_stats(self, hours: int = 24) -> Dict[str, Any]:
        """Return aggregated stats for the last N hours."""
        cutoff = (
            _dt.datetime.now() - _dt.timedelta(hours=hours)
        ).isoformat()
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM requests WHERE timestamp > ? ORDER BY timestamp DESC",
            (cutoff,),
        ).fetchall()
        conn.close()

        total_cost = sum(r["estimated_cost"] or 0.0 for r in rows)
        total_input = sum(r["input_tokens"] or 0 for r in rows)
        total_output = sum(r["output_tokens"] or 0 for r in rows)
        total_protected = sum(r["protected_tokens"] or 0 for r in rows)
        total_compressed = sum(r["compressed_tokens"] or 0 for r in rows)
        total_cache_read = sum(r["cache_read_tokens"] or 0 for r in rows)
        latencies = [r["latency_ms"] for r in rows if r["latency_ms"]]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0

        return {
            "request_count": len(rows),
            "total_cost_usd": round(total_cost, 6),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_protected_tokens": total_protected,
            "total_compressed_tokens": total_compressed,
            "total_cache_read_tokens": total_cache_read,
            "avg_latency_ms": round(avg_latency, 1),
            "hours": hours,
        }

    def get_by_model(self) -> List[Dict[str, Any]]:
        """Return per-model aggregated stats."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT model,
                      COUNT(*) as count,
                      SUM(estimated_cost) as total_cost,
                      SUM(input_tokens) as input_tokens,
                      SUM(output_tokens) as output_tokens,
                      AVG(latency_ms) as avg_latency
               FROM requests
               GROUP BY model
               ORDER BY total_cost DESC"""
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def _check_budget_alert(
        self,
        current_cost: float = 0.0,
        _daily_limit: Optional[float] = None,
        _threshold_pct: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Check if daily spend has crossed the budget alert threshold."""
        daily_limit = _daily_limit if _daily_limit is not None else BUDGET_DAILY_LIMIT_USD
        threshold_pct = (
            _threshold_pct if _threshold_pct is not None else BUDGET_ALERT_THRESHOLD_PCT
        )
        if daily_limit <= 0:
            return None

        today = _dt.date.today().isoformat()
        conn = sqlite3.connect(str(self.db_path))
        try:
            row = conn.execute(
                "SELECT SUM(estimated_cost) as total FROM requests WHERE timestamp LIKE ?",
                (f"{today}%",),
            ).fetchone()
            total_spent = (row[0] or 0.0) if row else 0.0
            pct_used = (total_spent / daily_limit) * 100.0

            if pct_used >= threshold_pct:
                # Check if we've already alerted today
                existing = conn.execute(
                    "SELECT id FROM budget_alerts WHERE timestamp LIKE ? AND alert_type = 'daily'",
                    (f"{today}%",),
                ).fetchone()
                if not existing:
                    conn.execute(
                        """INSERT INTO budget_alerts
                           (timestamp, alert_type, daily_limit, total_spent, pct_used, notified)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            _dt.datetime.now().isoformat(),
                            "daily",
                            daily_limit,
                            total_spent,
                            round(pct_used, 2),
                            1,
                        ),
                    )
                    conn.commit()
                    return {
                        "alert": True,
                        "total_spent": total_spent,
                        "daily_limit": daily_limit,
                        "pct_used": pct_used,
                    }
        finally:
            conn.close()
        return None

    def get_budget_alert_status(
        self,
        _daily_limit: Optional[float] = None,
        _threshold_pct: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Return current budget status."""
        daily_limit = _daily_limit if _daily_limit is not None else BUDGET_DAILY_LIMIT_USD
        threshold_pct = (
            _threshold_pct if _threshold_pct is not None else BUDGET_ALERT_THRESHOLD_PCT
        )
        today = _dt.date.today().isoformat()
        conn = sqlite3.connect(str(self.db_path))
        try:
            row = conn.execute(
                "SELECT SUM(estimated_cost) as total FROM requests WHERE timestamp LIKE ?",
                (f"{today}%",),
            ).fetchone()
            total_spent = (row[0] or 0.0) if row else 0.0
            pct_used = (total_spent / daily_limit * 100.0) if daily_limit > 0 else 0.0
            alert_row = conn.execute(
                "SELECT timestamp FROM budget_alerts WHERE timestamp LIKE ? AND alert_type='daily' ORDER BY id DESC LIMIT 1",
                (f"{today}%",),
            ).fetchone()
        finally:
            conn.close()

        return {
            "total_spent": round(total_spent, 6),
            "daily_limit": daily_limit,
            "remaining": max(0.0, round(daily_limit - total_spent, 6)),
            "pct_used": round(pct_used, 2),
            "alert_triggered": alert_row is not None,
            "last_alert_at": alert_row[0] if alert_row else None,
            "threshold_pct": threshold_pct,
        }

    def get_savings_report(self, since: Optional[str] = None) -> Dict[str, Any]:
        """Return savings summary (protected + compressed tokens)."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        if since:
            rows = conn.execute(
                "SELECT * FROM requests WHERE timestamp > ?", (since,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM requests").fetchall()
        conn.close()

        total_input = sum(r["input_tokens"] or 0 for r in rows)
        total_protected = sum(r["protected_tokens"] or 0 for r in rows)
        total_compressed = sum(r["compressed_tokens"] or 0 for r in rows)
        total_would_save = sum(r["would_have_saved"] or 0 for r in rows)
        total_cost = sum(r["estimated_cost"] or 0.0 for r in rows)

        protection_pct = (
            round(total_protected / total_input * 100, 1) if total_input else 0.0
        )

        return {
            "request_count": len(rows),
            "total_input_tokens": total_input,
            "total_protected_tokens": total_protected,
            "total_compressed_tokens": total_compressed,
            "protection_pct": protection_pct,
            "would_have_saved_tokens": total_would_save,
            "total_cost_usd": round(total_cost, 6),
            "savings_amount": round(total_would_save * 0.000003, 6),
        }

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return the N most recent logged requests."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Last-request stats (module-level — updated by proxy request handler)
# Transferred from monolith (TPK-CONSOLIDATION-A2c, lines 3220–3256)
# ---------------------------------------------------------------------------

LAST_REQUEST: Dict[str, Any] = {
    "request_id": None,
    "timestamp": None,
    "model": None,
    "input_tokens_raw": 0,
    "input_tokens_sent": 0,
    "tokens_saved": 0,
    "percent_saved": 0.0,
    "cost_saved": 0.0,
    "output_tokens": 0,
}
_LAST_REQUEST_LOCK = threading.Lock()


def update_last_request(
    request_id: str,
    model: str,
    input_raw: int,
    input_sent: int,
    tokens_saved: int,
    cost_saved: float,
    output_tokens: int,
) -> None:
    """Thread-safe update of last request stats."""
    import datetime as _dt_mod
    with _LAST_REQUEST_LOCK:
        LAST_REQUEST["request_id"] = request_id
        LAST_REQUEST["timestamp"] = _dt_mod.datetime.now().isoformat()
        LAST_REQUEST["model"] = model
        LAST_REQUEST["input_tokens_raw"] = input_raw
        LAST_REQUEST["input_tokens_sent"] = input_sent
        LAST_REQUEST["tokens_saved"] = tokens_saved
        LAST_REQUEST["percent_saved"] = (
            round(tokens_saved / input_raw * 100, 1) if input_raw > 0 else 0.0
        )
        LAST_REQUEST["cost_saved"] = round(cost_saved, 6)
        LAST_REQUEST["output_tokens"] = output_tokens


# ---------------------------------------------------------------------------
# mutation_audit housekeeping
# ---------------------------------------------------------------------------

def _prune_mutation_audit(conn: "sqlite3.Connection", ttl_days: int) -> int:
    """Delete mutation_audit rows older than ttl_days. Returns number of rows deleted.

    Should be called from the request-handling path or DB worker loop
    on a periodic basis (e.g. once per N requests or on Monitor startup
    alongside _init_db).
    """
    cur = conn.execute(
        "DELETE FROM mutation_audit WHERE timestamp < datetime('now', '-' || ? || ' days')",
        (ttl_days,),
    )
    conn.commit()
    return cur.rowcount
