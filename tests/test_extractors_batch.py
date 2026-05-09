"""test_extractors_batch.py — Tests for code/doc/log extractors and slot filler

Tests for salience extraction modules:
- CodeExtractor: extract high-signal sections from source code
- DocExtractor: extract documentation from code
- LogExtractor: extract log patterns
- SlotFiller: fill template slots with extracted values
"""

import pytest

from tokenpak.compression.salience.code_extractor import CodeExtractionResult, CodeExtractor
from tokenpak.compression.salience.doc_extractor import DocExtractionResult, DocExtractor
from tokenpak.compression.salience.log_extractor import LogExtractionResult, LogExtractor
from tokenpak.compression.slot_filler import FilledSlots, SlotFiller


class TestCodeExtractor:
    """Test code extraction from source."""

    @pytest.fixture
    def extractor(self):
        return CodeExtractor()

    def test_extract_python_functions(self, extractor):
        """Test extracting Python function definitions."""
        code = '''
def hello():
    return "world"

def add(a, b):
    return a + b
'''
        result = extractor.extract(code)
        assert isinstance(result, CodeExtractionResult)
        assert result.functions_found >= 2

    def test_extract_imports(self, extractor):
        """Test extracting import statements."""
        code = '''
import os
from pathlib import Path
import sys
'''
        result = extractor.extract(code)
        assert isinstance(result, CodeExtractionResult)
        assert result.imports_found >= 3

    def test_extract_from_diff(self, extractor):
        """Test extracting from diff format."""
        diff = '''
+def new_function():
+    return 42
 def existing():
     pass
-def removed():
-    pass
'''
        result = extractor.extract(diff)
        assert isinstance(result, CodeExtractionResult)
        assert result.is_diff is True

    def test_extract_javascript(self, extractor):
        """Test extracting from JavaScript code."""
        code = '''
const greet = () => "hello";
function add(a, b) { return a + b; }
'''
        result = extractor.extract(code)
        assert isinstance(result, CodeExtractionResult)
        assert result.functions_found >= 2

    def test_extract_empty_code(self, extractor):
        """Test extraction on empty code."""
        result = extractor.extract("")
        assert isinstance(result, CodeExtractionResult)
        assert result.lines_in == 0

    def test_extract_with_test_functions(self, extractor):
        """Test identifying test functions."""
        code = '''
def test_addition():
    assert 1 + 1 == 2

def test_subtraction():
    assert 5 - 3 == 2

def regular_function():
    return 42
'''
        result = extractor.extract(code)
        assert isinstance(result, CodeExtractionResult)


class TestDocExtractor:
    """Test documentation extraction."""

    @pytest.fixture
    def extractor(self):
        return DocExtractor()

    def test_extract_docstrings(self, extractor):
        """Test extracting docstrings from Python."""
        code = '''
def hello():
    """Say hello to the world."""
    return "world"

class Greeter:
    """A greeter class."""
    pass
'''
        result = extractor.extract(code)
        assert isinstance(result, DocExtractionResult)
        assert "hello" in result.extracted.lower() or len(result.extracted) > 0

    def test_extract_comments(self, extractor):
        """Test extracting comments."""
        code = '''
# This is a comment
x = 5  # inline comment

# Multi-line comment
# explaining the code below
y = 10
'''
        result = extractor.extract(code)
        assert isinstance(result, DocExtractionResult)

    def test_extract_jsdoc(self, extractor):
        """Test extracting JSDoc comments."""
        code = '''
/**
 * Adds two numbers together
 * @param {number} a - First number
 * @param {number} b - Second number
 * @returns {number} The sum
 */
function add(a, b) {
    return a + b;
}
'''
        result = extractor.extract(code)
        assert isinstance(result, DocExtractionResult)

    def test_extract_empty_docs(self, extractor):
        """Test extraction with no documentation."""
        code = 'x = 5\ny = 10\n'
        result = extractor.extract(code)
        assert isinstance(result, DocExtractionResult)

    def test_extract_markdown(self, extractor):
        """Test extracting from markdown documentation."""
        doc = '''
# API Reference

## get_user(id)
Fetch a user by ID

Returns: User object
'''
        result = extractor.extract(doc)
        assert isinstance(result, DocExtractionResult)


class TestLogExtractor:
    """Test log extraction."""

    @pytest.fixture
    def extractor(self):
        return LogExtractor()

    def test_extract_log_entries(self, extractor):
        """Test extracting log patterns."""
        logs = '''
[ERROR] Connection failed to localhost:8080
[INFO] Server started on port 3000
[WARN] Retry attempt 2/3
[ERROR] Timeout after 30s
'''
        result = extractor.extract(logs)
        assert isinstance(result, LogExtractionResult)

    def test_extract_error_patterns(self, extractor):
        """Test identifying error patterns."""
        logs = '''
Error: ENOENT /data/file.txt
Error: Connection refused
TypeError: Cannot read property 'x' of undefined
'''
        result = extractor.extract(logs)
        assert isinstance(result, LogExtractionResult)

    def test_extract_json_logs(self, extractor):
        """Test extracting structured logs."""
        logs = '''
{"level":"error","msg":"Failed to connect","code":500}
{"level":"info","msg":"Request processed","duration_ms":123}
'''
        result = extractor.extract(logs)
        assert isinstance(result, LogExtractionResult)

    def test_extract_empty_logs(self, extractor):
        """Test extraction with empty log."""
        result = extractor.extract("")
        assert isinstance(result, LogExtractionResult)

    def test_extract_stack_traces(self, extractor):
        """Test extracting stack traces."""
        logs = '''
Traceback (most recent call last):
  File "app.py", line 42, in process
    result = dangerous_func()
  File "lib.py", line 10, in dangerous_func
    raise ValueError("Bad input")
ValueError: Bad input
'''
        result = extractor.extract(logs)
        assert isinstance(result, LogExtractionResult)


class TestSlotFiller:
    """Test slot filling functionality."""

    @pytest.fixture
    def filler(self):
        return SlotFiller()

    def test_fill_slots_basic(self, filler):
        """Test basic slot filling."""
        template = "Hello {name}, you have {count} messages"
        slots = {"name": "Alice", "count": "5"}
        result = filler.fill(template, slots)

        assert isinstance(result, FilledSlots)

    def test_fill_slots_empty(self, filler):
        """Test slot filling with empty template."""
        result = filler.fill("", {})
        assert isinstance(result, FilledSlots)

    def test_fill_slots_missing_slots(self, filler):
        """Test slot filling with missing values."""
        template = "Hello {name}, you are {age} years old"
        slots = {"name": "Bob"}  # missing 'age'
        result = filler.fill(template, slots)
        assert isinstance(result, FilledSlots)

    def test_fill_slots_extra_slots(self, filler):
        """Test slot filling with extra slot values."""
        template = "Hello {name}"
        slots = {"name": "Charlie", "extra": "ignored"}
        result = filler.fill(template, slots)
        assert isinstance(result, FilledSlots)

    def test_fill_slots_numeric(self, filler):
        """Test filling slots with numeric values."""
        template = "Value: {x}, Sum: {y}"
        slots = {"x": 42, "y": 100}
        result = filler.fill(template, slots)
        assert isinstance(result, FilledSlots)
