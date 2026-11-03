"""Every SQLite connection in long-lived TokenPak code is closed deterministically.

On CPython 3.11+ a ``sqlite3.Connection`` holds a reference cycle through its
statement cache, so a connection that is merely dropped stays open until the
cyclic garbage collector happens to run a full collection. In a busy proxy
that can take a long time, and while a persistent WAL writer is open SQLite
also parks the file descriptors of closed connections for reuse, so every
connection that is open at the same time raises the process's permanent
``monitor.db`` descriptor count.

This audit parses every module in ``tokenpak/`` and classifies each
``sqlite3.connect(...)`` call, and each call of a module-local function that
returns one, by how the connection is released:

- ``with closing(...)`` (optionally followed by the connection's own
  transaction context),
- assigned to a name that a ``finally`` block closes,
- returned to the caller (a factory, whose calls are checked in turn),
- stored in a documented, bounded cache (``_DOCUMENTED_CACHES``).

``with sqlite3.connect(...) as conn`` only commits or rolls back; it does not
close, so it fails the audit. CLI command modules are out of scope: they run
in a short-lived process and the operating system releases everything at
exit.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "tokenpak"

#: Process-lifetime CLI entry points: a connection lives at most as long as
#: one command invocation.
_OUT_OF_SCOPE_PREFIXES = ("cli/", "_cli_core.py")

#: Connections intentionally held beyond one call. Each is bounded and closed
#: by its owner; the reason is recorded here so a new cache needs review.
_DOCUMENTED_CACHES = {
    # One persistent writer connection per process, owned by the writer
    # thread, replaced only when the monitor path changes.
    ("proxy/monitor.py", "_get_db_connection"): "single writer connection",
    # Schema-initialisation connection; Monitor.__init__ closes it in finally.
    ("proxy/monitor.py", "_init_db"): "closed by Monitor.__init__",
    # Instance-owned connection with an explicit close().
    ("orchestration/dispatch/ledger/db.py", "__init__"): "instance-owned, close()",
    # Per-thread connections held by a holder that closes them when the
    # thread exits (tokenpak.core.sqlite_lifecycle.ThreadConnection).
    ("telemetry/budget.py", "_conn"): "per-thread, closed at thread exit",
    ("telemetry/cost_tracker.py", "_conn"): "per-thread, closed at thread exit",
    ("telemetry/proxy_storage.py", "_conn"): "per-thread, closed at thread exit",
    ("telemetry/replay.py", "_conn"): "per-thread, closed at thread exit",
    ("core/registry/__init__.py", "_get_connection"): "per-thread, closed at thread exit",
    # TelemetryDB keeps one "keeper" connection for its own lifetime (it holds
    # a shared in-memory database open); close() closes it and every
    # per-thread connection, which also close at thread exit.
    ("telemetry/storage_base.py", "__init__"): "keeper connection, close()",
    ("telemetry/storage.py", "__init__"): "keeper connection, close()",
    # The connection is handed to the RecallStore instance, which owns it
    # and closes it in close() / __exit__.
    ("companion/recall/store.py", "open"): "instance-owned, close()",
    # Companion stores cache one write connection per instance; close()
    # releases it and the proxy closes every per-request instance.
    ("companion/budget/tracker.py", "_writer"): "instance-owned, close()",
    ("companion/journal/store.py", "_writer"): "instance-owned, close()",
}


def _is_sqlite_connect(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "connect"
        and isinstance(func.value, ast.Name)
        # ``_db``/``_sqlite`` alias tokenpak.companion._sqlite, whose
        # connect() is the shared companion connection factory.
        and func.value.id in {"sqlite3", "_sqlite3", "sqlite", "_db", "_sqlite"}
    )


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        if func.value.id in {"self", "cls"}:
            return func.attr
    return ""


def _closes_name(scope: ast.AST, name: str) -> bool:
    """True when a finally block in ``scope`` closes ``name``."""
    for node in ast.walk(scope):
        if not isinstance(node, ast.Try):
            continue
        for stmt in node.finalbody:
            for sub in ast.walk(stmt):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "close"
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == name
                ):
                    return True
    for node in ast.walk(scope):
        if isinstance(node, ast.withitem):
            expr = node.context_expr
            if (
                isinstance(expr, ast.Call)
                and isinstance(expr.func, ast.Name)
                and expr.func.id == "closing"
                and expr.args
                and isinstance(expr.args[0], ast.Name)
                and expr.args[0].id == name
            ):
                return True
    return False


def _returns_name(scope: ast.AST, name: str) -> bool:
    return any(
        isinstance(n, ast.Return) and isinstance(n.value, ast.Name) and n.value.id == name
        for n in ast.walk(scope)
    )


def _classify(call: ast.Call, parents: dict[ast.AST, ast.AST]) -> str:
    parent = parents.get(call)
    if (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, (ast.Name, ast.Attribute))
        and getattr(parent.func, "id", getattr(parent.func, "attr", "")) == "closing"
    ):
        return "closed"
    if isinstance(parent, ast.withitem):
        return "with-connection-not-closed"
    if (
        isinstance(parent, ast.Attribute)
        and parent.attr == "close"
        and isinstance(parents.get(parent), ast.Call)
    ):
        return "closed"  # opened and closed in one expression
    if isinstance(parent, ast.Return):
        return "factory"
    scope: ast.AST | None = parent
    while scope is not None and not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        scope = parents.get(scope)
    if isinstance(parent, (ast.Assign, ast.AnnAssign)):
        target = parent.targets[0] if isinstance(parent, ast.Assign) else parent.target
        if isinstance(target, ast.Name) and scope is not None:
            if _closes_name(scope, target.id):
                return "closed"
            if _returns_name(scope, target.id):
                return "factory"
            return "not-closed-in-finally"
        return "stored"
    return "unclassified"


def _audit() -> list[tuple[str, int, str, str]]:
    findings: list[tuple[str, int, str, str]] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" in f"/{rel}" or rel.startswith(_OUT_OF_SCOPE_PREFIXES):
            continue
        text = path.read_text(encoding="utf-8")
        if "connect(" not in text:
            continue  # no connection is opened here
        tree = ast.parse(text, str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node

        def enclosing(node: ast.AST) -> str:
            cur = parents.get(node)
            while cur is not None:
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    return cur.name
                cur = parents.get(cur)
            return "<module>"

        factories: set[str] = set()
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _is_sqlite_connect(n)]
        for call in calls:
            if _classify(call, parents) == "factory":
                factories.add(enclosing(call))
        # Second pass: calls of module-local factories are opens too.
        local = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and _call_name(n) in factories and n not in calls
        ]
        cached = {func for (path, func) in _DOCUMENTED_CACHES if path == rel}
        for call in calls + local:
            func = enclosing(call)
            if (rel, func) in _DOCUMENTED_CACHES or _call_name(call) in cached:
                continue
            kind = _classify(call, parents)
            # A factory hands the connection to its callers, which are
            # audited as opens in their own right.
            if kind not in {"closed", "factory"}:
                findings.append((rel, call.lineno, func, kind))
    return findings


def test_every_long_lived_sqlite_connection_is_closed_deterministically() -> None:
    findings = _audit()
    report = "\n".join(
        f"tokenpak/{rel}:{line} {func}: {kind}" for rel, line, func, kind in findings
    )
    assert not findings, (
        "SQLite connections that are not closed in finally/closing() and are "
        "not a documented bounded cache:\n" + report
    )


def test_documented_caches_still_exist() -> None:
    """A stale allowlist entry would silently widen the audit."""
    present: set[tuple[str, str]] = set()
    for rel, func in _DOCUMENTED_CACHES:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
                if any(
                    isinstance(n, ast.Call)
                    and (_is_sqlite_connect(n) or _call_name(n) in {"_connect", "_get_connection"})
                    for n in ast.walk(node)
                ):
                    present.add((rel, func))
    assert present == set(_DOCUMENTED_CACHES)
