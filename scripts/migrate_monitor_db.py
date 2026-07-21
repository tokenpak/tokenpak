#!/usr/bin/env python3
"""
Monitor DB legacy migration — detect / merge / archive
======================================================

Consolidate request-ledger rows from legacy ``monitor.db`` locations into the
single canonical store at ``~/.tpk/monitor.db``.

Legacy locations probed (in addition to any ``$TOKENPAK_DB`` override):

    ~/tokenpak/monitor.db          (pre-dot historical)
    ~/.tokenpak/monitor.db         (dotfile legacy)
    ~/.tokenpak/data/monitor.db    (dotfile/data legacy)

Behaviour contract:

* **Dry-run by default.** Nothing on disk is touched unless ``--apply`` is
  passed. Dry-run prints the planned merges and row counts only.
* **Never delete.** After a successful merge a source is *renamed* to
  ``<name>.legacy-<YYYY-MM-DD>`` — it is never ``rm``'d. The user's history is
  theirs.
* **Idempotent.** Re-running once sources are archived (or absent) is a no-op.
* **Never recreate ``~/tokenpak/monitor.db``.** Migrating away from a legacy
  path must not re-seed a second divergent store.
* **Schema-tolerant.** Writers historically emit slightly different ``requests``
  schemas; rows are merged over the *intersection* of columns and de-duplicated
  on the natural content key (every common column except the autoincrement
  ``id``). A source whose ``requests`` table is missing the minimal key columns
  is reported and skipped — never silently dropped or fabricated.
* **Dangling legacy symlinks** (e.g. ``~/.tokenpak/monitor.db`` -> an absent
  ``~/tokenpak/monitor.db``) are archived-in-place, never followed into a new
  store.

Usage:
    python scripts/migrate_monitor_db.py            # dry-run (default)
    python scripts/migrate_monitor_db.py --apply    # perform the migration
    python scripts/migrate_monitor_db.py --apply --home /custom/HOME

Exit codes:
    0 — OK (no work needed, or migration completed / planned cleanly)
    2 — one or more sources skipped due to schema mismatch (needs attention)
"""

from __future__ import annotations

import argparse
import datetime
import sqlite3
from pathlib import Path
from typing import Callable, List, Optional

CANONICAL_DIRNAME = ".tpk"
REQUESTS_TABLE = "requests"
# Minimal columns a legacy ``requests`` table must carry to be mergeable.
REQUIRED_COLUMNS = ("timestamp",)


# ---------------------------------------------------------------------------
# Path discovery
# ---------------------------------------------------------------------------


def canonical_target(home: Path) -> Path:
    """Canonical monitor.db target (``<home>/.tpk/monitor.db``)."""
    return home / CANONICAL_DIRNAME / "monitor.db"


def legacy_candidates(home: Path) -> List[Path]:
    """Ordered legacy monitor.db locations to probe under *home*."""
    return [
        home / "tokenpak" / "monitor.db",
        home / ".tokenpak" / "monitor.db",
        home / ".tokenpak" / "data" / "monitor.db",
    ]


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return [row[1] for row in cur.fetchall()]


def _has_requests_table(conn: sqlite3.Connection) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (REQUESTS_TABLE,),
    )
    return cur.fetchone() is not None


def _requests_create_sql(conn: sqlite3.Connection) -> Optional[str]:
    cur = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (REQUESTS_TABLE,),
    )
    row = cur.fetchone()
    return row[0] if row else None


def _target_schema_issue(target: Path) -> Optional[str]:
    """Return a fail-closed reason when an existing target is incompatible."""
    try:
        conn = sqlite3.connect(str(target))
        try:
            if not _has_requests_table(conn):
                return "canonical target has no 'requests' table"
            columns = _table_columns(conn, REQUESTS_TABLE)
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"canonical target is unreadable: {exc}"

    missing = [column for column in REQUIRED_COLUMNS if column not in columns]
    if missing:
        return f"canonical target requests table missing columns: {missing}"
    return None


def _row_count(path: Path) -> int:
    try:
        conn = sqlite3.connect(str(path))
        try:
            if not _has_requests_table(conn):
                return 0
            return conn.execute(f"SELECT COUNT(*) FROM {REQUESTS_TABLE}").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return 0


# ---------------------------------------------------------------------------
# Source classification
# ---------------------------------------------------------------------------


class Source:
    """A classified migration source."""

    def __init__(self, path: Path, kind: str, rows: int = 0, note: str = ""):
        self.path = path
        self.kind = kind  # "mergeable" | "stale_symlink" | "mismatch" | "empty"
        self.rows = rows
        self.note = note


def classify_source(path: Path, target: Path) -> Optional[Source]:
    """Classify a candidate path. Returns None if it is not a real source."""
    # Dangling symlink: points at an absent target. Archive in place, never follow.
    if path.is_symlink() and not path.exists():
        return Source(path, "stale_symlink", note=f"dangling -> {path.readlink()}")

    if not path.exists():
        return None

    # Skip the canonical target itself (resolve to compare real files).
    try:
        if path.resolve() == target.resolve():
            return None
    except OSError:
        pass

    try:
        conn = sqlite3.connect(str(path))
    except sqlite3.Error as exc:  # pragma: no cover - corrupt file
        return Source(path, "mismatch", note=f"unreadable: {exc}")
    try:
        if not _has_requests_table(conn):
            return Source(path, "mismatch", note="no 'requests' table")
        cols = _table_columns(conn, REQUESTS_TABLE)
        missing = [c for c in REQUIRED_COLUMNS if c not in cols]
        if missing:
            return Source(
                path, "mismatch", note=f"requests table missing columns: {missing}"
            )
        rows = conn.execute(f"SELECT COUNT(*) FROM {REQUESTS_TABLE}").fetchone()[0]
    finally:
        conn.close()
    return Source(path, "mergeable", rows=rows)


# ---------------------------------------------------------------------------
# Merge engine
# ---------------------------------------------------------------------------


def _ensure_target(target: Path, template_source: Path) -> None:
    """Create the canonical target DB (+ requests schema) if it is absent."""
    if target.exists():
        return
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    src = sqlite3.connect(str(template_source))
    try:
        create_sql = _requests_create_sql(src)
    finally:
        src.close()
    dst = sqlite3.connect(str(target))
    try:
        if create_sql:
            dst.execute(create_sql)
            dst.commit()
    finally:
        dst.close()


def _merge_one(target: Path, source: Path) -> int:
    """Merge *source* requests rows into *target*. Returns rows inserted."""
    tconn = sqlite3.connect(str(target))
    sconn = sqlite3.connect(str(source))
    inserted = 0
    try:
        tcols = _table_columns(tconn, REQUESTS_TABLE)
        scols = _table_columns(sconn, REQUESTS_TABLE)
        # Common content columns, in target order, excluding the surrogate id.
        common = [c for c in tcols if c in scols and c != "id"]
        if not common:
            raise sqlite3.DatabaseError(
                "source and canonical target have no common request columns"
            )

        col_list = ", ".join(common)
        existing = set(
            tuple(r)
            for r in tconn.execute(f"SELECT {col_list} FROM {REQUESTS_TABLE}").fetchall()
        )
        placeholders = ", ".join("?" for _ in common)
        insert_sql = (
            f"INSERT INTO {REQUESTS_TABLE} ({col_list}) VALUES ({placeholders})"
        )
        for row in sconn.execute(f"SELECT {col_list} FROM {REQUESTS_TABLE}"):
            key = tuple(row)
            if key in existing:
                continue
            tconn.execute(insert_sql, row)
            existing.add(key)
            inserted += 1
        tconn.commit()
    finally:
        sconn.close()
        tconn.close()
    return inserted


def _archive(path: Path, today: str) -> Path:
    """Rename *path* to ``<name>.legacy-<today>`` (never delete)."""
    archived = path.with_name(f"{path.name}.legacy-{today}")
    # Avoid clobbering a prior archive on the same day.
    suffix = 1
    while archived.exists():
        archived = path.with_name(f"{path.name}.legacy-{today}.{suffix}")
        suffix += 1
    path.rename(archived)
    return archived


def migrate(
    target: Path,
    candidates: List[Path],
    apply: bool = False,
    today: Optional[str] = None,
    log: Callable[[str], None] = print,
) -> dict:
    """Detect / merge / archive legacy monitor DBs into *target*.

    Returns a summary dict; performs no filesystem mutation unless *apply*.
    """
    today = today or datetime.date.today().isoformat()
    sources = [s for s in (classify_source(p, target) for p in candidates) if s]

    summary = {
        "target": str(target),
        "apply": apply,
        "merged": [],        # list[(path, rows_inserted)]
        "archived": [],      # list[(path, archived_path)]
        "skipped": [],       # list[(path, reason)]
        "rows_inserted": 0,
    }

    mergeable = [s for s in sources if s.kind == "mergeable" and s.rows > 0]
    empties = [s for s in sources if s.kind in ("mergeable", "empty") and s.rows == 0]
    stale = [s for s in sources if s.kind == "stale_symlink"]
    mismatched = [s for s in sources if s.kind == "mismatch"]

    if not mergeable and not stale and not empties:
        log(f"✓ Nothing to migrate — canonical store: {target}")
        for s in mismatched:
            log(f"⚠ skipped (schema mismatch): {s.path} — {s.note}")
            summary["skipped"].append((str(s.path), s.note))
        summary["exit_code"] = 2 if mismatched else 0
        return summary

    log(f"Canonical target: {target}{'' if target.exists() else '  (will be created)'}")
    log(f"Mode: {'APPLY' if apply else 'DRY-RUN (no changes; pass --apply to migrate)'}")

    # Never archive a valid source behind an incompatible existing target. An
    # operator must repair or explicitly replace that target before migration.
    if apply and mergeable and target.exists():
        target_issue = _target_schema_issue(target)
        if target_issue:
            log(f"⚠ skipped: {target} — {target_issue}")
            summary["skipped"].append((str(target), target_issue))
            summary["exit_code"] = 2
            return summary

    # Materialise the target from the first mergeable source if it is absent.
    if apply and mergeable and not target.exists():
        _ensure_target(target, mergeable[0].path)

    for s in mergeable:
        if apply:
            inserted = _merge_one(target, s.path)
            archived = _archive(s.path, today)
            log(f"  merged {inserted}/{s.rows} new rows from {s.path}")
            log(f"  archived source -> {archived}")
            summary["merged"].append((str(s.path), inserted))
            summary["archived"].append((str(s.path), str(archived)))
            summary["rows_inserted"] += inserted
        else:
            log(f"  would merge up to {s.rows} rows from {s.path}")
            log(f"  would archive source -> {s.path}.legacy-{today}")
            summary["merged"].append((str(s.path), s.rows))

    for s in empties:
        # An empty legacy store still gets archived so readers stop probing it.
        if apply:
            archived = _archive(s.path, today)
            log(f"  archived empty source -> {archived}")
            summary["archived"].append((str(s.path), str(archived)))
        else:
            log(f"  would archive empty source -> {s.path}.legacy-{today}")

    for s in stale:
        if apply:
            archived = _archive(s.path, today)
            log(f"  archived stale symlink -> {archived} ({s.note})")
            summary["archived"].append((str(s.path), str(archived)))
        else:
            log(f"  would archive stale symlink -> {s.path}.legacy-{today} ({s.note})")

    for s in mismatched:
        log(f"⚠ skipped (schema mismatch): {s.path} — {s.note}")
        summary["skipped"].append((str(s.path), s.note))

    summary["exit_code"] = 2 if mismatched else 0
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy monitor.db stores into the canonical ~/.tpk/monitor.db",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the migration (default is a no-op dry-run).",
    )
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="Override the home base for path discovery (testing / non-default HOME).",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help="Explicit canonical target path (overrides --home discovery).",
    )
    parser.add_argument(
        "--source",
        action="append",
        type=Path,
        default=None,
        help="Explicit legacy source path (repeatable; overrides --home discovery).",
    )
    args = parser.parse_args(argv)

    home = args.home if args.home is not None else Path.home()
    target = args.target if args.target is not None else canonical_target(home)
    candidates = args.source if args.source is not None else legacy_candidates(home)

    summary = migrate(target, candidates, apply=args.apply)
    return int(summary.get("exit_code", 0))


if __name__ == "__main__":
    raise SystemExit(main())
