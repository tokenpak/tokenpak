# SPDX-License-Identifier: Apache-2.0
"""``RecallStore`` — opens (and lazily migrates) the recall storage database.

Surface
-------
The module exposes:

- ``RecallStore`` — a thin wrapper around an ``sqlite3.Connection`` that
  knows how to open the file, apply PRAGMAs, run any pending migrations,
  and close. The underlying connection is exposed as ``self.conn`` for
  later code to attach further helpers without changing this surface.
- ``RecallStore.upsert_pak(...)`` — metadata-only write path. Inserts a
  new row keyed on ``pak_id`` or replaces the existing row's metadata
  in place. The v2 FTS triggers keep ``paks_fts`` consistent.
- ``RecallStore.list_paks(filters)`` — metadata-only read path. Returns
  a paginated :class:`PakListResult` ordered newest-first. Caps page
  size at ``LIST_LIMIT_MAX``; supports keyset pagination via opaque
  cursor tokens.
- ``RecallStore.get_pak(pak_id)`` — single-row metadata fetch, or ``None``.
- ``open_recall_store(path)`` — convenience factory that resolves the
  default DB location when ``path`` is ``None``.
- ``UpsertResult`` / ``PakRow`` / ``PakListFilters`` / ``PakListResult``
  — NamedTuple records describing inputs / outputs.

This module does *not* expose any ranking, scoring, or full-text
search surface; those are deferred to a later phase.

Default DB path resolution
--------------------------
1. ``TOKENPAK_RECALL_DB`` env var (matches the broader ``tokenpak.core.paths``
   convention).
2. ``~/.tokenpak/companion/recall.db`` (companion subsystem default,
   matching ``journal.db`` placement).

The parent directory is created on first open.

Concurrency
-----------
WAL is enabled so concurrent readers don't block writers. A 5-second
``busy_timeout`` covers transient lock contention. ``foreign_keys`` is
on so cascade behaviour from the schema fires as expected.
"""

from __future__ import annotations

import base64
import logging
import os
import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import Any, NamedTuple, Optional

from tokenpak.companion.recall.migrations import apply_migrations, current_version
from tokenpak.companion.recall.schema import SCHEMA_VERSION

_DEFAULT_REL_PATH = ".tokenpak/companion/recall.db"
_ENV_VAR = "TOKENPAK_RECALL_DB"

_log = logging.getLogger(__name__)


# Required (non-empty, non-whitespace) fields on ``upsert_pak``.
_REQUIRED_FIELDS: tuple[str, ...] = (
    "pak_id",
    "pak_type",
    "source_type",
    "authority",
    "title",
    "content_hash",
)


# Pagination bounds for ``list_paks``. The default and the maximum are the
# same: callers asking for more than ``LIST_LIMIT_MAX`` rows are silently
# clamped, and callers passing nothing get the same page size.
LIST_LIMIT_DEFAULT: int = 100
LIST_LIMIT_MAX: int = 100


# Column order used by ``list_paks`` and ``get_pak`` — kept aligned with
# the :class:`PakRow` field order so the rowset can be splat into the
# NamedTuple constructor positionally.
_PAK_COLUMNS: str = (
    "pak_id, pak_type, project, topic, source_type, authority, "
    "title, summary, content_hash, created_at, updated_at, superseded_by"
)


class PakRow(NamedTuple):
    """A single ``paks`` row, as read by :meth:`RecallStore.list_paks` /
    :meth:`RecallStore.get_pak`.

    Field order matches :data:`_PAK_COLUMNS` so a positional unpack of
    the SQLite row builds the record without translation. ``project``,
    ``topic``, and ``superseded_by`` are nullable in the schema; the
    rest are required.
    """

    pak_id: str
    pak_type: str
    project: Optional[str]
    topic: Optional[str]
    source_type: str
    authority: str
    title: str
    summary: str
    content_hash: str
    created_at: str
    updated_at: str
    superseded_by: Optional[str]


class PakListFilters(NamedTuple):
    """Inputs to :meth:`RecallStore.list_paks`.

    Attributes:
        project: When set, restrict rows to ``project = <value>``. Byte-literal
            match — no alias expansion.
        pak_type: When set, restrict rows to ``pak_type = <value>``. Byte-literal
            match — no alias expansion.
        limit: Requested page size. Clamped to ``[1, LIST_LIMIT_MAX]``.
        cursor: Opaque pagination cursor returned by a previous call. ``None``
            on the first page.
    """

    project: Optional[str] = None
    pak_type: Optional[str] = None
    limit: int = LIST_LIMIT_DEFAULT
    cursor: Optional[str] = None


class PakListResult(NamedTuple):
    """Outputs from :meth:`RecallStore.list_paks`.

    Attributes:
        items: The rows on this page, newest-first.
        next_cursor: Opaque cursor to pass to the next call, or ``None``
            when no more rows match the filters.
        limit: The effective page size used (after clamping).
        truncated: ``True`` if more rows match the filters than this page
            returned. Always implies ``next_cursor`` is not ``None`` when
            ``items`` is non-empty.
    """

    items: list[PakRow]
    next_cursor: Optional[str]
    limit: int
    truncated: bool


class ReasonCodeEntry(NamedTuple):
    """One row in ``pak_reason_codes``.

    The ``reason_code`` string is a registry-defined enum value (see the
    sibling ``tokenpak/registry`` repo, ``schemas/tip/pak-reason-codes-v1.schema.json``);
    the runtime does not enforce the catalogue here — additive new codes
    land via a separate Class B amendment and the JSON Schema validator.

    Attributes:
        reason_code: Snake_case enum identifier (e.g. ``"current_task"``).
        weight: Caller-supplied weight in ``[0.0, 1.0]``. The CHECK
            constraint at the SQL layer rejects out-of-range values.
            Defaults to ``1.0`` if omitted.
    """

    reason_code: str
    weight: float = 1.0


class RiskFlagEntry(NamedTuple):
    """One row in ``pak_risk_flags``.

    The ``risk_flag`` string is a registry-defined enum value (see the
    sibling ``tokenpak/registry`` repo, ``schemas/tip/pak-risk-flags-v1.schema.json``).
    ``severity`` is one of ``"info"``, ``"warn"``, or ``"block"``.

    OSS callers may expose, persist, inspect, export, and validate
    ``severity="block"`` data; OSS does *not* enforce a Pro-style
    assembly refusal path on it — that enforcement is Pro Phase 3
    Context Package builder behaviour (OSS = data plane,
    Pro = enforcement).

    Attributes:
        risk_flag: Snake_case enum identifier (e.g. ``"mandatory_context_missing"``).
        severity: One of ``{"info", "warn", "block"}``. The CHECK
            constraint at the SQL layer rejects other values.
    """

    risk_flag: str
    severity: str


# Severity values accepted by ``set_pak_risk_flags``. The constant exists
# so tests / callers can assert against the same string set the schema
# CHECK constraint enforces. Discovery-style: callers reading this list
# (or the registry JSON) drive their own validation; the table itself
# stays the source of truth.
RISK_FLAG_SEVERITIES: frozenset[str] = frozenset({"info", "warn", "block"})


class UpsertResult(NamedTuple):
    """The outcome of a single :meth:`RecallStore.upsert_pak` call.

    Attributes:
        pak_id: The stable identity the row was written under.
        inserted: ``True`` if the row was newly created; ``False`` if an
            existing row was updated in place.
        body_changed: ``True`` iff an existing row's ``content_hash``
            differed from the incoming value. Always ``False`` when
            ``inserted`` is ``True``.
    """

    pak_id: str
    inserted: bool
    body_changed: bool


def _utc_now_iso8601() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def default_recall_db_path() -> Path:
    """Resolve the default recall DB path, honouring the env override."""
    if override := os.environ.get(_ENV_VAR):
        return Path(override).expanduser()
    return Path.home() / _DEFAULT_REL_PATH


class RecallStore:
    """Recall storage handle.

    Open via :meth:`RecallStore.open` (or the module-level
    :func:`open_recall_store`). The instance is a context manager so
    callers can ``with RecallStore.open() as store: ...`` and have the
    connection closed deterministically.
    """

    def __init__(self, conn: sqlite3.Connection, path: Path) -> None:
        # Direct construction is allowed but ``open`` is the supported path.
        self._conn = conn
        self._path = path

    @classmethod
    def open(cls, path: Optional[Path] = None) -> "RecallStore":
        """Open (and migrate if needed) the recall store at ``path``.

        If ``path`` is ``None``, the default location is used.
        """
        resolved = path if path is not None else default_recall_db_path()
        resolved.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(str(resolved))
        try:
            cls._apply_pragmas(conn)
            apply_migrations(conn)
        except Exception:
            conn.close()
            raise

        return cls(conn=conn, path=resolved)

    @staticmethod
    def _apply_pragmas(conn: sqlite3.Connection) -> None:
        # ``execute`` is fine — these are session-level pragmas, not statements
        # that need to be inside a transaction.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")

    @property
    def conn(self) -> sqlite3.Connection:
        """The underlying SQLite connection.

        Later PRs build their query/write helpers on top of this. PR 1
        exposes the connection as the only public surface beyond open/close.
        """
        return self._conn

    @property
    def path(self) -> Path:
        """The filesystem path the store was opened at."""
        return self._path

    @property
    def schema_version(self) -> int:
        """The schema version currently applied to the underlying DB."""
        return current_version(self._conn)

    # Metadata write surface ------------------------------------------------

    def upsert_pak(
        self,
        *,
        pak_id: str,
        pak_type: str,
        source_type: str,
        authority: str,
        title: str,
        content_hash: str,
        summary: str = "",
        project: Optional[str] = None,
        topic: Optional[str] = None,
        superseded_by: Optional[str] = None,
        now: Optional[str] = None,
    ) -> UpsertResult:
        """Insert or update a single Pak metadata row.

        The write is keyed on ``pak_id`` — that is the stable external
        identity callers use to address a Pak. Behaviour:

        - **No existing row** → INSERT. ``created_at`` and ``updated_at``
          are both set to ``now`` (or the current UTC time if ``now``
          is omitted).
        - **Existing row, identical ``content_hash``** → metadata-only
          UPDATE. ``created_at`` is preserved; ``updated_at`` is bumped.
        - **Existing row, different ``content_hash``** → UPDATE that
          replaces metadata and bumps ``content_hash`` / ``updated_at``.
          A warning is emitted (``logging.WARNING``) because the source
          object changed under the same identity; downstream audit /
          versioning lands in a later PR.

        Required (non-empty) fields are listed in ``_REQUIRED_FIELDS``;
        ``ValueError`` is raised if any are missing or all-whitespace.

        The FTS5 shadow is kept consistent by the v2 triggers — callers
        do not write to ``paks_fts`` directly.

        Parameters:
            pak_id: Stable Pak identity (e.g. ``vault://block/foo``).
            pak_type: Kind of Pak (e.g. ``vault``, ``code``).
            source_type: Source classification (e.g. ``code``, ``doc``).
            authority: Authority label for the source.
            title: Short heading; indexed in FTS.
            content_hash: Hex digest of the underlying body bytes.
            summary: Short summary; indexed in FTS. Defaults to ``""``.
            project: Optional project tag.
            topic: Optional topic tag.
            superseded_by: Optional ``pak_id`` of a superseding row.
            now: Optional ISO-8601 UTC string for deterministic testing.

        Returns:
            :class:`UpsertResult` describing what happened.

        Raises:
            ValueError: One of the required fields was missing /
                empty / whitespace-only.
            sqlite3.IntegrityError: A FK constraint failed (e.g. an
                unknown ``superseded_by``).
        """
        # Validation -------------------------------------------------------
        values = {
            "pak_id": pak_id,
            "pak_type": pak_type,
            "source_type": source_type,
            "authority": authority,
            "title": title,
            "content_hash": content_hash,
        }
        for name in _REQUIRED_FIELDS:
            v = values[name]
            if v is None or not isinstance(v, str) or not v.strip():
                raise ValueError(f"upsert_pak: required field {name!r} must be a non-empty string")
        if summary is None:
            summary = ""

        ts = now if now is not None else _utc_now_iso8601()
        conn = self._conn

        # Transaction ------------------------------------------------------
        # ``apply_migrations`` leaves the connection in autocommit mode
        # (``isolation_level=None``); make BEGIN explicit so the lookup +
        # write happen atomically and the FTS triggers fire as one unit.
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT content_hash FROM paks WHERE pak_id = ?",
                (pak_id,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO paks ("
                    "pak_id, pak_type, project, topic, source_type, authority, "
                    "title, summary, content_hash, created_at, updated_at, superseded_by"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        pak_id,
                        pak_type,
                        project,
                        topic,
                        source_type,
                        authority,
                        title,
                        summary,
                        content_hash,
                        ts,
                        ts,
                        superseded_by,
                    ),
                )
                inserted = True
                body_changed = False
            else:
                old_hash = row[0]
                body_changed = old_hash != content_hash
                conn.execute(
                    "UPDATE paks SET "
                    "pak_type = ?, project = ?, topic = ?, source_type = ?, "
                    "authority = ?, title = ?, summary = ?, content_hash = ?, "
                    "updated_at = ?, superseded_by = ? "
                    "WHERE pak_id = ?",
                    (
                        pak_type,
                        project,
                        topic,
                        source_type,
                        authority,
                        title,
                        summary,
                        content_hash,
                        ts,
                        superseded_by,
                        pak_id,
                    ),
                )
                inserted = False
                if body_changed:
                    _log.warning(
                        "recall.upsert_pak: content_hash changed for pak_id=%s "
                        "(old=%s new=%s); replacing metadata in place.",
                        pak_id,
                        _short_hash(old_hash),
                        _short_hash(content_hash),
                    )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise

        return UpsertResult(pak_id=pak_id, inserted=inserted, body_changed=body_changed)

    # Metadata read surface -------------------------------------------------

    def list_paks(self, filters: Optional[PakListFilters] = None) -> PakListResult:
        """Return a paginated page of ``paks`` rows, newest-first.

        Page ordering is ``(updated_at DESC, pak_id DESC)`` — the latter is
        the tiebreak when two rows share an ``updated_at``. Pagination is
        keyset-based: the cursor encodes the last row's ``(updated_at, pak_id)``
        tuple so the next page can resume without offsets (which scale poorly
        and skip rows under concurrent writes).

        Filters compose with ``AND``. ``project`` and ``pak_type`` are
        byte-literal — the schema column value must equal the filter
        verbatim. No alias / casefold / normalisation is applied.

        Parameters:
            filters: A :class:`PakListFilters` record. If ``None``, defaults
                are used (no filters, full page).

        Returns:
            :class:`PakListResult`. ``items`` is a possibly-empty list of
            :class:`PakRow`. ``truncated`` is ``True`` iff more rows match
            than fit on this page; in that case ``next_cursor`` is the
            cursor to use to fetch the next page. The ``limit`` field
            reports the effective limit after clamping.

        Raises:
            ValueError: ``filters.cursor`` is set and not a valid opaque
                cursor.
        """
        f = filters if filters is not None else PakListFilters()

        # Clamp limit to [1, LIST_LIMIT_MAX]. Non-positive values fall back
        # to the default rather than rejecting — defensive.
        raw_limit = f.limit if isinstance(f.limit, int) and f.limit > 0 else LIST_LIMIT_DEFAULT
        limit = min(raw_limit, LIST_LIMIT_MAX)

        where_clauses: list[str] = []
        params: list[Any] = []
        if f.project is not None:
            where_clauses.append("project = ?")
            params.append(f.project)
        if f.pak_type is not None:
            where_clauses.append("pak_type = ?")
            params.append(f.pak_type)
        if f.cursor:
            cur_ts, cur_id = _decode_cursor(f.cursor)
            # Keyset for ORDER BY updated_at DESC, pak_id DESC: the next page
            # starts strictly after (cur_ts, cur_id) in that DESC ordering —
            # i.e. (updated_at, pak_id) is less than the cursor's tuple.
            where_clauses.append("(updated_at < ? OR (updated_at = ? AND pak_id < ?))")
            params.extend([cur_ts, cur_ts, cur_id])

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        sql = (
            f"SELECT {_PAK_COLUMNS} FROM paks "
            f"{where_sql} "
            "ORDER BY updated_at DESC, pak_id DESC "
            "LIMIT ?"
        )
        # Fetch one more than the limit so we can detect truncation without
        # a separate COUNT query.
        params.append(limit + 1)
        rows = self._conn.execute(sql, params).fetchall()
        truncated = len(rows) > limit
        if truncated:
            rows = rows[:limit]
        items: list[PakRow] = [PakRow(*row) for row in rows]
        next_cursor: Optional[str] = None
        if truncated and items:
            last = items[-1]
            next_cursor = _encode_cursor(last.updated_at, last.pak_id)
        return PakListResult(
            items=items,
            next_cursor=next_cursor,
            limit=limit,
            truncated=truncated,
        )

    def get_pak(self, pak_id: str) -> Optional[PakRow]:
        """Return the ``paks`` row for ``pak_id``, or ``None`` if absent.

        Whitespace-only or empty ``pak_id`` returns ``None`` rather than
        running the SELECT — the schema rejects empty primary keys at
        write time, so a lookup on one is unambiguously a miss.
        """
        if not pak_id or not pak_id.strip():
            return None
        row = self._conn.execute(
            f"SELECT {_PAK_COLUMNS} FROM paks WHERE pak_id = ?",
            (pak_id,),
        ).fetchone()
        if row is None:
            return None
        return PakRow(*row)

    # Reason-code + risk-flag join surface ---------------------------------

    def set_pak_reason_codes(
        self,
        pak_id: str,
        codes: Sequence[ReasonCodeEntry],
        *,
        now: Optional[str] = None,
    ) -> None:
        """Replace the reason-code set for ``pak_id``.

        Idempotent under ``=`` semantics: the operation is DELETE-then-INSERT
        inside one transaction, so calling twice with the same ``codes`` set
        leaves the table in the same final state and emits no row-count
        delta.

        Duplicate ``reason_code`` values within ``codes`` are rejected with
        ``ValueError`` before any write — the (``pak_id``, ``reason_code``)
        primary key would otherwise raise mid-transaction, masking the
        caller's intent.

        Parameters:
            pak_id: An existing row in ``paks``. Foreign-key enforcement
                (``PRAGMA foreign_keys=ON`` set in :meth:`_apply_pragmas`)
                raises ``sqlite3.IntegrityError`` if the parent row is absent.
            codes: Sequence of :class:`ReasonCodeEntry`. May be empty —
                that clears any previously-stored codes for ``pak_id``.
            now: Optional ISO-8601 UTC string for ``created_at``. Defaults
                to the current UTC time.

        Raises:
            ValueError: ``pak_id`` is empty/whitespace, an entry's
                ``reason_code`` is empty/whitespace, an entry's ``weight``
                falls outside ``[0.0, 1.0]``, or ``codes`` contains
                duplicate ``reason_code`` values.
            sqlite3.IntegrityError: ``pak_id`` does not reference a row
                in ``paks`` (foreign-key violation).
        """
        if not pak_id or not pak_id.strip():
            raise ValueError("set_pak_reason_codes: pak_id must be a non-empty string")
        seen: set[str] = set()
        cleaned: list[tuple[str, float]] = []
        for entry in codes:
            code = entry.reason_code
            weight = entry.weight
            if not isinstance(code, str) or not code.strip():
                raise ValueError("set_pak_reason_codes: reason_code must be a non-empty string")
            if not isinstance(weight, (int, float)) or not (0.0 <= float(weight) <= 1.0):
                raise ValueError(f"set_pak_reason_codes: weight for {code!r} must be in [0.0, 1.0]")
            if code in seen:
                raise ValueError(f"set_pak_reason_codes: duplicate reason_code {code!r} in input")
            seen.add(code)
            cleaned.append((code, float(weight)))

        ts = now if now is not None else _utc_now_iso8601()
        conn = self._conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM pak_reason_codes WHERE pak_id = ?", (pak_id,))
            if cleaned:
                conn.executemany(
                    "INSERT INTO pak_reason_codes "
                    "(pak_id, reason_code, weight, created_at) VALUES (?, ?, ?, ?)",
                    [(pak_id, code, weight, ts) for code, weight in cleaned],
                )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise

    def get_pak_reason_codes(self, pak_id: str) -> list[ReasonCodeEntry]:
        """Return the reason codes attached to ``pak_id``.

        Returns the codes in ascending order by ``reason_code`` so the
        caller sees a stable ordering across calls. The result is a list
        of :class:`ReasonCodeEntry`; an unknown ``pak_id`` returns ``[]``.

        Whitespace-only or empty ``pak_id`` returns ``[]`` without a
        round-trip to the DB.
        """
        if not pak_id or not pak_id.strip():
            return []
        rows = self._conn.execute(
            "SELECT reason_code, weight FROM pak_reason_codes "
            "WHERE pak_id = ? ORDER BY reason_code ASC",
            (pak_id,),
        ).fetchall()
        return [ReasonCodeEntry(reason_code=r[0], weight=float(r[1])) for r in rows]

    def set_pak_risk_flags(
        self,
        pak_id: str,
        flags: Sequence[RiskFlagEntry],
        *,
        now: Optional[str] = None,
    ) -> None:
        """Replace the risk-flag set for ``pak_id``.

        Mirrors :meth:`set_pak_reason_codes` — idempotent DELETE-then-INSERT
        under one transaction. Duplicate ``risk_flag`` values within
        ``flags`` are rejected with ``ValueError`` before write.

        OSS exposes / persists / inspects / exports / validates
        ``severity="block"`` data. OSS does *not* implement an
        assembly-refusal path on it (that lives in the Pro Phase 3
        Context Package builder — OSS = data plane, Pro = enforcement).

        Parameters:
            pak_id: An existing row in ``paks``.
            flags: Sequence of :class:`RiskFlagEntry`. May be empty.
            now: Optional ISO-8601 UTC string for ``created_at``.

        Raises:
            ValueError: ``pak_id`` is empty/whitespace, an entry's
                ``risk_flag`` is empty/whitespace, an entry's ``severity``
                is not in ``{"info", "warn", "block"}``, or ``flags``
                contains duplicate ``risk_flag`` values.
            sqlite3.IntegrityError: ``pak_id`` does not reference a row
                in ``paks``.
        """
        if not pak_id or not pak_id.strip():
            raise ValueError("set_pak_risk_flags: pak_id must be a non-empty string")
        seen: set[str] = set()
        cleaned: list[tuple[str, str]] = []
        for entry in flags:
            flag = entry.risk_flag
            severity = entry.severity
            if not isinstance(flag, str) or not flag.strip():
                raise ValueError("set_pak_risk_flags: risk_flag must be a non-empty string")
            if severity not in RISK_FLAG_SEVERITIES:
                raise ValueError(
                    f"set_pak_risk_flags: severity for {flag!r} must be one of "
                    f"{sorted(RISK_FLAG_SEVERITIES)!r}, got {severity!r}"
                )
            if flag in seen:
                raise ValueError(f"set_pak_risk_flags: duplicate risk_flag {flag!r} in input")
            seen.add(flag)
            cleaned.append((flag, severity))

        ts = now if now is not None else _utc_now_iso8601()
        conn = self._conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM pak_risk_flags WHERE pak_id = ?", (pak_id,))
            if cleaned:
                conn.executemany(
                    "INSERT INTO pak_risk_flags "
                    "(pak_id, risk_flag, severity, created_at) VALUES (?, ?, ?, ?)",
                    [(pak_id, flag, severity, ts) for flag, severity in cleaned],
                )
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise

    def get_pak_risk_flags(self, pak_id: str) -> list[RiskFlagEntry]:
        """Return the risk flags attached to ``pak_id``.

        Returns flags ordered by ``risk_flag`` ascending. An unknown
        ``pak_id`` (or empty/whitespace) returns ``[]``.
        """
        if not pak_id or not pak_id.strip():
            return []
        rows = self._conn.execute(
            "SELECT risk_flag, severity FROM pak_risk_flags "
            "WHERE pak_id = ? ORDER BY risk_flag ASC",
            (pak_id,),
        ).fetchall()
        return [RiskFlagEntry(risk_flag=r[0], severity=r[1]) for r in rows]

    def close(self) -> None:
        """Close the underlying connection.

        Safe to call multiple times.
        """
        try:
            self._conn.close()
        except sqlite3.ProgrammingError:
            # Already closed — fine.
            pass

    # Context-manager sugar -------------------------------------------------

    def __enter__(self) -> "RecallStore":
        return self

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()


def open_recall_store(path: Optional[Path] = None) -> RecallStore:
    """Module-level convenience wrapper around :meth:`RecallStore.open`."""
    return RecallStore.open(path)


def _short_hash(value: object) -> str:
    """Render a hash for log lines without leaking the whole digest."""
    s = "" if value is None else str(value)
    return s[:12] + ("…" if len(s) > 12 else "")


# Cursor encode/decode --------------------------------------------------------
#
# The cursor is an opaque token to the caller: the only contract is that
# passing a cursor returned by a previous call yields the rows that would
# have come on the next page. The encoding chosen here is base64 of
# ``"<updated_at>|<pak_id>"`` — short, URL-safe, and easy to decode in
# tests without exposing the schema-level keyset detail.

_CURSOR_SEP = "|"


def _encode_cursor(updated_at: str, pak_id: str) -> str:
    raw = f"{updated_at}{_CURSOR_SEP}{pak_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    if not isinstance(cursor, str) or not cursor:
        raise ValueError("invalid cursor: empty or non-string")
    # Re-pad — ``urlsafe_b64encode`` may have produced a token without
    # trailing ``=`` after the ``rstrip`` step above.
    pad = (-len(cursor)) % 4
    padded = cursor + ("=" * pad)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f"invalid cursor: {exc!r}") from exc
    parts = raw.split(_CURSOR_SEP, 1)
    if len(parts) != 2:
        raise ValueError("invalid cursor: missing separator")
    return parts[0], parts[1]


__all__ = [
    "LIST_LIMIT_DEFAULT",
    "LIST_LIMIT_MAX",
    "PakListFilters",
    "PakListResult",
    "PakRow",
    "ReasonCodeEntry",
    "RecallStore",
    "RISK_FLAG_SEVERITIES",
    "RiskFlagEntry",
    "UpsertResult",
    "default_recall_db_path",
    "open_recall_store",
    "SCHEMA_VERSION",
]
