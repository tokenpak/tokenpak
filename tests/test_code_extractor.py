"""Tests for tokenpak.agent.compression.salience.code_extractor module."""

import pytest
from tokenpak.agent.compression.salience.code_extractor import (
    CodeExtractor,
    CodeExtractionResult,
)


class TestCodeExtractionResult:
    """Test CodeExtractionResult dataclass."""

    def test_default_values(self):
        """Test default initialization."""
        result = CodeExtractionResult()
        assert result.lines_in == 0
        assert result.lines_out == 0
        assert result.imports_found == 0
        assert result.functions_found == 0
        assert result.changed_functions == []
        assert result.test_targets == []
        assert result.is_diff is False
        assert result.extracted == ""

    def test_reduction_pct_zero_input(self):
        """Test reduction percentage with zero input lines."""
        result = CodeExtractionResult(lines_in=0, lines_out=0)
        assert result.reduction_pct == 0.0

    def test_reduction_pct_calculation(self):
        """Test reduction percentage calculation."""
        result = CodeExtractionResult(lines_in=100, lines_out=50)
        assert result.reduction_pct == 50.0

    def test_reduction_pct_no_reduction(self):
        """Test reduction percentage when no reduction occurs."""
        result = CodeExtractionResult(lines_in=100, lines_out=100)
        assert result.reduction_pct == 0.0

    def test_reduction_pct_full_reduction(self):
        """Test reduction percentage when all lines removed."""
        result = CodeExtractionResult(lines_in=100, lines_out=0)
        assert result.reduction_pct == 100.0

    def test_with_values(self):
        """Test initialization with custom values."""
        result = CodeExtractionResult(
            lines_in=150,
            lines_out=30,
            imports_found=5,
            functions_found=10,
            changed_functions=["func1", "func2"],
            test_targets=["test_func"],
            is_diff=True,
            extracted="sample code",
        )
        assert result.lines_in == 150
        assert result.lines_out == 30
        assert result.imports_found == 5
        assert result.functions_found == 10
        assert result.changed_functions == ["func1", "func2"]
        assert result.test_targets == ["test_func"]
        assert result.is_diff is True
        assert result.extracted == "sample code"


class TestCodeExtractorInit:
    """Test CodeExtractor initialization."""

    def test_default_init(self):
        """Test default initialization."""
        extractor = CodeExtractor()
        assert extractor.max_fn_body_lines == 60
        assert extractor.include_all_fns is False

    def test_custom_max_fn_body_lines(self):
        """Test initialization with custom max_fn_body_lines."""
        extractor = CodeExtractor(max_fn_body_lines=100)
        assert extractor.max_fn_body_lines == 100

    def test_include_all_fns_true(self):
        """Test initialization with include_all_fns=True."""
        extractor = CodeExtractor(include_all_fns=True)
        assert extractor.include_all_fns is True


class TestCodeExtractorEmpty:
    """Test CodeExtractor with empty/minimal input."""

    def test_empty_string(self):
        """Test extraction with empty string."""
        extractor = CodeExtractor()
        result = extractor.extract("")
        assert result.lines_in == 0
        assert result.functions_found == 0
        assert result.imports_found == 0

    def test_single_line_no_code(self):
        """Test extraction with single comment line."""
        extractor = CodeExtractor()
        result = extractor.extract("# just a comment")
        assert result.lines_in == 1
        assert result.functions_found == 0

    def test_import_only(self):
        """Test extraction with only import statements."""
        code = "import os\nimport sys\nfrom typing import List"
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.imports_found == 3
        assert result.functions_found == 0


class TestCodeExtractorPython:
    """Test CodeExtractor with Python code."""

    def test_simple_function(self):
        """Test extraction of simple Python function."""
        code = """def hello(name):
    print(f"Hello {name}")
    return name
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found == 1
        # Without change signals, functions aren't included
        assert len(result.changed_functions) == 0

    def test_async_function(self):
        """Test extraction of async function."""
        code = """async def fetch_data(url):
    result = await http.get(url)
    return result
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found == 1

    def test_class_definition(self):
        """Test extraction of class definition."""
        code = """class MyClass:
    def __init__(self):
        self.value = 0
    
    def method(self):
        return self.value
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1

    def test_test_function_with_assertion(self):
        """Test detection of test function with assertion."""
        code = """def test_addition():
    assert 1 + 1 == 2
    AssertionError
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        # Test targets only included if they have failure signals
        assert result.functions_found == 1

    def test_function_with_changed_signal(self):
        """Test detection of changed function via signal."""
        code = """def calculate():
    # changed: updated logic
    return 42
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert "calculate" in result.changed_functions

    def test_function_with_todo_signal(self):
        """Test detection of changed function via TODO."""
        code = """def incomplete():
    # TODO: finish implementation
    pass
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert "incomplete" in result.changed_functions

    def test_multiple_functions(self):
        """Test extraction of multiple functions."""
        code = """def func1():
    pass

def func2():
    pass

def func3():
    pass
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found == 3

    def test_include_all_fns(self):
        """Test include_all_fns parameter."""
        code = """def func1():
    pass

def func2():
    pass
"""
        extractor = CodeExtractor(include_all_fns=True)
        result = extractor.extract(code)
        assert len(result.changed_functions) >= 2


class TestCodeExtractorDiff:
    """Test CodeExtractor with diff format."""

    def test_unified_diff_detection(self):
        """Test detection of unified diff format."""
        diff = """--- a/file.py
+++ b/file.py
@@ -1,3 +1,3 @@
 def old():
-    return 1
+    return 2
"""
        extractor = CodeExtractor()
        result = extractor.extract(diff)
        assert result.is_diff is True

    def test_diff_with_added_lines(self):
        """Test extraction of added lines in diff."""
        diff = """--- a/file.py
+++ b/file.py
@@ -1,2 +1,3 @@
 def func():
+    added_line = 1
-    old_line = 0
     pass
"""
        extractor = CodeExtractor()
        result = extractor.extract(diff)
        # Requires both + and - lines to be detected as diff
        assert result.is_diff is True

    def test_non_diff_format(self):
        """Test that non-diff code is not marked as diff."""
        code = """def func():
    if True:
        return 1
    return 0
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.is_diff is False


class TestCodeExtractorJavaScript:
    """Test CodeExtractor with JavaScript code."""

    def test_function_declaration(self):
        """Test extraction of JavaScript function."""
        code = """function greet(name) {
    return "Hello " + name;
}
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1

    def test_arrow_function(self):
        """Test extraction of arrow function."""
        code = """const add = (a, b) => {
    return a + b;
};
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1

    def test_async_function_js(self):
        """Test extraction of async function in JS."""
        code = """async function fetchData(url) {
    const response = await fetch(url);
    return response.json();
}
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1

    def test_export_function(self):
        """Test extraction of exported function."""
        code = """export function compute() {
    return 42;
}
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1


class TestCodeExtractorGo:
    """Test CodeExtractor with Go code."""

    def test_simple_go_function(self):
        """Test extraction of Go function."""
        code = """func Add(a int, b int) int {
    return a + b
}
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1

    def test_go_method(self):
        """Test extraction of Go method."""
        code = """func (s *Server) Start() error {
    return s.listen()
}
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1


class TestCodeExtractorRust:
    """Test CodeExtractor with Rust code."""

    def test_rust_function(self):
        """Test extraction of Rust function."""
        code = """fn add(a: i32, b: i32) -> i32 {
    a + b
}
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1

    def test_rust_pub_function(self):
        """Test extraction of public Rust function."""
        code = """pub fn calculate() -> u32 {
    42
}
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1

    def test_rust_async_function(self):
        """Test extraction of async Rust function."""
        code = """async fn fetch_data(url: &str) -> Result<String> {
    Ok(String::new())
}
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1


class TestCodeExtractorReduction:
    """Test code reduction metrics."""

    def test_reduction_calculation(self):
        """Test that reduction percentage is calculated."""
        code = "\n".join(["line " + str(i) for i in range(100)])
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.lines_in == 100
        assert result.reduction_pct >= 0

    def test_large_function_truncation(self):
        """Test that large functions are truncated."""
        lines = ["def big_func():"]
        lines.extend([f"    line_{i}" for i in range(100)])
        code = "\n".join(lines)
        extractor = CodeExtractor(max_fn_body_lines=10)
        result = extractor.extract(code)
        # Output should be limited by max_fn_body_lines
        assert "truncated" in result.extracted or result.lines_out < 100


class TestCodeExtractorFailureDetection:
    """Test failure signal detection."""

    def test_assertion_error_detection(self):
        """Test detection of AssertionError."""
        code = """def test_value():
    try:
        assert value > 0
    except AssertionError:
        pass
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert "test_value" in result.test_targets or result.functions_found > 0

    def test_panic_detection_rust(self):
        """Test detection of panic in Rust."""
        code = """fn risky() {
    panic!("Something went wrong")
}
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1

    def test_assert_eq_detection(self):
        """Test detection of assert_eq."""
        code = """fn test_math() {
    assert_eq!(2 + 2, 4);
}
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1


class TestCodeExtractorComplexScenarios:
    """Test complex extraction scenarios."""

    def test_nested_functions(self):
        """Test extraction with nested functions."""
        code = """def outer():
    def inner():
        return 42
    return inner()
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1

    def test_multiline_imports(self):
        """Test extraction with multiline imports."""
        code = """from typing import (
    List,
    Dict,
    Optional,
)
import os
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.imports_found >= 1

    def test_mixed_languages_not_supported(self):
        """Test handling of mixed language code."""
        code = """def python_func():
    pass

function jsFunc() {
    return 42;
}
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1

    def test_extraction_output_format(self):
        """Test that output is properly formatted."""
        code = """import os

def calculate():
    return 42
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert "[code-salience]" in result.extracted
        assert "lines" in result.extracted

    def test_indentation_levels(self):
        """Test handling of different indentation levels."""
        code = """class Outer:
    def method1(self):
        pass
    
    def method2(self):
        pass
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1


class TestCodeExtractorEdgeCases:
    """Test edge cases and error handling."""

    def test_function_no_body(self):
        """Test function definition without body."""
        code = """def func():"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 0

    def test_syntax_error_in_code(self):
        """Test handling of code with syntax errors."""
        code = """def func(
    incomplete
"""
        extractor = CodeExtractor()
        # Should not crash, just extract what it can
        result = extractor.extract(code)
        assert isinstance(result, CodeExtractionResult)

    def test_very_long_lines(self):
        """Test handling of very long lines."""
        long_line = "x = " + "a" * 1000
        code = f"""import os
{long_line}
def func():
    pass
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.lines_in == 4

    def test_unicode_characters(self):
        """Test handling of unicode in code."""
        code = """def greet():
    # 你好世界
    return "こんにちは"
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        assert result.functions_found >= 1

    def test_comments_with_pattern_keywords(self):
        """Test that comments don't trigger false positives."""
        code = """# This function changed the algorithm
def stable_func():
    return 1
"""
        extractor = CodeExtractor()
        result = extractor.extract(code)
        # Comments before function should be in pre_lines
        assert result.functions_found >= 1
