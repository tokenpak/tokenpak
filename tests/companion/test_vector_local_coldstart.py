"""Regression: ``tokenpak.vault.retrieval.vector_local`` must import cheaply,
without pulling in the heavy ML stack (``sentence_transformers`` /
``transformers`` / ``torch``).

Background
----------
The companion MCP server's import chain (companion → proxy → router → vault
retrieval) reaches this module. ``vector_local`` used to
``import sentence_transformers`` at module-load time, which transitively pulls
in ``transformers`` + ``torch`` — a ~13s cold import. That delay made the MCP
server miss Claude Code's MCP-connect window, so Claude Code reported it as a
failed setup ("⚠ N setup issues: MCP").

The fix makes ``sentence_transformers`` lazy in ``vector_local``: availability
is *detected* at import via ``importlib.util.find_spec`` (cheap, no torch), and
the real backend is imported only when retrieval is invoked, through
``_load_sentence_transformer()`` (the call ``_ensure_model`` makes). These tests
lock that contract at the ``vector_local`` boundary.

Scope note (Track A)
--------------------
These tests deliberately assert the contract at ``vector_local`` itself and do
**not** import ``tokenpak.companion.mcp_server`` — that MCP-server substrate is
tracked on a separate track and is not part of canonical staging. Proving the
property at ``vector_local`` is both sufficient (it is the module that did the
heavy import) and canonical-only.

Each test runs in a fresh subprocess so ``sys.modules`` is clean (other tests
in the suite may legitimately import torch). To avoid the repo-root cwd shadow
that otherwise resolves ``tokenpak`` to a bare namespace package, the
subprocess runs from a throwaway temporary directory (not via the ``-P`` flag,
which is Python 3.11+ and would break the 3.10 CI leg, and unlike ``-P``/``-I``
does not isolate the user-site editable install used in local dev).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile

# The heavy, slow-to-import optional ML dependencies that must NOT be pulled in
# as a side effect of importing vector_local.
HEAVY_MODULES = ("sentence_transformers", "transformers", "torch")

_SUBPROC_TIMEOUT = 120  # generous: a cold torch import (if it regressed) is ~13s


def _run_py(code: str) -> subprocess.CompletedProcess:
    """Run ``code`` in a fresh interpreter from a neutral working directory.

    Running from a throwaway temp dir keeps the implicit ``''`` (cwd) entry that
    ``python -c`` puts on ``sys.path`` pointed at an empty directory, so
    ``tokenpak`` resolves to the *installed* package rather than the repo-root
    source tree (the cwd shadow). This replaces the ``-P`` flag, which is only
    available on Python 3.11+ (the CI matrix includes 3.10); it also avoids
    ``-P``/``-I`` isolation, which would drop the user-site editable install
    relied on in local dev.
    """
    with tempfile.TemporaryDirectory() as neutral_cwd:
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=neutral_cwd,
            capture_output=True,
            text=True,
            timeout=_SUBPROC_TIMEOUT,
        )


def test_vector_local_import_does_not_load_heavy_ml_stack():
    """Importing vector_local must not import sentence_transformers /
    transformers / torch, yet must still detect backend availability."""
    code = (
        "import sys\n"
        "import tokenpak.vault.retrieval.vector_local as vl\n"
        f"loaded = [m for m in {HEAVY_MODULES!r} if m in sys.modules]\n"
        "print('LOADED:' + ','.join(loaded))\n"
        "print('AVAILABLE:' + str(vl._ST_AVAILABLE))\n"
    )
    proc = _run_py(code)
    assert proc.returncode == 0, f"vector_local import raised:\n{proc.stderr}"
    loaded_line = [ln for ln in proc.stdout.splitlines() if ln.startswith("LOADED:")][-1]
    loaded = [m for m in loaded_line[len("LOADED:") :].split(",") if m]
    assert loaded == [], (
        "importing tokenpak.vault.retrieval.vector_local pulled in heavy ML "
        f"modules {loaded}; these must stay lazy so the companion MCP server "
        "answers `initialize` promptly (see _load_sentence_transformer)."
    )
    # Availability detection must still reflect reality — without importing it.
    expected = importlib.util.find_spec("sentence_transformers") is not None
    assert f"AVAILABLE:{expected}" in proc.stdout, proc.stdout


def test_vector_retrieval_loads_backend_on_demand():
    """The other half of the contract: laziness is not 'never'. Invoking
    ``_load_sentence_transformer()`` (the call ``_ensure_model`` makes) must
    perform the real ``from sentence_transformers import SentenceTransformer``
    and wire the class into the module global.

    A lightweight fake ``sentence_transformers`` is injected so the import
    *wiring* is exercised deterministically, without paying the real ~13s
    ``torch``/``transformers`` cost (which would make timing flaky in CI). The
    companion test above already proves the real backend is detected (and not
    loaded) at import time.
    """
    code = (
        "import sys, types, importlib.machinery\n"
        "fake = types.ModuleType('sentence_transformers')\n"
        "fake.__spec__ = importlib.machinery.ModuleSpec('sentence_transformers', loader=None)\n"
        "class _FakeST:\n"
        "    def __init__(self, *a, **k): pass\n"
        "fake.SentenceTransformer = _FakeST\n"
        "sys.modules['sentence_transformers'] = fake\n"
        "import tokenpak.vault.retrieval.vector_local as vl\n"
        "assert vl.SentenceTransformer is None, 'class bound before first use'\n"
        "cls = vl._load_sentence_transformer()\n"
        "assert cls is _FakeST, cls\n"
        "assert vl.SentenceTransformer is _FakeST, 'module global not populated on demand'\n"
        "print('OK')\n"
    )
    proc = _run_py(code)
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout, proc.stdout
