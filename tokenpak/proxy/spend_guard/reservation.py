# SPDX-License-Identifier: Apache-2.0
"""Durable, scoped admission against the existing rolling-cap policy.

The request ledger remains the recorded usage source. Its fresh baseline is
read *after* the reservation write lock is acquired; a caller cannot pass an
older baseline into admission. Committed usage must become visible before a
hold is settled. Expired holds retain their unresolved history.
"""

from __future__ import annotations

import hashlib
import math
import os
import secrets
import sqlite3
import stat
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .rolling_caps import CapBreach, RollingCapsConfig, RollingUsage, _query_recorded_usage

_DIMENSIONS = (
    ("agent_cost_usd", "per_agent_max_cost_usd", "cost_usd"),
    ("agent_tokens_total", "per_agent_max_tokens_total", "tokens_total"),
    ("agent_cache_read_tokens", "per_agent_max_cache_read_tokens", "cache_read_tokens"),
    ("fleet_cost_usd", "per_fleet_max_cost_usd", "cost_usd"),
    ("fleet_tokens_total", "per_fleet_max_tokens_total", "tokens_total"),
    ("fleet_cache_read_tokens", "per_fleet_max_cache_read_tokens", "cache_read_tokens"),
)
_HISTORY_SECONDS = 30 * 24 * 3600
_MAX_HISTORY_ROWS = 100_000


class ReservationUnavailable(RuntimeError):
    """The native accounting state cannot support an admission decision."""


def _number(value: object, name: str, *, integer: bool = False) -> int | float:
    if type(value) not in ((int,) if integer else (int, float)):
        raise ValueError(f"{name} has an invalid numeric type")
    try:
        valid = math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be finite and nonnegative")
    return value


def _identity(value: str, name: str, *, empty: bool = False) -> str:
    if (
        not isinstance(value, str)
        or (not value and not empty)
        or len(value) > 512
        or value.strip() != value
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError(f"invalid {name}")
    return value


@dataclass(frozen=True)
class Projection:
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int

    def __post_init__(self) -> None:
        _number(self.cost_usd, "cost_usd")
        for name in ("input_tokens", "output_tokens", "cache_read_tokens"):
            _number(getattr(self, name), name, integer=True)

    @property
    def tokens_total(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ReservationRef:
    store_path: str
    ledger_key: str
    reservation_id: str

    def __post_init__(self) -> None:
        if not Path(self.store_path).is_absolute():
            raise ValueError("reservation store path must be absolute")
        if len(self.ledger_key) != 64 or any(c not in "0123456789abcdef" for c in self.ledger_key):
            raise ValueError("invalid reservation ledger key")
        if (
            not self.reservation_id.startswith("tpr_")
            or len(self.reservation_id) != 36
            or any(c not in "0123456789abcdef" for c in self.reservation_id[4:])
        ):
            raise ValueError("invalid reservation identity")


@dataclass
class ReservationBreach(CapBreach):
    settled_used: float = 0.0
    reserved_active: float = 0.0


def pessimistic_output_reservation(
    max_tokens: int | None, model_context_tokens: int | None, input_tokens: int
) -> int:
    _number(input_tokens, "input_tokens", integer=True)
    if max_tokens is not None:
        _number(max_tokens, "max_tokens", integer=True)
        if max_tokens == 0:
            raise ValueError("max_tokens must be positive when declared")
        return max_tokens
    if model_context_tokens is None:
        return 32000
    _number(model_context_tokens, "model_context_tokens", integer=True)
    if model_context_tokens == 0:
        return 32000
    return min(max(0, model_context_tokens - input_tokens), 32000)


def _private_connection(path: Path, *, write: bool, create: bool = False) -> sqlite3.Connection:
    if create:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ReservationUnavailable("reservation store must be an owned regular file")
    if info.st_mode & 0o077:
        if not create:
            raise ReservationUnavailable("reservation store is not private")
        os.chmod(path, 0o600, follow_symlinks=False)
    conn = sqlite3.connect(
        path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
        uri=True,
        timeout=2.0,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    return conn


def _schema(conn: sqlite3.Connection) -> None:
    """Add columns without discarding rows from an older reservation store."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS budget_reservations (
            reservation_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL DEFAULT '', fleet_id TEXT NOT NULL DEFAULT '',
            agent_id TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
            expires_at REAL NOT NULL, reserved_input_tokens INTEGER NOT NULL DEFAULT 0,
            reserved_output_tokens INTEGER NOT NULL DEFAULT 0,
            reserved_cost_usd REAL NOT NULL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'active', actual_cost_usd REAL, actual_tokens INTEGER
        )""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(budget_reservations)")}
        for name, definition in (
            ("ledger_key", "TEXT NOT NULL DEFAULT ''"),
            ("owner_instance_id", "TEXT NOT NULL DEFAULT ''"),
            ("request_id", "TEXT NOT NULL DEFAULT ''"),
            ("reserved_cache_read_tokens", "INTEGER NOT NULL DEFAULT 0"),
            ("actual_cache_read_tokens", "INTEGER"),
            ("settled_at", "REAL"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE budget_reservations ADD COLUMN {name} {definition}")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_reservation_scope "
            "ON budget_reservations(ledger_key, status, expires_at)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS budget_reservation_generation "
            "(ledger_key TEXT PRIMARY KEY, generation INTEGER NOT NULL)"
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS budget_guard_coverage (
            coverage_id TEXT PRIMARY KEY, ledger_key TEXT NOT NULL,
            owner_instance_id TEXT NOT NULL, session_id TEXT NOT NULL,
            started_at REAL NOT NULL, ended_at REAL, attempts INTEGER NOT NULL DEFAULT 0,
            reservation_id TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'preflight'
        )""")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_guard_coverage_scope "
            "ON budget_guard_coverage(ledger_key, state, ended_at)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_guard_coverage_reservation "
            "ON budget_guard_coverage(ledger_key, reservation_id) WHERE reservation_id != ''"
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _generation_changed(conn: sqlite3.Connection, ledger_key: str) -> None:
    conn.execute(
        "INSERT INTO budget_reservation_generation VALUES (?, 1) "
        "ON CONFLICT(ledger_key) DO UPDATE SET generation=generation+1",
        (ledger_key,),
    )


def _maintain_history(
    conn, ledger_key: str, window_seconds: int, history_seconds: int, max_records: int
) -> None:
    """Bound completed metadata during writes; unresolved evidence is retained."""
    cutoff = time.time() - max(history_seconds, window_seconds)
    deleted = 0
    for table, identity, predicate in (
        (
            "budget_reservations",
            "reservation_id",
            "status IN ('settled', 'released') AND settled_at < ?",
        ),
        (
            "budget_guard_coverage",
            "coverage_id",
            "state IN ('recorded', 'unsent') AND ended_at < ?",
        ),
    ):
        deleted += conn.execute(
            f"DELETE FROM {table} WHERE {identity} IN (SELECT {identity} FROM {table} "
            f"WHERE ledger_key=? AND {predicate} LIMIT 1000)",
            (ledger_key, cutoff),
        ).rowcount
        size = conn.execute(
            f"SELECT COUNT(*) FROM (SELECT 1 FROM {table} LIMIT ?)", (max_records,)
        ).fetchone()[0]
        if size >= max_records:
            raise ReservationUnavailable("reservation metadata capacity exhausted")
    if deleted:
        _generation_changed(conn, ledger_key)


def _validate_recorded_rows(conn, cutoff):
    invalid = conn.execute(
        "SELECT 1 FROM requests WHERE timestamp >= ? AND ("
        "estimated_cost IS NULL OR estimated_cost < 0 OR "
        "typeof(estimated_cost) NOT IN ('integer', 'real') OR "
        "input_tokens IS NULL OR input_tokens < 0 OR typeof(input_tokens) != 'integer' OR "
        "output_tokens IS NULL OR output_tokens < 0 OR typeof(output_tokens) != 'integer' OR "
        "cache_read_tokens IS NULL OR cache_read_tokens < 0 OR "
        "typeof(cache_read_tokens) != 'integer') LIMIT 1",
        (cutoff,),
    ).fetchone()
    if invalid:
        raise ReservationUnavailable("recorded usage contains unmeasurable values")
    if conn.execute(
        "SELECT 1 FROM requests WHERE timestamp >= ? AND guard_reservation_id != '' "
        "AND guard_usage_complete != 1 LIMIT 1",
        (cutoff,),
    ).fetchone():
        raise ReservationUnavailable("recorded reservation usage is incomplete")


class ReservationStore:
    """One configured guard store bound to one canonical monitor path.

    Construction and read methods never provision or migrate a store. The
    logical path binding is not exclusive ownership proof; the production
    snapshot must also validate its serving domain and request coverage.
    """

    def __init__(
        self,
        audit_db_path: str | Path,
        monitor_db_path: str | Path,
        *,
        history_seconds: int | None = None,
        max_records: int | None = None,
    ):
        self.history_seconds = _HISTORY_SECONDS if history_seconds is None else history_seconds
        self.max_records = _MAX_HISTORY_ROWS if max_records is None else max_records
        for name in ("history_seconds", "max_records"):
            if not _number(getattr(self, name), name, integer=True):
                raise ValueError(f"{name} must be positive")
        self.path = Path(audit_db_path).expanduser().absolute()
        self.monitor_path = Path(monitor_db_path).expanduser().resolve()
        with closing(sqlite3.connect(self.monitor_path.as_uri() + "?mode=ro", uri=True)) as ledger:
            row = ledger.execute(
                "SELECT ledger_id FROM guard_accounting_domain WHERE id=1"
            ).fetchone()
        if row is None or not isinstance(row[0], str) or len(row[0]) != 32:
            raise ReservationUnavailable("monitor accounting identity is unavailable")
        self.ledger_id = row[0]
        self.ledger_key = hashlib.sha256(
            (str(self.monitor_path) + ":" + self.ledger_id).encode()
        ).hexdigest()
        self.audit_store_sha256 = hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()

    def _bind_domain(self) -> None:
        # One monitor must not split its budget over independent guard files.
        with closing(
            sqlite3.connect(self.monitor_path.as_uri() + "?mode=rw", uri=True, timeout=2)
        ) as ledger:
            ledger.execute("BEGIN IMMEDIATE")
            try:
                ledger.execute(
                    "UPDATE guard_accounting_domain SET audit_store_sha256=? "
                    "WHERE id=1 AND ledger_id=? AND audit_store_sha256 IS NULL",
                    (self.audit_store_sha256, self.ledger_id),
                )
                self._check_domain(ledger)
                ledger.commit()
            except BaseException:
                ledger.rollback()
                raise

    def _check_domain(self, ledger) -> None:
        row = ledger.execute(
            "SELECT ledger_id, audit_store_sha256 FROM guard_accounting_domain WHERE id=1"
        ).fetchone()
        if row is None or tuple(row) != (self.ledger_id, self.audit_store_sha256):
            raise ReservationUnavailable(
                "monitor belongs to another reservation store or generation"
            )

    def _recorded(
        self, agent_id: str, window_seconds: int, now: float
    ) -> tuple[RollingUsage, dict[str, tuple[str, str]]]:
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - window_seconds))
        # Missing DB/schema and unreadable state are unavailable, never zero.
        with closing(
            sqlite3.connect(self.monitor_path.as_uri() + "?mode=ro", uri=True, timeout=2.0)
        ) as conn:
            conn.execute("BEGIN")
            deadline = time.monotonic() + 2.0
            self._check_domain(conn)
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            _validate_recorded_rows(conn, cutoff)
            usage = _query_recorded_usage(conn, cutoff, [], agent_id=agent_id)
            for key, value in usage.items():
                _number(value, key)
            committed = {
                row[0]: (row[1], row[2])
                for row in conn.execute(
                    "SELECT guard_reservation_id, session_id, lower(agent_id) FROM requests WHERE guard_ledger_key=? "
                    "AND guard_reservation_id != '' AND guard_usage_complete=1",
                    (self.ledger_key,),
                )
            }
            return usage, committed

    def _active(
        self,
        conn: sqlite3.Connection,
        agent_id: str,
        now: float,
        committed: dict[str, tuple[str, str]] | None = None,
    ) -> dict[str, float]:
        committed = committed or {}
        unscoped = conn.execute(
            "SELECT 1 FROM budget_reservations WHERE ledger_key='' "
            "AND status IN ('active', 'expired') LIMIT 1",
        ).fetchone()
        if unscoped:
            raise ReservationUnavailable("active legacy reservations have unknown ledger scope")
        result = {key: 0 for key, _, _ in _DIMENSIONS}
        rows = conn.execute(
            "SELECT * FROM budget_reservations WHERE ledger_key=? "
            "AND status IN ('active', 'expired')",
            (self.ledger_key,),
        )
        for row in rows:
            if row["reservation_id"] in committed:
                if committed[row["reservation_id"]] != (row["session_id"], row["agent_id"]):
                    raise ReservationUnavailable("committed reservation attribution changed")
                continue  # monitor commit is visible even if notification was lost
            expiry = _number(row["expires_at"], "expires_at")
            if expiry <= now or row["status"] == "expired":
                raise ReservationUnavailable("unresolved expired reservation")
            projection = Projection(
                row["reserved_cost_usd"],
                row["reserved_input_tokens"],
                row["reserved_output_tokens"],
                row["reserved_cache_read_tokens"],
            )
            for key, _, field in _DIMENSIONS:
                if key.startswith("fleet_") or row["agent_id"] == agent_id:
                    result[key] += getattr(projection, field)
                    _number(result[key], key)
        return result

    def reserve(
        self,
        *,
        session_id: str,
        agent_id: str,
        owner_instance_id: str,
        request_id: str,
        projection: Projection,
        caps: RollingCapsConfig,
        ttl_seconds: int = 600,
        force: bool = False,
    ) -> tuple[ReservationRef | None, ReservationBreach | None]:
        """Resolve, check and reserve under the same cross-process write lock."""
        _identity(session_id, "session_id", empty=True)
        agent_id = _identity(agent_id, "agent_id", empty=True).lower()
        _identity(owner_instance_id, "owner_instance_id")
        _identity(request_id, "request_id")
        if (
            not isinstance(projection, Projection)
            or type(force) is not bool
            or type(caps.enabled) is not bool
        ):
            raise ValueError("invalid reservation projection or policy")
        _number(ttl_seconds, "ttl_seconds", integer=True)
        _number(caps.window_seconds, "window_seconds", integer=True)
        if not ttl_seconds or not caps.window_seconds:
            raise ValueError("reservation TTL and rolling window must be positive")
        for _, field, _ in _DIMENSIONS:
            _number(getattr(caps, field), field)
        self._bind_domain()
        reservation_id = "tpr_" + secrets.token_hex(16)
        with closing(_private_connection(self.path, write=True, create=True)) as conn:
            _schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            try:
                now = time.time()
                _maintain_history(
                    conn,
                    self.ledger_key,
                    caps.window_seconds,
                    self.history_seconds,
                    self.max_records,
                )
                if not force and caps.enabled:
                    recorded, committed = self._recorded(agent_id, caps.window_seconds, now)
                    active = self._active(conn, agent_id, now, committed)
                    for key, field, add_field in _DIMENSIONS:
                        cap = getattr(caps, field)
                        if cap <= 0 or (key.startswith("agent_") and not agent_id):
                            continue
                        add = getattr(projection, add_field)
                        if recorded[key] + active[key] + add > cap:
                            conn.rollback()
                            return None, ReservationBreach(
                                cap_dimension=field.replace("_max", ""),
                                agent_id=agent_id or "unknown",
                                window_seconds=caps.window_seconds,
                                used=recorded[key] + active[key],
                                cap=cap,
                                projected_add=add,
                                retry_after_seconds=min(ttl_seconds, 60),
                                settled_used=recorded[key],
                                reserved_active=active[key],
                            )
                conn.execute(
                    "INSERT INTO budget_reservations (reservation_id, session_id, agent_id, "
                    "created_at, expires_at, reserved_input_tokens, reserved_output_tokens, "
                    "reserved_cost_usd, ledger_key, owner_instance_id, request_id, "
                    "reserved_cache_read_tokens) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        reservation_id,
                        session_id,
                        agent_id,
                        now,
                        now + ttl_seconds,
                        projection.input_tokens,
                        projection.output_tokens,
                        projection.cost_usd,
                        self.ledger_key,
                        owner_instance_id,
                        request_id,
                        projection.cache_read_tokens,
                    ),
                )
                _generation_changed(conn, self.ledger_key)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return ReservationRef(str(self.path), self.ledger_key, reservation_id), None

    def settle_after_commit(self, ref: ReservationRef, actual: Projection) -> bool:
        """Called only after the actual monitor row has committed.

        A late successful commit may resolve an expired hold. This does not
        discard expiry history by reclassifying it as a timely settlement.
        """
        if ref.store_path != str(self.path) or ref.ledger_key != self.ledger_key:
            raise ValueError("reservation reference belongs to another accounting scope")
        # Verify the durable row, not a caller's promise that enqueue succeeded.
        with closing(sqlite3.connect(self.monitor_path.as_uri() + "?mode=ro", uri=True)) as ledger:
            self._check_domain(ledger)
            rows = ledger.execute(
                "SELECT estimated_cost, input_tokens, output_tokens, cache_read_tokens, session_id, lower(agent_id) "
                "FROM requests WHERE guard_reservation_id=? AND guard_ledger_key=? "
                "AND guard_usage_complete=1",
                (ref.reservation_id, ref.ledger_key),
            ).fetchall()
        if len(rows) != 1 or Projection(*rows[0][:4]) != actual:
            raise ReservationUnavailable("reservation settlement lacks matching committed usage")
        with closing(_private_connection(self.path, write=True)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                attribution = conn.execute(
                    "SELECT session_id, agent_id FROM budget_reservations WHERE reservation_id=? AND ledger_key=?",
                    (ref.reservation_id, self.ledger_key),
                ).fetchone()
                if attribution is None or tuple(attribution) != tuple(rows[0][4:]):
                    raise ReservationUnavailable("committed reservation attribution changed")
                cursor = conn.execute(
                    "UPDATE budget_reservations SET status='settled', actual_cost_usd=?, "
                    "actual_tokens=?, actual_cache_read_tokens=?, settled_at=? "
                    "WHERE reservation_id=? AND ledger_key=? AND status IN ('active', 'expired')",
                    (
                        actual.cost_usd,
                        actual.tokens_total,
                        actual.cache_read_tokens,
                        time.time(),
                        ref.reservation_id,
                        self.ledger_key,
                    ),
                )
                if cursor.rowcount:
                    _generation_changed(conn, self.ledger_key)
                    conn.execute(
                        "UPDATE budget_guard_coverage SET state='recorded' "
                        "WHERE ledger_key=? AND reservation_id=? AND attempts=1",
                        (self.ledger_key, ref.reservation_id),
                    )
                conn.commit()
                return bool(cursor.rowcount)
            except BaseException:
                conn.rollback()
                raise

    def read_active(self, agent_id: str) -> dict[str, float]:
        """Read existing holds without schema creation, pruning or chmod."""
        with closing(_private_connection(self.path, write=False)) as conn:
            conn.execute("BEGIN")
            return self._active(
                conn, _identity(agent_id, "agent_id", empty=True).lower(), time.time()
            )

    def release_unsent(self, ref: ReservationRef) -> bool:
        """Release only when the owning request has not attempted a provider send."""
        if ref.store_path != str(self.path) or ref.ledger_key != self.ledger_key:
            raise ValueError("reservation reference belongs to another accounting scope")
        with closing(_private_connection(self.path, write=True)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    "UPDATE budget_reservations SET status='released', settled_at=? "
                    "WHERE reservation_id=? AND ledger_key=? AND status='active'",
                    (time.time(), ref.reservation_id, self.ledger_key),
                )
                if cursor.rowcount:
                    _generation_changed(conn, self.ledger_key)
                conn.commit()
                return bool(cursor.rowcount)
            except BaseException:
                conn.rollback()
                raise

    def begin_request(
        self, session_id: str, owner_instance_id: str, window_seconds: int = 3600
    ) -> str:
        _identity(session_id, "session_id", empty=True)
        _identity(owner_instance_id, "owner_instance_id")
        _number(window_seconds, "window_seconds", integer=True)
        self._bind_domain()
        coverage_id = "tpc_" + secrets.token_hex(16)
        with closing(_private_connection(self.path, write=True, create=True)) as conn:
            _schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            try:
                _maintain_history(
                    conn, self.ledger_key, window_seconds, self.history_seconds, self.max_records
                )
                conn.execute(
                    "INSERT INTO budget_guard_coverage "
                    "(coverage_id, ledger_key, owner_instance_id, session_id, started_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (coverage_id, self.ledger_key, owner_instance_id, session_id, time.time()),
                )
                _generation_changed(conn, self.ledger_key)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        return coverage_id

    def attempted_send(self, coverage_id: str, ref: ReservationRef | None) -> None:
        if ref is not None and (
            ref.ledger_key != self.ledger_key or ref.store_path != str(self.path)
        ):
            raise ValueError("reservation reference belongs to another accounting scope")
        self._update_coverage(
            "attempts=attempts+1, reservation_id=?, state='sending'",
            coverage_id,
            (ref.reservation_id if ref else "",),
        )

    def finish_request(self, coverage_id: str) -> None:
        # A response finishing before the async commit must not appear idle
        # and completely accounted. The monitor's correlated commit resolves it.
        self._update_coverage(
            "ended_at=?, state=CASE WHEN attempts=0 THEN 'unsent' "
            "WHEN state='recorded' THEN state ELSE 'awaiting_usage' END",
            coverage_id,
            (time.time(),),
        )

    def _update_coverage(self, assignments: str, coverage_id: str, values: tuple) -> None:
        with closing(_private_connection(self.path, write=True)) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = conn.execute(
                    "UPDATE budget_guard_coverage SET "
                    + assignments
                    + " WHERE coverage_id=? AND ledger_key=?",
                    (*values, coverage_id, self.ledger_key),
                )
                if cursor.rowcount != 1:
                    raise ReservationUnavailable("request coverage record is unavailable")
                _generation_changed(conn, self.ledger_key)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def snapshot(self, session_id: str, window_seconds: int) -> dict:
        """Read a generation-fenced, correlated view without writes or pruning."""
        _identity(session_id, "session_id")
        _number(window_seconds, "window_seconds", integer=True)
        if not window_seconds:
            raise ValueError("window must be positive")
        now = time.time()
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - window_seconds))
        with closing(_private_connection(self.path, write=False)) as conn:
            conn.execute("BEGIN")
            generation = self._read_generation(conn)
            deadline = time.monotonic() + 2.0
            conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            coverage = conn.execute(
                "SELECT * FROM budget_guard_coverage WHERE ledger_key=? "
                "AND (ended_at IS NULL OR ended_at >= ?) LIMIT 100001",
                (self.ledger_key, now - window_seconds),
            ).fetchall()
            if len(coverage) > 100000:
                raise ReservationUnavailable("snapshot coverage limit exceeded")
            # Scope and attribution are persisted facts, never an external
            # process's empty session map. Keep all monitor queries in one view.
            with closing(
                sqlite3.connect(self.monitor_path.as_uri() + "?mode=ro", uri=True)
            ) as ledger:
                ledger.execute("BEGIN")
                ledger.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                self._check_domain(ledger)
                agents = {
                    row[0]
                    for row in ledger.execute(
                        "SELECT DISTINCT lower(agent_id) FROM requests WHERE session_id=? "
                        "AND timestamp >= ?",
                        (session_id, cutoff),
                    )
                }
                agent = next(iter(agents)) if len(agents) == 1 else None
                rows = ledger.execute(
                    "SELECT guard_reservation_id, guard_ledger_key, guard_usage_complete, "
                    "session_id, lower(agent_id) FROM requests WHERE timestamp >= ? LIMIT 100001",
                    (cutoff,),
                ).fetchall()
                if len(rows) > 100000:
                    raise ReservationUnavailable("snapshot row limit exceeded")
                _validate_recorded_rows(ledger, cutoff)
                recorded = _query_recorded_usage(ledger, cutoff, [], agent_id=agent or "")
                for key, value in recorded.items():
                    _number(value, key)
                committed = {
                    row[0]: (row[3], row[4])
                    for row in rows
                    if row[1] == self.ledger_key and row[2] == 1
                }
                known = {}
                identifiers = list({row[0] for row in rows if row[0]})
                for start in range(0, len(identifiers), 500):
                    batch = identifiers[start : start + 500]
                    placeholders = ",".join("?" for _ in batch)
                    known.update(
                        {
                            row[0]: (row[1], row[2])
                            for row in conn.execute(
                                "SELECT reservation_id, session_id, agent_id FROM budget_reservations "
                                f"WHERE ledger_key=? AND reservation_id IN ({placeholders})",
                                (self.ledger_key, *batch),
                            )
                        }
                    )
                covered = {row["reservation_id"] for row in coverage if row["attempts"] == 1}
                reasons = []
                if any(
                    not row[0]
                    or row[0] not in covered
                    or known.get(row[0]) != (row[3], row[4])
                    or row[1] != self.ledger_key
                    or row[2] != 1
                    for row in rows
                ):
                    reasons.append("ledger_coverage_incomplete")
                if conn.execute(
                    "SELECT 1 FROM budget_reservations r WHERE r.ledger_key=? "
                    "AND r.status IN ('active', 'expired') AND NOT EXISTS "
                    "(SELECT 1 FROM budget_guard_coverage c WHERE c.ledger_key=r.ledger_key "
                    "AND c.reservation_id=r.reservation_id) LIMIT 1",
                    (self.ledger_key,),
                ).fetchone():
                    reasons.append("reservation_coverage_incomplete")
                pending = self._active(conn, agent or "", now, committed)
            for request in coverage:
                if request["ended_at"] is None:
                    reasons.append("request_in_progress")
                elif request["attempts"] and not (
                    request["attempts"] == 1 and request["reservation_id"] in committed
                ):
                    reasons.append("forward_usage_unresolved")
            observed = bool(agents)
            if not observed:
                reasons.append("session_unobserved")
            if len(agents) > 1:
                reasons.append("session_agent_contradiction")
        # This is a second fresh connection, not a reread of the first SQLite
        # transaction's frozen snapshot. Any intervening lifecycle write refuses.
        with closing(_private_connection(self.path, write=False)) as fence:
            if self._read_generation(fence) != generation:
                raise ReservationUnavailable("accounting changed during observation")
        if not agent:
            for values in (recorded, pending):
                for key in values:
                    if key.startswith("agent_"):
                        values[key] = None
        return {
            "observed_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "window_seconds": window_seconds,
            "ledger_scope_sha256": self.ledger_key,
            "accounting_generation": generation,
            "session_observed": observed,
            "agent_caps_applicable": bool(agent),
            "agent_attribution_available": bool(agent),
            "recorded_usage": recorded,
            "pending_projected_usage": pending,
            "expired_pending_count": 0,
            "components_may_overlap": False,
            "guard_evidence_eligible": not reasons,
            "reason_codes": sorted(set(reasons)),
        }

    def _read_generation(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT generation FROM budget_reservation_generation WHERE ledger_key=?",
            (self.ledger_key,),
        ).fetchone()
        if row is None:
            raise ReservationUnavailable("accounting generation is unavailable")
        return _number(row[0], "generation", integer=True)
