"""Unit tests for processors/code_treesitter.py — TreeSitterProcessor."""

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# EXTENSION_TO_LANG — module-level constant
# ---------------------------------------------------------------------------


class TestExtensionToLang:
    def test_py_maps_to_python(self):
        from tokenpak.compression.processors.code_treesitter import EXTENSION_TO_LANG

        assert EXTENSION_TO_LANG[".py"] == "python"

    def test_js_maps_to_javascript(self):
        from tokenpak.compression.processors.code_treesitter import EXTENSION_TO_LANG

        assert EXTENSION_TO_LANG[".js"] == "javascript"

    def test_jsx_maps_to_javascript(self):
        from tokenpak.compression.processors.code_treesitter import EXTENSION_TO_LANG

        assert EXTENSION_TO_LANG[".jsx"] == "javascript"

    def test_ts_maps_to_typescript(self):
        from tokenpak.compression.processors.code_treesitter import EXTENSION_TO_LANG

        assert EXTENSION_TO_LANG[".ts"] == "typescript"

    def test_tsx_maps_to_typescript(self):
        from tokenpak.compression.processors.code_treesitter import EXTENSION_TO_LANG

        assert EXTENSION_TO_LANG[".tsx"] == "typescript"

    def test_go_maps_to_go(self):
        from tokenpak.compression.processors.code_treesitter import EXTENSION_TO_LANG

        assert EXTENSION_TO_LANG[".go"] == "go"

    def test_rs_maps_to_rust(self):
        from tokenpak.compression.processors.code_treesitter import EXTENSION_TO_LANG

        assert EXTENSION_TO_LANG[".rs"] == "rust"

    def test_has_seven_entries(self):
        from tokenpak.compression.processors.code_treesitter import EXTENSION_TO_LANG

        assert len(EXTENSION_TO_LANG) == 7


# ---------------------------------------------------------------------------
# _detect_language
# ---------------------------------------------------------------------------


class TestDetectLanguage:
    def test_py_extension(self):
        from tokenpak.compression.processors.code_treesitter import _detect_language

        assert _detect_language("myfile.py") == "python"

    def test_js_extension(self):
        from tokenpak.compression.processors.code_treesitter import _detect_language

        assert _detect_language("app.js") == "javascript"

    def test_jsx_extension(self):
        from tokenpak.compression.processors.code_treesitter import _detect_language

        assert _detect_language("component.jsx") == "javascript"

    def test_ts_extension(self):
        from tokenpak.compression.processors.code_treesitter import _detect_language

        assert _detect_language("app.ts") == "typescript"

    def test_tsx_extension(self):
        from tokenpak.compression.processors.code_treesitter import _detect_language

        assert _detect_language("component.tsx") == "typescript"

    def test_go_extension(self):
        from tokenpak.compression.processors.code_treesitter import _detect_language

        assert _detect_language("main.go") == "go"

    def test_rs_extension(self):
        from tokenpak.compression.processors.code_treesitter import _detect_language

        assert _detect_language("lib.rs") == "rust"

    def test_unknown_extension_returns_none(self):
        from tokenpak.compression.processors.code_treesitter import _detect_language

        assert _detect_language("readme.txt") is None

    def test_no_extension_returns_none(self):
        from tokenpak.compression.processors.code_treesitter import _detect_language

        assert _detect_language("Makefile") is None

    def test_nested_path_with_py(self):
        from tokenpak.compression.processors.code_treesitter import _detect_language

        assert _detect_language("/some/deeply/nested/path/module.py") == "python"

    def test_uppercase_extension_not_matched(self):
        from tokenpak.compression.processors.code_treesitter import _detect_language

        # _detect_language lower-cases the suffix — .PY becomes .py
        assert _detect_language("file.PY") == "python"

    def test_empty_path_returns_none(self):
        from tokenpak.compression.processors.code_treesitter import _detect_language

        assert _detect_language("") is None


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:
    def test_returns_bool(self):
        from tokenpak.compression.processors.code_treesitter import is_available

        result = is_available()
        assert isinstance(result, bool)

    def test_false_when_ts_library_not_installed(self):
        """tree_sitter_languages is not installed in the test environment."""
        from tokenpak.compression.processors.code_treesitter import is_available

        assert is_available() is False

    def test_reflects_ts_available_flag(self):
        import tokenpak.compression.processors.code_treesitter as mod

        with patch.object(mod, "_TS_AVAILABLE", True):
            assert mod.is_available() is True
        with patch.object(mod, "_TS_AVAILABLE", False):
            assert mod.is_available() is False


# ---------------------------------------------------------------------------
# _text helper
# ---------------------------------------------------------------------------


class TestTextHelper:
    def test_decodes_node_text(self):
        from tokenpak.compression.processors.code_treesitter import _text

        node = MagicMock()
        node.text = b"hello world"
        assert _text(node) == "hello world"

    def test_none_text_returns_empty(self):
        from tokenpak.compression.processors.code_treesitter import _text

        node = MagicMock()
        node.text = None
        assert _text(node) == ""

    def test_empty_bytes_returns_empty_string(self):
        from tokenpak.compression.processors.code_treesitter import _text

        node = MagicMock()
        node.text = b""
        assert _text(node) == ""

    def test_bytes_with_unicode(self):
        from tokenpak.compression.processors.code_treesitter import _text

        node = MagicMock()
        node.text = "héllo".encode("utf-8")
        assert _text(node) == "héllo"


# ---------------------------------------------------------------------------
# _sig_before_body helper
# ---------------------------------------------------------------------------


class TestSigBeforeBody:
    def _make_node(self, text: str, children=None):
        node = MagicMock()
        node.text = text.encode("utf-8")
        node.start_byte = 0
        node.children = children or []
        return node

    def test_no_body_child_returns_full_text(self):
        from tokenpak.compression.processors.code_treesitter import _sig_before_body

        node = self._make_node("def foo():")
        result = _sig_before_body(node, ("block",))
        assert result == "def foo():"

    def test_body_child_trims_at_offset(self):
        from tokenpak.compression.processors.code_treesitter import _sig_before_body

        src = "def foo():\n    pass"
        node = self._make_node(src)

        body_child = MagicMock()
        body_child.type = "block"
        body_child.start_byte = len("def foo():")  # offset 10
        node.children = [body_child]

        result = _sig_before_body(node, ("block",))
        assert "def foo():" in result
        assert "pass" not in result

    def test_non_body_children_do_not_truncate(self):
        from tokenpak.compression.processors.code_treesitter import _sig_before_body

        node = self._make_node("def foo(a, b):")

        non_body = MagicMock()
        non_body.type = "parameters"
        node.children = [non_body]

        result = _sig_before_body(node, ("block",))
        assert "def foo(a, b):" in result

    def test_multiple_body_types_tuple(self):
        from tokenpak.compression.processors.code_treesitter import _sig_before_body

        src = "function foo() {"
        node = self._make_node(src)

        body_child = MagicMock()
        body_child.type = "statement_block"
        body_child.start_byte = len("function foo() ")
        node.children = [body_child]

        result = _sig_before_body(node, ("statement_block", "block"))
        assert "function foo()" in result
        assert "{" not in result


# ---------------------------------------------------------------------------
# _first_docstring helper
# ---------------------------------------------------------------------------


class TestFirstDocstring:
    def test_returns_none_for_empty_block(self):
        from tokenpak.compression.processors.code_treesitter import _first_docstring

        block = MagicMock()
        block.children = []
        assert _first_docstring(block) is None

    def test_returns_none_when_first_non_comment_is_not_expression(self):
        from tokenpak.compression.processors.code_treesitter import _first_docstring

        block = MagicMock()
        child = MagicMock()
        child.type = "return_statement"
        block.children = [child]
        assert _first_docstring(block) is None

    def test_finds_docstring_in_expression_statement(self):
        from tokenpak.compression.processors.code_treesitter import _first_docstring

        block = MagicMock()

        string_node = MagicMock()
        string_node.type = "string"

        expr_stmt = MagicMock()
        expr_stmt.type = "expression_statement"
        expr_stmt.children = [string_node]
        expr_stmt.text = b'"""My docstring."""'

        block.children = [expr_stmt]
        result = _first_docstring(block)
        assert result == '"""My docstring."""'

    def test_skips_newline_before_expression(self):
        from tokenpak.compression.processors.code_treesitter import _first_docstring

        block = MagicMock()

        newline = MagicMock()
        newline.type = "\n"

        string_node = MagicMock()
        string_node.type = "string"

        expr_stmt = MagicMock()
        expr_stmt.type = "expression_statement"
        expr_stmt.children = [string_node]
        expr_stmt.text = b'"""docstring"""'

        block.children = [newline, expr_stmt]
        result = _first_docstring(block)
        assert result == '"""docstring"""'

    def test_skips_comment_before_expression(self):
        from tokenpak.compression.processors.code_treesitter import _first_docstring

        block = MagicMock()

        comment = MagicMock()
        comment.type = "comment"

        string_node = MagicMock()
        string_node.type = "string"

        expr_stmt = MagicMock()
        expr_stmt.type = "expression_statement"
        expr_stmt.children = [string_node]
        expr_stmt.text = b'"""docstring"""'

        block.children = [comment, expr_stmt]
        result = _first_docstring(block)
        assert result == '"""docstring"""'

    def test_expression_without_string_child_returns_none(self):
        from tokenpak.compression.processors.code_treesitter import _first_docstring

        block = MagicMock()

        non_string = MagicMock()
        non_string.type = "call"

        expr_stmt = MagicMock()
        expr_stmt.type = "expression_statement"
        expr_stmt.children = [non_string]

        block.children = [expr_stmt]
        result = _first_docstring(block)
        assert result is None


# ---------------------------------------------------------------------------
# extract() — public API
# ---------------------------------------------------------------------------


class TestExtract:
    def test_returns_none_when_ts_unavailable(self):
        from tokenpak.compression.processors.code_treesitter import extract

        # tree_sitter_languages not installed in test env
        result = extract("def foo(): pass", "myfile.py")
        assert result is None

    def test_returns_none_for_unknown_extension_when_ts_unavailable(self):
        from tokenpak.compression.processors.code_treesitter import extract

        result = extract("some data", "file.txt")
        assert result is None

    def test_returns_none_when_ts_available_but_lang_unsupported(self):
        import tokenpak.compression.processors.code_treesitter as mod

        with patch.object(mod, "_TS_AVAILABLE", True):
            result = mod.extract("some data", "file.txt")
        assert result is None

    def test_returns_none_when_ts_available_and_no_path(self):
        import tokenpak.compression.processors.code_treesitter as mod

        with patch.object(mod, "_TS_AVAILABLE", True):
            result = mod.extract("def foo(): pass", "")
        assert result is None

    def test_returns_none_on_parse_exception(self):
        import tokenpak.compression.processors.code_treesitter as mod

        with (
            patch.object(mod, "_TS_AVAILABLE", True),
            patch.object(
                mod, "_ts_get_parser", create=True, side_effect=RuntimeError("parse failure")
            ),
        ):
            result = mod.extract("def foo(): pass", "myfile.py")
        assert result is None

    def test_returns_extracted_string_when_ts_succeeds(self):
        import tokenpak.compression.processors.code_treesitter as mod

        mock_tree = MagicMock()
        mock_tree.root_node.children = []

        mock_parser = MagicMock()
        mock_parser.parse.return_value = mock_tree

        with (
            patch.object(mod, "_TS_AVAILABLE", True),
            patch.object(mod, "_ts_get_parser", create=True, return_value=mock_parser),
        ):
            result = mod.extract("", "myfile.py")
        # Empty root → empty output is falsy; extract returns the joined string
        assert result is not None


# ---------------------------------------------------------------------------
# TreeSitterProcessor — initialization
# ---------------------------------------------------------------------------


class TestTreeSitterProcessorInit:
    def test_instantiation_without_fallback(self):
        from tokenpak.compression.processors.code_treesitter import TreeSitterProcessor

        proc = TreeSitterProcessor()
        assert proc is not None

    def test_default_fallback_is_code_processor(self):
        from tokenpak.compression.processors.code import CodeProcessor
        from tokenpak.compression.processors.code_treesitter import TreeSitterProcessor

        proc = TreeSitterProcessor()
        assert isinstance(proc._fallback, CodeProcessor)

    def test_custom_fallback_stored(self):
        from tokenpak.compression.processors.code_treesitter import TreeSitterProcessor

        mock_fallback = MagicMock()
        proc = TreeSitterProcessor(fallback=mock_fallback)
        assert proc._fallback is mock_fallback

    def test_has_process_method(self):
        from tokenpak.compression.processors.code_treesitter import TreeSitterProcessor

        proc = TreeSitterProcessor()
        assert callable(proc.process)


# ---------------------------------------------------------------------------
# TreeSitterProcessor.process() — fallback behavior
# ---------------------------------------------------------------------------


class TestTreeSitterProcessorProcess:
    def test_delegates_to_fallback_when_ts_unavailable(self):
        """With tree-sitter absent, process() must call the fallback."""
        from tokenpak.compression.processors.code_treesitter import TreeSitterProcessor

        mock_fallback = MagicMock()
        mock_fallback.process.return_value = "fallback result"

        proc = TreeSitterProcessor(fallback=mock_fallback)
        result = proc.process("def foo(): pass", path="myfile.py")

        mock_fallback.process.assert_called_once_with("def foo(): pass", "myfile.py")
        assert result == "fallback result"

    def test_delegates_to_fallback_for_unsupported_extension(self):
        from tokenpak.compression.processors.code_treesitter import TreeSitterProcessor

        mock_fallback = MagicMock()
        mock_fallback.process.return_value = "generic output"

        proc = TreeSitterProcessor(fallback=mock_fallback)
        result = proc.process("some text", path="file.md")

        mock_fallback.process.assert_called_once()
        assert result == "generic output"

    def test_returns_ts_result_when_extract_succeeds(self):
        import tokenpak.compression.processors.code_treesitter as mod
        from tokenpak.compression.processors.code_treesitter import TreeSitterProcessor

        mock_fallback = MagicMock()
        proc = TreeSitterProcessor(fallback=mock_fallback)

        with patch.object(mod, "extract", return_value="extracted API surface"):
            result = proc.process("def foo(): pass", path="myfile.py")

        assert result == "extracted API surface"
        mock_fallback.process.assert_not_called()

    def test_falls_back_when_extract_returns_none(self):
        import tokenpak.compression.processors.code_treesitter as mod
        from tokenpak.compression.processors.code_treesitter import TreeSitterProcessor

        mock_fallback = MagicMock()
        mock_fallback.process.return_value = "code processor result"
        proc = TreeSitterProcessor(fallback=mock_fallback)

        with patch.object(mod, "extract", return_value=None):
            result = proc.process("def foo(): pass", path="myfile.py")

        assert result == "code processor result"
        mock_fallback.process.assert_called_once()

    def test_falls_back_when_extract_returns_only_whitespace(self):
        import tokenpak.compression.processors.code_treesitter as mod
        from tokenpak.compression.processors.code_treesitter import TreeSitterProcessor

        mock_fallback = MagicMock()
        mock_fallback.process.return_value = "fallback"
        proc = TreeSitterProcessor(fallback=mock_fallback)

        with patch.object(mod, "extract", return_value="   \n  "):
            result = proc.process("def foo(): pass", path="myfile.py")

        assert result == "fallback"
        mock_fallback.process.assert_called_once()

    def test_empty_content_uses_code_processor_fallback(self):
        from tokenpak.compression.processors.code_treesitter import TreeSitterProcessor

        proc = TreeSitterProcessor()
        result = proc.process("", path="myfile.py")
        assert result == ""

    def test_process_with_default_empty_path(self):
        from tokenpak.compression.processors.code_treesitter import TreeSitterProcessor

        mock_fallback = MagicMock()
        mock_fallback.process.return_value = "out"
        proc = TreeSitterProcessor(fallback=mock_fallback)
        result = proc.process("content")
        assert result == "out"
        mock_fallback.process.assert_called_once_with("content", "")
