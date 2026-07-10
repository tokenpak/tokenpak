"""Regression: the companion MCP server entry point must import cheaply.

Background
----------
``tokenpak claude`` / ``tokenpak codex`` spawn the MCP server as
``python3 -m tokenpak.companion.mcp.server`` and the client expects an
``initialize`` response inside its MCP-connect window. If the server import
chain eagerly pulled in the heavy optional ML stack
(``sentence_transformers`` / ``transformers`` / ``torch``), the first launch
could exceed the client's startup timeout even though the server code was
otherwise healthy.

The durable fix makes those backends lazy: availability is detected cheaply at
import and the model is loaded only when a retrieval tool is actually invoked.
``test_vector_local_coldstart.py`` locks that contract at the
``vault.retrieval.vector_local`` boundary; by its own scope note it does not
import the MCP server module. This test closes that gap by asserting the
property at the canonical server entry point itself: the exact module the
launcher spawns and the docs describe.

Each assertion runs in a fresh subprocess from a neutral working directory so
``sys.modules`` is clean and the repo-root cwd does not shadow ``tokenpak`` as a
bare namespace package. We avoid the ``-P`` flag, which is Python 3.11+ and
would break the 3.10 CI leg.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile

HEAVY_MODULES = ("sentence_transformers", "transformers", "torch")

_SUBPROC_TIMEOUT = 120


def _run_py(code: str) -> subprocess.CompletedProcess[str]:
    """Run ``code`` in a fresh interpreter from a neutral working directory."""
    with tempfile.TemporaryDirectory() as neutral_cwd:
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=neutral_cwd,
            capture_output=True,
            text=True,
            timeout=_SUBPROC_TIMEOUT,
        )


def _line_after(prefix: str, output: str) -> str:
    return [ln for ln in output.splitlines() if ln.startswith(prefix)][-1][
        len(prefix) :
    ]


def test_mcp_server_import_does_not_load_heavy_ml_stack() -> None:
    """Importing the MCP server entry point must stay free of the ML stack."""
    code = (
        "import sys\n"
        "import tokenpak.companion.mcp.server  # noqa: F401\n"
        f"loaded = [m for m in {HEAVY_MODULES!r} if m in sys.modules]\n"
        "print('LOADED:' + ','.join(loaded))\n"
    )
    proc = _run_py(code)
    assert proc.returncode == 0, f"MCP server import raised:\n{proc.stderr}"
    loaded = [m for m in _line_after("LOADED:", proc.stdout).split(",") if m]
    assert loaded == [], (
        "importing tokenpak.companion.mcp.server pulled in heavy ML modules "
        f"{loaded}; these must stay lazy so the server answers initialize "
        "inside the client's MCP-connect window"
    )


def test_mcp_server_exposes_canonical_tool_registry() -> None:
    """The server's tool registry must import without the heavy stack."""
    code = (
        "import sys\n"
        "from tokenpak.companion.mcp.tools import TOOLS\n"
        f"loaded = [m for m in {HEAVY_MODULES!r} if m in sys.modules]\n"
        "print('LOADED:' + ','.join(loaded))\n"
        "print('TOOLS:' + ','.join(t.name for t in TOOLS))\n"
    )
    proc = _run_py(code)
    assert proc.returncode == 0, f"tools import raised:\n{proc.stderr}"
    loaded = [m for m in _line_after("LOADED:", proc.stdout).split(",") if m]
    assert loaded == [], f"importing the tool registry loaded heavy modules {loaded}"

    names = set(_line_after("TOOLS:", proc.stdout).split(","))
    assert {"journal_write", "prune_context"} <= names, names
    assert {"check_budget", "vault_search"} <= names, names
