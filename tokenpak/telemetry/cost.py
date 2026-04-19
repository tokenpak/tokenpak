"""
TokenPak Cost Calculation Engine (cost.py)

Service layer for cost calculation with:
  - tp_pricing table for versioned per-model rates
  - Baseline / actual / savings formulas
  - Pricing version resolution by event timestamp
  - CostResult dataclass
  - Reprocessing support helpers

The existing PricingCatalog (pricing.py) handles per-token math;
this module owns the DB schema, version resolution, and orchestration.

Usage:
    engine = CostEngine(db_path="telemetry.db")
    result = engine.calculate(
        model="claude-sonnet-4-6",
        raw_input_tokens=10000,
        final_input_tokens=6000,
        output_tokens=500,
        event_ts="2026-02-27T12:00:00Z",
    )
    # result.baseline_cost, result.actual_cost, result.savings_amount
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Current pricing rates (USD per 1K input/output tokens)
# Source: official docs, 2026-02 snapshot
# ---------------------------------------------------------------------------
SEED_PRICING: List[dict] = [
    # Anthropic
    {
        "provider": "anthropic",
        "model": "claude-opus-4-6",
        "input_rate": 15.00,
        "output_rate": 75.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-opus-4-5",
        "input_rate": 15.00,
        "output_rate": 75.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "input_rate": 3.00,
        "output_rate": 15.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "input_rate": 3.00,
        "output_rate": 15.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-haiku-4-6",
        "input_rate": 0.80,
        "output_rate": 4.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "input_rate": 0.80,
        "output_rate": 4.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-haiku-3-5",
        "input_rate": 0.80,
        "output_rate": 4.00,
        "source": "official",
    },
    # OpenAI
    {
        "provider": "openai",
        "model": "gpt-4o",
        "input_rate": 5.00,
        "output_rate": 15.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "input_rate": 0.15,
        "output_rate": 0.60,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-4-turbo",
        "input_rate": 10.00,
        "output_rate": 30.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-3.5-turbo",
        "input_rate": 0.50,
        "output_rate": 1.50,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "o1",
        "input_rate": 15.00,
        "output_rate": 60.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "o1-mini",
        "input_rate": 3.00,
        "output_rate": 12.00,
        "source": "official",
    },
    # Google
    {
        "provider": "google",
        "model": "gemini-2.0-flash",
        "input_rate": 0.10,
        "output_rate": 0.40,
        "source": "official",
    },
    {
        "provider": "google",
        "model": "gemini-2.0-pro",
        "input_rate": 3.50,
        "output_rate": 10.50,
        "source": "official",
    },
    {
        "provider": "google",
        "model": "gemini-1.5-pro",
        "input_rate": 3.50,
        "output_rate": 10.50,
        "source": "official",
    },
    {
        "provider": "google",
        "model": "gemini-1.5-flash",
        "input_rate": 0.075,
        "output_rate": 0.30,
        "source": "official",
    },
    # Fallback (unknown model)
    {
        "provider": "unknown",
        "model": "_fallback",
        "input_rate": 3.00,
        "output_rate": 15.00,
        "source": "estimated",
    },
]

CURRENT_PRICING_VERSION = "2026.02"
CURRENT_EFFECTIVE_DATE = "2026-02-01"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
@dataclass
class CostResult:
    """Result of a cost calculation for a single event."""

    model: str
    pricing_version: str
    raw_input_tokens: int
    final_input_tokens: int
    output_tokens: int
    baseline_cost: float  # cost if no compression applied
    actual_cost: float  # cost after compression
    savings_amount: float  # baseline - actual (never negative)
    savings_pct: float  # savings_amount / baseline_cost * 100
    data_source: str  # "official" | "estimated" | "fallback"

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "pricing_version": self.pricing_version,
            "raw_input_tokens": self.raw_input_tokens,
            "final_input_tokens": self.final_input_tokens,
            "output_tokens": self.output_tokens,
            "baseline_cost": round(self.baseline_cost, 6),
            "actual_cost": round(self.actual_cost, 6),
            "savings_amount": round(self.savings_amount, 6),
            "savings_pct": round(self.savings_pct, 4),
            "data_source": self.data_source,
        }


@dataclass
class Pricing:
    """A single model pricing record."""

    provider: str
    model: str
    input_rate: float  # USD per 1K tokens
    output_rate: float  # USD per 1K tokens
    version: str
    effective_date: str
    source: str = "official"

    @property
    def input_per_token(self) -> float:
        return self.input_rate / 1000.0

    @property
    def output_per_token(self) -> float:
        return self.output_rate / 1000.0


# ---------------------------------------------------------------------------
# Cost Engine
# ---------------------------------------------------------------------------
class CostEngine:
    """
    Cost calculation service with DB-backed versioned pricing.

    Args:
        db_path: Path to telemetry SQLite database.
    """

    DDL = """
    CREATE TABLE IF NOT EXISTS tp_pricing (
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
    );
    CREATE INDEX IF NOT EXISTS idx_tp_pricing_model
        ON tp_pricing(model, effective_date);
    CREATE INDEX IF NOT EXISTS idx_tp_pricing_version
        ON tp_pricing(version);
    """

    # Fallback rates for unknown models
    _FALLBACK_INPUT_RATE = 3.00  # USD/1K (sonnet-tier estimate)
    _FALLBACK_OUTPUT_RATE = 15.00

    def __init__(self, db_path: str = "telemetry.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._pricing_cache: dict[tuple, Pricing] = {}
        self._init_db()

    # ------------------------------------------------------------------
    # DB init & seeding
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create tp_pricing table and seed with current rates if empty."""
        with self._lock:
            conn = self._connect()
            for stmt in self.DDL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    conn.execute(stmt)
            conn.commit()

            # Seed if table is empty
            count = conn.execute("SELECT COUNT(*) FROM tp_pricing").fetchone()[0]
            if count == 0:
                self._seed(conn)
            conn.close()

    def _seed(self, conn: sqlite3.Connection) -> None:
        """Insert default pricing rows."""
        for row in SEED_PRICING:
            conn.execute(
                """INSERT INTO tp_pricing
                   (version, effective_date, provider, model, input_rate, output_rate, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    CURRENT_PRICING_VERSION,
                    CURRENT_EFFECTIVE_DATE,
                    row["provider"],
                    row["model"],
                    row["input_rate"],
                    row["output_rate"],
                    row["source"],
                ),
            )
        conn.commit()
        logger.info(
            f"tp_pricing seeded with {len(SEED_PRICING)} rows (version {CURRENT_PRICING_VERSION})"
        )

    # ------------------------------------------------------------------
    # Pricing resolution
    # ------------------------------------------------------------------
    def get_pricing(self, model: str, event_ts: Optional[str] = None) -> Pricing:
        """
        Resolve pricing for a model at a given event timestamp.

        Uses the most recent version whose effective_date <= event date.
        Falls back to fallback pricing if model is unknown.

        Args:
            model: Model identifier (e.g. "claude-sonnet-4-6")
            event_ts: ISO timestamp of the event (default: now)

        Returns:
            Pricing record.
        """
        event_date = self._parse_date(event_ts)
        cache_key = (model, event_date)

        if cache_key in self._pricing_cache:
            return self._pricing_cache[cache_key]

        conn = self._connect()
        # Exact match first
        row = conn.execute(
            """SELECT * FROM tp_pricing
               WHERE model = ? AND effective_date <= ?
               ORDER BY effective_date DESC LIMIT 1""",
            (model, event_date),
        ).fetchone()

        if row is None:
            # Try fuzzy: partial model name match
            row = self._fuzzy_match(conn, model, event_date)

        conn.close()

        if row:
            pricing = Pricing(
                provider=row["provider"],
                model=row["model"],
                input_rate=row["input_rate"],
                output_rate=row["output_rate"],
                version=row["version"],
                effective_date=row["effective_date"],
                source=row["source"],
            )
        else:
            # Fallback pricing for unknown models
            pricing = Pricing(
                provider="unknown",
                model=model,
                input_rate=self._FALLBACK_INPUT_RATE,
                output_rate=self._FALLBACK_OUTPUT_RATE,
                version="fallback",
                effective_date=event_date,
                source="estimated",
            )
            logger.warning(f"No pricing found for model '{model}', using fallback")

        self._pricing_cache[cache_key] = pricing
        return pricing

    def _fuzzy_match(self, conn: sqlite3.Connection, model: str, event_date: str):
        """Try matching by model name substring."""
        model_lower = model.lower()
        rows = conn.execute(
            "SELECT * FROM tp_pricing WHERE effective_date <= ? ORDER BY effective_date DESC",
            (event_date,),
        ).fetchall()
        for row in rows:
            if row["model"] in model_lower or model_lower in row["model"]:
                return row
        return None

    @staticmethod
    def _parse_date(ts: Optional[str]) -> str:
        """Parse a timestamp string to YYYY-MM-DD, defaulting to today."""
        if not ts:
            return datetime.now(timezone.utc).date().isoformat()
        try:
            # Handle various ISO formats
            dt = ts.replace("Z", "+00:00")
            return datetime.fromisoformat(dt).date().isoformat()
        except (ValueError, AttributeError):
            return datetime.now(timezone.utc).date().isoformat()

    # ------------------------------------------------------------------
    # Cost calculation
    # ------------------------------------------------------------------
    def calculate(
        self,
        model: str,
        raw_input_tokens: int,
        final_input_tokens: int,
        output_tokens: int,
        event_ts: Optional[str] = None,
        cache_read_tokens: int = 0,
    ) -> CostResult:
        """
        Calculate baseline, actual, and savings for a single event.

        Args:
            model: Model identifier.
            raw_input_tokens: Tokens BEFORE compression (for baseline).
            final_input_tokens: Tokens AFTER compression (actual billing).
            output_tokens: Output tokens (same for baseline and actual).
            event_ts: Event ISO timestamp for pricing version resolution.
            cache_read_tokens: Cache-read tokens (reduces actual cost).

        Returns:
            CostResult with all cost fields.
        """
        # Clamp negative values
        raw = max(0, raw_input_tokens)
        final = max(0, final_input_tokens)
        out = max(0, output_tokens)

        pricing = self.get_pricing(model, event_ts)

        # Baseline: what would have been billed without compression
        baseline_cost = raw * pricing.input_per_token + out * pricing.output_per_token

        # Actual: billed tokens after compression
        effective_input = max(0, final - cache_read_tokens)
        actual_cost = effective_input * pricing.input_per_token + out * pricing.output_per_token

        # Savings (never negative — rounding artifacts clamped)
        savings_amount = max(0.0, baseline_cost - actual_cost)
        savings_pct = (savings_amount / baseline_cost * 100.0) if baseline_cost > 0 else 0.0

        return CostResult(
            model=model,
            pricing_version=pricing.version,
            raw_input_tokens=raw,
            final_input_tokens=final,
            output_tokens=out,
            baseline_cost=baseline_cost,
            actual_cost=actual_cost,
            savings_amount=savings_amount,
            savings_pct=savings_pct,
            data_source=pricing.source,
        )

    # ------------------------------------------------------------------
    # Pricing catalog management
    # ------------------------------------------------------------------
    def list_pricing(self, version: Optional[str] = None) -> List[dict]:
        """List all pricing entries, optionally filtered by version."""
        conn = self._connect()
        if version:
            rows = conn.execute(
                "SELECT * FROM tp_pricing WHERE version = ? ORDER BY provider, model",
                (version,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tp_pricing ORDER BY version DESC, provider, model"
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def add_pricing(
        self,
        provider: str,
        model: str,
        input_rate: float,
        output_rate: float,
        version: Optional[str] = None,
        effective_date: Optional[str] = None,
        source: str = "official",
    ) -> int:
        """Insert a new pricing record. Returns the new row id."""
        version = version or CURRENT_PRICING_VERSION
        effective_date = effective_date or datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                """INSERT INTO tp_pricing
                   (version, effective_date, provider, model, input_rate, output_rate, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (version, effective_date, provider, model, input_rate, output_rate, source),
            )
            conn.commit()
            row_id = cur.lastrowid
            conn.close()
        # Invalidate cache
        self._pricing_cache.clear()
        assert row_id is not None
        return row_id

    # ------------------------------------------------------------------
    # Reprocessing
    # ------------------------------------------------------------------
    def reprocess_costs(
        self,
        from_date: str,
        to_date: str,
        pricing_version: Optional[str] = None,
    ) -> dict:
        """
        Recalculate costs for events in a date range.

        Reads raw/final token counts from tp_usage + tp_events,
        recalculates using current (or specified) pricing,
        and updates tp_costs rows.

        Args:
            from_date: Start date YYYY-MM-DD (inclusive).
            to_date:   End date YYYY-MM-DD (inclusive).
            pricing_version: Override pricing version (default: current).

        Returns:
            Summary dict with rows_processed, rows_updated, errors.
        """
        conn = self._connect()
        rows_processed = 0
        rows_updated = 0
        errors = 0

        try:
            # Fetch events in range
            events = conn.execute(
                """SELECT e.trace_id, e.model, e.ts,
                          u.input_billed AS final_input, u.input_est AS raw_input,
                          u.output_billed AS output
                   FROM tp_events e
                   LEFT JOIN tp_usage u ON u.trace_id = e.trace_id
                   WHERE DATE(e.ts) >= ? AND DATE(e.ts) <= ?
                     AND e.status != 'error'""",
                (from_date, to_date),
            ).fetchall()

            for event in events:
                rows_processed += 1
                try:
                    model = event["model"] or "unknown"
                    raw = event["raw_input"] or 0
                    final = event["final_input"] or 0
                    out = event["output"] or 0
                    ts = event["ts"]

                    # Use override version if specified
                    if pricing_version:
                        pricing = self._get_pricing_by_version(conn, model, pricing_version)
                        if pricing is None:
                            pricing = self.get_pricing(model, ts)
                    else:
                        pricing = self.get_pricing(model, ts)

                    result = self.calculate(model, raw, final, out, event_ts=ts)

                    # Update tp_costs
                    existing = conn.execute(
                        "SELECT trace_id FROM tp_costs WHERE trace_id = ?",
                        (event["trace_id"],),
                    ).fetchone()

                    if existing:
                        conn.execute(
                            """UPDATE tp_costs SET
                               baseline_cost = ?, actual_cost = ?,
                               savings_total = ?, pricing_version = ?,
                               cost_source = ?
                               WHERE trace_id = ?""",
                            (
                                result.baseline_cost,
                                result.actual_cost,
                                result.savings_amount,
                                result.pricing_version,
                                result.data_source,
                                event["trace_id"],
                            ),
                        )
                        rows_updated += 1
                except Exception as e:
                    logger.warning(f"Reprocess error for trace {event['trace_id']}: {e}")
                    errors += 1

            conn.commit()
        finally:
            conn.close()

        logger.info(
            f"Reprocess complete: {rows_processed} events, "
            f"{rows_updated} updated, {errors} errors "
            f"({from_date} → {to_date})"
        )
        return {
            "from_date": from_date,
            "to_date": to_date,
            "rows_processed": rows_processed,
            "rows_updated": rows_updated,
            "errors": errors,
            "pricing_version": pricing_version or CURRENT_PRICING_VERSION,
        }

    def _get_pricing_by_version(
        self, conn: sqlite3.Connection, model: str, version: str
    ) -> Optional[Pricing]:
        """Look up pricing for a specific version."""
        row = conn.execute(
            "SELECT * FROM tp_pricing WHERE model = ? AND version = ? LIMIT 1",
            (model, version),
        ).fetchone()
        if row:
            return Pricing(
                provider=row["provider"],
                model=row["model"],
                input_rate=row["input_rate"],
                output_rate=row["output_rate"],
                version=row["version"],
                effective_date=row["effective_date"],
                source=row["source"],
            )
        return None


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------
def calculate_baseline(raw_input_tokens: int, output_tokens: int, pricing: Pricing) -> float:
    """Compute baseline cost (no compression applied)."""
    return max(
        0.0, raw_input_tokens * pricing.input_per_token + output_tokens * pricing.output_per_token
    )


def calculate_actual(
    final_input_tokens: int, output_tokens: int, pricing: Pricing, cache_read_tokens: int = 0
) -> float:
    """Compute actual cost (after compression)."""
    effective = max(0, final_input_tokens - cache_read_tokens)
    return max(0.0, effective * pricing.input_per_token + output_tokens * pricing.output_per_token)


def calculate_savings(baseline: float, actual: float) -> tuple[float, float]:
    """Return (savings_amount, savings_pct). Never negative."""
    amount = max(0.0, baseline - actual)
    pct = (amount / baseline * 100.0) if baseline > 0 else 0.0
    return amount, pct
