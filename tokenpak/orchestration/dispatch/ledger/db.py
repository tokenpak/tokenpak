"""Dispatch Run Ledger — SQLite persistence for Dispatch execution records.

The Run Ledger is the durable store for the ten Dispatch record classes
produced during a run (Standards Delta v0 §4–§5). It lives under the canonical
TokenPak home (``~/.tpk/dispatch/runs.db``, resolved via
:func:`tokenpak._paths.under`) and **never** writes into the project repo
(acceptance criterion 6).

Design (acceptance criteria 1–9):

* **One table per record class**, created/upgraded by the versioned, idempotent
  migration ladder in :mod:`.migrations` (criteria 2, 3).
* **Atomic writes** — every write helper commits a single ``INSERT OR REPLACE``
  inside a transaction; an error rolls the transaction back so a partial row is
  never visible (criterion 4).
* **Faithful serialization** — records are stored as their full
  ``model.model_dump_json()`` blob in a ``payload`` column, alongside indexed
  identity columns; reads reconstruct the pydantic model via
  ``model_validate_json()`` (criterion 8).
* **Effect lifecycle** — :meth:`RunLedger.record_planned_effect` writes a
  ``planned`` DispatchEffect *before* tool execution;
  :meth:`RunLedger.mark_effect_applied` / :meth:`RunLedger.mark_effect_failed`
  finalize it after. :meth:`RunLedger.select_dangling_planned_effects` returns
  ``planned`` effects with no ``finalized_at`` for resume reconciliation
  (Standards Delta v0 §4.8 / §5.5, criterion 5).
* **Execution records only** — the ledger does not promote records to canonical
  Pak types (criterion 7).

Pydantic is imported at the package boundary (:mod:`.__init__`) with a guarded
install hint; this module assumes the record models import cleanly.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Iterator, Optional

from tokenpak import _paths

from ..models import (
    DispatchArtifact,
    DispatchDecision,
    DispatchEffect,
    DispatchJob,
    DispatchManifest,
    DispatchReceipt,
    DispatchRoute,
    DispatchRun,
    DispatchStationRun,
    LateResult,
)
from ..models.enums import EffectStatus
from .migrations import get_current_schema_version, migrate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pydantic import BaseModel

# On-disk location of the Run Ledger database, relative to the resolved
# TokenPak home. Routed through ``_paths.under`` so the location follows
# ``TOKENPAK_HOME`` / ``~/.tpk`` / legacy resolution and is never hardcoded.
_LEDGER_DB_PARTS: tuple[str, ...] = ("dispatch", "runs.db")


def ledger_db_path() -> Path:
    """Return the Run Ledger DB path (``<tokenpak-home>/dispatch/runs.db``).

    Pure path resolution via :func:`tokenpak._paths.under`; the parent directory
    is not required to exist (it is created by :class:`RunLedger` on open).
    """

    return _paths.under(*_LEDGER_DB_PARTS)


class RunLedger:
    """SQLite-backed store for Dispatch execution records.

    Open the ledger at the canonical path with no arguments, or pass an explicit
    ``db_path`` (used by tests against ``tmp_path``)::

        ledger = RunLedger()                     # ~/.tpk/dispatch/runs.db
        ledger = RunLedger(db_path=tmp / "runs.db")

    On open the parent directory is created (mode 0700, matching the home
    contract) and the migration ladder is applied so the schema is current.
    """

    def __init__(self, db_path: Optional[Path | str] = None) -> None:
        self.db_path: Path = Path(db_path) if db_path is not None else ledger_db_path()
        # Create the parent directory but NEVER under the project repo — the path
        # always resolves under the TokenPak home (criterion 6). Mode 0700 mirrors
        # ``_paths.ensure_home`` (the dir may hold execution records).
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        migrate(self._conn)

    # -- lifecycle ----------------------------------------------------------

    @property
    def schema_version(self) -> int:
        """The migrated-to schema version recorded in the database header."""

        return get_current_schema_version(self._conn)

    def close(self) -> None:
        """Close the underlying SQLite connection."""

        self._conn.close()

    def __enter__(self) -> "RunLedger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transaction helper -------------------------------------------------

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Wrap a write in a transaction; rollback on error (criterion 4).

        On any exception the transaction is rolled back so no partial row is
        ever visible, then the exception is re-raised for the caller.
        """

        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def _insert(self, table: str, columns: dict[str, object]) -> None:
        """Atomic ``INSERT OR REPLACE`` of one row into *table*."""

        cols = ", ".join(columns)
        placeholders = ", ".join(["?"] * len(columns))
        sql = f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({placeholders})"
        with self._transaction() as conn:
            conn.execute(sql, tuple(columns.values()))

    @staticmethod
    def _iso(value: Optional[datetime]) -> Optional[str]:
        """Serialize a datetime to ISO-8601, passing ``None`` through."""

        return value.isoformat() if value is not None else None

    def _read_payload(
        self, table: str, record_id: str, model: "type[BaseModel]"
    ) -> Optional["BaseModel"]:
        """Read one row's ``payload`` and reconstruct *model*; ``None`` if absent."""

        row = self._conn.execute(
            f"SELECT payload FROM {table} WHERE id = ?", (record_id,)
        ).fetchone()
        if row is None:
            return None
        return model.model_validate_json(row["payload"])

    # -- DispatchJob --------------------------------------------------------

    def write_job(self, job: DispatchJob) -> None:
        self._insert(
            "dispatch_jobs",
            {
                "id": job.id,
                "source_task_packet_id": job.source_task_packet_id,
                "detected_intent": job.detected_intent,
                "autonomy_mode": job.autonomy_mode.value,
                "status": job.status.value,
                "created_at": self._iso(job.created_at),
                "payload": job.model_dump_json(),
            },
        )

    def read_job(self, job_id: str) -> Optional[DispatchJob]:
        return self._read_payload("dispatch_jobs", job_id, DispatchJob)  # type: ignore[return-value]

    # -- DispatchManifest ---------------------------------------------------

    def write_manifest(self, manifest: DispatchManifest) -> None:
        self._insert(
            "dispatch_manifests",
            {
                "id": manifest.id,
                "job_id": manifest.job_id,
                "route_id": manifest.route_id,
                "status": manifest.status.value,
                "payload": manifest.model_dump_json(),
            },
        )

    def read_manifest(self, manifest_id: str) -> Optional[DispatchManifest]:
        return self._read_payload(  # type: ignore[return-value]
            "dispatch_manifests", manifest_id, DispatchManifest
        )

    # -- DispatchRoute ------------------------------------------------------

    def write_route(self, route: DispatchRoute) -> None:
        self._insert(
            "dispatch_routes",
            {
                "id": route.id,
                "name": route.name,
                "default_risk": route.default_risk.value,
                "payload": route.model_dump_json(),
            },
        )

    def read_route(self, route_id: str) -> Optional[DispatchRoute]:
        return self._read_payload("dispatch_routes", route_id, DispatchRoute)  # type: ignore[return-value]

    # -- DispatchRun --------------------------------------------------------

    def write_run(self, run: DispatchRun) -> None:
        self._insert(
            "dispatch_runs",
            {
                "id": run.id,
                "job_id": run.job_id,
                "manifest_id": run.manifest_id,
                "route_id": run.route_id,
                "status": run.status,
                "started_at": self._iso(run.started_at),
                "ended_at": self._iso(run.ended_at),
                "receipt_id": run.receipt_id,
                "payload": run.model_dump_json(),
            },
        )

    def read_run(self, run_id: str) -> Optional[DispatchRun]:
        return self._read_payload("dispatch_runs", run_id, DispatchRun)  # type: ignore[return-value]

    # -- DispatchStationRun -------------------------------------------------

    def write_station_run(self, station_run: DispatchStationRun) -> None:
        self._insert(
            "dispatch_station_runs",
            {
                "id": station_run.id,
                "run_id": station_run.run_id,
                "station_id": station_run.station_id,
                "worker_id": station_run.worker_id,
                "status": station_run.status.value,
                "attempt_number": station_run.attempt_number,
                "payload": station_run.model_dump_json(),
            },
        )

    def read_station_run(self, station_run_id: str) -> Optional[DispatchStationRun]:
        return self._read_payload(  # type: ignore[return-value]
            "dispatch_station_runs", station_run_id, DispatchStationRun
        )

    def read_station_runs_for_run(self, run_id: str) -> list[DispatchStationRun]:
        """Return every station run for *run_id*, ordered by insertion (rowid).

        Resume reconciliation (Standards Delta v0 §5.5) needs the station runs of
        a run in execution order so it can inspect the *last* one. SQLite assigns
        a monotonically increasing implicit ``rowid`` in insert order, so ordering
        by it reproduces the order the runner wrote the rows (the runner runs
        stations sequentially — there is no parallel interleaving to disambiguate).
        """

        rows = self._conn.execute(
            "SELECT payload FROM dispatch_station_runs WHERE run_id = ? ORDER BY rowid ASC",
            (run_id,),
        ).fetchall()
        return [DispatchStationRun.model_validate_json(row["payload"]) for row in rows]

    def read_effects_for_station_run(self, station_run_id: str) -> list[DispatchEffect]:
        """Return every effect recorded for *station_run_id* (ordered by created_at).

        Used by resume reconciliation (Standards Delta v0 §5.5 cases 3 & 4) to
        enumerate the applied / planned effects of the interrupted station so the
        runner can compare current workspace hashes against each effect's
        ``after_hash`` / ``before_hash``.
        """

        rows = self._conn.execute(
            "SELECT payload FROM dispatch_effects WHERE station_run_id = ? "
            "ORDER BY created_at ASC, rowid ASC",
            (station_run_id,),
        ).fetchall()
        return [DispatchEffect.model_validate_json(row["payload"]) for row in rows]

    # -- DispatchDecision ---------------------------------------------------

    def write_decision(self, decision: DispatchDecision) -> None:
        self._insert(
            "dispatch_decisions",
            {
                "id": decision.id,
                "job_id": decision.job_id,
                "scope": decision.scope.value,
                "status": decision.status.value,
                "risk_level": decision.risk_level.value,
                "created_at": self._iso(decision.created_at),
                "payload": decision.model_dump_json(),
            },
        )

    def read_decision(self, decision_id: str) -> Optional[DispatchDecision]:
        return self._read_payload(  # type: ignore[return-value]
            "dispatch_decisions", decision_id, DispatchDecision
        )

    # -- DispatchArtifact ---------------------------------------------------

    def write_artifact(self, artifact: DispatchArtifact) -> None:
        self._insert(
            "dispatch_artifacts",
            {
                "id": artifact.id,
                "job_id": artifact.job_id,
                "station_run_id": artifact.station_run_id,
                "kind": artifact.kind,
                "content_hash": artifact.content_hash,
                "created_at": self._iso(artifact.created_at),
                "payload": artifact.model_dump_json(),
            },
        )

    def read_artifact(self, artifact_id: str) -> Optional[DispatchArtifact]:
        return self._read_payload(  # type: ignore[return-value]
            "dispatch_artifacts", artifact_id, DispatchArtifact
        )

    # -- DispatchReceipt ----------------------------------------------------

    def write_receipt(self, receipt: DispatchReceipt) -> None:
        self._insert(
            "dispatch_receipts",
            {
                "id": receipt.id,
                "job_id": receipt.job_id,
                "run_id": receipt.run_id,
                "route_id": receipt.route_id,
                "final_status": receipt.final_status,
                "created_at": self._iso(receipt.created_at),
                "payload": receipt.model_dump_json(),
            },
        )

    def read_receipt(self, receipt_id: str) -> Optional[DispatchReceipt]:
        return self._read_payload(  # type: ignore[return-value]
            "dispatch_receipts", receipt_id, DispatchReceipt
        )

    # -- LateResult ---------------------------------------------------------

    def write_late_result(self, late_result: LateResult) -> None:
        self._insert(
            "late_results",
            {
                "id": late_result.id,
                "job_id": late_result.job_id,
                "station_run_id": late_result.station_run_id,
                "received_at": self._iso(late_result.received_at),
                "payload": late_result.model_dump_json(),
            },
        )

    def read_late_result(self, late_result_id: str) -> Optional[LateResult]:
        return self._read_payload("late_results", late_result_id, LateResult)  # type: ignore[return-value]

    # -- DispatchEffect (generic write/read) --------------------------------

    def write_effect(self, effect: DispatchEffect) -> None:
        """Persist a DispatchEffect at whatever lifecycle state it carries.

        The lifecycle helpers below (:meth:`record_planned_effect` /
        :meth:`mark_effect_applied` / :meth:`mark_effect_failed`) are the
        preferred API for the §4.8 protocol; this is the low-level writer they
        build on, and is also usable directly for replay/import.
        """

        self._insert(
            "dispatch_effects",
            {
                "id": effect.id,
                "job_id": effect.job_id,
                "station_run_id": effect.station_run_id,
                "tool_name": effect.tool_name,
                "target_type": effect.target_type.value,
                "target": effect.target,
                "status": effect.status.value,
                "created_at": self._iso(effect.created_at),
                "finalized_at": self._iso(effect.finalized_at),
                "payload": effect.model_dump_json(),
            },
        )

    def read_effect(self, effect_id: str) -> Optional[DispatchEffect]:
        return self._read_payload("dispatch_effects", effect_id, DispatchEffect)  # type: ignore[return-value]

    # -- DispatchEffect lifecycle (Standards Delta v0 §4.8) -----------------

    def record_planned_effect(self, effect: DispatchEffect) -> DispatchEffect:
        """Write a ``planned`` effect BEFORE tool execution (§4.8 protocol).

        The effect MUST be in the ``planned`` state with no ``finalized_at``;
        this is the durable "I am about to mutate" marker that resume
        reconciliation reads if execution is interrupted. Returns the effect
        unchanged for caller convenience.
        """

        if effect.status is not EffectStatus.PLANNED:
            raise ValueError(
                "record_planned_effect requires a 'planned' effect "
                f"(got {effect.status.value!r})"
            )
        if effect.finalized_at is not None:
            raise ValueError(
                "a planned effect must not carry finalized_at "
                "(it is set when the effect is finalized)"
            )
        self.write_effect(effect)
        return effect

    def mark_effect_applied(
        self,
        effect_id: str,
        *,
        finalized_at: Optional[datetime] = None,
        after_hash: Optional[str] = None,
        rollback_available: Optional[bool] = None,
    ) -> DispatchEffect:
        """Transition a planned effect to ``applied`` AFTER success (§4.8).

        Sets ``status=applied`` and ``finalized_at`` (defaulting to now in UTC).
        Optionally records the post-write ``after_hash`` and
        ``rollback_available`` flag if the caller computed them after the write.
        The full row (typed columns + payload) is rewritten atomically. Raises
        ``KeyError`` if the effect id is unknown.
        """

        return self._finalize_effect(
            effect_id,
            EffectStatus.APPLIED,
            finalized_at=finalized_at,
            after_hash=after_hash,
            rollback_available=rollback_available,
        )

    def mark_effect_failed(
        self,
        effect_id: str,
        *,
        finalized_at: Optional[datetime] = None,
    ) -> DispatchEffect:
        """Transition a planned effect to ``failed`` on error (§4.8).

        Sets ``status=failed`` and ``finalized_at``. Raises ``KeyError`` if the
        effect id is unknown.
        """

        return self._finalize_effect(
            effect_id, EffectStatus.FAILED, finalized_at=finalized_at
        )

    def _finalize_effect(
        self,
        effect_id: str,
        status: EffectStatus,
        *,
        finalized_at: Optional[datetime] = None,
        after_hash: Optional[str] = None,
        rollback_available: Optional[bool] = None,
    ) -> DispatchEffect:
        """Load → mutate → re-persist an effect to a finalized state (atomic)."""

        effect = self.read_effect(effect_id)
        if effect is None:
            raise KeyError(f"unknown effect id {effect_id!r}")
        effect.status = status
        effect.finalized_at = finalized_at or datetime.now().astimezone()
        if after_hash is not None:
            effect.after_hash = after_hash
        if rollback_available is not None:
            effect.rollback_available = rollback_available
        self.write_effect(effect)
        return effect

    def select_dangling_planned_effects(self, run_id: str) -> list[DispatchEffect]:
        """Return ``planned`` effects with no ``finalized_at`` for *run_id*.

        These are the "effect started but never finalized" records of Standards
        Delta v0 §5.5 step 4 — an interrupted effect that resume reconciliation
        must inspect. Effects are linked to a run via their station runs
        (DispatchEffect.station_run_id → DispatchStationRun.run_id), so this
        joins through ``dispatch_station_runs``. Results are ordered by
        ``created_at`` (oldest first) for deterministic reconciliation.
        """

        rows = self._conn.execute(
            """
            SELECT e.payload AS payload
            FROM dispatch_effects AS e
            JOIN dispatch_station_runs AS sr
              ON e.station_run_id = sr.id
            WHERE sr.run_id = ?
              AND e.status = ?
              AND e.finalized_at IS NULL
            ORDER BY e.created_at ASC
            """,
            (run_id, EffectStatus.PLANNED.value),
        ).fetchall()
        return [DispatchEffect.model_validate_json(row["payload"]) for row in rows]


__all__ = ["RunLedger", "ledger_db_path"]
