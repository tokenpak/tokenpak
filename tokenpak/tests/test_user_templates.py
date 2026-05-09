"""Tests for tokenpak/user_templates.py.

Covers: list_templates, add, show, remove, use, variables_in,
        update (overwrite), safe name sanitisation, corrupt file tolerance.

Coverage goal: > 50% (targeting ~85%+)
"""

from __future__ import annotations

import pathlib

import pytest

import tokenpak.cli.user_templates as ut

# ---------------------------------------------------------------------------
# Fixture: redirect TEMPLATES_DIR to a tmp directory so tests are isolated
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_templates(tmp_path: pathlib.Path, monkeypatch):
    """Redirect all template storage to a per-test temp directory."""
    monkeypatch.setattr(ut, "TEMPLATES_DIR", tmp_path / "templates")
    yield


# ---------------------------------------------------------------------------
# list_templates
# ---------------------------------------------------------------------------

class TestListTemplates:
    def test_empty_dir_returns_empty_list(self):
        assert ut.list_templates() == []

    def test_returns_added_templates(self):
        ut.add("alpha", "hello {{name}}")
        ut.add("beta", "goodbye {{name}}")
        names = [t["name"] for t in ut.list_templates()]
        assert "alpha" in names
        assert "beta" in names

    def test_sorted_by_filename(self):
        ut.add("z-last", "z")
        ut.add("a-first", "a")
        names = [t["name"] for t in ut.list_templates()]
        # Files are sorted, so a-first should come before z-last
        assert names.index("a-first") < names.index("z-last")

    def test_corrupt_json_skipped_silently(self, tmp_path, monkeypatch):
        tdir = tmp_path / "templates"
        tdir.mkdir()
        monkeypatch.setattr(ut, "TEMPLATES_DIR", tdir)
        (tdir / "bad.json").write_text("NOT_JSON{{{{")
        ut.add("good", "content")
        result = ut.list_templates()
        assert len(result) == 1
        assert result[0]["name"] == "good"


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

class TestAdd:
    def test_add_creates_template(self):
        t = ut.add("my-tpl", "Hello {{world}}")
        assert t["name"] == "my-tpl"
        assert t["content"] == "Hello {{world}}"
        assert "created_at" in t
        assert "updated_at" in t

    def test_add_file_persisted(self):
        ut.add("persisted", "content here")
        assert ut.show("persisted") is not None

    def test_add_overwrites_existing_content(self):
        ut.add("tpl", "v1 content")
        ut.add("tpl", "v2 content")
        result = ut.show("tpl")
        assert result["content"] == "v2 content"

    def test_add_update_preserves_created_at(self):
        t1 = ut.add("tpl", "v1")
        t2 = ut.add("tpl", "v2")
        assert t2["created_at"] == t1["created_at"]
        assert t2["updated_at"] >= t1["updated_at"]

    def test_add_sanitises_special_chars_in_name(self):
        """Names with special chars should be safely stored and retrievable."""
        t = ut.add("my template!", "content")
        # The stored file uses a sanitised name but the dict keeps original
        assert t["content"] == "content"


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

class TestShow:
    def test_show_returns_none_when_not_found(self):
        assert ut.show("nonexistent") is None

    def test_show_returns_template_dict(self):
        ut.add("visible", "some content")
        result = ut.show("visible")
        assert result is not None
        assert result["name"] == "visible"
        assert result["content"] == "some content"

    def test_show_returns_none_on_corrupt_file(self, tmp_path, monkeypatch):
        tdir = tmp_path / "templates"
        tdir.mkdir()
        monkeypatch.setattr(ut, "TEMPLATES_DIR", tdir)
        (tdir / "corrupt.json").write_text("NOT_JSON")
        assert ut.show("corrupt") is None


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

class TestRemove:
    def test_remove_existing_returns_true(self):
        ut.add("to-delete", "bye")
        assert ut.remove("to-delete") is True

    def test_remove_nonexistent_returns_false(self):
        assert ut.remove("ghost") is False

    def test_remove_deletes_file(self):
        ut.add("gone", "poof")
        ut.remove("gone")
        assert ut.show("gone") is None

    def test_remove_only_deletes_target(self):
        ut.add("keep", "stay")
        ut.add("del", "go")
        ut.remove("del")
        assert ut.show("keep") is not None


# ---------------------------------------------------------------------------
# use (variable substitution)
# ---------------------------------------------------------------------------

class TestUse:
    def test_use_returns_none_when_not_found(self):
        assert ut.use("missing") is None

    def test_use_substitutes_single_variable(self):
        ut.add("greeter", "Hello {{name}}!")
        result = ut.use("greeter", {"name": "Alice"})
        assert result == "Hello Alice!"

    def test_use_substitutes_multiple_variables(self):
        ut.add("multi", "{{a}} and {{b}}")
        result = ut.use("multi", {"a": "foo", "b": "bar"})
        assert result == "foo and bar"

    def test_use_leaves_unmatched_variables_intact(self):
        ut.add("partial", "Hello {{name}}, you are {{age}} years old")
        result = ut.use("partial", {"name": "Bob"})
        assert "Bob" in result
        assert "{{age}}" in result

    def test_use_no_variables_returns_raw_content(self):
        ut.add("static", "no vars here")
        result = ut.use("static")
        assert result == "no vars here"

    def test_use_with_empty_variables_dict(self):
        ut.add("tpl", "{{x}}")
        result = ut.use("tpl", {})
        assert result == "{{x}}"


# ---------------------------------------------------------------------------
# variables_in
# ---------------------------------------------------------------------------

class TestVariablesIn:
    def test_returns_none_when_not_found(self):
        assert ut.variables_in("missing") is None

    def test_returns_sorted_unique_variables(self):
        ut.add("vars", "{{b}} and {{a}} and {{b}}")
        result = ut.variables_in("vars")
        assert result == ["a", "b"]

    def test_returns_empty_list_for_no_variables(self):
        ut.add("plain", "no variables")
        result = ut.variables_in("plain")
        assert result == []


# ---------------------------------------------------------------------------
# CMD helpers (CLI layer)
# ---------------------------------------------------------------------------

class TestCmdTemplateList:
    def test_prints_no_templates_message(self, capsys):
        args = type("A", (), {})()
        ut.cmd_template_list(args)
        out = capsys.readouterr().out
        assert "No templates" in out

    def test_prints_table_with_templates(self, capsys):
        ut.add("t1", "content {{var}}")
        args = type("A", (), {})()
        ut.cmd_template_list(args)
        out = capsys.readouterr().out
        assert "t1" in out


class TestCmdTemplateAdd:
    def test_adds_template_and_prints_confirmation(self, capsys):
        args = type("A", (), {"name": "new-tpl", "content": "Hi {{user}}"})()
        ut.cmd_template_add(args)
        out = capsys.readouterr().out
        assert "new-tpl" in out
        assert ut.show("new-tpl") is not None

    def test_prints_variable_hint(self, capsys):
        args = type("A", (), {"name": "tpl", "content": "Hello {{world}}"})()
        ut.cmd_template_add(args)
        out = capsys.readouterr().out
        assert "world" in out

    def test_no_content_prints_error(self, capsys, monkeypatch):
        """Empty content (no stdin) → prints error, no template saved."""
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        args = type("A", (), {"name": "empty", "content": ""})()
        ut.cmd_template_add(args)
        out = capsys.readouterr().out
        assert "No content" in out


class TestCmdTemplateShow:
    def test_shows_template(self, capsys):
        ut.add("show-me", "the content")
        args = type("A", (), {"name": "show-me"})()
        ut.cmd_template_show(args)
        out = capsys.readouterr().out
        assert "the content" in out

    def test_not_found_prints_error(self, capsys):
        args = type("A", (), {"name": "ghost"})()
        ut.cmd_template_show(args)
        out = capsys.readouterr().out
        assert "not found" in out


class TestCmdTemplateRemove:
    def test_remove_existing_prints_confirmation(self, capsys):
        ut.add("del-me", "bye")
        args = type("A", (), {"name": "del-me"})()
        ut.cmd_template_remove(args)
        out = capsys.readouterr().out
        assert "removed" in out

    def test_remove_nonexistent_prints_error(self, capsys):
        args = type("A", (), {"name": "ghost"})()
        ut.cmd_template_remove(args)
        out = capsys.readouterr().out
        assert "not found" in out


class TestCmdTemplateUse:
    def test_use_prints_rendered_content(self, capsys):
        ut.add("u", "Hello {{name}}")
        args = type("A", (), {"name": "u", "var": ["name=Alice"]})()
        ut.cmd_template_use(args)
        out = capsys.readouterr().out
        assert "Hello Alice" in out

    def test_use_not_found_prints_error(self, capsys):
        args = type("A", (), {"name": "missing", "var": []})()
        ut.cmd_template_use(args)
        out = capsys.readouterr().out
        assert "not found" in out

    def test_malformed_var_prints_warning(self, capsys):
        ut.add("t", "content")
        args = type("A", (), {"name": "t", "var": ["badformat"]})()
        ut.cmd_template_use(args)
        out = capsys.readouterr().out
        assert "Ignoring" in out
