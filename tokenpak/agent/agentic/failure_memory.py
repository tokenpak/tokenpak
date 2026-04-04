"""TokenPak — Failure Signature Database + Repair Recipes

Persistent memory of past failures and their proven repair paths.
When errors recur, the agent recalls proven fixes instead of reasoning
from scratch.

Key concepts
------------
- FailureSignature: dataclass capturing error class, pattern, root causes,
  repair steps, confidence, and validation state.
- FailureMemoryDB: CRUD + matching + learning loop.
  Persists to ~/.tokenpak/failure_signatures.json.
- Recipe matching: normalise error text → regex match → best confidence.
- Learning loop: success → confidence up; failure → down.
  After N_VALIDATE_SUCCESSES successes the recipe is marked validated.

Usage
-----
    db = FailureMemoryDB()
    recipe = db.match("Connection refused: port 5432")
    if recipe:
        for step in recipe.repair_recipe:
            ...
        db.record_repair_outcome(recipe.signature_id, success=True)
    else:
        db.add(FailureSignature(
            signature_id="pg_conn_refused",
            error_class="port_bind_failure",
            error_pattern=r"Connection refused.*port 5432",
            root_causes=["postgres not running"],
            repair_recipe=["systemctl start postgresql"],
        ))
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SIGNATURES_PATH = Path.home() / ".tokenpak" / "failure_signatures.json"

# Number of successful repairs before a signature is marked validated
N_VALIDATE_SUCCESSES = 3

# Confidence bounds
_CONF_MIN = 0.0
_CONF_MAX = 1.0
_CONF_STEP_UP = 0.1
_CONF_STEP_DOWN = 0.15


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class FailureSignature:
    """A recorded failure pattern with repair recipe and confidence."""

    signature_id: str
    error_class: str  # e.g. "port_bind_failure", "auth_error"
    error_pattern: str  # regex or normalised error text fragment
    root_causes: list[str] = field(default_factory=list)
    repair_recipe: list[str] = field(default_factory=list)  # ordered repair steps
    confidence: float = 0.5  # 0–1, updated by learning loop
    success_count: int = 0
    failure_count: int = 0
    last_seen: str = ""
    validated: bool = False  # True once the fix has held N times

    def __post_init__(self) -> None:
        if not self.last_seen:
            self.last_seen = _now_iso()
        self.confidence = float(max(_CONF_MIN, min(_CONF_MAX, self.confidence)))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise(text: str) -> str:
    """Strip extra whitespace and lower-case for pattern matching."""
    return " ".join(text.lower().split())


def _sig_to_dict(sig: FailureSignature) -> dict:
    return asdict(sig)


def _sig_from_dict(d: dict) -> FailureSignature:
    return FailureSignature(
        signature_id=d["signature_id"],
        error_class=d.get("error_class", "unknown"),
        error_pattern=d.get("error_pattern", ""),
        root_causes=d.get("root_causes", []),
        repair_recipe=d.get("repair_recipe", []),
        confidence=float(d.get("confidence", 0.5)),
        success_count=int(d.get("success_count", 0)),
        failure_count=int(d.get("failure_count", 0)),
        last_seen=d.get("last_seen", _now_iso()),
        validated=bool(d.get("validated", False)),
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load_sigs(path: Path) -> dict[str, FailureSignature]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        return {k: _sig_from_dict(v) for k, v in raw.items()}
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}


def _save_sigs(sigs: dict[str, FailureSignature], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: _sig_to_dict(v) for k, v in sigs.items()}, indent=2))


# ---------------------------------------------------------------------------
# Public API — FailureMemoryDB
# ---------------------------------------------------------------------------


class FailureMemoryDB:
    """Persistent failure signature store with CRUD, matching, and learning.

    Args:
        storage_path: Path to the JSON file.  Defaults to
                      ``~/.tokenpak/failure_signatures.json``.
    """

    def __init__(self, storage_path: Optional[Path | str] = None) -> None:
        self._path = Path(storage_path) if storage_path else DEFAULT_SIGNATURES_PATH
        self._sigs: dict[str, FailureSignature] = _load_sigs(self._path)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, sig: FailureSignature) -> FailureSignature:
        """Add or overwrite a failure signature.

        Returns the stored signature.
        """
        if not sig.signature_id:
            sig.signature_id = str(uuid.uuid4())
        self._sigs[sig.signature_id] = sig
        self._persist()
        return sig

    def get(self, signature_id: str) -> Optional[FailureSignature]:
        """Return a signature by ID, or None if not found."""
        return self._sigs.get(signature_id)

    def update(self, sig: FailureSignature) -> FailureSignature:
        """Update an existing signature (upsert)."""
        return self.add(sig)

    def delete(self, signature_id: str) -> bool:
        """Remove a signature.  Returns True if it existed."""
        if signature_id in self._sigs:
            del self._sigs[signature_id]
            self._persist()
            return True
        return False

    def list_all(self) -> list[FailureSignature]:
        """Return all signatures, sorted by confidence descending."""
        return sorted(self._sigs.values(), key=lambda s: s.confidence, reverse=True)

    def count(self) -> int:
        return len(self._sigs)

    # ------------------------------------------------------------------
    # Recipe matching
    # ------------------------------------------------------------------

    def match(self, error_text: str) -> Optional[FailureSignature]:
        """Find the best-matching signature for an error message.

        Normalises *error_text*, tries each signature's regex pattern
        against it, and returns the one with the highest confidence.

        If no pattern matches, records a new signature shell with empty
        recipe and returns None so the caller knows to build one.

        Args:
            error_text: Raw error string from an exception / log.

        Returns:
            Best-matching FailureSignature or None.
        """
        normalised = _normalise(error_text)
        candidates: list[FailureSignature] = []

        for sig in self._sigs.values():
            if not sig.error_pattern:
                continue
            try:
                if re.search(sig.error_pattern, normalised, re.IGNORECASE):
                    candidates.append(sig)
            except re.error:
                # Bad pattern — try plain substring match
                if sig.error_pattern.lower() in normalised:
                    candidates.append(sig)

        if not candidates:
            # Record unknown error as a new empty signature
            self._record_unknown(error_text)
            return None

        # Return highest-confidence match; break ties by most recent
        candidates.sort(
            key=lambda s: (s.confidence, s.last_seen),
            reverse=True,
        )
        best = candidates[0]
        best.last_seen = _now_iso()
        self._persist()
        return best

    def match_all(self, error_text: str) -> list[FailureSignature]:
        """Return all matching signatures, sorted by confidence descending."""
        normalised = _normalise(error_text)
        results: list[FailureSignature] = []
        for sig in self._sigs.values():
            if not sig.error_pattern:
                continue
            try:
                if re.search(sig.error_pattern, normalised, re.IGNORECASE):
                    results.append(sig)
            except re.error:
                if sig.error_pattern.lower() in normalised:
                    results.append(sig)
        return sorted(results, key=lambda s: s.confidence, reverse=True)

    # ------------------------------------------------------------------
    # Learning loop
    # ------------------------------------------------------------------

    def record_repair_outcome(
        self,
        signature_id: str,
        *,
        success: bool,
    ) -> Optional[FailureSignature]:
        """Update a signature after a repair attempt.

        - Success: increment success_count, nudge confidence up.
          After N_VALIDATE_SUCCESSES, mark validated.
        - Failure: increment failure_count, nudge confidence down.

        Args:
            signature_id: ID of the signature that was applied.
            success:       Whether the repair held.

        Returns:
            Updated signature or None if not found.
        """
        sig = self._sigs.get(signature_id)
        if sig is None:
            return None

        if success:
            sig.success_count += 1
            sig.confidence = min(_CONF_MAX, sig.confidence + _CONF_STEP_UP)
            if sig.success_count >= N_VALIDATE_SUCCESSES:
                sig.validated = True
        else:
            sig.failure_count += 1
            sig.confidence = max(_CONF_MIN, sig.confidence - _CONF_STEP_DOWN)
            # Un-validate if confidence drops below threshold
            if sig.confidence < 0.4:
                sig.validated = False

        sig.last_seen = _now_iso()
        self._persist()
        return sig

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_unknown(self, error_text: str) -> FailureSignature:
        """Create a new shell signature for an unseen error."""
        sig_id = f"auto_{uuid.uuid4().hex[:8]}"
        # Build a safe normalised pattern from the first 80 chars
        snippet = _normalise(error_text)[:80]
        safe_pattern = re.escape(snippet[:40])  # Escape for safety
        sig = FailureSignature(
            signature_id=sig_id,
            error_class="unknown",
            error_pattern=safe_pattern,
            root_causes=[],
            repair_recipe=[],
            confidence=0.1,
        )
        self._sigs[sig_id] = sig
        self._persist()
        return sig

    def _persist(self) -> None:
        _save_sigs(self._sigs, self._path)

    def reload(self) -> None:
        """Re-read the on-disk store (useful after external edits)."""
        self._sigs = _load_sigs(self._path)


# ---------------------------------------------------------------------------
# Module-level convenience functions (operate on a shared singleton)
# ---------------------------------------------------------------------------

_default_db: Optional[FailureMemoryDB] = None


def _get_db() -> FailureMemoryDB:
    global _default_db
    if _default_db is None:
        _default_db = FailureMemoryDB()
    return _default_db


def match_recipe(error_text: str) -> Optional[FailureSignature]:
    """Match *error_text* against the default DB.  Returns best match or None."""
    return _get_db().match(error_text)


def record_outcome(signature_id: str, *, success: bool) -> Optional[FailureSignature]:
    """Record repair outcome in the default DB."""
    return _get_db().record_repair_outcome(signature_id, success=success)


def add_signature(sig: FailureSignature) -> FailureSignature:
    """Add a signature to the default DB."""
    return _get_db().add(sig)
