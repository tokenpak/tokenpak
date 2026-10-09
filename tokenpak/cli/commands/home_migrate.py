# SPDX-License-Identifier: Apache-2.0
"""``tokenpak home migrate`` — consolidate a split home, merging and never overwriting.

An install can end up with state in both ``~/.tokenpak`` (legacy) and ``~/.tpk``
(canonical): a companion that started writing the canonical home while the
proxy stayed on the legacy one. This module merges the legacy home into the
canonical one.

Rules, in one place:

* The product-state set comes from :mod:`tokenpak._paths`. Anything else in the
  legacy home is reported as left in place and is never read or copied.
* The legacy tree is never written. SQLite sources are snapshotted into a
  private temporary directory first, so a database with un-checkpointed WAL
  rows is read completely and no ``-wal``/``-shm`` file is touched in place.
* Symbolic links are never followed or copied through; they are recreated as
  links with the same target.
* Nothing is copied byte-for-byte onto a live database. A missing database is
  created from a snapshot through the SQLite backup API; an existing one gets
  rows merged in a transaction.
* Every target file is backed up under ``<canonical>/backups/home-migrate-<utc>/``
  immediately before it is first modified.
* The default is a dry run. ``--apply`` writes, and only while TokenPak is idle.
"""

from __future__ import annotations

import filecmp
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

from tokenpak.cli.exit_codes import EXIT_BUSY, EXIT_FAILURE, EXIT_OK

COPY = "COPY"
MERGE = "MERGE"
SKIP_IDENTICAL = "SKIP-identical"
SKIP_CANONICAL = "SKIP-canonical"
SKIP_LOG = "SKIP-log"
SKIP_RUNTIME = "SKIP-runtime"
KEEP_LEGACY = "KEEP-legacy-only"
CONFLICT = "CONFLICT"
LINK = "LINK"

_SQLITE_MAGIC = b"SQLite format 3\x00"
_SQLITE_SUFFIXES = (".db", ".sqlite", ".sqlite3")
_BATCH = 5000
_LICENSE = "license.json"
_LEGACY_SUFFIX = ".legacy"


class _Busy(Exception):
    """Something is using TokenPak or a target database right now."""


@dataclass
class Item:
    """One line of the plan."""

    path: str
    action: str
    detail: str = ""
    rows: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "action": self.action, "detail": self.detail, "rows": self.rows}


@dataclass
class _Ctx:
    legacy: Path
    canonical: Path
    apply: bool
    scratch: Path
    stamp: str
    items: list[Item] = field(default_factory=list)
    backed_up: set[Path] = field(default_factory=set)
    counter: int = 0

    @property
    def backup_root(self) -> Path:
        return self.canonical / "backups" / f"home-migrate-{self.stamp}"

    def tmp(self, suffix: str = "") -> Path:
        self.counter += 1
        return self.scratch / f"{self.counter}{suffix}"


# --------------------------------------------------------------------------
# File helpers
# --------------------------------------------------------------------------


def _private_mode(path: Path) -> int:
    """Source mode with group and other bits cleared (the home is private)."""
    try:
        return stat.S_IMODE(path.stat().st_mode) & 0o700
    except OSError:
        return 0o600


def _mkdirs(path: Path, stop: Path) -> None:
    """Create *path* and any missing parents with mode 0700."""
    todo: list[Path] = []
    cur = path
    while not cur.exists():
        todo.append(cur)
        cur = cur.parent
    for d in reversed(todo):
        d.mkdir(mode=0o700)
        os.chmod(d, 0o700)


def _is_sqlite(path: Path) -> bool:
    if not path.name.endswith(_SQLITE_SUFFIXES):
        return False
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _snapshot(src: Path, ctx: _Ctx) -> Path:
    """A consistent private copy of a SQLite database, WAL rows included.

    Copies the database and its ``-wal`` into the scratch directory (never the
    ``-shm``, which is rebuilt), opens that copy, and backs it up into a clean
    single file. The source files are only read.
    """
    work = ctx.tmp("-snap")
    work.mkdir()
    copy = work / src.name
    for _ in range(3):
        before = _sizes(src)
        shutil.copyfile(src, copy)
        wal = Path(str(src) + "-wal")
        if wal.exists():
            shutil.copyfile(wal, Path(str(copy) + "-wal"))
        if before == _sizes(src):
            break
    else:
        raise _Busy(f"{src.name} is changing while being read")
    out = work / "snapshot.db"
    source = sqlite3.connect(str(copy))
    try:
        target = sqlite3.connect(str(out))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return out


def _sizes(src: Path) -> tuple[int, int]:
    wal = Path(str(src) + "-wal")
    return (src.stat().st_size, wal.stat().st_size if wal.exists() else -1)


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------


def _backup(dest: Path, ctx: _Ctx) -> None:
    """Back up *dest* (once) before it is modified. Apply mode only."""
    if not ctx.apply or dest in ctx.backed_up or not dest.exists():
        return
    rel = dest.relative_to(ctx.canonical)
    target = ctx.backup_root / rel
    _mkdirs(target.parent, ctx.canonical)
    if _is_sqlite(dest):
        src = sqlite3.connect(str(dest), timeout=1.0)
        try:
            out = sqlite3.connect(str(target))
            try:
                src.backup(out)
            finally:
                out.close()
        finally:
            src.close()
    else:
        shutil.copy2(dest, target)
    os.chmod(target, _private_mode(dest) or 0o600)
    ctx.backed_up.add(dest)


# --------------------------------------------------------------------------
# SQLite merge
# --------------------------------------------------------------------------


def _plural(n: int, word: str) -> str:
    return word if n == 1 else word + "s"


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _tables(conn: sqlite3.Connection, schema: str = "main") -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    rows = conn.execute(
        f"SELECT name, sql FROM {schema}.sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    for name, sql in rows:
        info = conn.execute(f"PRAGMA {schema}.table_info({_quote(name)})").fetchall()
        unique = any(
            r[2] for r in conn.execute(f"PRAGMA {schema}.index_list({_quote(name)})").fetchall()
        )
        out[name] = {
            "virtual": "VIRTUAL" in (sql or "").upper().split("(")[0],
            "without_rowid": "WITHOUT ROWID" in (sql or "").upper(),
            "cols": [(r[1], (r[2] or "").upper(), r[3], r[5]) for r in info],
            "unique": unique,
        }
    return out


def _alias_col(meta: dict[str, Any]) -> Optional[str]:
    """The rowid-alias column (``INTEGER PRIMARY KEY``), if the table has one."""
    pks = [c for c in meta["cols"] if c[3]]
    if len(pks) == 1 and pks[0][1] == "INTEGER" and not meta["without_rowid"]:
        return pks[0][0]
    return None


def _row_key(cols: list[str], row: tuple[Any, ...]) -> tuple[Any, ...]:
    """Dedupe key: ``(session, type, content_hash)`` when hashed, else the whole row."""
    if "content_hash" in cols:
        value = row[cols.index("content_hash")]
        if value is not None:
            parts = [row[cols.index(c)] for c in ("session_id", "entry_type") if c in cols]
            return ("hash", *parts, value)
    return ("row", hashlib.blake2b(repr(row).encode(), digest_size=16).digest())


def _merge_db(snapshot: Path, dest: sqlite3.Connection) -> tuple[Optional[str], int]:
    """Merge *snapshot* rows into the open *dest*. Returns ``(conflict, new_rows)``.

    Schema is compared before anything is written; a mismatch writes nothing.
    """
    src = sqlite3.connect(str(snapshot))
    try:
        theirs = _tables(src)
        ours = _tables(dest)
        if any(m["virtual"] for m in theirs.values()):
            return _VIRTUAL, 0
        if set(theirs) != set(ours):
            return "table set differs", 0
        for name, meta in theirs.items():
            if (
                meta["cols"] != ours[name]["cols"]
                or meta["without_rowid"] != ours[name]["without_rowid"]
            ):
                return f"schema differs in table {name}", 0
        dest.execute("ATTACH DATABASE ? AS _legacy", (str(snapshot),))
        try:
            dest.execute("BEGIN IMMEDIATE")
            total = 0
            try:
                for name, meta in theirs.items():
                    total += _merge_table(src, dest, name, meta)
                dest.execute("COMMIT")
            except BaseException:
                dest.execute("ROLLBACK")
                raise
        finally:
            dest.execute("DETACH DATABASE _legacy")
        return None, total
    finally:
        src.close()


def _merge_table(
    src: sqlite3.Connection, dest: sqlite3.Connection, name: str, meta: dict[str, Any]
) -> int:
    alias = _alias_col(meta)
    cols = [c[0] for c in meta["cols"] if c[0] != alias]
    if not cols:
        return 0
    quoted = ", ".join(_quote(c) for c in cols)
    before = dest.total_changes
    if alias is None and (any(c[3] for c in meta["cols"]) or meta["unique"]):
        # Natural primary key or unique constraint: the database dedupes.
        dest.execute(
            f"INSERT OR IGNORE INTO main.{_quote(name)} ({quoted}) "
            f"SELECT {quoted} FROM _legacy.{_quote(name)}"
        )
        return dest.total_changes - before
    # Autoincrement id (collides across homes) or no key at all: dedupe by
    # content hash where present, else by the whole row, and let ids be reassigned.
    seen: set[tuple[Any, ...]] = set()
    for row in dest.execute(f"SELECT {quoted} FROM {_quote(name)}"):
        seen.add(_row_key(cols, row))
    insert = (
        f"INSERT OR IGNORE INTO {_quote(name)} ({quoted}) VALUES ({', '.join('?' * len(cols))})"
    )
    cursor = src.execute(
        f"SELECT {quoted} FROM {_quote(name)}" + (f" ORDER BY {_quote(alias)}" if alias else "")
    )
    while True:
        batch = cursor.fetchmany(_BATCH)
        if not batch:
            break
        fresh = []
        for row in batch:
            key = _row_key(cols, row)
            if key not in seen:
                seen.add(key)
                fresh.append(row)
        if fresh:
            dest.executemany(insert, fresh)
    return dest.total_changes - before


_VIRTUAL = "virtual tables cannot be merged"


def _digest(conn: sqlite3.Connection) -> dict[str, tuple[Any, ...]]:
    """Order-independent content digest of every real table (shadow tables included)."""
    out: dict[str, tuple[Any, ...]] = {}
    for name, meta in _tables(conn).items():
        if meta["virtual"]:
            continue  # its content lives in the shadow tables, which are real tables
        hashes = sorted(
            hashlib.blake2b(repr(row).encode(), digest_size=16).digest()
            for row in conn.execute(f"SELECT * FROM {_quote(name)}")
        )
        top = hashlib.blake2b(b"".join(hashes), digest_size=16).digest()
        out[name] = (tuple(c[0] for c in meta["cols"]), len(hashes), top)
    return out


def _digest_path(path: Path) -> dict[str, tuple[Any, ...]]:
    conn = sqlite3.connect(str(path))
    try:
        return _digest(conn)
    finally:
        conn.close()


def _virtual_table_db(snap: Path, dest: Path, rel: str, ctx: _Ctx, same: bool) -> Item:
    """A database with virtual tables is not merged row by row.

    Identical content is already migrated (SKIP-identical). Otherwise the
    canonical database is kept and the legacy one is saved beside it, as for any
    other file that differs.
    """
    if same:
        return Item(rel, SKIP_IDENTICAL, "same content")
    side = dest.with_name(dest.name + _LEGACY_SUFFIX)
    detail = f"{_VIRTUAL}; canonical kept, legacy copy beside it as {side.name}"
    if side.exists() and _is_sqlite_content(side) and _digest_path(side) == _digest_path(snap):
        return Item(rel, CONFLICT, detail)
    if ctx.apply:
        _backup(side, ctx)
        reader = sqlite3.connect(str(snap))
        try:
            tmp = side.with_name(f".{side.name}.migrate-tmp")
            writer = sqlite3.connect(str(tmp))
            try:
                reader.backup(writer)
            finally:
                writer.close()
        finally:
            reader.close()
        os.chmod(tmp, 0o600)
        os.replace(tmp, side)
    return Item(rel, CONFLICT, detail)


def _is_sqlite_content(path: Path) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(16) == _SQLITE_MAGIC
    except OSError:
        return False


def _check_unlocked(path: Path) -> None:
    """Raise :class:`_Busy` when another connection holds a write lock on *path*."""
    try:
        conn = sqlite3.connect(str(path), timeout=0.3)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("ROLLBACK")
        finally:
            conn.close()
    except sqlite3.OperationalError as error:
        raise _Busy(f"{path.name} is locked ({error})") from error


def _process_sqlite(src: Path, dest: Path, rel: str, ctx: _Ctx) -> Item:
    try:
        snap = _snapshot(src, ctx)
    except sqlite3.DatabaseError as error:
        return Item(rel, CONFLICT, f"legacy database is unreadable ({error})")
    if not dest.exists():
        if ctx.apply:
            _mkdirs(dest.parent, ctx.canonical)
            tmp = dest.with_name(f".{dest.name}.migrate-tmp")
            reader = sqlite3.connect(str(snap))
            try:
                writer = sqlite3.connect(str(tmp))
                try:
                    reader.backup(writer)
                finally:
                    writer.close()
            finally:
                reader.close()
            os.chmod(tmp, _private_mode(src))
            os.replace(tmp, dest)
        count = _count_rows(snap)
        return Item(rel, COPY, f"{count} {_plural(count, 'row')}", count)
    if not _is_sqlite(dest):
        return Item(rel, CONFLICT, "canonical file is not a SQLite database")
    if ctx.apply:
        _check_unlocked(dest)
        _backup(dest, ctx)
        conn = sqlite3.connect(str(dest), timeout=1.0, isolation_level=None)
    else:
        conn = sqlite3.connect(str(_snapshot(dest, ctx)), isolation_level=None)
    try:
        try:
            reason, added = _merge_db(snap, conn)
            if reason == _VIRTUAL:
                same = _digest(conn) == _digest_path(snap)
            else:
                same = False
        except sqlite3.OperationalError as error:
            if ctx.apply:
                raise _Busy(f"{dest.name} is locked ({error})") from error
            raise
    finally:
        conn.close()
    if reason == _VIRTUAL:
        return _virtual_table_db(snap, dest, rel, ctx, same)
    if reason:
        return Item(rel, CONFLICT, f"{reason}; nothing copied")
    if added == 0:
        return Item(rel, SKIP_IDENTICAL, "0 new rows")
    return Item(rel, MERGE, f"{added} new {_plural(added, 'row')}", added)


def _count_rows(snap: Path) -> int:
    conn = sqlite3.connect(str(snap))
    try:
        return sum(
            conn.execute(f"SELECT COUNT(*) FROM {_quote(n)}").fetchone()[0] for n in _tables(conn)
        )
    finally:
        conn.close()


# --------------------------------------------------------------------------
# Per-entry rules
# --------------------------------------------------------------------------


def _json_equal(a: Path, b: Path) -> bool:
    try:
        return json.loads(a.read_text(encoding="utf-8")) == json.loads(
            b.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return False


def _copy_file(src: Path, dest: Path, ctx: _Ctx, *, force_private: bool = False) -> None:
    if not ctx.apply:
        return
    _mkdirs(dest.parent, ctx.canonical)
    tmp = dest.with_name(f".{dest.name}.migrate-tmp")
    shutil.copyfile(src, tmp)
    os.chmod(tmp, 0o600 if force_private else _private_mode(src))
    os.replace(tmp, dest)


def _process_file(src: Path, dest: Path, rel: str, ctx: _Ctx) -> Item:
    name = src.name
    if name.endswith(".log"):
        return Item(rel, SKIP_LOG, "logs are not carried over")
    if _is_sqlite(src):
        return _process_sqlite(src, dest, rel, ctx)
    is_license = rel == _LICENSE
    if not dest.exists():
        _copy_file(src, dest, ctx, force_private=is_license)
        return Item(rel, COPY, "")
    if dest.is_dir():
        return Item(rel, CONFLICT, "canonical entry is a directory; nothing copied")
    if filecmp.cmp(src, dest, shallow=False) or (name.endswith(".json") and _json_equal(src, dest)):
        return Item(rel, SKIP_IDENTICAL, "")
    if is_license:
        return Item(rel, SKIP_CANONICAL, "canonical license kept")
    if name.endswith(".jsonl"):
        return _merge_lines(src, dest, rel, ctx)
    side = dest.with_name(dest.name + _LEGACY_SUFFIX)
    detail = f"canonical kept; legacy copy beside it as {side.name}"
    if side.exists() and filecmp.cmp(src, side, shallow=False):
        return Item(rel, CONFLICT, detail)
    if ctx.apply:
        _backup(side, ctx)
        _copy_file(src, side, ctx)
    return Item(rel, CONFLICT, detail)


def _merge_lines(src: Path, dest: Path, rel: str, ctx: _Ctx) -> Item:
    """Append legacy lines the canonical file lacks (append-only record files)."""
    have = set(dest.read_text(encoding="utf-8", errors="replace").splitlines())
    new = [
        line
        for line in src.read_text(encoding="utf-8", errors="replace").splitlines()
        if line and line not in have
    ]
    if not new:
        return Item(rel, SKIP_IDENTICAL, "0 new lines")
    if ctx.apply:
        _backup(dest, ctx)
        existing = dest.read_bytes()
        with open(dest, "ab") as handle:
            if existing and not existing.endswith(b"\n"):
                handle.write(b"\n")
            handle.write(("\n".join(new) + "\n").encode("utf-8"))
    return Item(rel, MERGE, f"{len(new)} new {_plural(len(new), 'line')}", len(new))


def _process_link(src: Path, dest: Path, rel: str, ctx: _Ctx) -> Item:
    """Recreate a symbolic link in the canonical home. Never follows or copies through it.

    A relative target that stays inside the legacy home is recreated exactly as
    written, so it points at the migrated sibling. One that leaves the legacy
    home is resolved against the directory the link sits in and recreated as an
    absolute link, so it keeps pointing at the same file.
    """
    target = os.readlink(src)
    note = ""
    if not os.path.isabs(target):
        resolved = os.path.normpath(os.path.join(os.path.dirname(src), target))
        legacy_root = os.path.normpath(str(ctx.legacy))
        if resolved == legacy_root or resolved.startswith(legacy_root + os.sep):
            note = " (relative, stays inside the home)"  # keep the link as written
        else:
            target = resolved
            note = " (relative target made absolute)"
    if os.path.lexists(dest):
        if dest.is_symlink() and os.readlink(dest) == target:
            return Item(rel, SKIP_IDENTICAL, f"same link -> {target}")
        return Item(rel, CONFLICT, "canonical already has a different entry here; link not created")
    if ctx.apply:
        _mkdirs(dest.parent, ctx.canonical)
        os.symlink(target, dest)
    return Item(rel, LINK, f"-> {target}{note}")


def _process(src: Path, dest: Path, rel: str, ctx: _Ctx) -> None:
    from tokenpak import _paths

    name = src.name
    if _paths.is_runtime_entry(name):
        ctx.items.append(Item(rel, SKIP_RUNTIME, "live process state is not carried over"))
        return
    if src.is_symlink():
        ctx.items.append(_process_link(src, dest, rel, ctx))
        return
    if src.is_dir():
        if dest.exists() and not dest.is_dir():
            ctx.items.append(Item(rel, CONFLICT, "canonical entry is a file; nothing copied"))
            return
        for child in sorted(src.iterdir(), key=lambda p: p.name):
            _process(child, dest / child.name, f"{rel}/{child.name}", ctx)
        return
    if name.endswith(_LEGACY_SUFFIX):
        return
    ctx.items.append(_process_file(src, dest, rel, ctx))


# --------------------------------------------------------------------------
# Busy gate
# --------------------------------------------------------------------------


def _open_by_others(roots: list[Path]) -> list[str]:
    """Names of database files under *roots* that another process holds open."""
    proc = Path("/proc")
    if not proc.is_dir():
        return []
    mine = os.getpid()
    prefixes = tuple(str(r.resolve()) + os.sep for r in roots if r.exists())
    held: set[str] = set()
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == mine:
            continue
        try:
            for fd in (entry / "fd").iterdir():
                try:
                    target = os.readlink(fd)
                except OSError:
                    continue
                if target.startswith(prefixes) and target.endswith(_SQLITE_SUFFIXES):
                    held.add(target)
        except OSError:
            continue
    return sorted(held)


def busy_reasons(legacy: Path, canonical: Path, *, wait: bool = True) -> list[str]:
    """Why a migration must not write right now. Empty means clear."""
    from tokenpak.cli.commands import update_apply

    reasons: list[str] = []
    if wait:
        reason, _ = update_apply.idle_gate()
    else:
        reason = update_apply.busy_reason()
    if reason:
        reasons.append(f"the proxy is in use ({reason})")
    for held in _open_by_others([legacy, canonical]):
        reasons.append(f"a running process has {held} open (a companion session is active)")
    for locked in _locked_targets(canonical):
        reasons.append(f"{locked} is locked by another connection")
    return reasons


def _locked_targets(canonical: Path) -> list[str]:
    """Existing canonical state databases that cannot take a write lock now."""
    from tokenpak import _paths

    found: list[str] = []
    for name in sorted(_paths.product_state_names()):
        top = canonical / name
        files = [top] if top.is_file() else list(top.rglob("*")) if top.is_dir() else []
        for path in files:
            if path.is_file() and _is_sqlite(path):
                try:
                    _check_unlocked(path)
                except _Busy:
                    found.append(str(path.relative_to(canonical)))
    return found


# --------------------------------------------------------------------------
# Plan / run
# --------------------------------------------------------------------------


def _is_sidecar(name: str, state: frozenset[str]) -> bool:
    """A ``-wal``/``-shm``/``-journal`` file that belongs to a state database."""
    for suffix in ("-wal", "-shm", "-journal"):
        if name.endswith(suffix) and name[: -len(suffix)] in state:
            return True
    return False


def build(legacy: Path, canonical: Path, *, apply: bool) -> tuple[list[Item], Optional[Path]]:
    """Compute (and with ``apply``, execute) the migration.

    Returns the plan and the backup directory used (``None`` if nothing needed one).
    """
    from tokenpak import _paths

    state = _paths.product_state_names()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    with tempfile.TemporaryDirectory(prefix="tokenpak-home-migrate-") as scratch:
        ctx = _Ctx(legacy, canonical, apply, Path(scratch), stamp)
        for entry in sorted(legacy.iterdir(), key=lambda p: p.name):
            if entry.name in state:
                _process(entry, canonical / entry.name, entry.name, ctx)
            elif entry.name.endswith(_LEGACY_SUFFIX) or _is_sidecar(entry.name, state):
                continue
            else:
                ctx.items.append(Item(entry.name, KEEP_LEGACY, "not TokenPak state; left in place"))
        built = ctx.backup_root if ctx.backed_up else None
        return ctx.items, built


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def _collapse(items: list[Item]) -> Iterator[tuple[str, str, str]]:
    """Yield ``(action, path, detail)`` lines; quiet repeats collapse per directory."""
    quiet = {COPY, SKIP_IDENTICAL, SKIP_LOG, SKIP_RUNTIME}
    groups: dict[tuple[str, str], list[Item]] = {}
    order: list[Any] = []
    for item in items:
        parent = item.path.rsplit("/", 1)[0] if "/" in item.path else ""
        if item.action in quiet and parent:
            key = (parent, item.action)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append(item)
        else:
            order.append(item)
    for entry in order:
        if isinstance(entry, Item):
            yield entry.action, entry.path, entry.detail
            continue
        members = groups[entry]
        if len(members) > 3:
            rows = sum(m.rows for m in members)
            extra = f", {rows} rows" if rows else ""
            yield entry[1], f"{entry[0]}/", f"{len(members)} files{extra}"
        else:
            for m in members:
                yield m.action, m.path, m.detail


def _print_plan(items: list[Item], *, apply: bool, legacy: Path, canonical: Path) -> None:
    verb = "Applied" if apply else "Plan (dry run, nothing was changed)"
    print(f"{verb}: {legacy} -> {canonical}\n")
    state_items = [i for i in items if i.action != KEEP_LEGACY]
    for action, path, detail in _collapse(state_items):
        tail = f"  {detail}" if detail else ""
        print(f"  {action:<17} {path}{tail}")
    kept = [i.path for i in items if i.action == KEEP_LEGACY]
    if kept:
        print(f"\nLeft in place (not TokenPak state, {len(kept)} entries):")
        line = "  "
        for name in kept:
            if len(line) + len(name) + 2 > 78:
                print(line.rstrip(", "))
                line = "  "
            line += name + ", "
        print(line.rstrip(", "))


def _summary(items: list[Item]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.action] = counts.get(item.action, 0) + 1
    return counts


def run(args: Any) -> int:
    """Entry point for ``tokenpak home migrate``."""
    from tokenpak import _paths

    apply = bool(getattr(args, "apply", False))
    as_json = bool(getattr(args, "as_json", False))
    legacy = _paths.legacy_home()
    canonical = _paths.canonical_home()

    if os.environ.get(_paths.ENV_VAR, "").strip():
        print(f"✗ tokenpak home migrate - {_paths.ENV_VAR} is set.", file=sys.stderr)
        print(
            "\n  This command moves state between the two default homes (~/.tokenpak and "
            "~/.tpk),\n  and a custom home is a separate install.\n"
            f"  Unset it and run again: env -u {_paths.ENV_VAR} tokenpak home migrate",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    if not legacy.is_dir():
        if as_json:
            print(json.dumps({"applied": False, "items": [], "message": "nothing to migrate"}))
        else:
            print(f"Nothing to migrate: there is no legacy home at {legacy}.")
        return EXIT_OK

    notes: list[str] = []
    if apply:
        try:
            reasons = busy_reasons(legacy, canonical)
        except Exception as error:  # a failed probe must not read as "idle"
            reasons = [f"the idle check could not complete ({error})"]
        if reasons:
            print(
                "✗ tokenpak home migrate - TokenPak is in use. Nothing was changed.\n",
                file=sys.stderr,
            )
            for reason in reasons:
                print(f"  - {reason}", file=sys.stderr)
            print(
                "\n  Close companion sessions and let the proxy go idle, then run:\n"
                "  tokenpak home migrate --apply",
                file=sys.stderr,
            )
            return EXIT_BUSY
    else:
        try:
            notes = busy_reasons(legacy, canonical, wait=False)
        except Exception:
            notes = []

    try:
        items, backup_dir = build(legacy, canonical, apply=apply)
    except _Busy as error:
        print(
            f"✗ tokenpak home migrate - {error}. Stopped; check the backups, then retry.",
            file=sys.stderr,
        )
        return EXIT_BUSY
    except (OSError, sqlite3.Error) as error:
        print(f"✗ tokenpak home migrate - {error}", file=sys.stderr)
        print(
            "\n  Nothing in the legacy home was changed. Re-run with --apply once fixed.",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    counts = _summary(items)
    if as_json:
        print(
            json.dumps(
                {
                    "applied": apply,
                    "legacy": str(legacy),
                    "canonical": str(canonical),
                    "items": [i.as_dict() for i in items],
                    "summary": counts,
                    "busy": notes,
                },
                indent=2,
            )
        )
        return EXIT_OK

    _print_plan(items, apply=apply, legacy=legacy, canonical=canonical)
    print()
    print("Summary: " + ", ".join(f"{n} {a}" for a, n in sorted(counts.items())))
    if counts.get(CONFLICT):
        print(
            "Conflicts keep the canonical copy. Review the ones marked CONFLICT; "
            "legacy files were copied beside the canonical ones as <name>.legacy."
            if apply
            else "Conflicts keep the canonical copy; --apply writes <name>.legacy beside "
            "files that differ."
        )
    if apply:
        if backup_dir is not None:
            print(f"Backups of every changed target: {backup_dir}")
        print(
            f"\n✅ Done. {legacy} is untouched. Once you have run TokenPak from {canonical} "
            "for a while and are satisfied, you can remove the legacy home yourself;"
            "\n   this command never does."
        )
    else:
        for note in notes:
            print(f"\nNote: --apply would be refused right now: {note}.")
        print("\nRe-run with --apply to write these changes.")
    return EXIT_OK


def add_arguments(parser: Any) -> None:
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the changes (default: print the plan only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without writing (this is the default)",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true", help="Machine-readable output"
    )


__all__ = [
    "COPY",
    "CONFLICT",
    "KEEP_LEGACY",
    "LINK",
    "MERGE",
    "SKIP_IDENTICAL",
    "Item",
    "add_arguments",
    "build",
    "busy_reasons",
    "run",
]
