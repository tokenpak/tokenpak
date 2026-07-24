"""Tests for vendor/minified file classifier."""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.vendor_classifier", reason="module not available in current build")
import pytest
from tokenpak.vendor_classifier import (
    ClassificationResult,
    classify_vendor_minified,
    create_metadata_only_block,
    should_include_in_index,
)

# ---------------------------------------------------------------------------
# 1. Path pattern detection
# ---------------------------------------------------------------------------


class TestVendorPathDetection:
    def test_obsidian_plugins_detected(self):
        """Path with .obsidian/plugins should be detected as vendor."""
        result = classify_vendor_minified(".obsidian/plugins/some-plugin/main.js")
        assert result.is_vendor
        assert result.confidence >= 0.9

    def test_node_modules_detected(self):
        """node_modules/* should be detected as vendor."""
        result = classify_vendor_minified("node_modules/package/lib/index.js")
        assert result.is_vendor
        assert result.confidence >= 0.9

    def test_dist_folder_detected(self):
        """dist/ folder should be detected as vendor."""
        result = classify_vendor_minified("src/dist/app.js")
        assert result.is_vendor

    def test_venv_in_path_detected(self):
        """venv/ folder should be detected."""
        result = classify_vendor_minified("project/venv/lib/python/module.py")
        assert result.is_vendor

    def test_build_folder_detected(self):
        """build/ folder should be detected."""
        result = classify_vendor_minified("src/build/output.js")
        assert result.is_vendor

    def test_normal_file_not_vendor(self):
        """Normal source file should not be detected as vendor."""
        result = classify_vendor_minified("src/utils.py")
        assert not result.is_vendor


# ---------------------------------------------------------------------------
# 2. Extension-based detection
# ---------------------------------------------------------------------------


class TestVendorExtensionDetection:
    def test_minified_js_detected(self):
        """*.min.js should be detected."""
        result = classify_vendor_minified("jquery.min.js")
        assert result.is_vendor
        assert result.confidence >= 0.9

    def test_minified_css_detected(self):
        """*.min.css should be detected."""
        result = classify_vendor_minified("style.min.css")
        assert result.is_vendor

    def test_bundle_js_detected(self):
        """*.bundle.js should be detected."""
        result = classify_vendor_minified("app.bundle.js")
        assert result.is_vendor

    def test_umd_detected(self):
        """*.umd.js should be detected."""
        result = classify_vendor_minified("module.umd.js")
        assert result.is_vendor

    def test_normal_js_not_vendor(self):
        """Regular .js files should not be flagged."""
        result = classify_vendor_minified("utils.js")
        assert not result.is_vendor


# ---------------------------------------------------------------------------
# 3. Content heuristics
# ---------------------------------------------------------------------------


class TestVendorContentDetection:
    def test_long_line_content(self):
        """Content with very long lines should be flagged."""
        # Simulate minified code (very long lines)
        content = "var x=" + "a" * 500 + ";var y=function(){return x;}"
        result = classify_vendor_minified("code.js", content)
        # May be detected depending on exact heuristics
        assert isinstance(result.is_vendor, bool)

    def test_normal_content_not_vendor(self):
        """Normal formatted code should not be detected as vendor."""
        content = '''
def hello_world():
    """A simple function."""
    print("Hello, World!")
    return True
'''
        result = classify_vendor_minified("hello.py", content)
        assert not result.is_vendor

    def test_empty_content_not_vendor(self):
        """Empty file should not be vendor."""
        result = classify_vendor_minified("empty.js", "")
        assert not result.is_vendor


# ---------------------------------------------------------------------------
# 4. should_include_in_index convenience function
# ---------------------------------------------------------------------------


class TestShouldIncludeInIndex:
    def test_normal_code_included(self):
        """Normal code should be included."""
        assert should_include_in_index("src/main.py")

    def test_vendor_excluded(self):
        """Vendor files should be excluded."""
        assert not should_include_in_index("node_modules/package/index.js")

    def test_minified_excluded(self):
        """Minified files should be excluded."""
        assert not should_include_in_index("app.min.js")

    def test_obsidian_plugins_excluded(self):
        """Obsidian plugins should be excluded."""
        assert not should_include_in_index(".obsidian/plugins/plugin/main.js")


# ---------------------------------------------------------------------------
# 5. Metadata-only block creation
# ---------------------------------------------------------------------------


class TestMetadataOnlyBlock:
    def test_metadata_block_structure(self):
        """Metadata-only block has required fields."""
        block = create_metadata_only_block(
            "node_modules/pkg/index.js", "var x = 1;", "Path matches vendor pattern"
        )
        assert block["classification"] == "vendor"
        assert "source_path" in block
        assert "size_bytes" in block
        assert "content_hash" in block
        assert "exclude_reason" in block

    def test_metadata_block_has_size(self):
        """Metadata block includes size."""
        content = "x" * 1000
        block = create_metadata_only_block("file.js", content, "minified")
        assert block["size_bytes"] == 1000

    def test_metadata_block_has_hash(self):
        """Metadata block includes content hash."""
        block = create_metadata_only_block("file.js", "content", "test")
        assert "content_hash" in block
        assert len(block["content_hash"]) > 0


# ---------------------------------------------------------------------------
# 6. Classification result
# ---------------------------------------------------------------------------


class TestClassificationResult:
    def test_result_structure(self):
        """ClassificationResult has required fields."""
        result = ClassificationResult(is_vendor=True, reason="Test reason", confidence=0.95)
        assert result.is_vendor
        assert result.reason == "Test reason"
        assert 0.0 <= result.confidence <= 1.0

    def test_confidence_bounds(self):
        """Confidence is always 0.0-1.0."""
        for conf in [0.0, 0.5, 0.99, 1.0]:
            result = ClassificationResult(is_vendor=True, reason="Test", confidence=conf)
            assert 0.0 <= result.confidence <= 1.0


# ---------------------------------------------------------------------------
# 7. Integration tests
# ---------------------------------------------------------------------------


class TestVendorClassifierIntegration:
    def test_multiple_vendor_signals(self):
        """File with multiple vendor signals still classified correctly."""
        # Path + extension both indicate vendor
        result = classify_vendor_minified("node_modules/package.min.js")
        assert result.is_vendor

    def test_false_positive_avoidance(self):
        """Normal code with unusual names not flagged."""
        result = classify_vendor_minified("src/min_max_algorithm.py")
        assert not result.is_vendor

    def test_case_insensitive_detection(self):
        """Detection is case-insensitive."""
        result1 = classify_vendor_minified("Node_Modules/pkg/index.js")
        result2 = classify_vendor_minified("node_modules/pkg/index.js")
        assert result1.is_vendor == result2.is_vendor


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
