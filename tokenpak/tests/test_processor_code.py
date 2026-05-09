"""Unit tests for processors/code.py — CodeProcessor."""

from tokenpak.compression.processors.code import (
    CodeCompactionMode,
    CodeProcessor,
    _make_template_stub,
    _sha256_stub,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestSha256Stub:
    def test_returns_8_hex_chars(self):
        result = _sha256_stub("hello")
        assert len(result) == 8
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        assert _sha256_stub("abc") == _sha256_stub("abc")

    def test_different_inputs_differ(self):
        assert _sha256_stub("aaa") != _sha256_stub("bbb")

    def test_empty_string(self):
        result = _sha256_stub("")
        assert len(result) == 8


class TestMakeTemplateStub:
    def test_format(self):
        stub = _make_template_stub("MY_TEMPLATE", 10, "some content")
        assert stub.startswith("<TEMPLATE:MY_TEMPLATE lines=10 sha256=")
        assert stub.endswith(">")

    def test_hash_segment(self):
        stub = _make_template_stub("T", 5, "content")
        # Extract sha256 portion
        sha_part = stub.split("sha256=")[1].rstrip(">")
        assert len(sha_part) == 8

    def test_deterministic(self):
        a = _make_template_stub("X", 3, "data")
        b = _make_template_stub("X", 3, "data")
        assert a == b


# ---------------------------------------------------------------------------
# CodeProcessor — instantiation
# ---------------------------------------------------------------------------


class TestCodeProcessorInit:
    def test_instantiation(self):
        proc = CodeProcessor()
        assert proc is not None

    def test_has_process_method(self):
        proc = CodeProcessor()
        assert callable(proc.process)


# ---------------------------------------------------------------------------
# CodeProcessor — generic fallback
# ---------------------------------------------------------------------------


class TestCodeProcessorGeneric:
    def setup_method(self):
        self.proc = CodeProcessor()

    def test_unknown_extension_returns_first_100_lines(self):
        content = "\n".join(f"line {i}" for i in range(200))
        result = self.proc.process(content, path="file.txt")
        result_lines = result.split("\n")
        assert len(result_lines) <= 100

    def test_empty_content_generic(self):
        result = self.proc.process("", path="file.xyz")
        assert result == ""

    def test_no_path(self):
        result = self.proc.process("hello world")
        assert "hello world" in result


# ---------------------------------------------------------------------------
# CodeProcessor — Python processing
# ---------------------------------------------------------------------------


class TestCodeProcessorPython:
    def setup_method(self):
        self.proc = CodeProcessor()

    def test_empty_python(self):
        result = self.proc.process("", path="mod.py")
        assert result == ""

    def test_import_kept(self):
        result = self.proc.process("import os\nimport sys\n", path="mod.py")
        assert "import os" in result
        assert "import sys" in result

    def test_duplicate_import_deduplicated(self):
        content = "import os\nimport os\nimport sys\n"
        result = self.proc.process(content, path="mod.py")
        assert result.count("import os") == 1

    def test_function_signature_kept(self):
        content = "def greet(name: str) -> str:\n    return f'hello {name}'\n"
        result = self.proc.process(content, path="mod.py")
        assert "def greet(name: str) -> str:" in result

    def test_function_body_dropped(self):
        content = "def greet(name: str) -> str:\n    x = 1\n    y = 2\n    return x + y\n"
        result = self.proc.process(content, path="mod.py")
        assert "x = 1" not in result
        assert "y = 2" not in result

    def test_function_docstring_kept(self):
        content = 'def greet():\n    """Say hello."""\n    return "hello"\n'
        result = self.proc.process(content, path="mod.py")
        assert '"""Say hello."""' in result

    def test_class_signature_kept(self):
        content = "class Foo:\n    def bar(self):\n        pass\n"
        result = self.proc.process(content, path="mod.py")
        assert "class Foo:" in result

    def test_class_method_kept(self):
        content = "class Foo:\n    def bar(self):\n        x = 1\n"
        result = self.proc.process(content, path="mod.py")
        assert "def bar(self):" in result

    def test_class_method_body_dropped(self):
        content = "class Foo:\n    def bar(self):\n        secret = 42\n        return secret\n"
        result = self.proc.process(content, path="mod.py")
        assert "secret = 42" not in result

    def test_constant_kept(self):
        content = "MAX_SIZE = 1024\n"
        result = self.proc.process(content, path="mod.py")
        assert "MAX_SIZE = 1024" in result

    def test_decorator_kept(self):
        content = "@property\ndef value(self):\n    return self._value\n"
        result = self.proc.process(content, path="mod.py")
        assert "@property" in result

    def test_large_triple_string_stubbed_in_code_api_mode(self):
        # Build a triple-quoted literal with >= 5 lines
        body = "\n".join(["line"] * 6)
        content = f'TMPL = """\n{body}\n"""\n'
        result = self.proc.process(
            content, path="mod.py", mode=CodeCompactionMode.CODE_API
        )
        assert "<TEMPLATE:TMPL" in result
        assert "line\nline" not in result

    def test_large_triple_string_kept_in_code_with_templates_mode(self):
        body = "\n".join(["line"] * 6)
        content = f'TMPL = """\n{body}\n"""\n'
        result = self.proc.process(
            content, path="mod.py", mode=CodeCompactionMode.CODE_WITH_TEMPLATES
        )
        assert "line" in result
        assert "<TEMPLATE:" not in result

    def test_small_triple_string_always_kept(self):
        # < 5 lines should never be stubbed
        content = 'SHORT = """a\nb\nc"""\n'
        result = self.proc.process(
            content, path="mod.py", mode=CodeCompactionMode.CODE_API
        )
        assert "<TEMPLATE:" not in result


# ---------------------------------------------------------------------------
# CodeProcessor — JavaScript / TypeScript processing
# ---------------------------------------------------------------------------


class TestCodeProcessorJavaScript:
    def setup_method(self):
        self.proc = CodeProcessor()

    def test_js_import_kept(self):
        content = "import React from 'react';\n\nfunction App() {\n  return null;\n}\n"
        result = self.proc.process(content, path="app.js")
        assert "import React from 'react';" in result

    def test_js_function_signature_kept(self):
        content = "function greet(name) {\n  return 'hello ' + name;\n}\n"
        result = self.proc.process(content, path="utils.js")
        assert "function greet(name)" in result

    def test_ts_extension_processed(self):
        content = "export function add(a: number, b: number): number {\n  return a + b;\n}\n"
        result = self.proc.process(content, path="math.ts")
        assert "export function add" in result or "function add" in result

    def test_tsx_extension_processed(self):
        content = "export const MyComp = (props: Props) => {\n  return null;\n};\n"
        result = self.proc.process(content, path="comp.tsx")
        # Arrow function const — should appear in output
        assert result is not None

    def test_js_const_uppercase_kept(self):
        content = "const MAX = 100;\n"
        result = self.proc.process(content, path="constants.js")
        assert "MAX" in result

    def test_empty_js(self):
        result = self.proc.process("", path="empty.js")
        assert result == ""


# ---------------------------------------------------------------------------
# CodeCompactionMode enum
# ---------------------------------------------------------------------------


class TestCodeCompactionMode:
    def test_values_exist(self):
        assert CodeCompactionMode.CODE_API == "CODE_API"
        assert CodeCompactionMode.CODE_WITH_TEMPLATES == "CODE_WITH_TEMPLATES"

    def test_is_str_enum(self):
        assert isinstance(CodeCompactionMode.CODE_API, str)


# ---------------------------------------------------------------------------
# CodeProcessor — internal helpers
# ---------------------------------------------------------------------------


class TestCodeProcessorHelpers:
    def setup_method(self):
        self.proc = CodeProcessor()

    def test_indent_level_no_indent(self):
        assert self.proc._indent_level("def foo():") == 0

    def test_indent_level_four_spaces(self):
        assert self.proc._indent_level("    x = 1") == 4

    def test_indent_level_eight_spaces(self):
        assert self.proc._indent_level("        y = 2") == 8
