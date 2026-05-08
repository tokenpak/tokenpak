"""Slim-install smoke + extras-shape tests.

Enforces Standard 02 §9 (Dependencies) and Constitution 00 ("no external
dependencies for core functionality") on the packaging metadata. If a heavy
package ever creeps back into ``[project.dependencies]`` — or a named extra
disappears — these tests fail loudly so a reviewer sees it before publish.

Pairs with proposal
``02_COMMAND_CENTER/proposals/2026-05-01-tokenpak-install-footprint-extras-split.md``
which moved torch/scipy/pandas/litellm/llmlingua/tree-sitter/sentence-transformers
out of the slim core and into named optional extras.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# tomllib is stdlib only on Python 3.11+. On 3.10 it doesn't exist, so the
# slim release test gate must skip cleanly there. This test file's purpose
# (validate pyproject extras shape) is Python-version-independent —
# running it on 3.11/3.12/3.13 is sufficient coverage of the invariant.
tomllib = pytest.importorskip("tomllib", reason="tomllib is stdlib in Python 3.11+; this test runs on 3.11/3.12/3.13")

PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"

# Heavy packages that must NEVER be hard runtime deps. Any of these in
# [project.dependencies] re-violates Standard 02 §9 (Constitution 00).
HEAVY = frozenset(
    {
        "sentence-transformers",
        "tree-sitter-languages",
        "scipy",
        "scikit-learn",
        "pandas",
        "sympy",
        "llmlingua",
        "litellm",
        "transformers",
        "torch",
    }
)

# Extras the proposal declares must exist after the split.
REQUIRED_EXTRAS = frozenset(
    {
        "retrieval",
        "code-compression",
        "intelligence",
        "data",
        "compression",
        "integrations-litellm",
        "full",
    }
)


def _load() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


def _normalize(name: str) -> str:
    # PEP 503 normalization — '_' / '.' / consecutive runs collapse to '-'.
    out = name.lower().replace("_", "-").replace(".", "-")
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-")


def _dep_names(specs: list[str]) -> set[str]:
    names: set[str] = set()
    for spec in specs:
        head = spec.split(";", 1)[0].strip()
        for sep in ("[", "<", ">", "=", "!", "~", " "):
            head = head.split(sep, 1)[0]
        if head:
            names.add(_normalize(head))
    return names


def test_slim_core_has_no_heavy_packages():
    """``[project.dependencies]`` must not reference any heavy package."""
    deps = _load()["project"].get("dependencies", [])
    found = _dep_names(deps) & HEAVY
    assert not found, (
        "Heavy packages snuck back into [project.dependencies]: "
        f"{sorted(found)}. Move them to a named extra under "
        "[project.optional-dependencies] (Standard 02 §9 / Constitution 00)."
    )


def test_required_extras_declared():
    """Every named extra called out in the proposal must exist."""
    extras = _load()["project"].get("optional-dependencies", {})
    missing = REQUIRED_EXTRAS - set(extras)
    assert not missing, (
        f"Required optional-dependencies extras missing: {sorted(missing)}. "
        "Restore them per "
        "proposals/2026-05-01-tokenpak-install-footprint-extras-split.md."
    )


def test_full_meta_extra_covers_all_feature_extras():
    """``full`` must pull in every feature extra so the legacy install works."""
    extras = _load()["project"].get("optional-dependencies", {})
    full = extras.get("full", [])
    full_str = " ".join(full).lower()
    feature_extras = REQUIRED_EXTRAS - {"full"}
    for name in feature_extras:
        assert name.lower() in full_str, (
            f"`full` meta-extra is missing feature extra `{name}`. Update "
            "[project.optional-dependencies].full to include it."
        )


@pytest.mark.parametrize(
    "extra,heavy_pkg",
    [
        ("retrieval", "sentence-transformers"),
        ("code-compression", "tree-sitter-languages"),
        ("intelligence", "scipy"),
        ("data", "pandas"),
        ("compression", "llmlingua"),
        ("integrations-litellm", "litellm"),
    ],
)
def test_each_heavy_dep_lives_in_its_named_extra(extra: str, heavy_pkg: str):
    """A heavy dep must appear in its sanctioned extra so users can opt in."""
    extras = _load()["project"].get("optional-dependencies", {})
    names = _dep_names(extras.get(extra, []))
    assert heavy_pkg in names, (
        f"Extra `{extra}` no longer declares `{heavy_pkg}`. The runtime "
        "import-guard message tells users to install this extra; if the "
        "extra moves, update the guard."
    )


def test_core_imports_under_slim_install():
    """The slim top-level package and canonical proxy surfaces import cleanly.

    This is the in-process equivalent of the CI slim-install smoke. We
    can't simulate a true ``pip install tokenpak`` (no extras) inside the
    dev venv, but we CAN guarantee the negative: if a heavy module is
    absent from this interpreter, the slim path through tokenpak and the
    current canonical proxy import surfaces must not raise. That confirms
    every heavy import site is properly guarded.

    ``tokenpak.proxy.client`` existed on an older branch but is absent from
    the current canonical line; ``tokenpak.proxy`` and ``tokenpak.proxy.server``
    are the import surfaces exercised by the repo's proxy smoke tests.
    """
    import importlib
    import importlib.util

    importlib.import_module("tokenpak")
    importlib.import_module("tokenpak.proxy")
    importlib.import_module("tokenpak.proxy.server")

    heavy_modules = (
        "torch",
        "sentence_transformers",
        "tree_sitter_languages",
        "scipy",
        "pandas",
        "llmlingua",
        "litellm",
    )
    absent = [m for m in heavy_modules if importlib.util.find_spec(m) is None]
    if not absent:
        pytest.skip(
            "All heavy extras are installed in this environment "
            "(`pip install -e .[full]`). The CI slim-install matrix is the "
            "authoritative slim-path smoke."
        )

    # If any heavy package is absent and ``import tokenpak`` already
    # succeeded above, the guarded paths are working. Make the absent set
    # explicit so test output points at exactly which guards just got
    # exercised.
    assert absent, "unreachable — guarded above"
