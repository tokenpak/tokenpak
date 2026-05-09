# SPDX-License-Identifier: Apache-2.0
"""TokenPak vault doctor staleness check (VDS-03).

Reads ``~/.tokenpak/vault.yaml`` (the v1 schema landed by VDS-01) plus the
per-path ``last_indexed`` / ``expected_interval_seconds`` health metadata,
and emits one finding per registered path describing whether its index is:

* ``ok``      — fresh: ``now - last_indexed <= expected_interval * 2``
* ``stale``   — last rebuild older than ``expected_interval * 2``
* ``missing`` — configured directory does not exist or is unreadable
* ``never``   — registered but never indexed (no ``last_indexed`` metadata)
* ``corrupt`` — metadata can't be parsed (non-ISO timestamp, etc.)
* ``failed``  — last reindex failed (``last_index_status != 'ok'``)

Manual schedules (``schedule: manual``) deliberately do NOT warn solely on
age — a path the user only rebuilds by hand should not pollute doctor
output. They DO warn on ``missing`` / ``corrupt`` / ``failed``.

Findings are returned as plain dicts so the caller (``cli/commands/doctor.py``)
can format them; this module is the single source of truth for *what counts as
stale*, not for how the warning is rendered.

Spec: ``01_PROJECTS/tokenpak/initiatives/2026-04-28-tokenpak-vault-directory-scheduling/03-SPEC.md``
Component 3 — "Warns if last rebuild > expected interval × 2"
Acceptance: AC-VDS-06 in 05-ACCEPTANCE.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from tokenpak.vault import config as vault_config

# ``expected_interval_seconds × DEFAULT_STALE_FACTOR`` is the staleness threshold.
# Spec: "Warns if last rebuild > expected interval × 2".
DEFAULT_STALE_FACTOR = 2

# Used when a path has no ``expected_interval_seconds`` configured AND no
# ``schedule`` we can derive one from. 24h matches ``vault_health.DEFAULT_STALE_SECONDS``
# so doctor stays consistent with the legacy index.json freshness check.
FALLBACK_INTERVAL_SECONDS = 24 * 60 * 60


@dataclass
class PathFinding:
    """One per-path result emitted by :func:`check_vault_paths`.

    ``status`` is one of ``ok``, ``stale``, ``missing``, ``never``, ``corrupt``,
    ``failed``. ``severity`` is the doctor verdict (``pass`` / ``warn``) the
    caller should record.
    """

    path: str
    status: str
    severity: str  # "pass" or "warn"
    message: str
    schedule: Optional[str] = None
    age_seconds: Optional[float] = None
    threshold_seconds: Optional[float] = None
    last_indexed: Optional[str] = None
    last_index_status: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "status": self.status,
            "severity": self.severity,
            "message": self.message,
            "schedule": self.schedule,
            "age_seconds": (
                round(self.age_seconds, 1) if self.age_seconds is not None else None
            ),
            "threshold_seconds": self.threshold_seconds,
            "last_indexed": self.last_indexed,
            "last_index_status": self.last_index_status,
        }


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def check_vault_paths(
    cfg: Optional[vault_config.VaultConfig] = None,
    *,
    cfg_path: Optional[Path] = None,
    now: Optional[datetime] = None,
) -> list[PathFinding]:
    """Return a finding per registered path in ``vault.yaml``.

    ``cfg`` wins if both are passed; otherwise ``cfg_path`` is loaded; otherwise
    the canonical path (``TOKENPAK_VAULT_CONFIG`` or ``~/.tokenpak/vault.yaml``)
    is used.
    """
    if cfg is None:
        cfg = vault_config.load(cfg_path)
    when = now or datetime.now(timezone.utc)

    findings: list[PathFinding] = []
    for entry in vault_config.iter_registered_paths(cfg):
        findings.append(_check_one(entry, now=when))
    return findings


def load_and_check(cfg_path: Optional[Path] = None) -> tuple[list[PathFinding], Optional[str]]:
    """Load ``vault.yaml`` and run the staleness check.

    Returns ``(findings, error)``. ``error`` is None on success; if the config
    file is unreadable or the schema is wrong, ``error`` carries a one-line
    description and ``findings`` is empty. The caller maps ``error`` to a
    single ``warn`` so a corrupt config never fails an unrelated check.
    """
    try:
        cfg = vault_config.load(cfg_path)
    except Exception as exc:
        return [], f"vault.yaml unreadable: {exc}"
    return check_vault_paths(cfg), None


# ---------------------------------------------------------------------------
# Per-path logic
# ---------------------------------------------------------------------------


def _check_one(entry: vault_config.VaultPathEntry, *, now: datetime) -> PathFinding:
    path_str = entry.path
    schedule = entry.schedule
    is_manual = (schedule or "").strip().lower() == "manual"

    # Path-existence check applies regardless of schedule. A registered path
    # that's been deleted or unmounted is always worth surfacing.
    p = Path(path_str).expanduser()
    if not p.exists():
        return PathFinding(
            path=path_str,
            status="missing",
            severity="warn",
            message=f"vault path missing: {path_str}",
            schedule=schedule,
            last_indexed=entry.last_indexed,
            last_index_status=entry.last_index_status,
        )

    # Last reindex explicitly failed — surface regardless of schedule.
    if entry.last_index_status and entry.last_index_status.lower() not in ("ok", ""):
        return PathFinding(
            path=path_str,
            status="failed",
            severity="warn",
            message=(
                f"last reindex failed: {path_str} "
                f"(status={entry.last_index_status})"
            ),
            schedule=schedule,
            last_indexed=entry.last_indexed,
            last_index_status=entry.last_index_status,
        )

    # Never indexed.
    if not entry.last_indexed:
        if is_manual:
            # Manual paths legitimately may not have run yet; pass quietly.
            return PathFinding(
                path=path_str,
                status="never",
                severity="pass",
                message=f"vault path registered (manual, no rebuild yet): {path_str}",
                schedule=schedule,
            )
        return PathFinding(
            path=path_str,
            status="never",
            severity="warn",
            message=(
                f"vault path never indexed: {path_str} "
                "(run: tokenpak index --reindex-path <path>)"
            ),
            schedule=schedule,
        )

    # Parse the timestamp. Corrupt timestamps warn even on manual schedules
    # because they signal config-file rot.
    try:
        last_dt = _parse_iso_z(entry.last_indexed)
    except ValueError:
        return PathFinding(
            path=path_str,
            status="corrupt",
            severity="warn",
            message=(
                f"vault metadata corrupt: {path_str} "
                f"(unparseable last_indexed={entry.last_indexed!r})"
            ),
            schedule=schedule,
            last_indexed=entry.last_indexed,
            last_index_status=entry.last_index_status,
        )

    age_seconds = max(0.0, (now - last_dt).total_seconds())

    # Manual schedule: do not warn on age alone.
    if is_manual:
        return PathFinding(
            path=path_str,
            status="ok",
            severity="pass",
            message=(
                f"vault path fresh (manual, last indexed "
                f"{_humanize_age(age_seconds)} ago): {path_str}"
            ),
            schedule=schedule,
            age_seconds=age_seconds,
            last_indexed=entry.last_indexed,
            last_index_status=entry.last_index_status,
        )

    threshold = _resolve_threshold(entry)
    if age_seconds > threshold:
        return PathFinding(
            path=path_str,
            status="stale",
            severity="warn",
            message=(
                f"vault index stale: {path_str} — last rebuild "
                f"{_humanize_age(age_seconds)} ago "
                f"(threshold {_humanize_age(threshold)})"
            ),
            schedule=schedule,
            age_seconds=age_seconds,
            threshold_seconds=threshold,
            last_indexed=entry.last_indexed,
            last_index_status=entry.last_index_status,
        )

    return PathFinding(
        path=path_str,
        status="ok",
        severity="pass",
        message=(
            f"vault index fresh: {path_str} "
            f"(last rebuild {_humanize_age(age_seconds)} ago)"
        ),
        schedule=schedule,
        age_seconds=age_seconds,
        threshold_seconds=threshold,
        last_indexed=entry.last_indexed,
        last_index_status=entry.last_index_status,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_threshold(entry: vault_config.VaultPathEntry) -> float:
    """Compute ``expected_interval_seconds × DEFAULT_STALE_FACTOR``.

    Falls back to :data:`FALLBACK_INTERVAL_SECONDS` × factor when the entry has
    no explicit interval (e.g. user wrote ``schedule: every 6 hours`` but VDS-02
    hasn't filled ``expected_interval_seconds`` yet).
    """
    eis = entry.expected_interval_seconds
    if eis is None or eis <= 0:
        eis = FALLBACK_INTERVAL_SECONDS
    return float(eis) * DEFAULT_STALE_FACTOR


def _parse_iso_z(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; supports the ``Z`` suffix used by VDS-01."""
    if not value:
        raise ValueError("empty timestamp")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _humanize_age(seconds: float) -> str:
    """Render a duration in the most useful unit (s/m/h/d)."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def summarize(findings: Iterable[PathFinding]) -> dict:
    """Aggregate findings into a one-line summary dict for the doctor record."""
    counts = {"ok": 0, "stale": 0, "missing": 0, "never": 0, "corrupt": 0, "failed": 0}
    for f in findings:
        counts[f.status] = counts.get(f.status, 0) + 1
    return counts
