# SPDX-License-Identifier: Apache-2.0
"""TIP Intent Contract v1 — Phase 0 in-process representation.

Phase 0 keeps the contract as a small frozen dataclass; the typed
schema lift into ``tokenpak/core/contracts/`` happens in Intent-1.
The on-disk JSON Schema for the wire-side contract lives in the
registry (``schemas/tip/intent-contract-v1.json`` — separate
deliverable per the proposal §4 PR-I0-2).

Three concerns live in this module:

1. :class:`IntentContract` — the per-request canonical object.
2. :func:`attach_intent_headers` — apply the five wire headers to a
   mutable header mapping. The §4.3 capability gate is the caller's
   responsibility (so the gate stays expressed in one place at the
   call site in :mod:`tokenpak.proxy.server`).
3. :class:`IntentTelemetryStore` — SQLite writer for the
   ``intent_events`` table (one row per request).

No business logic, no header-emission decision making — those live
at the call site so they can be reasoned about in one place.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, MutableMapping, Optional, Tuple

if TYPE_CHECKING:
    from tokenpak.proxy.intent_classifier import IntentClassification

INTENT_HEADER_CLASS = "X-TokenPak-Intent-Class"
INTENT_HEADER_CONFIDENCE = "X-TokenPak-Intent-Confidence"
INTENT_HEADER_SUBTYPE = "X-TokenPak-Intent-Subtype"
INTENT_HEADER_RISK = "X-TokenPak-Contract-Risk"
INTENT_HEADER_ID = "X-TokenPak-Contract-Id"

# Wire-emission gate label per Standard #23 §4.3. Single source of
# truth so server.py doesn't have to spell the literal again.
GATE_CAPABILITY = "tip.intent.contract-headers-v1"


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntentContract:
    """Per-request TIP Intent Contract (Phase 0 shape).

    ``contract_id`` is a 26-char Crockford-base32 ULID-shaped string:
    timestamp prefix + random tail. Sortable + collision-resistant.
    """

    contract_id: str
    intent_class: str
    confidence: float
    subtype: Optional[str]
    risk: str  # "low" | "medium" | "high"
    slots_present: Tuple[str, ...]
    slots_missing: Tuple[str, ...]
    intent_source: str
    catch_all_reason: Optional[str]
    raw_prompt_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "intent_class": self.intent_class,
            "confidence": self.confidence,
            "subtype": self.subtype,
            "risk": self.risk,
            "slots_present": list(self.slots_present),
            "slots_missing": list(self.slots_missing),
            "intent_source": self.intent_source,
            "catch_all_reason": self.catch_all_reason,
            "raw_prompt_hash": self.raw_prompt_hash,
        }


def hash_prompt(text: str) -> str:
    """SHA-256 hex digest of the raw prompt — telemetry dedup key.

    Storing the hash (not the prompt) keeps Architecture §7.1 prompt
    locality intact: prompts stay in the per-request log; the
    cross-request telemetry DB only ever sees an opaque digest.
    """
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def derive_risk(intent_class: str, confidence: float, slots_missing: Tuple[str, ...]) -> str:
    """Map a classification to a 3-bucket risk label.

    Phase 0 risk is heuristic: high-side-effect intents (``execute``,
    ``create``) start at ``medium`` and drop to ``high`` if any
    required slot is missing; observation intents (``status``,
    ``usage``, ``query``, ``search``, ``explain``) start at ``low``;
    everything else is ``low`` unless confidence is borderline. Phase
    1 will lift this into a typed ``RiskAssessor`` aware of the
    recipe table.
    """
    if intent_class in ("execute", "create"):
        return "high" if slots_missing else "medium"
    if intent_class in ("debug", "plan"):
        return "medium" if confidence < 0.7 else "low"
    return "low"


def make_contract_id() -> str:
    """ULID-shaped opaque id (26 chars). Phase 0 uses a portable

    timestamp+random hex format rather than a third-party ULID
    library — keeps the dependency surface clean.
    """
    ts_ms = int(time.time() * 1000)
    # 16 random bits per Crockford convention; we just hexlify since
    # the field is opaque to consumers.
    rand = secrets.token_hex(8)  # 16 hex chars
    return f"{ts_ms:013x}{rand}"  # 13 hex (ms) + 16 hex (rand) = 29 chars


# ---------------------------------------------------------------------------
# Header attachment
# ---------------------------------------------------------------------------


def attach_intent_headers(
    headers: MutableMapping[str, str],
    contract: IntentContract,
) -> None:
    """Mutate ``headers`` to carry the five Intent-0 wire headers.

    Idempotent — calling twice with the same contract overwrites with
    the same values. Pre-existing values for these header names are
    overwritten (the proxy is the authoritative source for TIP
    headers per Standard #23 §4 + §22 §5.1).

    The capability gate is NOT applied here. Callers are required to
    check ``GATE_CAPABILITY in adapter.capabilities`` before invoking
    this function — keeping the gate expressed at the call site is
    the explicit pattern from the standards (§4.3).
    """
    headers[INTENT_HEADER_CLASS] = contract.intent_class
    headers[INTENT_HEADER_CONFIDENCE] = f"{contract.confidence:.2f}"
    if contract.subtype is not None:
        headers[INTENT_HEADER_SUBTYPE] = contract.subtype
    headers[INTENT_HEADER_RISK] = contract.risk
    headers[INTENT_HEADER_ID] = contract.contract_id


# ---------------------------------------------------------------------------
# Telemetry store (SQLite)
# ---------------------------------------------------------------------------


_DEFAULT_DB_PATH = Path(os.environ.get("TOKENPAK_HOME", str(Path.home() / ".tokenpak"))) / "telemetry.db"

_INTENT_EVENTS_DDL = """\
CREATE TABLE IF NOT EXISTS intent_events (
    request_id           TEXT PRIMARY KEY,
    contract_id          TEXT NOT NULL,
    timestamp            TEXT NOT NULL,
    raw_prompt_hash      TEXT NOT NULL,
    intent_class         TEXT NOT NULL,
    intent_confidence    REAL NOT NULL,
    intent_slots_present TEXT NOT NULL,
    intent_slots_missing TEXT NOT NULL,
    intent_source        TEXT NOT NULL,
    catch_all_reason     TEXT,
    tip_headers_emitted  INTEGER NOT NULL,
    tip_headers_stripped INTEGER NOT NULL,
    tokens_in            INTEGER,
    tokens_out           INTEGER,
    latency_ms           INTEGER
);
CREATE INDEX IF NOT EXISTS idx_intent_events_class
    ON intent_events (intent_class, timestamp);
CREATE INDEX IF NOT EXISTS idx_intent_events_confidence
    ON intent_events (intent_confidence);
"""


@dataclass
class IntentTelemetryRow:
    """One row of the ``intent_events`` table (write side)."""

    request_id: str
    contract: IntentContract
    timestamp: str
    tip_headers_emitted: bool
    tip_headers_stripped: bool
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    latency_ms: Optional[int] = None


class IntentTelemetryStore:
    """Per-host SQLite writer for ``intent_events``.

    Constructed lazily; opens (or creates) ``~/.tokenpak/telemetry.db``
    on first :meth:`write`. Single connection guarded by a lock —
    Phase 0 traffic is low; Intent-1 will switch to the existing
    ``services.telemetry_service`` async writer once the schema lift
    is complete.
    """

    _LOCK = threading.Lock()

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.executescript(_INTENT_EVENTS_DDL)
            conn.commit()
            self._conn = conn
        return self._conn

    def write(self, row: IntentTelemetryRow) -> None:
        """Insert one row. Best-effort — exceptions are not raised.

        Telemetry is a side-channel; a write failure must not break
        the outbound request. Caller logs a warning if the writer
        misbehaves (server.py wraps the call).
        """
        c = row.contract
        try:
            with self._LOCK:
                conn = self._connect()
                conn.execute(
                    """
                    INSERT OR REPLACE INTO intent_events (
                        request_id, contract_id, timestamp, raw_prompt_hash,
                        intent_class, intent_confidence,
                        intent_slots_present, intent_slots_missing,
                        intent_source, catch_all_reason,
                        tip_headers_emitted, tip_headers_stripped,
                        tokens_in, tokens_out, latency_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row.request_id,
                        c.contract_id,
                        row.timestamp,
                        c.raw_prompt_hash,
                        c.intent_class,
                        c.confidence,
                        json.dumps(list(c.slots_present)),
                        json.dumps(list(c.slots_missing)),
                        c.intent_source,
                        c.catch_all_reason,
                        1 if row.tip_headers_emitted else 0,
                        1 if row.tip_headers_stripped else 0,
                        row.tokens_in,
                        row.tokens_out,
                        row.latency_ms,
                    ),
                )
                conn.commit()
        except Exception:  # noqa: BLE001 — best-effort; never propagate
            return

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# Module-level singleton — production code pulls one shared writer.
_DEFAULT_STORE: Optional[IntentTelemetryStore] = None


def get_default_store() -> IntentTelemetryStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = IntentTelemetryStore()
    return _DEFAULT_STORE


def set_default_store(store: Optional[IntentTelemetryStore]) -> None:
    """Test hook — swap the default writer (e.g. for an in-memory DB)."""
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


# ---------------------------------------------------------------------------
# Convenience constructor — classification → contract
# ---------------------------------------------------------------------------


def build_contract(
    *,
    classification: "IntentClassification",
    raw_prompt: str,
    subtype: Optional[str] = None,
) -> IntentContract:
    """Assemble an :class:`IntentContract` from a classifier output.

    Phase 0 has no subtype taxonomy yet — the field is reserved for
    Intent-1 (where the typed-class lift produces canonical
    subtypes). Caller may pass an explicit ``subtype`` if a
    higher-layer caller computed one.
    """
    return IntentContract(
        contract_id=make_contract_id(),
        intent_class=classification.intent_class,
        confidence=classification.confidence,
        subtype=subtype,
        risk=derive_risk(
            classification.intent_class,
            classification.confidence,
            classification.slots_missing,
        ),
        slots_present=classification.slots_present,
        slots_missing=classification.slots_missing,
        intent_source=classification.intent_source,
        catch_all_reason=classification.catch_all_reason,
        raw_prompt_hash=hash_prompt(raw_prompt),
    )


# Imported lazily to avoid circular imports at module load: the
# classifier imports from `intent_policy`, which lives alongside this
# module. We re-export `IntentClassification` for convenience but
# don't bind it at module-level.
def __getattr__(name: str) -> Any:  # pragma: no cover — lazy re-export
    if name == "IntentClassification":
        from tokenpak.proxy.intent_classifier import IntentClassification

        return IntentClassification
    raise AttributeError(name)


__all__ = [
    "GATE_CAPABILITY",
    "INTENT_HEADER_CLASS",
    "INTENT_HEADER_CONFIDENCE",
    "INTENT_HEADER_ID",
    "INTENT_HEADER_RISK",
    "INTENT_HEADER_SUBTYPE",
    "IntentContract",
    "IntentTelemetryRow",
    "IntentTelemetryStore",
    "attach_intent_headers",
    "build_contract",
    "derive_risk",
    "get_default_store",
    "hash_prompt",
    "make_contract_id",
    "set_default_store",
]
