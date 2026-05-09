"""Tests for tokenpak.user_templates — local user prompt template CRUD."""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.user_templates", reason="module not available in current build")
import types

import pytest
import tokenpak.user_templates as ut

# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_templates_dir(tmp_path, monkeypatch):
    """Redirect all template storage to a temp directory for isolation."""
    monkeypatch.setattr(ut, "TEMPLATES_DIR", tmp_path / ".tokenpak" / "templates")
    yield


# ── list_templates ────────────────────────────────────────────────────────────

def test_list_empty():
    assert ut.list_templates() == []


def test_list_after_add():
    ut.add("alpha", "Hello {{name}}")
    ut.add("beta", "Goodbye {{name}}")
    names = [t["name"] for t in ut.list_templates()]
    assert "alpha" in names
    assert "beta" in names


def test_list_sorted_by_name():
    ut.add("zebra", "z")
    ut.add("apple", "a")
    names = [t["name"] for t in ut.list_templates()]
    assert names == sorted(names)


# ── add / show ────────────────────────────────────────────────────────────────

def test_add_creates_template():
    t = ut.add("greet", "Hi {{name}}!")
    assert t["name"] == "greet"
    assert t["content"] == "Hi {{name}}!"
    assert "created_at" in t
    assert "updated_at" in t


def test_add_overwrites_content():
    ut.add("greet", "v1")
    t = ut.add("greet", "v2")
    assert t["content"] == "v2"
    # created_at preserved
    assert t["created_at"] == ut.show("greet")["created_at"]


def test_show_returns_none_for_missing():
    assert ut.show("nonexistent") is None


def test_show_returns_dict():
    ut.add("hello", "Hello world!")
    t = ut.show("hello")
    assert t is not None
    assert t["content"] == "Hello world!"


# ── remove ────────────────────────────────────────────────────────────────────

def test_remove_returns_true_when_found():
    ut.add("del_me", "delete this")
    assert ut.remove("del_me") is True
    assert ut.show("del_me") is None


def test_remove_returns_false_when_missing():
    assert ut.remove("does_not_exist") is False


# ── use ───────────────────────────────────────────────────────────────────────

def test_use_no_variables():
    ut.add("plain", "Static content here.")
    result = ut.use("plain")
    assert result == "Static content here."


def test_use_substitutes_variables():
    ut.add("greet", "Hello {{name}}, welcome to {{place}}!")
    result = ut.use("greet", {"name": "Kevin", "place": "Vietnam"})
    assert result == "Hello Kevin, welcome to Vietnam!"


def test_use_partial_substitution():
    ut.add("partial", "Hi {{name}}, your role is {{role}}.")
    result = ut.use("partial", {"name": "Alice"})
    # Unresolved variables remain as-is
    assert "Alice" in result
    assert "{{role}}" in result


def test_use_returns_none_for_missing():
    assert ut.use("not_here", {}) is None


def test_use_empty_variables():
    ut.add("tmpl", "No vars here.")
    assert ut.use("tmpl", {}) == "No vars here."


# ── variables_in ──────────────────────────────────────────────────────────────

def test_variables_in_finds_vars():
    ut.add("multi", "{{a}} and {{b}} and {{a}} again")
    vs = ut.variables_in("multi")
    assert vs == ["a", "b"]  # sorted, deduplicated


def test_variables_in_none_for_missing():
    assert ut.variables_in("ghost") is None


def test_variables_in_empty_for_no_vars():
    ut.add("static", "No variables here.")
    assert ut.variables_in("static") == []


# ── CLI commands ──────────────────────────────────────────────────────────────

def _make_args(**kwargs):
    """Build a minimal argparse-like namespace."""
    ns = types.SimpleNamespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


def test_cmd_list_empty(capsys):
    ut.cmd_template_list(_make_args())
    out = capsys.readouterr().out
    assert "No templates" in out


def test_cmd_list_shows_names(capsys):
    ut.add("foo", "{{x}}")
    ut.cmd_template_list(_make_args())
    out = capsys.readouterr().out
    assert "foo" in out
    assert "{{x}}" in out


def test_cmd_add_with_content(capsys):
    ut.cmd_template_add(_make_args(name="test", content="Say {{word}}"))
    out = capsys.readouterr().out
    assert "✅" in out
    assert "test" in out
    saved = ut.show("test")
    assert saved["content"] == "Say {{word}}"


def test_cmd_show_found(capsys):
    ut.add("view_me", "Content here {{val}}")
    ut.cmd_template_show(_make_args(name="view_me"))
    out = capsys.readouterr().out
    assert "view_me" in out
    assert "Content here {{val}}" in out


def test_cmd_show_not_found(capsys):
    ut.cmd_template_show(_make_args(name="nope"))
    out = capsys.readouterr().out
    assert "not found" in out


def test_cmd_remove_found(capsys):
    ut.add("byebye", "remove me")
    ut.cmd_template_remove(_make_args(name="byebye"))
    out = capsys.readouterr().out
    assert "✅" in out
    assert ut.show("byebye") is None


def test_cmd_remove_not_found(capsys):
    ut.cmd_template_remove(_make_args(name="ghost"))
    out = capsys.readouterr().out
    assert "not found" in out


def test_cmd_use_with_vars(capsys):
    ut.add("tmpl", "Hi {{name}}, you are {{age}} years old.")
    ut.cmd_template_use(_make_args(name="tmpl", var=["name=Alice", "age=30"]))
    out = capsys.readouterr().out
    assert "Hi Alice, you are 30 years old." in out


def test_cmd_use_not_found(capsys):
    ut.cmd_template_use(_make_args(name="missing", var=[]))
    out = capsys.readouterr().out
    assert "not found" in out
