"""
TokenPak Monitor — SQLite telemetry, request logging, budget tracking.

Extracted from runtime/proxy.py (Phase 1f of TPK-RESTRUCTURE).
Original location: class Monitor (lines 2320-3204) + SQLite helpers (lines 2248-2319).
"""

import sqlite3
import sys
import threading
from datetime import datetime
from queue import Empty, Queue

# ---------------------------------------------------------------------------
# Migration system (optional — graceful fallback)
# ---------------------------------------------------------------------------
try:
    from db_migrations import get_current_schema_version
    from db_migrations import migrate as db_migrate
    MIGRATION_AVAILABLE = True
except ImportError:
    MIGRATION_AVAILABLE = False

    def db_migrate(conn):
        pass

    def get_current_schema_version(conn):
        return 0

# ---------------------------------------------------------------------------
# Budget config — resolved from env at import time (same as proxy.py)
# ---------------------------------------------------------------------------
import os as _os

BUDGET_DAILY_LIMIT_USD: float = float(_os.environ.get("TOKENPAK_BUDGET_DAILY_LIMIT_USD", "0"))
BUDGET_ALERT_THRESHOLD_PCT: float = float(_os.environ.get("TOKENPAK_BUDGET_ALERT_PCT", "80"))

# ---------------------------------------------------------------------------
# SQLite write queue — async background writes, <0.1ms enqueue cost
# ---------------------------------------------------------------------------

_DB_CONNECTION = None
_DB_LOCK = threading.Lock()
_DB_WRITE_QUEUE = None
_DB_QUEUE_LOCK = threading.Lock()
_DB_QUEUE_MAX_SIZE = 1000
_DB_BACKGROUND_THREAD = None
_DB_BACKGROUND_STOP = threading.Event()


def _init_db_write_queue():
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
            _DB_BACKGROUND_THREAD.start()


def _db_writer_worker():
    """Background worker thread that drains the DB write queue."""
    while not _DB_BACKGROUND_STOP.is_set():
        try:
            # Block for up to 1 second waiting for items
            work_item = _DB_WRITE_QUEUE.get(timeout=1.0)
            if work_item is None:  # Poison pill to stop
                break

            db_path, insert_params = work_item
            try:
                with _DB_LOCK:
                    conn = _get_db_connection(db_path)
                    conn.execute(
                        """INSERT INTO requests
                           (timestamp,model,request_type,input_tokens,output_tokens,estimated_cost,
                            latency_ms,status_code,endpoint,compilation_mode,protected_tokens,
                            compressed_tokens,injected_tokens,injected_sources,cache_read_tokens,cache_creation_tokens,
                            would_have_saved,cache_origin,attribution_source)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        insert_params,
                    )
                    conn.commit()
            except Exception as e:
                print(f"[TokenPak] DB write error: {e}", file=sys.stderr)
            finally:
                _DB_WRITE_QUEUE.task_done()
        except Empty:
            continue
        except Exception as e:
            print(f"[TokenPak] DB worker error: {e}", file=sys.stderr)


def _get_db_connection(db_path: str) -> sqlite3.Connection:
    """Get or create persistent SQLite connection with WAL mode enabled."""
    global _DB_CONNECTION
    if _DB_CONNECTION is None:
        _DB_CONNECTION = sqlite3.connect(
            db_path,
            check_same_thread=False,  # Required for ThreadedHTTPServer
        )
        _DB_CONNECTION.execute("PRAGMA journal_mode=WAL")
        _DB_CONNECTION.execute("PRAGMA synchronous=NORMAL")
        _DB_CONNECTION.execute("PRAGMA busy_timeout=5000")
    return _DB_CONNECTION


# ---------------------------------------------------------------------------
# Monitor class
# ---------------------------------------------------------------------------


class Monitor:
    def __init__(self, db_path):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
        # Start background worker on first Monitor creation
        try:
            _init_db_write_queue()
        except NameError:
            pass

    def _init_db(self):
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
                would_have_saved INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON requests(timestamp)")
        # Add columns if upgrading from v3
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN injected_tokens INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN injected_sources TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN cache_read_tokens INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN cache_creation_tokens INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN would_have_saved INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE requests ADD COLUMN cache_origin TEXT DEFAULT 'unknown'")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute(
                "ALTER TABLE requests ADD COLUMN attribution_source TEXT DEFAULT 'unknown'"
            )
        except sqlite3.OperationalError:
            pass
        conn.commit()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS budget_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent_id TEXT DEFAULT "",
                period TEXT DEFAULT "daily",
                budget_usd REAL,
                spent_usd REAL,
                pct_used REAL,
                triggered INTEGER DEFAULT 1
            )
        """)
        conn.commit()

        # CCG-02: session_id on requests + mutation_audit table
        try:
            from tokenpak.proxy.db import ensure_schema as _ccg02_ensure_schema
            _ccg02_ensure_schema(conn)
            conn.commit()
        except Exception as e:
            print(f"⚠️  CCG-02 schema migration error (non-fatal): {e}")

        # Run migrations to bring DB schema up to current version
        try:
            if MIGRATION_AVAILABLE:
                try:
                    db_migrate(conn)
                    version = get_current_schema_version(conn)
                    print(f"✅ DB schema version: {version}")
                except Exception as e:
                    print(f"⚠️  Migration error (non-fatal): {e}")
        except NameError:
            pass

        conn.close()
        global _DB_CONNECTION
        _DB_CONNECTION = None  # reset so next call reopens fresh

    def log(
        self,
        model,
        input_tokens,
        output_tokens,
        cost,
        latency_ms,
        status_code,
        endpoint,
        compilation_mode="",
        protected_tokens=0,
        compressed_tokens=0,
        injected_tokens=0,
        injected_sources="",
        cache_read_tokens=0,
        cache_creation_tokens=0,
        would_have_saved=0,
        cache_origin="unknown",
        request_id=None,
        attribution_source="unknown",
    ):
        # SC-02: forward a TIP-shaped row to any installed conformance
        # observer before the DB write. No-op when no observer is
        # installed; ship-safe. See services/diagnostics/conformance/
        # for the observer contract.
        #
        # SC-03: ``request_id`` is the wire request id plumbed from
        # the proxy handler (X-Request-Id header). When absent — e.g.
        # callers that haven't been updated yet — fall back to a
        # monitor-local synthetic ID so the schema's required field
        # is satisfied and the row is still correlatable locally.
        try:
            import time as _time
            from datetime import datetime as _dt
            from datetime import timezone as _tz

            from tokenpak.core.contracts import tip_version as _tip_version
            from tokenpak.services.diagnostics import conformance as _conformance

            _rid = (
                request_id
                if isinstance(request_id, str) and request_id
                else f"monitor-{int(_time.time() * 1_000_000)}"
            )
            _tip_row = {
                "request_id": _rid,
                "timestamp": _dt.now(_tz.utc).isoformat().replace("+00:00", "Z"),
                "tip_version": _tip_version.CURRENT,
                "profile": "tip-proxy",
                "model": model,
                "status": int(status_code) if status_code is not None else 0,
                "cache_origin": (
                    cache_origin
                    if cache_origin in ("proxy", "client", "unknown")
                    else "unknown"
                ),
                "tokens_in": int(input_tokens or 0),
                "tokens_out": int(output_tokens or 0),
                "savings_cache_tokens": int(cache_read_tokens or 0),
                "provider_ms": float(latency_ms or 0),
            }
            _conformance.notify_telemetry_row(_tip_row)
        except Exception:
            # Observer notification must never break the monitor write path.
            pass

        # Enqueue write instead of writing directly (async, <0.1ms return)
        insert_params = (
            datetime.now().isoformat(),
            model,
            "chat",
            input_tokens,
            output_tokens,
            cost,
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
            cache_origin,
            attribution_source if isinstance(attribution_source, str) else "unknown",
        )
        _queued = False
        try:
            _DB_WRITE_QUEUE.put_nowait((self.db_path, insert_params))
            _queued = True
        except (NameError, Exception):
            _conn = sqlite3.connect(str(self.db_path))
            _conn.execute(
                "INSERT INTO requests (timestamp, model, request_type, input_tokens, output_tokens, "
                "estimated_cost, latency_ms, status_code, endpoint, compilation_mode, protected_tokens, "
                "compressed_tokens, injected_tokens, injected_sources, cache_read_tokens, cache_creation_tokens, "
                "would_have_saved, cache_origin, attribution_source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                insert_params,
            )
            _conn.commit()
            _conn.close()
        try:
            # When queued async, cost not yet in DB — pass it as current_cost.
            # When written synchronously (fallback), cost already in DB — pass 0.
            self._check_budget_alert(current_cost=cost if (_queued and cost) else 0)
        except Exception:
            pass

    def get_stats(self, hours=24):
        conn = _get_db_connection(self.db_path)
        row = conn.execute(
            """
            SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),
                   COALESCE(SUM(estimated_cost),0), COALESCE(AVG(latency_ms),0),
                   COALESCE(SUM(protected_tokens),0), COALESCE(SUM(compressed_tokens),0),
                   COALESCE(SUM(injected_tokens),0),
                   COALESCE(SUM(cache_read_tokens),0),
                   COALESCE(SUM(cache_creation_tokens),0)
            FROM requests WHERE timestamp >= datetime('now', ?)
        """,
            (f"-{hours} hours",),
        ).fetchone()
        return {
            "requests": row[0],
            "input_tokens": row[1],
            "output_tokens": row[2],
            "total_cost": round(row[3], 4),
            "avg_latency_ms": round(row[4], 0),
            "protected_tokens": row[5],
            "compressed_tokens": row[6],
            "injected_tokens": row[7],
            "cache_read_tokens": row[8],
            "cache_creation_tokens": row[9],
        }

    def get_by_model(self):
        conn = _get_db_connection(self.db_path)
        rows = conn.execute("""
            SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(estimated_cost),
                   SUM(cache_read_tokens), SUM(cache_creation_tokens), COALESCE(SUM(compressed_tokens),0)
            FROM requests GROUP BY model ORDER BY SUM(estimated_cost) DESC
        """).fetchall()
        result = {}
        for r in rows:
            input_tokens = r[2] or 0
            compressed_tokens = r[7] or 0
            compression_ratio = round(compressed_tokens / input_tokens, 4) if input_tokens > 0 else 0.0
            result[r[0]] = {
                "requests": r[1],
                "input_tokens": input_tokens,
                "output_tokens": r[3],
                "cost": round(r[4], 4),
                "cache_read_tokens": r[5] or 0,
                "cache_creation_tokens": r[6] or 0,
                "compressed_tokens": compressed_tokens,
                "compression_ratio": compression_ratio,
            }
        return result

    def _check_budget_alert(self, current_cost=0, _daily_limit=None, _threshold_pct=None):
        try:
            daily_limit = _daily_limit if _daily_limit is not None else BUDGET_DAILY_LIMIT_USD
        except NameError:
            daily_limit = 0.0
        try:
            threshold_pct = _threshold_pct if _threshold_pct is not None else BUDGET_ALERT_THRESHOLD_PCT
        except NameError:
            threshold_pct = 80.0
        if daily_limit <= 0:
            return
        conn = sqlite3.connect(str(self.db_path))
        try:
            spent = conn.execute(
                'SELECT COALESCE(SUM(estimated_cost), 0) FROM requests WHERE date(timestamp) = date("now")'
            ).fetchone()[0] or 0.0
            total_spent = float(spent) + float(current_cost)
            if total_spent >= daily_limit * threshold_pct / 100:
                existing = conn.execute(
                    'SELECT COUNT(*) FROM budget_alerts WHERE date(timestamp) = date("now") AND period="daily"'
                ).fetchone()[0]
                if existing == 0:
                    import datetime as _dt
                    conn.execute(
                        "INSERT INTO budget_alerts (timestamp, period, budget_usd, spent_usd, pct_used, triggered) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            _dt.datetime.now().isoformat(),
                            "daily",
                            daily_limit,
                            total_spent,
                            round(total_spent / daily_limit * 100, 2),
                            1,
                        ),
                    )
                    conn.commit()
        finally:
            conn.close()

    def get_budget_alert_status(self, _daily_limit=None, _threshold_pct=None):
        try:
            daily_limit = _daily_limit if _daily_limit is not None else BUDGET_DAILY_LIMIT_USD
        except NameError:
            daily_limit = 0.0
        try:
            threshold_pct = _threshold_pct if _threshold_pct is not None else BUDGET_ALERT_THRESHOLD_PCT
        except NameError:
            threshold_pct = 80.0
        conn = sqlite3.connect(str(self.db_path))
        try:
            spent = conn.execute(
                'SELECT COALESCE(SUM(estimated_cost), 0) FROM requests WHERE date(timestamp) = date("now")'
            ).fetchone()[0] or 0.0
            spent = float(spent)
            pct_used = round(spent / daily_limit * 100, 2) if daily_limit > 0 else 0.0
            remaining = max(0.0, daily_limit - spent)
            alert_triggered = (pct_used >= threshold_pct) if daily_limit > 0 else False
            last_row = conn.execute(
                "SELECT timestamp FROM budget_alerts ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_alert_at = last_row[0] if last_row else None
        finally:
            conn.close()
        return {
            "spent_usd": round(spent, 4),
            "budget_usd": daily_limit,
            "pct_used": pct_used,
            "remaining_usd": round(remaining, 4),
            "alert_triggered": alert_triggered,
            "last_alert_at": last_alert_at,
        }

    def get_savings_report(self, since=None):
        conn = sqlite3.connect(str(self.db_path))
        try:
            where = ""
            params = []
            if since:
                where = "WHERE date(timestamp) >= ?"
                params = [since]
            row = conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM(compressed_tokens),0), COALESCE(SUM(cache_read_tokens),0) FROM requests {where}",
                params,
            ).fetchone()
            total_requests = row[0] or 0
            total_compressed = row[1] or 0
            total_cache_read = row[2] or 0
            total_tokens_saved = int(total_compressed + total_cache_read)
            total_cost_saved = round(
                total_compressed * 3.00 / 1_000_000 + total_cache_read * 2.70 / 1_000_000, 4
            )

            # by model
            model_rows = conn.execute(
                f"SELECT model, COUNT(*), COALESCE(SUM(compressed_tokens),0), COALESCE(SUM(cache_read_tokens),0) FROM requests {where} GROUP BY model",
                params,
            ).fetchall()
            savings_by_model = {}
            for r in model_rows:
                comp = r[2] or 0
                cr = r[3] or 0
                savings_by_model[r[0]] = {
                    "requests": r[1],
                    "tokens_saved": int(comp + cr),
                    "cost_saved_usd": round(
                        comp * 3.00 / 1_000_000 + cr * 2.70 / 1_000_000, 4
                    ),
                }

            # by date (last 7 days)
            date_where = 'WHERE date(timestamp) >= date("now", "-7 days")'
            date_params = []
            if since:
                date_where = 'WHERE date(timestamp) >= ? AND date(timestamp) >= date("now", "-7 days")'
                date_params = [since]
            date_rows = conn.execute(
                f"SELECT date(timestamp), COALESCE(SUM(compressed_tokens),0), COALESCE(SUM(cache_read_tokens),0) FROM requests {date_where} GROUP BY date(timestamp) ORDER BY date(timestamp)",
                date_params,
            ).fetchall()
            savings_by_date_7d = []
            for r in date_rows:
                comp = r[1] or 0
                cr = r[2] or 0
                savings_by_date_7d.append(
                    {
                        "date": r[0],
                        "tokens_saved": int(comp + cr),
                        "cost_saved_usd": round(
                            comp * 3.00 / 1_000_000 + cr * 2.70 / 1_000_000, 4
                        ),
                    }
                )
        finally:
            conn.close()
        return {
            "total_requests": total_requests,
            "total_tokens_saved": total_tokens_saved,
            "total_cost_saved_usd": total_cost_saved,
            "savings_by_model": savings_by_model,
            "savings_by_date_7d": savings_by_date_7d,
        }

    def recent(self, limit=20):
        conn = _get_db_connection(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
