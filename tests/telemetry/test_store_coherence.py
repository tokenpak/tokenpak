"""Telemetry store coherence tests.

Covers the fixes for:
  1. telemetry.db path split-brain — one resolver, one env var (+ alias)
     moves writer AND reader to the same file; no repo-root defaults.
  2. Phantom schema — no telemetry module queries a table that no code
     creates (schema-conformance walk + deleted-module regression tests).
  3. Missing uniqueness keys — tp_spend UNIQUE(request_id) upsert and
     tp_pricing UNIQUE(version, provider, model) idempotent seeding, with
     additive dedupe migrations for legacy databases.
  4. TelemetryDB threading — per-thread connections; one trace's
     event/usage/cost rows land in ONE transaction; a failed usage/cost
     write is never reported as pipeline success.
  5. CWD-relative DB paths — registry/routing-ledger defaults anchor to
     the home config dir (with a warned legacy-CWD fallback), and routing
     ledger writes survive OperationalError without crashing callers.
"""

from __future__ import annotations

import ast
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tokenpak.core.paths import get_db_path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[2]


def _clear_db_env(monkeypatch):
    monkeypatch.delenv("TOKENPAK_TELEMETRY_DB", raising=False)
    monkeypatch.delenv("TOKENPAK_DB_PATH", raising=False)


def _make_event(trace_id: str, ts: float | None = None):
    from tokenpak.telemetry.models import TelemetryEvent

    return TelemetryEvent(
        trace_id=trace_id,
        request_id=f"req-{trace_id}",
        event_type="request_end",
        ts=ts or time.time(),
        provider="anthropic",
        model="test-model",
        agent_id="tester",
        status="ok",
    )


def _make_usage(trace_id: str):
    from tokenpak.telemetry.models import Usage

    return Usage(
        trace_id=trace_id,
        usage_source="provider_reported",
        confidence="high",
        input_billed=10,
        output_billed=5,
        input_est=10,
        output_est=5,
        total_tokens=15,
        total_tokens_billed=15,
        total_tokens_est=15,
    )


def _make_cost(trace_id: str):
    from tokenpak.telemetry.models import Cost

    return Cost(trace_id=trace_id, cost_total=0.01, actual_cost=0.01)


# ===========================================================================
# 1. Single DB resolver
# ===========================================================================


class TestSingleResolver:
    def test_env_var_moves_writer_and_reader_to_one_file(self, tmp_path, monkeypatch):
        """TOKENPAK_TELEMETRY_DB must move BOTH the writer (TelemetryDB via
        the resolver) and the reader (query_dsl default) to the same file."""
        from tokenpak.telemetry import query_dsl
        from tokenpak.telemetry.storage import TelemetryDB

        _clear_db_env(monkeypatch)
        db_file = tmp_path / "telemetry.db"
        monkeypatch.setenv("TOKENPAK_TELEMETRY_DB", str(db_file))

        writer_path = get_db_path("telemetry.db")
        assert writer_path == db_file

        db = TelemetryDB(str(writer_path))
        trace_id = uuid.uuid4().hex
        db.insert_trace(_make_event(trace_id), usage=_make_usage(trace_id))
        db.close()

        # Reader resolves its default through the same resolver.
        rows = query_dsl.get_recent_events()  # db_path=None -> resolver
        assert any(r["trace_id"] == trace_id for r in rows)

    def test_deprecated_alias_env_var_honored(self, tmp_path, monkeypatch):
        _clear_db_env(monkeypatch)
        alias_file = tmp_path / "alias.db"
        monkeypatch.setenv("TOKENPAK_DB_PATH", str(alias_file))
        assert get_db_path("telemetry.db") == alias_file

    def test_canonical_env_var_wins_over_alias(self, tmp_path, monkeypatch):
        _clear_db_env(monkeypatch)
        monkeypatch.setenv("TOKENPAK_DB_PATH", str(tmp_path / "alias.db"))
        monkeypatch.setenv("TOKENPAK_TELEMETRY_DB", str(tmp_path / "canonical.db"))
        assert get_db_path("telemetry.db") == tmp_path / "canonical.db"

    def test_api_module_routes_through_resolver(self, tmp_path, monkeypatch):
        from tokenpak.telemetry import api

        _clear_db_env(monkeypatch)
        db_file = tmp_path / "api.db"
        monkeypatch.setenv("TOKENPAK_TELEMETRY_DB", str(db_file))
        assert api._get_db_path() == db_file
        # The alias env var reaches api consumers too (previously it was
        # read directly and diverged from the resolver).
        monkeypatch.delenv("TOKENPAK_TELEMETRY_DB")
        monkeypatch.setenv("TOKENPAK_DB_PATH", str(tmp_path / "alias.db"))
        assert api._get_db_path() == tmp_path / "alias.db"

    def test_query_dsl_default_routes_through_resolver(self, tmp_path, monkeypatch):
        from tokenpak.telemetry import query_dsl

        _clear_db_env(monkeypatch)
        db_file = tmp_path / "dsl.db"
        monkeypatch.setenv("TOKENPAK_TELEMETRY_DB", str(db_file))
        assert query_dsl._default_db_path() == db_file

    def test_no_module_level_repo_root_defaults(self):
        """No telemetry-consuming module may keep its own
        ``Path(__file__).parent...`` repo-root telemetry.db default."""
        offenders = []
        for rel in (
            "tokenpak/telemetry/api.py",
            "tokenpak/telemetry/query_dsl.py",
            "tokenpak/telemetry/milestones.py",
        ):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            if re.search(r"__file__.*telemetry\.db|telemetry\.db.*__file__", text):
                offenders.append(rel)
        assert not offenders, f"repo-root telemetry.db defaults remain: {offenders}"


# ===========================================================================
# 2. Phantom schema
# ===========================================================================

_TABLE = r"[A-Za-z_][A-Za-z0-9_]*"
_QUERY_PATTERNS = [
    re.compile(r"\bFROM\s+(" + _TABLE + r")", re.IGNORECASE),
    re.compile(r"\bINSERT(?:\s+OR\s+[A-Za-z]+)?\s+INTO\s+(" + _TABLE + r")", re.IGNORECASE),
    re.compile(r"\bUPDATE\s+(" + _TABLE + r")\s+SET", re.IGNORECASE),
    re.compile(r"\bDELETE\s+FROM\s+(" + _TABLE + r")", re.IGNORECASE),
]
_CREATE_PATTERN = re.compile(
    r"CREATE\s+(?:TEMP(?:ORARY)?\s+)?(?:TABLE|VIEW|VIRTUAL\s+TABLE)\s+"
    r"(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(" + _TABLE + ")",
    re.IGNORECASE,
)
_SQLISH = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b")
# SQLite-internal tables plus PRAGMA-style virtual sources.
_BUILTIN_TABLES = {"sqlite_master", "sqlite_sequence", "sqlite_temp_master"}


def _string_constants(path: Path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


class TestSchemaConformance:
    def test_no_telemetry_module_queries_absent_tables(self):
        """Walk every SQL string in the telemetry package and require each
        queried table name to have a CREATE TABLE/VIEW statement somewhere
        in the tokenpak package. This is the regression guard for the
        removed phantom-schema modules (which queried ``events`` /
        ``rollups`` tables that no code ever created)."""
        created = set()
        for p in (REPO_ROOT / "tokenpak").rglob("*.py"):
            for m in _CREATE_PATTERN.finditer(
                p.read_text(encoding="utf-8", errors="replace")
            ):
                created.add(m.group(1).lower())

        missing: dict[str, set[str]] = {}
        for p in (REPO_ROOT / "tokenpak" / "telemetry").rglob("*.py"):
            for s in _string_constants(p):
                if not _SQLISH.search(s):
                    continue
                for rx in _QUERY_PATTERNS:
                    for m in rx.finditer(s):
                        name = m.group(1).lower()
                        if name in created or name in _BUILTIN_TABLES:
                            continue
                        if name.startswith("pragma_"):
                            continue
                        missing.setdefault(name, set()).add(
                            str(p.relative_to(REPO_ROOT))
                        )

        assert not missing, (
            "telemetry modules query tables that no code creates: "
            + "; ".join(f"{t} (in {sorted(fs)})" for t, fs in sorted(missing.items()))
        )

    @pytest.mark.parametrize(
        "module",
        [
            "tokenpak.telemetry.integrity.reconciliation",
            "tokenpak.telemetry.integrity.anomalies",
            "tokenpak.telemetry.operational.pruning",
            "tokenpak.telemetry.operational.health",
            "tokenpak.telemetry.monitoring.monitor",
        ],
    )
    def test_phantom_modules_removed(self, module):
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(module)

    def test_dead_monitor_twin_not_reexported(self):
        import tokenpak.telemetry.monitoring as monitoring

        assert not hasattr(monitoring, "Monitor")
        assert "Monitor" not in monitoring.__all__

    def test_functional_monitoring_surfaces_survive(self):
        from tokenpak.telemetry.monitoring import HealthChecker  # noqa: F401
        from tokenpak.telemetry.operational.rbac_core import Permission  # noqa: F401


# ===========================================================================
# 3. Uniqueness keys
# ===========================================================================


class TestSpendUniqueness:
    def _tracker(self, db_path=":memory:"):
        from tokenpak.telemetry.budget import BudgetConfig, BudgetTracker

        return BudgetTracker(config=BudgetConfig(), db_path=str(db_path))

    def test_double_record_spend_one_row(self):
        tracker = self._tracker()
        tracker.record_spend(1.0, request_id="dup-1", model="m1")
        tracker.record_spend(2.5, request_id="dup-1", model="m1")
        rows = tracker.list_spend(limit=10)
        assert len(rows) == 1
        assert rows[0]["cost_usd"] == 2.5  # newest wins
        assert tracker.total_spent("all") == 2.5

    def test_empty_request_id_rejected(self):
        tracker = self._tracker()
        with pytest.raises(ValueError):
            tracker.record_spend(1.0)
        with pytest.raises(ValueError):
            tracker.record_spend(1.0, request_id="")
        assert tracker.list_spend(limit=10) == []

    def test_legacy_duplicates_deduped_on_migration(self, tmp_path):
        db_file = tmp_path / "budget.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            """
            CREATE TABLE tp_spend (
                request_id   TEXT NOT NULL,
                timestamp    TEXT NOT NULL,
                model        TEXT NOT NULL DEFAULT '',
                cost_usd     REAL NOT NULL DEFAULT 0,
                tokens_input  INTEGER NOT NULL DEFAULT 0,
                tokens_output INTEGER NOT NULL DEFAULT 0,
                agent        TEXT NOT NULL DEFAULT ''
            )
            """
        )
        for cost in (1.0, 2.0, 3.0):
            conn.execute(
                "INSERT INTO tp_spend (request_id, timestamp, cost_usd) VALUES (?, ?, ?)",
                ("legacy-dup", "2026-01-01T00:00:00", cost),
            )
        conn.execute(
            "INSERT INTO tp_spend (request_id, timestamp, cost_usd) VALUES (?, ?, ?)",
            ("legacy-unique", "2026-01-01T00:00:00", 9.0),
        )
        conn.commit()
        conn.close()

        tracker = self._tracker(db_file)  # migration runs here
        rows = {r["request_id"]: r for r in tracker.list_spend(limit=10)}
        assert set(rows) == {"legacy-dup", "legacy-unique"}
        assert rows["legacy-dup"]["cost_usd"] == 3.0  # newest kept

        # Unique index exists and enforces.
        conn = sqlite3.connect(str(db_file))
        idx = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='tp_spend'"
            )
        }
        conn.close()
        assert "idx_spend_request_id" in idx


class TestPricingUniqueness:
    def test_double_init_seeds_once(self, tmp_path):
        from tokenpak.telemetry.cost import SEED_PRICING, CostEngine

        db_file = tmp_path / "telemetry.db"
        CostEngine(db_path=str(db_file))
        CostEngine(db_path=str(db_file))  # second process/instance
        conn = sqlite3.connect(str(db_file))
        count = conn.execute("SELECT COUNT(*) FROM tp_pricing").fetchone()[0]
        conn.close()
        assert count == len(SEED_PRICING)

    def test_direct_double_seed_is_idempotent(self, tmp_path):
        """Simulates the cross-process COUNT-then-seed race: even if two
        writers both decide to seed, the uniqueness key keeps one row set."""
        from tokenpak.telemetry.cost import SEED_PRICING, CostEngine

        db_file = tmp_path / "telemetry.db"
        engine = CostEngine(db_path=str(db_file))
        conn = sqlite3.connect(str(db_file))
        engine._seed(conn)  # racing second seed
        count = conn.execute("SELECT COUNT(*) FROM tp_pricing").fetchone()[0]
        conn.close()
        assert count == len(SEED_PRICING)

    def test_legacy_duplicate_rows_deduped_on_migration(self, tmp_path):
        from tokenpak.telemetry.cost import CostEngine

        db_file = tmp_path / "telemetry.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute(
            """
            CREATE TABLE tp_pricing (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                version        TEXT    NOT NULL,
                effective_date DATE    NOT NULL,
                provider       TEXT    NOT NULL,
                model          TEXT    NOT NULL,
                input_rate     REAL    NOT NULL,
                output_rate    REAL    NOT NULL,
                currency       TEXT    NOT NULL DEFAULT 'USD',
                source         TEXT    NOT NULL DEFAULT 'official',
                created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Double-seeded legacy state: same key, diverging rates.
        for rate in (1.0, 2.0):
            conn.execute(
                "INSERT INTO tp_pricing (version, effective_date, provider, model, "
                "input_rate, output_rate) VALUES ('v-old', '2026-01-01', 'p', 'm', ?, ?)",
                (rate, rate * 5),
            )
        conn.commit()
        conn.close()

        CostEngine(db_path=str(db_file))  # migration runs here
        conn = sqlite3.connect(str(db_file))
        rows = conn.execute(
            "SELECT input_rate FROM tp_pricing WHERE version='v-old' AND provider='p' AND model='m'"
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == 2.0  # newest kept


# ===========================================================================
# 4. TelemetryDB threading + transactional traces
# ===========================================================================


class TestStorageThreading:
    def test_per_thread_connections(self, tmp_path):
        from tokenpak.telemetry.storage import TelemetryDB

        db = TelemetryDB(tmp_path / "t.db")
        conn_ids = set()
        barrier = threading.Barrier(4)

        def grab():
            barrier.wait()
            conn_ids.add(id(db._conn))

        with ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(lambda _: grab(), range(4)))
        assert len(conn_ids) == 4, "threads must not share one sqlite connection"
        db.close()

    def test_concurrent_ingest_no_partial_traces(self, tmp_path):
        """4 writer threads ingesting complete traces concurrently: every
        trace must end up with event+usage+cost rows (no interleaved-commit
        corruption, no half-written traces)."""
        from tokenpak.telemetry.storage import TelemetryDB

        db = TelemetryDB(tmp_path / "t.db")
        per_thread = 25
        threads = 4

        def ingest(worker: int):
            for i in range(per_thread):
                trace_id = f"w{worker}-t{i}"
                db.insert_trace(
                    _make_event(trace_id),
                    usage=_make_usage(trace_id),
                    cost=_make_cost(trace_id),
                )

        with ThreadPoolExecutor(max_workers=threads) as ex:
            list(ex.map(ingest, range(threads)))

        conn = sqlite3.connect(str(tmp_path / "t.db"))
        events = {r[0] for r in conn.execute("SELECT trace_id FROM tp_events")}
        usages = {r[0] for r in conn.execute("SELECT trace_id FROM tp_usage")}
        costs = {r[0] for r in conn.execute("SELECT trace_id FROM tp_costs")}
        conn.close()
        expected = {f"w{w}-t{i}" for w in range(threads) for i in range(per_thread)}
        assert events == expected
        assert usages == expected
        assert costs == expected
        db.close()

    def test_insert_trace_is_atomic(self, tmp_path, monkeypatch):
        """A failed usage insert must roll back the event insert too."""
        from tokenpak.telemetry.storage import TelemetryDB

        db = TelemetryDB(tmp_path / "t.db")

        def boom(*args, **kwargs):
            raise sqlite3.OperationalError("injected failure")

        monkeypatch.setattr(db, "_insert_usages", boom)
        trace_id = uuid.uuid4().hex
        with pytest.raises(sqlite3.OperationalError):
            db.insert_trace(_make_event(trace_id), usage=_make_usage(trace_id))

        conn = sqlite3.connect(str(tmp_path / "t.db"))
        n = conn.execute(
            "SELECT COUNT(*) FROM tp_events WHERE trace_id=?", (trace_id,)
        ).fetchone()[0]
        conn.close()
        assert n == 0, "event row must not survive a failed trace transaction"
        db.close()

    def test_memory_db_shared_across_threads(self):
        from tokenpak.telemetry.storage import TelemetryDB

        db = TelemetryDB(":memory:")
        trace_id = uuid.uuid4().hex

        def write():
            db.insert_trace(_make_event(trace_id))

        t = threading.Thread(target=write)
        t.start()
        t.join()
        rows = db._conn.execute(
            "SELECT COUNT(*) FROM tp_events WHERE trace_id=?", (trace_id,)
        ).fetchone()[0]
        assert rows == 1, ":memory: DB must be visible across threads"
        db.close()


class TestPipelineTruthfulness:
    def _event(self):
        return {
            "model": "claude-test",
            "provider": "anthropic",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "status": "ok",
        }

    def test_happy_path_persists_full_trace(self, tmp_path):
        from tokenpak.telemetry.pipeline import TelemetryPipeline
        from tokenpak.telemetry.storage import TelemetryDB

        db = TelemetryDB(tmp_path / "p.db")
        result = TelemetryPipeline(db).process(self._event())
        assert result.success is True
        conn = sqlite3.connect(str(tmp_path / "p.db"))
        assert conn.execute("SELECT COUNT(*) FROM tp_events").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tp_usage").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tp_costs").fetchone()[0] == 1
        conn.close()
        db.close()

    def test_failed_usage_cost_write_not_reported_as_success(
        self, tmp_path, monkeypatch
    ):
        """The old pipeline swallowed usage/cost insert failures and still
        returned success=True. A trace whose rows cannot all be persisted
        must be reported as a failure."""
        from tokenpak.telemetry.pipeline import TelemetryPipeline
        from tokenpak.telemetry.storage import TelemetryDB

        db = TelemetryDB(tmp_path / "p.db")

        def boom(*args, **kwargs):
            raise sqlite3.OperationalError("injected usage-write failure")

        monkeypatch.setattr(db, "_insert_usages", boom)
        result = TelemetryPipeline(db).process(self._event())
        assert result.success is False
        assert result.error
        db.close()


# ===========================================================================
# 5. CWD-independent DB paths
# ===========================================================================


class TestCwdIndependence:
    def test_routing_ledger_default_ignores_cwd(self, tmp_path, monkeypatch):
        from tokenpak.routing.routing_ledger import RoutingLedger

        home = tmp_path / "home"
        monkeypatch.setenv("TOKENPAK_HOME", str(home))

        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        monkeypatch.chdir(dir_a)
        ledger_a = RoutingLedger()
        monkeypatch.chdir(dir_b)
        ledger_b = RoutingLedger()

        assert ledger_a.db_path == ledger_b.db_path
        assert Path(ledger_a.db_path) == home / "routing_ledger.db"
        assert not (dir_a / ".tokenpak").exists()
        assert not (dir_b / ".tokenpak").exists()

    def test_registry_default_ignores_cwd(self, tmp_path, monkeypatch):
        from tokenpak.core.registry import BlockRegistry

        home = tmp_path / "home"
        monkeypatch.setenv("TOKENPAK_HOME", str(home))

        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        monkeypatch.chdir(dir_a)
        reg_a = BlockRegistry()
        monkeypatch.chdir(dir_b)
        reg_b = BlockRegistry()
        try:
            assert reg_a.db_path == reg_b.db_path
            assert Path(reg_a.db_path) == home / "registry.db"
            assert not (dir_a / ".tokenpak").exists()
            assert not (dir_b / ".tokenpak").exists()
        finally:
            reg_a.close()
            reg_b.close()

    def test_legacy_cwd_file_used_as_fallback_with_warning(
        self, tmp_path, monkeypatch, caplog
    ):
        from tokenpak.routing.routing_ledger import RoutingLedger

        home = tmp_path / "home"
        monkeypatch.setenv("TOKENPAK_HOME", str(home))
        workdir = tmp_path / "work"
        workdir.mkdir()
        monkeypatch.chdir(workdir)

        # Pre-existing legacy CWD-relative ledger, no canonical file.
        legacy = workdir / ".tokenpak" / "routing_ledger.db"
        RoutingLedger(str(legacy))  # creates the legacy file

        import logging

        with caplog.at_level(logging.WARNING, logger="tokenpak.routing.routing_ledger"):
            ledger = RoutingLedger()
        assert Path(ledger.db_path).resolve() == legacy.resolve()
        joined = " ".join(rec.getMessage() for rec in caplog.records)
        assert "routing_ledger.db" in joined
        assert str(home / "routing_ledger.db") in joined

    def test_explicit_path_still_respected(self, tmp_path, monkeypatch):
        from tokenpak.routing.routing_ledger import RoutingLedger

        monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "home"))
        explicit = tmp_path / "elsewhere" / "ledger.db"
        explicit.parent.mkdir()
        ledger = RoutingLedger(str(explicit))
        assert ledger.db_path == str(explicit)

    def test_ledger_write_errors_counted_not_raised(self, tmp_path, monkeypatch):
        from tokenpak.routing.routing_ledger import RoutingLedger

        monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "home"))
        ledger = RoutingLedger()

        def locked():
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(ledger, "_connect", locked)
        row_id = ledger.log_transaction(
            model="m", query="q", context_blocks=[], response="r"
        )
        assert row_id == -1
        assert ledger.record_outcome(1, accepted=True) is False
        assert ledger._write_errors == 2
