"""StageRegistry semantics."""

from __future__ import annotations

import pytest

from tokenpak.proxy.optimization import StageRegistry
from tokenpak.proxy.optimization.stage import NoOpStage


def test_empty_registry_is_empty():
    reg = StageRegistry()
    assert len(reg) == 0
    assert reg.names() == []
    assert "anything" not in reg


def test_register_and_iterate_in_insertion_order():
    reg = StageRegistry()
    a = NoOpStage(name="a")
    b = NoOpStage(name="b")
    c = NoOpStage(name="c")
    reg.register(b)
    reg.register(a)
    reg.register(c)
    assert reg.names() == ["b", "a", "c"]
    assert [s.name for s in reg] == ["b", "a", "c"]


def test_re_register_replaces():
    reg = StageRegistry()
    s1 = NoOpStage(name="dup")
    s2 = NoOpStage(name="dup")
    reg.register(s1)
    reg.register(s2)
    assert len(reg) == 1
    assert reg.get("dup") is s2


def test_unregister():
    reg = StageRegistry()
    reg.register(NoOpStage(name="a"))
    reg.register(NoOpStage(name="b"))
    reg.unregister("a")
    assert reg.names() == ["b"]
    # Unregistering missing names is a no-op
    reg.unregister("does-not-exist")
    assert reg.names() == ["b"]


def test_clear():
    reg = StageRegistry()
    reg.register(NoOpStage(name="a"))
    reg.register(NoOpStage(name="b"))
    reg.clear()
    assert len(reg) == 0


def test_register_requires_name():
    reg = StageRegistry()
    with pytest.raises(ValueError):
        reg.register(NoOpStage(name=""))
