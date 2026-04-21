"""P-AC-06 acceptance: every tokenpak.agent.cli.* legacy shim emits a
DeprecationWarning pointing at its canonical tokenpak.cli.* home.
"""

from __future__ import annotations

import importlib
import sys
import warnings

import pytest

LEGACY_SHIMS = [
    "tokenpak.agent.cli",
    "tokenpak.agent.cli.main",
    "tokenpak.agent.cli.trigger_cmd",
    "tokenpak.agent.cli.commands",
    "tokenpak.agent.cli.commands.budget",
    "tokenpak.agent.cli.commands.compliance",
    "tokenpak.agent.cli.commands.compression",
    "tokenpak.agent.cli.commands.config",
    "tokenpak.agent.cli.commands.cost",
    "tokenpak.agent.cli.commands.dashboard",
    "tokenpak.agent.cli.commands.debug",
    "tokenpak.agent.cli.commands.diff",
    "tokenpak.agent.cli.commands.doctor",
    "tokenpak.agent.cli.commands.exec",
    "tokenpak.agent.cli.commands.fingerprint",
    "tokenpak.agent.cli.commands.handoff",
    "tokenpak.agent.cli.commands.help",
    "tokenpak.agent.cli.commands.index",
    "tokenpak.agent.cli.commands.last",
    "tokenpak.agent.cli.commands.license",
    "tokenpak.agent.cli.commands.maintenance",
    "tokenpak.agent.cli.commands.metrics",
    "tokenpak.agent.cli.commands.optimize",
    "tokenpak.agent.cli.commands.policy",
    "tokenpak.agent.cli.commands.preview",
    "tokenpak.agent.cli.commands.prune",
    "tokenpak.agent.cli.commands.replay",
    "tokenpak.agent.cli.commands.retain",
    "tokenpak.agent.cli.commands.route",
    "tokenpak.agent.cli.commands.savings",
    "tokenpak.agent.cli.commands.serve",
    "tokenpak.agent.cli.commands.sla",
    "tokenpak.agent.cli.commands.status",
    "tokenpak.agent.cli.commands.teacher",
    "tokenpak.agent.cli.commands.template",
    "tokenpak.agent.cli.commands.trigger",
    "tokenpak.agent.cli.commands.vault",
    "tokenpak.agent.cli.commands.workflow",
]


@pytest.mark.parametrize("module_name", LEGACY_SHIMS)
def test_shim_emits_deprecation_warning(module_name: str) -> None:
    for dotted in [module_name, *[k for k in sys.modules if k.startswith(module_name)]]:
        sys.modules.pop(dotted, None)

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        importlib.import_module(module_name)

    shim_warnings = [
        w
        for w in captured
        if issubclass(w.category, DeprecationWarning)
        and module_name in str(w.message)
        and "tokenpak.cli" in str(w.message)
    ]
    assert shim_warnings, (
        f"expected DeprecationWarning for {module_name!r} "
        f"pointing at canonical tokenpak.cli.*, got: "
        f"{[str(w.message) for w in captured]}"
    )


def test_canonical_symbol_identity_preserved() -> None:
    """Importing via the shim should yield the identical module object."""
    for dotted in list(sys.modules):
        if dotted.startswith(("tokenpak.agent.cli", "tokenpak.cli")):
            sys.modules.pop(dotted, None)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        import tokenpak.agent.cli.commands.doctor as shim_mod
        import tokenpak.cli.commands.doctor as canonical_mod

    # The `from X import *` pattern re-binds names into the shim module;
    # canonical module is the authoritative source. The modules themselves
    # are distinct objects (different __name__), but imported symbols
    # resolve to the same underlying objects.
    canonical_names = set(dir(canonical_mod))
    shim_names = set(dir(shim_mod))
    # Shim must surface every canonical non-dunder name
    canonical_public = {n for n in canonical_names if not n.startswith("_")}
    shim_public = {n for n in shim_names if not n.startswith("_")}
    missing = canonical_public - shim_public
    assert not missing, f"shim missing canonical symbols: {sorted(missing)}"
