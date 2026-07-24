"""
Pytest configuration for the tokenpak test suite.

Env-dependency markers (exclude these for a hermetic developer run):
  needs_proxy           — starts or connects to a real tokenpak ProxyServer/subprocess
  needs_webhook         — requires a live external API key (e.g. ANTHROPIC_API_KEY)
  needs_internal_alerts — requires tokenpak._internal.alerts (internal-only module)
  needs_cali_env        — requires a specific dev-host layout (/home/<user>/tokenpak)
  needs_fast_host       — timing-sensitive benchmark assertions; fail on slow/shared hosts

Hermetic developer run:
  pytest -m 'not needs_proxy and not needs_webhook and not needs_internal_alerts \\
             and not needs_cali_env and not needs_fast_host' --tb=short -q

See tests/TEST-ENV-MATRIX.md for the full dependency matrix.

Taxonomy markers (Std 02 §13 + Std 30 §5, ratified 2026-05-09):
  oss       — public OSS surface (default for tests/)
  optional  — requires a named optional extra (tests/optional/)
  internal  — internal / Pro / closed-source surface (tests/_internal/)
  legacy    — historical-compat tests (tests/legacy/)

Auto-applied by `_taxonomy_marker_for_path` based on test directory. Explicit
@pytest.mark.<taxonomy> overrides only when it matches the directory; mismatches
fail collection with a teaching error message.
"""

import pytest

# Std 30 §5 / Std 02 §13 — directory -> taxonomy marker mapping
TAXONOMY_DIR_RULES = (
    ("tests/_internal/", "internal"),
    ("tests/optional/", "optional"),
    ("tests/legacy/", "legacy"),
)
TAXONOMY_DEFAULT = "oss"
TAXONOMY_NAMES = frozenset({"oss", "optional", "internal", "legacy"})


def pytest_addoption(parser):
    """Add custom pytest options"""
    parser.addoption(
        "--update-baselines",
        action="store_true",
        default=False,
        help="Update baseline compression ratios (use after intentional changes)",
    )


# ---------------------------------------------------------------------------
# User-home isolation — applied at conftest IMPORT time, not in a fixture.
#
# Without this, any code path that resolves ``Path.home()`` /
# ``os.path.expanduser("~")`` (monitor.db, retry_events.jsonl, companion
# journal.db, lock registries, ...) silently pollutes the real
# ``~/.tokenpak`` / ``~/.tpk`` of whoever runs the suite.
#
# Why import time and not a session fixture: pytest imports test modules —
# and, transitively, product modules — during COLLECTION, which happens
# before any session fixture runs. Modules that bake ``Path.home()`` into
# module-level constants (e.g. codex skills_installer targets,
# orchestration retry paths) would capture the REAL home while tests later
# compare against the redirected one. Setting the env here, before any
# collection import, keeps import-time constants and runtime resolution
# consistent.
#
# Scope and limits:
#   - HOME (+ USERPROFILE) only. ``TOKENPAK_HOME`` is deliberately NOT set:
#     several suites monkeypatch ``Path.home()`` and rely on the documented
#     env-var-absent resolution order.
#   - Tests that monkeypatch HOME / ``Path.home()`` themselves still win:
#     per-test patches override this process-scoped value.
# ---------------------------------------------------------------------------
import atexit as _atexit
import os as _os
import shutil as _shutil
import tempfile as _tempfile
from pathlib import Path as _Path

_REAL_HOME = _Path(_os.path.expanduser("~"))
# Prefer tmpfs (/dev/shm) for the fake home when available: SQLite state
# files created there (monitor.db schema migration alone issues ~20
# ALTER TABLE fsyncs) stay in memory, so a loaded host disk cannot push
# per-test setup past the global 30s timeout. Fall back to system tmp.
_shm = _Path("/dev/shm")
if _shm.is_dir() and _os.access(_shm, _os.W_OK):
    _FAKE_HOME = _Path(_tempfile.mkdtemp(prefix="tokenpak-test-home-", dir=_shm))
else:
    _FAKE_HOME = _Path(_tempfile.mkdtemp(prefix="tokenpak-test-home-"))
_atexit.register(_shutil.rmtree, _FAKE_HOME, ignore_errors=True)
_os.environ["HOME"] = str(_FAKE_HOME)
# Windows equivalent — harmless elsewhere.
_os.environ["USERPROFILE"] = str(_FAKE_HOME)
# Keep read-mostly tool/model caches warm: libraries honouring XDG
# (huggingface, torch, ...) would otherwise re-download into the fake
# home. Only set when the runner has not chosen its own location.
if "XDG_CACHE_HOME" not in _os.environ and (_REAL_HOME / ".cache").is_dir():
    _os.environ["XDG_CACHE_HOME"] = str(_REAL_HOME / ".cache")

# Preserve user-site packages for subprocess children. Redirecting HOME hides
# the real ``~/.local``, so a ``pip install --user``-installed dependency (e.g.
# watchdog) becomes unimportable in HOME-redirected subprocesses — which can
# hang hook/CLI subprocess tests. Point PYTHONUSERBASE at the real home's
# ``.local`` (mirrors the XDG_CACHE_HOME carve-out above) so user-site stays on
# ``sys.path`` for children while ~/.tokenpak state remains isolated.
if "PYTHONUSERBASE" not in _os.environ and (_REAL_HOME / ".local").is_dir():
    _os.environ["PYTHONUSERBASE"] = str(_REAL_HOME / ".local")


@pytest.fixture(scope="session", autouse=True)
def _isolate_user_home():
    """Expose the import-time fake home (see module-level block above)."""
    yield _FAKE_HOME


def _taxonomy_marker_for_path(nodeid: str) -> str:
    """Return the expected taxonomy marker name for a given test nodeid path."""
    for prefix, marker in TAXONOMY_DIR_RULES:
        if nodeid.startswith(prefix):
            return marker
    return TAXONOMY_DEFAULT


def pytest_collection_modifyitems(config, items):
    """Auto-apply taxonomy markers based on test file path.

    Per Std 30 §5 (R5) + Std 02 §13. Every collected test gets exactly one
    taxonomy marker. Explicit @pytest.mark.<taxonomy> markers are honored
    when they match the directory; mismatches fail collection.

    This avoids per-file edits across hundreds of test files (decision D8 in
    `2026-05-09-release-gate-trust-hardening-decisions.md`).
    """
    failures = []
    for item in items:
        # nodeid looks like "tests/path/test_foo.py::test_bar"
        nodeid = item.nodeid
        expected = _taxonomy_marker_for_path(nodeid)

        # Inspect existing taxonomy markers on the item
        explicit = {m.name for m in item.iter_markers() if m.name in TAXONOMY_NAMES}

        if not explicit:
            # No explicit taxonomy marker — auto-apply directory default
            item.add_marker(pytest.mark.__getattr__(expected))
        elif len(explicit) == 1:
            (got,) = tuple(explicit)
            if got != expected:
                failures.append(
                    f"{nodeid}: explicit @pytest.mark.{got} conflicts with "
                    f"directory-derived marker @pytest.mark.{expected} "
                    f"(rule: tests/_internal/->internal, tests/optional/->optional, "
                    f"tests/legacy/->legacy, otherwise->oss; see Std 02 §13)"
                )
        else:
            failures.append(
                f"{nodeid}: multiple taxonomy markers {sorted(explicit)} — "
                f"per Std 30 §5, every test MUST carry exactly one"
            )

    if failures:
        # Emit collectively as a single collection-time failure
        raise pytest.UsageError(
            "Taxonomy marker validation failed (Std 02 §13 + Std 30 §5):\n  "
            + "\n  ".join(failures[:20])
            + (f"\n  ... and {len(failures) - 20} more" if len(failures) > 20 else "")
        )
