# SPDX-License-Identifier: Apache-2.0
"""Regex-level tests for the Forbidden Implementation Vocabulary lint.

These tests target the compound-form regex in
``.github/scripts/check-forbidden-impl-vocab.py``. The script is
imported by file path because ``.github/scripts/`` is not part of the
installed package surface — it ships as repo-tooling only.

What this file pins
-------------------
- Each forbidden compound form matches (case-insensitive).
- Standalone ``basis`` is **not** flagged. This is the explicit
  Kevin DECISION-PAKPLAN-12.1 / addendum §6.1 nuance: ``basis`` is
  allowed in normal English ("*on the basis of*", "*the basis for*")
  and only compound forms (``PAKBasis``, ``latent basis``,
  ``representative basis``, ``basis_packet`` / ``basis packet`` /
  ``basis_cache`` / ``basis_activation``) are flagged.
- The lint script's own paths and the standards file that *defines*
  the vocabulary are skipped from scans so the lint cannot trigger
  on its own input.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Load the script module by file path (it lives outside the package tree).
# ---------------------------------------------------------------------------

_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / ".github" / "scripts" / "check-forbidden-impl-vocab.py"
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location("_check_forbidden_impl_vocab", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


# ---------------------------------------------------------------------------
# Compound forms — must match.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "use the low-rank trick to cut tokens",
        "we project into a subspace before scoring",
        "this is a quantum-inspired strategy",
        "dequantized representation lands here",
        "the matrix factorization is computed first",
        "build a latent context profile per session",
        "store the latent basis in the cache",
        "share a representative basis across agents",
        "introduce PAKBasis as a new field",
        "the basis_packet enters memory next",
        "the basis packet enters memory next",
        "evict the basis_cache when memory pressure spikes",
        "evict the basis cache when memory pressure spikes",
        "trigger basis_activation on hot Paks",
        "trigger basis activation on hot Paks",
    ],
)
def test_forbidden_forms_match(script, line: str) -> None:
    assert script._FORBIDDEN_PATTERN.search(line) is not None, (
        f"expected compound-form hit on: {line!r}"
    )


@pytest.mark.parametrize(
    "line",
    [
        # Mixed case still matches.
        "Low-Rank approach",
        "PAKBASIS in caps",
        "Latent  Basis with two spaces",
    ],
)
def test_forbidden_forms_case_insensitive(script, line: str) -> None:
    assert script._FORBIDDEN_PATTERN.search(line) is not None


# ---------------------------------------------------------------------------
# Standalone ``basis`` — must NOT match (the §6.1 nuance).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "on the basis of the existing PR review",
        "the basis for the decision is Suki's audit",
        "we picked Sue as the basis for routing this PR",
        "rebased onto a stable basis branch",
        "schema 0003 is the basis migration",
    ],
)
def test_standalone_basis_not_flagged(script, line: str) -> None:
    """Standalone ``basis`` in normal English must not trigger.

    The compound-form regex was authored explicitly to skip this case
    per Kevin DECISION-PAKPLAN-12.1; if a future revision regresses
    that choice it should be a same-cycle Kevin re-decision, not a
    silent behaviour change.
    """
    assert script._FORBIDDEN_PATTERN.search(line) is None, (
        f"standalone 'basis' must not be flagged: {line!r}"
    )


# ---------------------------------------------------------------------------
# Skip-paths — the lint can't trigger on its own input.
# ---------------------------------------------------------------------------


def test_skip_path_substrings_cover_self_inputs(script) -> None:
    """The skip list must include the standards file defining the vocab,
    the script itself, the workflow that invokes it, and this test file."""
    skips = set(script._SKIP_PATH_SUBSTRINGS)
    assert any("08-naming-glossary.md" in s for s in skips)
    assert any("check-forbidden-impl-vocab.py" in s for s in skips)
    assert any("forbidden-impl-vocab-check.yml" in s for s in skips)
    assert any("test_forbidden_impl_vocab.py" in s for s in skips)


def test_should_scan_respects_skip_list(script) -> None:
    """``_should_scan`` returns False for any path matching the skip list."""
    assert (
        script._should_scan(Path("01_PROJECTS/tokenpak/standards/08-naming-glossary.md")) is False
    )
    assert script._should_scan(Path(".github/scripts/check-forbidden-impl-vocab.py")) is False
    assert script._should_scan(Path("tokenpak/companion/recall/schema.py")) is True


def test_should_scan_filters_by_extension(script) -> None:
    """Binary / image / unknown extensions are not scanned."""
    assert script._should_scan(Path("docs/architecture.png")) is False
    assert script._should_scan(Path("scripts/release.bin")) is False
    assert script._should_scan(Path("docs/protocol.md")) is True
    assert script._should_scan(Path("tokenpak/cli/__init__.py")) is True
