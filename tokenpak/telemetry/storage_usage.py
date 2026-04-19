"""Usage/Cost CRUD, pricing, prune, backfill, stats, and export mixin."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional

from tokenpak.telemetry.models import Cost, Usage
from tokenpak.telemetry.storage_base import _now


class UsageMixin:
    """Mixin providing Usage/Cost CRUD, pricing catalog, prune, and stats methods."""

    _conn: sqlite3.Connection

    def insert_usage(self, usage: Usage) -> None:
        """Persist a single :class:`Usage` record."""
        self._insert_usages([usage])

    def insert_usages(self, usages: list[Usage]) -> None:
        """Batch-insert a list of :class:`Usage` records."""
        self._insert_usages(usages)

    def _insert_usages(self, usages: list[Usage]) -> None:
        sql = """
        INSERT OR REPLACE INTO tp_usage
            (trace_id, usage_source, confidence, input_billed, output_billed,
             input_est, output_est, cache_read, cache_write, total_tokens,
             total_tokens_billed, total_tokens_est, provider_usage_raw)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        rows = [
            (
                u.trace_id,
                u.usage_source,
                u.confidence,
                u.input_billed,
                u.output_billed,
                u.input_est,
                u.output_est,
                u.cache_read,
                u.cache_write,
                u.total_tokens,
                u.total_tokens_billed,
                u.total_tokens_est,
                u.provider_usage_raw,
            )
            for u in usages
        ]
        self._conn.executemany(sql, rows)
        self._conn.commit()

    def insert_cost(self, cost: Cost) -> None:
        """Persist a single :class:`Cost` record."""
        self._insert_costs([cost])

    def insert_costs(self, costs: list[Cost]) -> None:
        """Batch-insert a list of :class:`Cost` records."""
        self._insert_costs(costs)

    def _insert_costs(self, costs: list[Cost]) -> None:
        sql = """
        INSERT OR REPLACE INTO tp_costs
            (trace_id, cost_input, cost_output, cost_cache_read,
             cost_cache_write, cost_total, cost_source, pricing_version,
             baseline_input_tokens, actual_input_tokens, output_tokens,
             baseline_cost, actual_cost, savings_total, savings_qmd, savings_tp)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        rows = [
            (
                c.trace_id,
                c.cost_input,
                c.cost_output,
                c.cost_cache_read,
                c.cost_cache_write,
                c.cost_total,
                c.cost_source,
                c.pricing_version,
                c.baseline_input_tokens,
                c.actual_input_tokens,
                c.output_tokens,
                c.baseline_cost,
                c.actual_cost,
                c.savings_total,
                c.savings_qmd,
                c.savings_tp,
            )
            for c in costs
        ]
        self._conn.executemany(sql, rows)
        self._conn.commit()

    def upsert_pricing_catalog(self, version: str, catalog_json: str) -> None:
        """Store a JSON snapshot of the pricing catalog.

        Parameters
        ----------
        version:
            Catalog version string (e.g. ``"v1"``).
        catalog_json:
            The serialised catalog data.
        """
        sql = """
        INSERT OR REPLACE INTO tp_pricing_catalog (version, captured_at, catalog_json)
        VALUES (?, ?, ?)
        """
        self._conn.execute(sql, (version, _now(), catalog_json))
        self._conn.commit()

    def get_pricing_catalog(self, version: str) -> Optional[dict[str, Any]]:
        """Retrieve a stored pricing catalog snapshot by version.

        Returns ``None`` if no snapshot for *version* exists.
        """
        cur = self._conn.cursor()
        cur.execute(
            "SELECT catalog_json FROM tp_pricing_catalog WHERE version = ?",
            (version,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    # ------------------------------------------------------------------
    # Retention / pruning
    # ------------------------------------------------------------------

    def prune(self, days: int = 90) -> int:
        """Delete events (and associated data) older than *days* days.

        Cascades to ``tp_usage``, ``tp_costs``, and ``tp_segments`` for
        any trace_id that no longer has a matching event.

        Parameters
        ----------
        days:
            Events with ``ts < (now - days * 86400)`` are deleted.

        Returns
        -------
        int
            Number of event rows deleted.
        """
        cutoff = _now() - days * 86_400
        cur = self._conn.cursor()

        # Collect trace_ids to prune
        cur.execute("SELECT DISTINCT trace_id FROM tp_events WHERE ts < ?", (cutoff,))
        old_traces = [r[0] for r in cur.fetchall()]

        if not old_traces:
            return 0

        placeholders = ",".join("?" * len(old_traces))

        cur.execute(
            f"DELETE FROM tp_events WHERE trace_id IN ({placeholders})",
            old_traces,
        )
        deleted = cur.rowcount

        cur.execute(
            f"DELETE FROM tp_usage WHERE trace_id IN ({placeholders})",
            old_traces,
        )
        cur.execute(
            f"DELETE FROM tp_costs WHERE trace_id IN ({placeholders})",
            old_traces,
        )
        cur.execute(
            f"DELETE FROM tp_segments WHERE trace_id IN ({placeholders})",
            old_traces,
        )

        self._conn.commit()
        return deleted

    # ------------------------------------------------------------------
    # Three-Stage Cost Ledger: backfill baseline costs
    # ------------------------------------------------------------------

    def backfill_baseline_costs(self, dry_run: bool = False) -> dict[str, int]:
        """Populate ``baseline_input_tokens`` and ``baseline_cost`` for
        existing traces that were inserted without compression data.

        Algorithm
        ---------
        For each trace in ``tp_costs`` where ``baseline_cost = 0``:

        1. Sum ``tokens_raw`` from ``tp_segments`` →
           ``baseline_input_tokens``.
        2. If no segment data, fall back to
           ``input_billed + cache_read`` from ``tp_usage`` as a proxy.
        3. Look up the model from ``tp_events`` and call
           :func:`~tokenpak.telemetry.pricing.compute_baseline_cost`.
        4. Compute ``savings_total = baseline_cost - cost_total``
           (floored at 0).
        5. UPDATE ``tp_costs`` unless *dry_run* is True.

        Parameters
        ----------
        dry_run:
            If True, compute everything but do not write to the DB.

        Returns
        -------
        dict
            ``{"eligible": N, "updated": N, "skipped": N}`` counts.
        """
        from tokenpak.telemetry.pricing import compute_baseline_cost as _cbc

        cur = self._conn.cursor()

        # Find traces that need baseline costs computed
        cur.execute("""
            SELECT c.trace_id, c.cost_total
            FROM tp_costs c
            WHERE c.baseline_cost = 0 AND c.baseline_input_tokens = 0
        """)
        rows = cur.fetchall()

        eligible = len(rows)
        updated = 0
        skipped = 0

        for row in rows:
            trace_id = row[0]
            cost_total = row[1]

            # Step 1: Try to get raw token count from segments
            cur.execute(
                "SELECT COALESCE(SUM(tokens_raw), 0) FROM tp_segments WHERE trace_id = ?",
                (trace_id,),
            )
            seg_row = cur.fetchone()
            baseline_input_tokens: int = seg_row[0] if seg_row else 0

            # Step 2: Fall back to usage proxy if no segment data
            if baseline_input_tokens == 0:
                cur.execute(
                    "SELECT COALESCE(input_billed, 0), COALESCE(cache_read, 0) "
                    "FROM tp_usage WHERE trace_id = ?",
                    (trace_id,),
                )
                usage_row = cur.fetchone()
                if usage_row:
                    baseline_input_tokens = usage_row[0] + usage_row[1]

            if baseline_input_tokens == 0:
                skipped += 1
                continue

            # Step 3: Look up model
            cur.execute(
                "SELECT model FROM tp_events WHERE trace_id = ? LIMIT 1",
                (trace_id,),
            )
            ev_row = cur.fetchone()
            model = ev_row[0] if ev_row else ""
            if not model:
                skipped += 1
                continue

            # Step 4: Compute baseline cost and savings
            baseline_cost = _cbc(model, baseline_input_tokens)
            if baseline_cost == 0.0:
                skipped += 1
                continue

            savings_total = max(0.0, baseline_cost - cost_total)

            # Step 5: Persist
            if not dry_run:
                cur.execute(
                    """UPDATE tp_costs
                       SET baseline_input_tokens = ?,
                           baseline_cost = ?,
                           savings_total = ?
                       WHERE trace_id = ?""",
                    (baseline_input_tokens, baseline_cost, savings_total, trace_id),
                )

            updated += 1

        if not dry_run:
            self._conn.commit()

        return {"eligible": eligible, "updated": updated, "skipped": skipped}

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Return row counts for each telemetry table."""
        cur = self._conn.cursor()
        result: dict[str, int] = {}
        for table in ("tp_events", "tp_segments", "tp_usage", "tp_costs"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            result[table] = cur.fetchone()[0]

        rollup_tables = (
            "tp_rollup_daily_model",
            "tp_rollup_daily_provider",
            "tp_rollup_daily_agent",
        )
        rollup_total = 0
        for table in rollup_tables:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            result[table] = count
            rollup_total += count
        result["tp_rollups"] = rollup_total
        return result

    # ------------------------------------------------------------------
    # Phase 5B: Query API methods
    # ------------------------------------------------------------------

    def get_unique_models(self) -> list[str]:
        """Return list of unique model identifiers seen."""
        cur = self._conn.cursor()
        cur.execute("SELECT DISTINCT model FROM tp_events WHERE model != '' ORDER BY model")
        return [r[0] for r in cur.fetchall()]

    def get_unique_providers(self) -> list[str]:
        """Return list of unique provider names seen."""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT DISTINCT provider FROM tp_events WHERE provider != '' ORDER BY provider"
        )
        return [r[0] for r in cur.fetchall()]

    def get_unique_agents(self) -> list[str]:
        """Return list of unique agent identifiers seen."""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT DISTINCT agent_id FROM tp_events WHERE agent_id != '' ORDER BY agent_id"
        )
        return [r[0] for r in cur.fetchall()]

    def export_trace(self, trace_id: str) -> dict[str, Any]:
        """Export a complete trace bundle as JSON-serializable dict.

        Includes event, usage, cost, segments, and metadata.
        """
        trace = self.get_trace(trace_id)  # type: ignore[attr-defined]
        return {
            "format": "tokenpak_trace_export_v1",
            "trace_id": trace_id,
            "exported_at": _now(),
            **trace,
        }

    # ------------------------------------------------------------------
    # Rollup computation (Phase 5B)
    # ------------------------------------------------------------------
