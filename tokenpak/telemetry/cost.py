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

import hashlib
import json
import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, cast

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache cost multipliers — per-provider cache read/creation pricing
# Transferred from monolith
# Source: Provider pricing docs
# read = fraction of input cost for cached tokens
# creation = multiplier on input cost for cache write (Anthropic only has surcharge)
# ---------------------------------------------------------------------------
try:
    from tokenpak.core.runtime.providers import Provider as _Provider

    CACHE_COST_MULTIPLIERS: Dict[object, Dict[str, float]] = {
        _Provider.ANTHROPIC: {"read": 0.10, "creation": 1.25},  # reads=10%, creation=125%
        _Provider.OPENAI: {"read": 0.50, "creation": 1.0},  # reads=50%, no creation surcharge
        _Provider.AZURE_OPENAI: {"read": 0.50, "creation": 1.0},
        _Provider.XAI: {"read": 0.50, "creation": 1.0},
        _Provider.GROQ: {"read": 0.0, "creation": 1.0},  # Free (volatile cache)
        _Provider.FIREWORKS: {"read": 0.0, "creation": 1.0},  # No cache pricing surcharge
        _Provider.TOGETHER: {"read": 0.0, "creation": 1.0},  # No cache pricing surcharge
        _Provider.GEMINI: {"read": 0.25, "creation": 1.0},  # 25% of input cost
        _Provider.BEDROCK: {"read": 0.10, "creation": 1.0},  # 10% of input cost
        _Provider.CODEX: {"read": 0.50, "creation": 1.0},  # Follows OpenAI pricing
        _Provider.UNKNOWN: {"read": 0.10, "creation": 1.25},  # Conservative default
    }
except (ImportError, AttributeError):
    # Fallback to string-keyed dict if Provider enum is unavailable
    CACHE_COST_MULTIPLIERS: Dict[str, Dict[str, float]] = {  # type: ignore[no-redef]  # string-keyed fallback table when the Provider enum import fails; redefines the enum-keyed table declared in the try block above
        "anthropic": {"read": 0.10, "creation": 1.25},
        "openai": {"read": 0.50, "creation": 1.0},
        "azure_openai": {"read": 0.50, "creation": 1.0},
        "xai": {"read": 0.50, "creation": 1.0},
        "groq": {"read": 0.0, "creation": 1.0},
        "fireworks": {"read": 0.0, "creation": 1.0},
        "together": {"read": 0.0, "creation": 1.0},
        "gemini": {"read": 0.25, "creation": 1.0},
        "bedrock": {"read": 0.10, "creation": 1.0},
        "codex": {"read": 0.50, "creation": 1.0},
        "unknown": {"read": 0.10, "creation": 1.25},
    }

# ---------------------------------------------------------------------------
# Current pricing rates (USD per 1M input/output tokens)
# Source: official provider pricing pages, fetched 2026-09-04
# ---------------------------------------------------------------------------
PricingSeedValue = str | float

UNIT_BASIS_USD_PER_1K = "USD_PER_1K"
UNIT_BASIS_USD_PER_1M = "USD_PER_1M"
UNKNOWN_METADATA = "UNKNOWN"
PROVENANCE_SEED_REFRESH = "SEED_REFRESH_V1"
PROVENANCE_CUSTOM = "CUSTOM"
PROVENANCE_FALLBACK = "FALLBACK"
SEED_PRICING_UNIT_BASIS = UNIT_BASIS_USD_PER_1M


SEED_PRICING: List[dict[str, PricingSeedValue]] = [
    # Anthropic
    {
        "provider": "anthropic",
        "model": "claude-fable-5-1",
        "input_rate": 10.00,
        "output_rate": 50.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-fable-5",
        "input_rate": 10.00,
        "output_rate": 50.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-mythos-5-1",
        "input_rate": 10.00,
        "output_rate": 50.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-mythos-5",
        "input_rate": 10.00,
        "output_rate": 50.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-opus-5",
        "input_rate": 5.00,
        "output_rate": 25.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-opus-4-8",
        "input_rate": 5.00,
        "output_rate": 25.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-opus-4-7",
        "input_rate": 5.00,
        "output_rate": 25.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-opus-4-6",
        "input_rate": 5.00,
        "output_rate": 25.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-opus-4-5",
        "input_rate": 5.00,
        "output_rate": 25.00,
        "source": "official",
    },
    {
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "input_rate": 2.00,
        "output_rate": 10.00,
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
        "input_rate": 1.00,
        "output_rate": 5.00,
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
        "model": "gpt-5.6-sol",
        "input_rate": 4.00,
        "output_rate": 20.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5.6",
        "input_rate": 4.00,
        "output_rate": 20.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5.6-terra",
        "input_rate": 2.00,
        "output_rate": 12.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "input_rate": 0.20,
        "output_rate": 1.20,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5.5",
        "input_rate": 5.00,
        "output_rate": 30.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5.4",
        "input_rate": 2.50,
        "output_rate": 15.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5.4-mini",
        "input_rate": 0.75,
        "output_rate": 4.50,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5.4-nano",
        "input_rate": 0.20,
        "output_rate": 1.25,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5.3-codex",
        "input_rate": 1.75,
        "output_rate": 14.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5.2",
        "input_rate": 1.75,
        "output_rate": 14.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5.1",
        "input_rate": 1.25,
        "output_rate": 10.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5",
        "input_rate": 1.25,
        "output_rate": 10.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5-mini",
        "input_rate": 0.25,
        "output_rate": 2.00,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-5-nano",
        "input_rate": 0.05,
        "output_rate": 0.40,
        "source": "official",
    },
    {
        "provider": "openai",
        "model": "gpt-4o",
        "input_rate": 2.50,
        "output_rate": 10.00,
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

CURRENT_PRICING_VERSION = "2026.09"
CURRENT_EFFECTIVE_DATE = "2026-09-04"


class PricingRefreshError(RuntimeError):
    """Base class for safe pricing refresh refusals."""


class PricingRefreshUnavailableError(PricingRefreshError):
    """The database cannot safely apply a seed refresh."""


class PricingRefreshCollisionError(PricingRefreshError):
    """A selected seed key collides with a non-identical stored row."""


class StalePricingRefreshPlanError(PricingRefreshError):
    """The database or catalog changed after a refresh was planned."""


class FreshPricingDatabasePopulatedError(PricingRefreshError):
    """Automatic empty-database seeding lost its fresh-empty predicate."""


class UnknownPricingUnitError(RuntimeError):
    """Strict lookup refused pricing whose stored unit basis is unknown."""


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
    pricing_provenance: str = UNKNOWN_METADATA
    unit_basis: str = UNKNOWN_METADATA

    def to_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "pricing_version": self.pricing_version,
            "raw_input_tokens": self.raw_input_tokens,
            "final_input_tokens": self.final_input_tokens,
            "output_tokens": self.output_tokens,
            "baseline_cost": self.baseline_cost,
            "actual_cost": self.actual_cost,
            "savings_amount": self.savings_amount,
            "savings_pct": round(self.savings_pct, 4),
            "data_source": self.data_source,
            "pricing_provenance": self.pricing_provenance,
            "unit_basis": self.unit_basis,
        }


@dataclass
class Pricing:
    """A single model pricing record."""

    provider: str
    model: str
    input_rate: float  # USD per 1K tokens (public compatibility contract)
    output_rate: float  # USD per 1K tokens (public compatibility contract)
    version: str
    effective_date: str
    source: str = "official"
    provenance: str = UNKNOWN_METADATA
    unit_basis: str = UNKNOWN_METADATA

    @property
    def input_per_token(self) -> float:
        return self.input_rate / 1_000.0

    @property
    def output_per_token(self) -> float:
        return self.output_rate / 1_000.0


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
        provenance     TEXT,
        unit_basis     TEXT,
        created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_tp_pricing_model
        ON tp_pricing(model, effective_date);
    CREATE INDEX IF NOT EXISTS idx_tp_pricing_version
        ON tp_pricing(version);
    CREATE TABLE IF NOT EXISTS tp_pricing_refresh_receipts (
        plan_hash              TEXT PRIMARY KEY,
        catalog_hash           TEXT NOT NULL,
        version                TEXT NOT NULL,
        effective_date         DATE NOT NULL,
        selected_keys_json     TEXT NOT NULL,
        row_fingerprints_json  TEXT NOT NULL,
        post_pricing_hash      TEXT NOT NULL,
        post_schema_hash       TEXT NOT NULL,
        applied_at             DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """

    # Existing rows are never deleted to install this index.  A duplicate-
    # bearing database remains readable, but seed refresh stays unavailable.
    _UNIQUE_INDEX_DDL = (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tp_pricing_unique "
        "ON tp_pricing(version, provider, model)"
    )
    _UNIQUE_KEY_COLUMNS = ("version", "provider", "model")

    # Explicitly tagged USD/1M constants.  The public Pricing/add_pricing
    # contract remains USD/1K, so these are normalized only at that boundary.
    _FALLBACK_PRICING: Mapping[str, str | float] = {
        "input_rate": 3.00,
        "output_rate": 15.00,
        "unit_basis": UNIT_BASIS_USD_PER_1M,
    }

    def __init__(self, db_path: str = "", strict_unknown_units: bool = False):
        from tokenpak.core.paths import get_db_path

        resolved_path = Path(db_path).expanduser() if db_path else get_db_path("telemetry.db")
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(resolved_path)
        self.strict_unknown_units = strict_unknown_units
        self._lock = threading.Lock()
        # Retained for callers that inspect the historical attribute.  DB-
        # backed lookups intentionally do not use it, so other processes are
        # visible on the next query without a restart.
        self._pricing_cache: dict[tuple[str, str], Pricing] = {}
        self._init_db()

    # ------------------------------------------------------------------
    # DB init & seeding
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _init_db(self) -> None:
        """Apply additive schema and seed only a genuinely empty catalog."""
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                for stmt in self.DDL.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(stmt)
                self._ensure_metadata_columns(conn)
                unique_ready = self._ensure_unique_pricing_key(conn)
                count = int(conn.execute("SELECT COUNT(*) FROM tp_pricing").fetchone()[0])
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

        if count == 0:
            if not unique_ready:
                raise PricingRefreshUnavailableError(
                    "fresh pricing database lacks the required unique "
                    "(version, provider, model) index"
                )
            selected_keys = [self._seed_key(row) for row in SEED_PRICING]
            try:
                plan = self.plan_seed_refresh(
                    selected_models=selected_keys,
                    overrides={key: True for key in selected_keys},
                    require_empty=True,
                )
                self.apply_seed_refresh(plan)
            except FreshPricingDatabasePopulatedError:
                logger.info("automatic pricing seed skipped because the catalog became populated")

    @staticmethod
    def _ensure_metadata_columns(conn: sqlite3.Connection) -> None:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(tp_pricing)")}
        if "provenance" not in columns:
            conn.execute("ALTER TABLE tp_pricing ADD COLUMN provenance TEXT")
        if "unit_basis" not in columns:
            conn.execute("ALTER TABLE tp_pricing ADD COLUMN unit_basis TEXT")

    @classmethod
    def _ensure_unique_pricing_key(cls, conn: sqlite3.Connection) -> bool:
        """Install the required index only when doing so preserves every row."""
        if cls._has_required_unique_index(conn):
            return True
        duplicate = conn.execute(
            """SELECT version, provider, model, COUNT(*) AS n
               FROM tp_pricing
               GROUP BY version, provider, model
               HAVING COUNT(*) > 1
               LIMIT 1"""
        ).fetchone()
        if duplicate is not None:
            logger.warning(
                "pricing refresh unavailable: duplicate pricing key preserved (%s, %s, %s)",
                duplicate["version"],
                duplicate["provider"],
                duplicate["model"],
            )
            return False
        try:
            conn.execute(cls._UNIQUE_INDEX_DDL)
        except sqlite3.IntegrityError:
            return False
        return cls._has_required_unique_index(conn)

    @classmethod
    def _has_required_unique_index(cls, conn: sqlite3.Connection) -> bool:
        for index in conn.execute("PRAGMA index_list(tp_pricing)"):
            # index_list columns: seq, name, unique, origin, partial.
            # A partial index does not protect every pricing row.
            if not bool(index[2]) or bool(index[4]):
                continue
            name = str(index[1]).replace("'", "''")
            raw_columns = list(conn.execute(f"PRAGMA index_info('{name}')"))
            if any(row[2] is None for row in raw_columns):
                continue
            columns = tuple(str(row[2]) for row in raw_columns)
            if columns == cls._UNIQUE_KEY_COLUMNS:
                return True
        return False

    @staticmethod
    def _index_snapshot(conn: sqlite3.Connection) -> list[dict[str, object]]:
        indexes: list[dict[str, object]] = []
        for index in conn.execute("PRAGMA index_list(tp_pricing)"):
            name = str(index[1])
            quoted = name.replace("'", "''")
            indexes.append(
                {
                    "name": name,
                    "unique": int(index[2]),
                    "origin": str(index[3]),
                    "partial": int(index[4]),
                    "columns": [
                        None if row[2] is None else str(row[2])
                        for row in conn.execute(f"PRAGMA index_info('{quoted}')")
                    ],
                }
            )
        return sorted(indexes, key=lambda value: str(value["name"]))

    @staticmethod
    def _canonical_hash(value: object) -> str:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _schema_snapshot(conn: sqlite3.Connection) -> list[dict[str, object]]:
        return [
            {
                "cid": int(row[0]),
                "name": str(row[1]),
                "type": str(row[2]),
                "notnull": int(row[3]),
                "default": row[4],
                "pk": int(row[5]),
            }
            for row in conn.execute("PRAGMA table_info(tp_pricing)")
        ]

    @staticmethod
    def _pricing_rows_snapshot(conn: sqlite3.Connection) -> list[dict[str, object]]:
        columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(tp_pricing)")]
        return [
            {column: row[column] for column in columns}
            for row in conn.execute("SELECT * FROM tp_pricing ORDER BY id")
        ]

    @staticmethod
    def _seed_key(row: Mapping[str, PricingSeedValue]) -> str:
        return f"{row['provider']}/{row['model']}"

    @classmethod
    def _seed_catalog_hash(cls) -> str:
        return cls._canonical_hash(
            {
                "unit_basis": SEED_PRICING_UNIT_BASIS,
                "rows": SEED_PRICING,
            }
        )

    @staticmethod
    def _normalize_rate_to_per_1k(rate: float, unit_basis: str) -> float:
        if unit_basis == UNIT_BASIS_USD_PER_1M:
            return float(rate) / 1_000.0
        if unit_basis == UNIT_BASIS_USD_PER_1K:
            return float(rate)
        raise ValueError(f"unsupported pricing unit basis: {unit_basis}")

    @classmethod
    def _target_seed_row(
        cls,
        seed: Mapping[str, PricingSeedValue],
        version: str,
        effective_date: str,
    ) -> dict[str, object]:
        return {
            "version": version,
            "effective_date": effective_date,
            "provider": str(seed["provider"]),
            "model": str(seed["model"]),
            "input_rate": cls._normalize_rate_to_per_1k(
                float(seed["input_rate"]), SEED_PRICING_UNIT_BASIS
            ),
            "output_rate": cls._normalize_rate_to_per_1k(
                float(seed["output_rate"]), SEED_PRICING_UNIT_BASIS
            ),
            "currency": "USD",
            "source": str(seed["source"]),
            "provenance": PROVENANCE_SEED_REFRESH,
            "unit_basis": UNIT_BASIS_USD_PER_1K,
        }

    @staticmethod
    def _is_identical_tagged_seed(
        stored: Mapping[str, object], target: Mapping[str, object]
    ) -> bool:
        compared = (
            "version",
            "effective_date",
            "provider",
            "model",
            "input_rate",
            "output_rate",
            "currency",
            "source",
            "provenance",
            "unit_basis",
        )
        return stored.get("provenance") == PROVENANCE_SEED_REFRESH and all(
            stored.get(column) == target.get(column) for column in compared
        )

    @classmethod
    def _validate_refresh_preconditions(cls, conn: sqlite3.Connection) -> None:
        duplicate = conn.execute(
            """SELECT version, provider, model, COUNT(*) AS n
               FROM tp_pricing
               GROUP BY version, provider, model
               HAVING COUNT(*) > 1
               ORDER BY version, provider, model
               LIMIT 1"""
        ).fetchone()
        if duplicate is not None:
            raise PricingRefreshUnavailableError(
                "pricing refresh unavailable: duplicate key preserved "
                f"({duplicate['version']}, {duplicate['provider']}, {duplicate['model']})"
            )
        if not cls._has_required_unique_index(conn):
            raise PricingRefreshUnavailableError(
                "pricing refresh unavailable: a correctly shaped unique index on "
                "(version, provider, model) is required"
            )

    @classmethod
    def _select_seed_rows(cls, selected_models: Sequence[str] | None) -> list[dict[str, Any]]:
        seeds = [dict(row) for row in SEED_PRICING]
        if selected_models is None:
            return seeds
        requested = list(selected_models)
        requested_set = set(requested)
        selected = [
            row
            for row in seeds
            if str(row["model"]) in requested_set or cls._seed_key(row) in requested_set
        ]
        matched = {str(row["model"]) for row in selected} | {cls._seed_key(row) for row in selected}
        missing = [value for value in requested if value not in matched]
        if missing:
            raise PricingRefreshError(f"unknown seed selection: {', '.join(missing)}")
        return selected

    @classmethod
    def _build_refresh_plan(
        cls,
        conn: sqlite3.Connection,
        *,
        selected_models: Sequence[str] | None,
        overrides: Mapping[str, bool],
        version: str,
        effective_date: str,
        require_empty: bool,
    ) -> dict[str, object]:
        cls._validate_refresh_preconditions(conn)
        seeds = cls._select_seed_rows(selected_models)
        selected_keys = [cls._seed_key(row) for row in seeds]
        missing_overrides = [key for key in selected_keys if key not in overrides]
        if missing_overrides:
            raise PricingRefreshError(
                "an explicit apply/skip override is required for every selected model: "
                + ", ".join(missing_overrides)
            )
        unexpected_overrides = sorted(set(overrides) - set(selected_keys))
        if unexpected_overrides:
            raise PricingRefreshError(
                "override keys were not selected: " + ", ".join(unexpected_overrides)
            )
        if any(type(overrides[key]) is not bool for key in selected_keys):
            raise PricingRefreshError("pricing refresh overrides must be boolean apply/skip values")

        stored_rows = cls._pricing_rows_snapshot(conn)
        if require_empty and stored_rows:
            raise FreshPricingDatabasePopulatedError(
                "automatic pricing seed requires an empty tp_pricing table"
            )
        schema = cls._schema_snapshot(conn)
        indexes = cls._index_snapshot(conn)
        shadowing: dict[str, list[dict[str, object]]] = {}
        identical_keys: list[str] = []
        insert_keys: list[str] = []
        seed_targets = [
            (seed, cls._target_seed_row(seed, version, effective_date)) for seed in seeds
        ]
        target_rows: list[dict[str, object]] = [
            {
                "seed": seed,
                "seed_unit_basis": SEED_PRICING_UNIT_BASIS,
                "target": target,
            }
            for seed, target in seed_targets
        ]

        for seed, target in seed_targets:
            key = cls._seed_key(seed)
            matches: list[dict[str, object]] = []
            exact_key_rows: list[dict[str, object]] = []
            target_model = str(target["model"]).lower()
            for stored in stored_rows:
                stored_model = str(stored["model"]).lower()
                relations: list[str] = []
                if stored_model == target_model:
                    relations.append("exact_model")
                elif stored_model in target_model or target_model in stored_model:
                    relations.append("fuzzy_model")
                if not relations:
                    continue
                if (
                    stored["version"] == version
                    and stored["provider"] == target["provider"]
                    and stored["model"] == target["model"]
                ):
                    relations.append("exact_key")
                    exact_key_rows.append(stored)
                if stored["version"] == version:
                    relations.append("same_version")
                stored_date = str(stored["effective_date"])
                if stored_date < effective_date:
                    relations.append("older_date")
                elif stored_date > effective_date:
                    relations.append("newer_date")
                else:
                    relations.append("same_date")
                matches.append(
                    {
                        "id": stored["id"],
                        "version": stored["version"],
                        "effective_date": stored["effective_date"],
                        "provider": stored["provider"],
                        "model": stored["model"],
                        "relations": relations,
                    }
                )
            for peer_seed, peer_target in seed_targets:
                if peer_seed is seed:
                    continue
                peer_model = str(peer_target["model"]).lower()
                if peer_model in target_model or target_model in peer_model:
                    matches.append(
                        {
                            "id": None,
                            "version": version,
                            "effective_date": effective_date,
                            "provider": peer_target["provider"],
                            "model": peer_target["model"],
                            "relations": ["selected_fuzzy_model", "same_version", "same_date"],
                        }
                    )
            shadowing[key] = matches
            if exact_key_rows:
                if len(exact_key_rows) == 1 and cls._is_identical_tagged_seed(
                    exact_key_rows[0], target
                ):
                    identical_keys.append(key)
                elif overrides[key]:
                    raise PricingRefreshCollisionError(
                        "pricing refresh key collision; choose skip or a distinct version: "
                        f"({version}, {target['provider']}, {target['model']})"
                    )
            elif overrides[key]:
                insert_keys.append(key)

        payload: dict[str, object] = {
            "plan_version": 1,
            "version": version,
            "effective_date": effective_date,
            "require_empty": require_empty,
            "selected_keys": selected_keys,
            "overrides": {key: overrides[key] for key in sorted(selected_keys)},
            "insert_keys": insert_keys,
            "identical_keys": identical_keys,
            "shadowing": shadowing,
            "catalog_hash": cls._seed_catalog_hash(),
            "selected_seed_hash": cls._canonical_hash(target_rows),
            "selected_rows": target_rows,
            "pricing_rows_hash": cls._canonical_hash(stored_rows),
            "pricing_row_count": len(stored_rows),
            "schema_hash": cls._canonical_hash(schema),
            "schema_columns": schema,
            "index_hash": cls._canonical_hash(indexes),
            "indexes": indexes,
        }
        return {**payload, "plan_hash": cls._canonical_hash(payload)}

    def plan_seed_refresh(
        self,
        selected_models: Sequence[str] | None = None,
        *,
        overrides: Mapping[str, bool],
        version: str = CURRENT_PRICING_VERSION,
        effective_date: str = CURRENT_EFFECTIVE_DATE,
        require_empty: bool = False,
    ) -> dict[str, object]:
        """Return a hashed, JSON-serializable seed refresh dry run.

        Each selected key requires an explicit boolean decision.  ``True``
        requests insertion; ``False`` records an intentional skip.  A date
        change never resolves an exact key collision because the durable key
        remains ``(version, provider, model)``.
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            plan = self._build_refresh_plan(
                conn,
                selected_models=selected_models,
                overrides=overrides,
                version=version,
                effective_date=effective_date,
                require_empty=require_empty,
            )
            conn.rollback()
            return plan
        finally:
            conn.close()

    @classmethod
    def _row_fingerprint(cls, row: Mapping[str, object]) -> str:
        return cls._canonical_hash(dict(row))

    @classmethod
    def _receipt_matches_current_state(cls, conn: sqlite3.Connection, receipt: sqlite3.Row) -> bool:
        if cls._canonical_hash(cls._pricing_rows_snapshot(conn)) != receipt["post_pricing_hash"]:
            return False
        if cls._canonical_hash(cls._schema_snapshot(conn)) != receipt["post_schema_hash"]:
            return False
        fingerprints = json.loads(str(receipt["row_fingerprints_json"]))
        for expected in fingerprints:
            row = conn.execute(
                "SELECT * FROM tp_pricing WHERE id = ?", (expected["id"],)
            ).fetchone()
            if row is None or cls._row_fingerprint(dict(row)) != expected["sha256"]:
                return False
        return True

    def apply_seed_refresh(self, plan: Mapping[str, object]) -> dict[str, object]:
        """Apply an unchanged refresh plan atomically under ``BEGIN IMMEDIATE``."""
        provided = dict(plan)
        plan_hash = str(provided.pop("plan_hash", ""))
        if not plan_hash or self._canonical_hash(provided) != plan_hash:
            raise StalePricingRefreshPlanError("pricing refresh plan hash is invalid")

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            receipt = conn.execute(
                "SELECT * FROM tp_pricing_refresh_receipts WHERE plan_hash = ?",
                (plan_hash,),
            ).fetchone()
            if receipt is not None:
                self._validate_refresh_preconditions(conn)
                if self._seed_catalog_hash() != receipt["catalog_hash"]:
                    raise StalePricingRefreshPlanError(
                        "pricing seed catalog changed after the refresh was applied"
                    )
                if not self._receipt_matches_current_state(conn, receipt):
                    raise StalePricingRefreshPlanError(
                        "previously applied pricing refresh no longer matches current rows"
                    )
                conn.rollback()
                return {
                    "status": "already_applied",
                    "plan_hash": plan_hash,
                    "inserted": len(json.loads(str(receipt["row_fingerprints_json"]))),
                }

            selected_keys = cast(list[str], provided["selected_keys"])
            overrides = cast(dict[str, bool], provided["overrides"])
            expected = self._build_refresh_plan(
                conn,
                selected_models=selected_keys,
                overrides=overrides,
                version=str(provided["version"]),
                effective_date=str(provided["effective_date"]),
                require_empty=bool(provided.get("require_empty", False)),
            )
            if expected != dict(plan):
                raise StalePricingRefreshPlanError(
                    "pricing rows, schema, selected seeds, or catalog changed after planning"
                )

            seed_by_key = {self._seed_key(row): row for row in SEED_PRICING}
            fingerprints: list[dict[str, object]] = []
            for key in cast(list[str], provided["insert_keys"]):
                target = self._target_seed_row(
                    seed_by_key[key],
                    str(provided["version"]),
                    str(provided["effective_date"]),
                )
                cursor = conn.execute(
                    """INSERT INTO tp_pricing
                       (version, effective_date, provider, model, input_rate,
                        output_rate, currency, source, provenance, unit_basis)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    tuple(
                        target[column]
                        for column in (
                            "version",
                            "effective_date",
                            "provider",
                            "model",
                            "input_rate",
                            "output_rate",
                            "currency",
                            "source",
                            "provenance",
                            "unit_basis",
                        )
                    ),
                )
                row_id = cursor.lastrowid
                assert row_id is not None
                inserted = conn.execute(
                    "SELECT * FROM tp_pricing WHERE id = ?", (row_id,)
                ).fetchone()
                assert inserted is not None
                fingerprints.append(
                    {
                        "id": int(row_id),
                        "key": key,
                        "sha256": self._row_fingerprint(dict(inserted)),
                    }
                )

            post_rows_hash = self._canonical_hash(self._pricing_rows_snapshot(conn))
            post_schema_hash = self._canonical_hash(self._schema_snapshot(conn))
            conn.execute(
                """INSERT INTO tp_pricing_refresh_receipts
                   (plan_hash, catalog_hash, version, effective_date,
                    selected_keys_json, row_fingerprints_json,
                    post_pricing_hash, post_schema_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan_hash,
                    provided["catalog_hash"],
                    provided["version"],
                    provided["effective_date"],
                    json.dumps(selected_keys, separators=(",", ":")),
                    json.dumps(fingerprints, sort_keys=True, separators=(",", ":")),
                    post_rows_hash,
                    post_schema_hash,
                ),
            )
            conn.commit()
            return {
                "status": "applied",
                "plan_hash": plan_hash,
                "inserted": len(fingerprints),
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

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

        conn = self._connect()
        try:
            # Exact match first.  id is the deterministic tie-break for
            # legacy/dev databases that predate the unique key.
            row = conn.execute(
                """SELECT * FROM tp_pricing
                   WHERE model = ? AND effective_date <= ?
                   ORDER BY effective_date DESC, id DESC LIMIT 1""",
                (model, event_date),
            ).fetchone()

            if row is None:
                row = self._fuzzy_match(conn, model, event_date)
        finally:
            conn.close()

        if row:
            pricing = self._pricing_from_row(row)
        else:
            # Fallback pricing for unknown models
            pricing = Pricing(
                provider="unknown",
                model=model,
                input_rate=self._normalize_rate_to_per_1k(
                    float(self._FALLBACK_PRICING["input_rate"]),
                    str(self._FALLBACK_PRICING["unit_basis"]),
                ),
                output_rate=self._normalize_rate_to_per_1k(
                    float(self._FALLBACK_PRICING["output_rate"]),
                    str(self._FALLBACK_PRICING["unit_basis"]),
                ),
                version="fallback",
                effective_date=event_date,
                source="estimated",
                provenance=PROVENANCE_FALLBACK,
                unit_basis=UNIT_BASIS_USD_PER_1K,
            )
            logger.warning(f"No pricing found for model '{model}', using fallback")

        self._enforce_known_unit(pricing)
        return pricing

    def _pricing_from_row(self, row: sqlite3.Row) -> Pricing:
        columns = set(row.keys())
        return Pricing(
            provider=row["provider"],
            model=row["model"],
            input_rate=row["input_rate"],
            output_rate=row["output_rate"],
            version=row["version"],
            effective_date=row["effective_date"],
            source=row["source"],
            provenance=(row["provenance"] or UNKNOWN_METADATA)
            if "provenance" in columns
            else UNKNOWN_METADATA,
            unit_basis=(row["unit_basis"] or UNKNOWN_METADATA)
            if "unit_basis" in columns
            else UNKNOWN_METADATA,
        )

    def _enforce_known_unit(self, pricing: Pricing) -> None:
        if self.strict_unknown_units and pricing.unit_basis != UNIT_BASIS_USD_PER_1K:
            raise UnknownPricingUnitError(
                f"pricing unit basis is unknown for {pricing.provider}/{pricing.model} "
                f"at version {pricing.version}"
            )

    def _fuzzy_match(
        self, conn: sqlite3.Connection, model: str, event_date: str
    ) -> Optional[sqlite3.Row]:
        """Try matching by model name substring."""
        model_lower = model.lower()
        rows = cast(
            list[sqlite3.Row],
            conn.execute(
                """SELECT * FROM tp_pricing
                   WHERE effective_date <= ?
                   ORDER BY effective_date DESC, id DESC""",
                (event_date,),
            ).fetchall(),
        )
        for row in rows:
            stored_model = str(row["model"]).lower()
            if stored_model in model_lower or model_lower in stored_model:
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
            pricing_provenance=pricing.provenance,
            unit_basis=pricing.unit_basis,
        )

    # ------------------------------------------------------------------
    # Pricing catalog management
    # ------------------------------------------------------------------
    def list_pricing(self, version: Optional[str] = None) -> List[dict[str, object]]:
        """List all pricing entries, optionally filtered by version."""
        conn = self._connect()
        if version:
            rows = conn.execute(
                """SELECT * FROM tp_pricing
                   WHERE version = ?
                   ORDER BY provider, model, id DESC""",
                (version,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM tp_pricing
                   ORDER BY version DESC, provider, model, id DESC"""
            ).fetchall()
        conn.close()
        result: list[dict[str, object]] = []
        for row in rows:
            values = {str(column): value for column, value in dict(row).items()}
            values["provenance"] = values.get("provenance") or UNKNOWN_METADATA
            values["unit_basis"] = values.get("unit_basis") or UNKNOWN_METADATA
            result.append(values)
        return result

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
        """Insert a custom USD/1K pricing record. Returns the new row id."""
        version = version or CURRENT_PRICING_VERSION
        effective_date = effective_date or datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            conn = self._connect()
            cur = conn.execute(
                """INSERT INTO tp_pricing
                   (version, effective_date, provider, model, input_rate, output_rate,
                    source, provenance, unit_basis)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    version,
                    effective_date,
                    provider,
                    model,
                    input_rate,
                    output_rate,
                    source,
                    PROVENANCE_CUSTOM,
                    UNIT_BASIS_USD_PER_1K,
                ),
            )
            conn.commit()
            row_id = cur.lastrowid
            conn.close()
        # Retained for compatibility even though DB-backed lookup no longer
        # consumes this cache.
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
    ) -> dict[str, str | int]:
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
            """SELECT * FROM tp_pricing
               WHERE model = ? AND version = ?
               ORDER BY id DESC LIMIT 1""",
            (model, version),
        ).fetchone()
        if row:
            pricing = self._pricing_from_row(row)
            self._enforce_known_unit(pricing)
            return pricing
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
