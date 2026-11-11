"""Write-through guard for the ``tokenpak.core.runtime.proxy`` compatibility
shim.

``tests/test_core_runtime_proxy_compat.py`` covers the read side (lazy
resolution via ``__getattr__``, no eager import, ``dir()``, the once-per-
process deprecation warning). This module covers the write side: assigning
or deleting one of the 13 legacy names on the shim must forward to
``tokenpak.proxy.bootstrap`` rather than shadowing it locally, while names
outside ``__all__`` remain ordinary module attributes.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.fixture(autouse=True)
def _restore_bootstrap_legacy_names():
    """Put every legacy name's ``tokenpak.proxy.bootstrap`` value back after
    each test, whether it passed or failed.

    The tests below write through the shim with plain ``setattr`` rather than
    ``monkeypatch.setattr``. From pytest 9.2, monkeypatch records a module
    attribute's old value from the module's own ``__dict__``; the shim never
    stores the legacy names there (``__getattr__`` resolves them), so the undo
    would delete the name and the shim would forward that delete to bootstrap,
    leaving the names missing for every later test in the process.
    """
    from tokenpak.core.runtime import proxy as compat
    from tokenpak.proxy import bootstrap

    saved = {name: getattr(bootstrap, name) for name in compat.__all__}
    yield
    for name, value in saved.items():
        setattr(bootstrap, name, value)


def test_lazy_names_resolve_to_canonical_objects():
    """Baseline read-through sanity check (also covered by the compat test
    module), kept here so this file stands alone as the shim's spec."""
    from tokenpak.core.runtime import proxy as compat
    from tokenpak.proxy import bootstrap

    assert len(compat.__all__) == 13
    for name in compat.__all__:
        assert getattr(compat, name) is getattr(bootstrap, name)


def test_assignment_on_shim_writes_through_to_bootstrap():
    from tokenpak.core.runtime import proxy as compat
    from tokenpak.proxy import bootstrap

    sentinel = object()
    compat.SESSION = sentinel
    assert bootstrap.SESSION is sentinel


def test_assignment_on_shim_writes_through_for_every_name():
    """Every one of the 13 legacy names forwards assignment, not just one."""
    from tokenpak.core.runtime import proxy as compat
    from tokenpak.proxy import bootstrap

    for name in compat.__all__:
        sentinel = object()
        setattr(compat, name, sentinel)
        assert getattr(bootstrap, name) is sentinel, name


def test_deleting_name_on_shim_deletes_on_bootstrap():
    from tokenpak.core.runtime import proxy as compat
    from tokenpak.proxy import bootstrap

    original = bootstrap.MUTATION_AUDIT_TTL_DAYS
    sentinel = object()
    compat.MUTATION_AUDIT_TTL_DAYS = sentinel
    assert bootstrap.MUTATION_AUDIT_TTL_DAYS is sentinel

    del compat.MUTATION_AUDIT_TTL_DAYS
    assert not hasattr(bootstrap, "MUTATION_AUDIT_TTL_DAYS")

    # restore so later tests / process-wide state see the real object again
    bootstrap.MUTATION_AUDIT_TTL_DAYS = original


def test_reassigning_after_delete_restores_bootstrap_attribute():
    from tokenpak.core.runtime import proxy as compat
    from tokenpak.proxy import bootstrap

    original = bootstrap.COMPILATION_MODE
    sentinel = object()
    compat.COMPILATION_MODE = sentinel
    assert bootstrap.COMPILATION_MODE is sentinel

    compat.COMPILATION_MODE = original
    assert bootstrap.COMPILATION_MODE is original


def test_attribute_outside_all_does_not_forward(monkeypatch):
    """Setting a name that is not one of the 13 legacy names is an ordinary
    module attribute -- it must not appear on bootstrap."""
    from tokenpak.core.runtime import proxy as compat
    from tokenpak.proxy import bootstrap

    monkeypatch.setattr(compat, "not_a_legacy_name", "local-value", raising=False)
    assert compat.not_a_legacy_name == "local-value"
    assert not hasattr(bootstrap, "not_a_legacy_name")


def test_importing_shim_does_not_eagerly_import_bootstrap():
    """The write-through mechanism (a __class__ swap on the module) must not
    itself trigger an eager import of tokenpak.proxy.bootstrap. Verified in
    a fresh subprocess so no other test's import caching can mask it."""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import tokenpak.core.runtime.proxy, sys; "
            "print('tokenpak.proxy.bootstrap' in sys.modules)",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", (
        f"importing the shim eagerly pulled in bootstrap: {result.stdout!r} {result.stderr!r}"
    )


def test_write_through_does_not_require_prior_read(monkeypatch):
    """Assignment must forward even if the name was never read through the
    shim first in this process (i.e. the mechanism does not depend on
    __getattr__ having already resolved and cached anything)."""
    code = (
        "import tokenpak.core.runtime.proxy as compat\n"
        "from tokenpak.proxy import bootstrap\n"
        "sentinel = object()\n"
        "compat.STABLE_CACHE_CONTROL_AUTO = sentinel\n"
        "assert bootstrap.STABLE_CACHE_CONTROL_AUTO is sentinel\n"
        "print('OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().splitlines()[-1] == "OK"


def test_shim_module_class_is_the_write_through_subclass():
    """Guard the mechanism itself: the module must actually have had its
    __class__ swapped, not merely happen to work by accident."""
    import types

    from tokenpak.core.runtime import proxy as compat

    assert type(compat) is not types.ModuleType
    assert isinstance(compat, types.ModuleType)
    assert "__setattr__" in type(compat).__dict__
    assert "__delattr__" in type(compat).__dict__
