# SPDX-License-Identifier: Apache-2.0
"""Every first path segment product code puts under the home must be classified.

``tokenpak home migrate`` only carries what ``tokenpak._paths`` knows is product
state. A module that starts writing a new file under the home without the
registry learning about it would be silently left behind by the next migration.

This test reads product code statically and collects the first literal segment
of every path built on the home:

* ``write_home() / "x"``, ``_paths.home() / "x"`` and chains of ``/``;
* ``under("x", ...)`` / ``write_under("x", ...)`` and ``get_db_path("x.db")``;
* ``os.path.join(<home>, "x")`` and ``<home>.joinpath("x")``;
* the same through names assigned from a home expression, and through
  module-level string/tuple constants (``under(*PARTS)``, ``home / NAME``).

Each segment must be in the migration registry, be runtime state, or appear in
``EXCLUDED`` below with a reason. Segments built from variables are only
allowed in the files listed in ``DYNAMIC`` with a reason.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tokenpak import _paths

ROOT = Path(__file__).resolve().parents[1]
SKIP_FILES = ("tokenpak/_paths.py", "tokenpak/cli/commands/home_migrate.py")

#: Not carried by migration, with the evidence for why that is right.
EXCLUDED = {
    "logs": "log directory (proxy/telemetry request logs): diagnostics, not product state",
    "debug": "debug capture blobs written by `tokenpak debug`: transient diagnostics",
    "test": "`tokenpak test` run logs and results: transient diagnostics",
    "incidents.log": "append-only log (auth_guard); logs are never carried",
    "scripts": "operator-supplied scripts looked up by swap_alert; TokenPak never writes it",
    ".env": "user-owned env file; config_env only checks its permissions",
    "cards": "named only in the uninstall purge list; no product code writes it",
}

#: Files whose home-relative segment is a variable, with why that is fine.
DYNAMIC = {
    "tokenpak/core/paths.py": "get_db_path(name): each caller's literal is collected separately",
    "tokenpak/cli/commands/uninstall.py": "iterates the purge list; it deletes, it does not define state",
    "tokenpak/_cli_core.py": "first-run flag file name constant, a registry file (.seen_intro)",
}


def _const_map(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    out: dict[str, tuple[str, ...]] = {}
    for node in tree.body:
        value, targets = None, []
        if isinstance(node, ast.Assign):
            value, targets = node.value, node.targets
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            value, targets = node.value, [node.target]
        if value is None:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            vals: tuple[str, ...] = (value.value,)
        elif isinstance(value, ast.Tuple) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
        ):
            vals = tuple(e.value for e in value.elts)  # type: ignore[union-attr]
        else:
            continue
        for t in targets:
            if isinstance(t, ast.Name):
                out[t.id] = vals
    return out


_HOME_FUNCS = {"home", "write_home", "resolved_home", "canonical_home"}
_UNDER_FUNCS = {"under", "write_under"}
_HOME_CALLEES = {"_write_home", "write_home", "resolved_home"}


def _is_home(node: ast.AST, aliases: set[str]) -> bool:
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _HOME_FUNCS:
            base = func.value
            return (isinstance(base, ast.Name) and "paths" in base.id) or (
                isinstance(base, ast.Attribute) and "paths" in base.attr
            )
        if isinstance(func, ast.Name) and func.id in _HOME_CALLEES:
            return True
        if isinstance(func, ast.Name) and func.id in {"str", "Path"} and node.args:
            return _is_home(node.args[0], aliases)
    return isinstance(node, ast.Name) and node.id in aliases


def _first(node: ast.AST, consts: dict[str, tuple[str, ...]]) -> str | None:
    """First segment named by *node*, or ``None`` when it is not a literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split("/")[0]
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id][0].split("/")[0]
    if isinstance(node, ast.Starred) and isinstance(node.value, ast.Name):
        vals = consts.get(node.value.id)
        return vals[0].split("/")[0] if vals else None
    return None


def collect(path: Path) -> list[tuple[int, str | None]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    consts = _const_map(tree)
    aliases: set[str] = set()
    for _ in range(2):
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if _is_home(node.value, aliases):
                    aliases.update(t.id for t in targets if isinstance(t, ast.Name))
    found: list[tuple[int, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            inner, cur = node, node.left
            while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
                inner, cur = cur, cur.left
            if inner is node and _is_home(cur, aliases):
                found.append((node.lineno, _first(node.right, consts)))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in _UNDER_FUNCS and node.args:
                found.append((node.lineno, _first(node.args[0], consts)))
            elif name == "get_db_path" and node.args:
                found.append((node.lineno, _first(node.args[0], consts)))
            elif name in {"companion_write_dir", "companion_run_dir"}:
                found.append((node.lineno, "companion"))
            elif name == "join" and len(node.args) > 1 and _is_home(node.args[0], aliases):
                found.append((node.lineno, _first(node.args[1], consts)))
            elif (
                name == "joinpath"
                and node.args
                and isinstance(func, ast.Attribute)
                and _is_home(func.value, aliases)
            ):
                found.append((node.lineno, _first(node.args[0], consts)))
    return found


def _product_files() -> list[Path]:
    files = []
    for path in sorted((ROOT / "tokenpak").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if rel in SKIP_FILES or rel.startswith("tokenpak/tests/"):
            continue
        files.append(path)
    return files


def test_every_home_segment_is_classified():
    registry = _paths.product_state_names()
    unclassified: dict[str, list[str]] = {}
    dynamic: list[str] = []
    for path in _product_files():
        rel = path.relative_to(ROOT).as_posix()
        for line, seg in collect(path):
            if seg is None:
                if rel not in DYNAMIC:
                    dynamic.append(f"{rel}:{line}")
            elif not (
                seg in registry
                or _paths.is_runtime_entry(seg)
                or seg in EXCLUDED
                or seg.endswith(".log")  # logs are skipped by the migration itself
            ):
                unclassified.setdefault(seg, []).append(f"{rel}:{line}")
    assert not unclassified, (
        "Product code puts these names under the home but `tokenpak home migrate` "
        "does not know them. Add each to _paths (_MIGRATION_STATE_FILES / "
        "_MIGRATION_STATE_DIRS, or _RUNTIME_ENTRIES) or to EXCLUDED here with a reason:\n"
        + "\n".join(f"  {k}: {v[0]}" for k, v in sorted(unclassified.items()))
    )
    assert not dynamic, (
        "A home path is built from a non-literal segment; use a literal or list the "
        "file in DYNAMIC with a reason:\n  " + "\n  ".join(dynamic)
    )


def test_excluded_and_registry_do_not_overlap_and_are_not_stale():
    registry = _paths.product_state_names()
    assert not (set(EXCLUDED) & registry), "a name cannot be both excluded and state"
    seen = {seg for p in _product_files() for _, seg in collect(p) if seg}
    stale = {n for n in EXCLUDED if n not in seen} - {"cards", "scripts", ".env"}
    assert not stale, f"EXCLUDED names no product code uses any more: {sorted(stale)}"


def test_collector_sees_the_patterns(tmp_path):
    sample = tmp_path / "m.py"
    sample.write_text(
        "from tokenpak import _paths\nimport os\n"
        "SUB = 'alpha'\nPARTS = ('beta', 'x.db')\n"
        "H = _paths.write_home()\n"
        "a = _paths.write_home() / 'one' / 'two'\n"
        "b = H / SUB\n"
        "c = _paths.under(*PARTS)\n"
        "d = os.path.join(str(_paths.home()), 'three')\n"
        "e = H.joinpath('four')\n"
    )
    assert sorted(s for _, s in collect(sample)) == sorted(
        ["one", "alpha", "beta", "three", "four"]
    )
