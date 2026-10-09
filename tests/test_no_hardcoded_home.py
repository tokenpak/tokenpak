# SPDX-License-Identifier: Apache-2.0
"""Product code must resolve the TokenPak home through ``tokenpak._paths``.

A module that builds ``~/.tokenpak/...`` or ``Path.home() / ".tokenpak"`` itself
writes to the legacy home even after an install has moved to ``~/.tpk``, which
re-splits the install the moment ``tokenpak home migrate`` finishes. This guard
fails on any such path in product code.

What is *not* flagged, deliberately:

* prose (messages, help, docstrings), which contains whitespace;
* relative project-local paths such as ``.tokenpak/registry.db`` (resolved
  against the working directory, not the home);
* vault index directories (``<vault>/.tokenpak``), which belong to the vault,
  not to the TokenPak home;
* temp/lock sidecar names such as ``.tokenpak-install.lock``.

Files exempt from the scan:
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "tokenpak"

#: path -> reason. Keep this list empty unless a path genuinely must name a home.
EXEMPT = {
    "tokenpak/_paths.py": "the resolver itself defines the home directory names",
    "tokenpak/cli/commands/home_migrate.py": "the migration engine names both homes by design",
}

_HOME_NAME = {".tokenpak", ".tpk"}
_PATH_LIKE = re.compile(r"^~/\.(tokenpak|tpk)(/\S*)?$")
_EMBEDDED = re.compile(r"~/\.(tokenpak|tpk)/\S*")


def _is_home_call(node: ast.AST) -> bool:
    if isinstance(node, ast.Call):
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        return name in {"home", "expanduser"}
    return False


def _is_home_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and "home" in node.id.lower()


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if (
                _PATH_LIKE.match(node.value)
                or _EMBEDDED.search(node.value)
                and " " not in node.value
            ):
                found.append(f"{path}:{node.lineno}: hard-coded home path {node.value!r}")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            if (
                isinstance(right, ast.Constant)
                and right.value in _HOME_NAME
                and _is_home_call(node.left)
            ):
                found.append(f"{path}:{node.lineno}: home() / {right.value!r}")
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"join", "joinpath"}:
                args = node.args
                names = [a.value for a in args if isinstance(a, ast.Constant)]
                if any(n in _HOME_NAME for n in names) and (
                    _is_home_call(args[0]) or _is_home_name(args[0])
                ):
                    found.append(f"{path}:{node.lineno}: join(home, '.tokenpak'/'.tpk')")
    return found


def test_product_code_has_no_hard_coded_home_path():
    problems: list[str] = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT.parent).as_posix()
        if rel in EXEMPT or rel.startswith("tokenpak/tests/"):
            continue
        problems.extend(_violations(path))
    assert not problems, (
        "Route these through tokenpak._paths (write_home()/under()/companion_write_dir()):\n"
        + "\n".join(problems)
    )


def test_guard_detects_the_patterns(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from pathlib import Path\nimport os\n"
        'A = Path.home() / ".tokenpak" / "x"\n'
        'B = os.path.expanduser("~/.tpk/y.db")\n'
        'C = os.path.join(os.path.expanduser("~"), ".tokenpak", "z")\n'
    )
    assert len(_violations(bad)) == 3
