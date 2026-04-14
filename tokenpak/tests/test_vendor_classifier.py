"""
Unit tests for vendor_classifier.py

Tests cover:
- Known vendor/minified path patterns
- File extensions (bundle, min, etc.)
- Content heuristics (long lines, punctuation)
- Edge cases (empty paths, None, partial names)
"""

import pytest
from tokenpak.compression.vendor_classifier import (
    classify_vendor_minified,
    should_include_in_index,
    create_metadata_only_block,
    _has_vendor_path,
    _has_vendor_extension,
    _is_minified_content,
)


class TestVendorPathPatterns:
    """Test vendor path pattern detection."""

    def test_obsidian_plugins_path(self):
        """Obsidian plugin paths should be detected as vendor."""
        path = "notes/.obsidian/plugins/some-plugin/main.js"
        result = classify_vendor_minified(path)
        assert result.is_vendor is True
        assert result.confidence >= 0.90

    def test_node_modules_path(self):
        """node_modules paths should be detected as vendor."""
        path = "project/node_modules/lodash/index.js"
        result = classify_vendor_minified(path)
        assert result.is_vendor is True
        assert result.confidence >= 0.90

    def test_dist_build_paths(self):
        """dist/ and build/ paths should be detected as vendor."""
        for path in [
            "app/dist/bundle.js",
            "project/build/output.css",
        ]:
            result = classify_vendor_minified(path)
            assert result.is_vendor is True

    def test_venv_paths(self):
        """Python venv paths should be detected as vendor."""
        # Pattern requires leading slash before .venv or venv
        for path in ["project/.venv/lib/python3.9/site-packages/pkg.py", "project/venv/bin/activate"]:
            result = classify_vendor_minified(path)
            assert result.is_vendor is True

    def test_vendor_directory(self):
        """vendor/ and third-party directories should be detected."""
        for path in ["app/vendor/lib.php", "src/third_party/util.js", "src/third-party/tool.py"]:
            result = classify_vendor_minified(path)
            assert result.is_vendor is True


class TestFileExtensions:
    """Test vendor extension detection."""

    def test_minified_extensions(self):
        """Minified extensions (.min.js, .min.css) should be detected."""
        for path in [
            "app.min.js",
            "style.min.css",
            "index.min.html",
        ]:
            result = classify_vendor_minified(path)
            assert result.is_vendor is True
            # Reason will be "Extension indicates minified/bundled"
            assert result.confidence >= 0.80

    def test_bundle_extensions(self):
        """Bundle extensions should be detected."""
        for path in [
            "app.bundle.js",
            "styles.bundle.css",
        ]:
            result = classify_vendor_minified(path)
            assert result.is_vendor is True

    def test_umd_extension(self):
        """UMD extensions should be detected."""
        path = "lib.umd.js"
        result = classify_vendor_minified(path)
        assert result.is_vendor is True

    def test_case_insensitive_extension(self):
        """Extension detection should be case-insensitive."""
        for path in ["APP.MIN.JS", "Style.Min.Css", "Index.BUNDLE.JS"]:
            result = classify_vendor_minified(path)
            assert result.is_vendor is True


class TestContentHeuristics:
    """Test content-based minification detection."""

    def test_long_lines_minified(self):
        """Very long lines indicate minified content."""
        # Simulate minified JavaScript with 250+ char lines
        minified = (
            "var a=function(){return 1;};var b=function(){return 2;};var c={x:1,y:2,z:3};"
            * 5  # Repeat to make it long
        )
        result = classify_vendor_minified("normal.js", minified)
        assert result.is_vendor is True

    def test_high_punctuation_minified(self):
        """High punctuation density indicates minified content."""
        # High punctuation density
        minified = "{}[]();:,={}[]();:,={}" * 30
        result = classify_vendor_minified("normal.js", minified)
        assert result.is_vendor is True

    def test_normal_code_not_minified(self):
        """Normal source code should not be detected as minified."""
        normal = """
def hello_world():
    '''A simple function.'''
    message = "Hello, World!"
    print(message)
    return message

if __name__ == '__main__':
    hello_world()
"""
        result = classify_vendor_minified("normal.py", normal)
        assert result.is_vendor is False

    def test_short_content_not_minified(self):
        """Short content should not be flagged as minified."""
        short = "var x = 1; var y = 2;"
        result = classify_vendor_minified("short.js", short)
        assert result.is_vendor is False


class TestIncludeInIndex:
    """Test should_include_in_index convenience function."""

    def test_vendor_excluded_high_confidence(self):
        """Vendor files with high confidence should be excluded."""
        path = "node_modules/lodash/index.js"
        assert should_include_in_index(path) is False

    def test_normal_files_included(self):
        """Normal files should be included."""
        path = "src/utils.js"
        assert should_include_in_index(path) is True

    def test_minified_extension_excluded(self):
        """Minified extensions should be excluded."""
        path = "dist/app.min.js"
        assert should_include_in_index(path) is False


class TestMetadataOnlyBlock:
    """Test metadata-only block creation for vendor files."""

    def test_creates_metadata_block(self):
        """Should create a metadata-only block."""
        path = "vendor/lib.js"
        content = "var x = function() { return 1; };"
        reason = "Path matches vendor pattern"

        block = create_metadata_only_block(path, content, reason)

        assert block["source_path"] == path
        assert block["size_bytes"] == len(content)
        assert "content_hash" in block
        assert block["classification"] == "vendor"
        assert block["exclude_reason"] == reason
        assert "[VENDOR]" in block["content"]

    def test_metadata_has_size_hash(self):
        """Metadata should include size and hash."""
        path = "node_modules/pkg/index.js"
        content = "long content" * 100

        block = create_metadata_only_block(path, content, "test reason")

        assert block["size_bytes"] == len(content)
        assert len(block["content_hash"]) == 16  # SHA256 first 16 chars


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_path(self):
        """Empty path should be handled gracefully."""
        result = classify_vendor_minified("")
        assert result.is_vendor is False

    def test_none_path_raises_or_handled(self):
        """None path should raise or be handled gracefully."""
        # Most implementations would raise TypeError, which is OK
        try:
            result = classify_vendor_minified(None)
            assert result.is_vendor is False
        except TypeError:
            pass  # Expected for None input

    def test_empty_content(self):
        """Empty content should not be flagged as minified."""
        result = classify_vendor_minified("file.js", "")
        assert result.is_vendor is False

    def test_whitespace_only_content(self):
        """Whitespace-only content should not be flagged as minified."""
        result = classify_vendor_minified("file.js", "   \n\n  \n  ")
        assert result.is_vendor is False

    def test_path_case_variations(self):
        """Path matching should handle case variations."""
        for path in [
            "Project/NODE_MODULES/lib.js",
            "App/.Obsidian/plugins/tool.js",
            "code/DIST/bundle.js",
        ]:
            result = classify_vendor_minified(path)
            assert result.is_vendor is True, f"Failed for path: {path}"


class TestConfidenceScores:
    """Test confidence score accuracy."""

    def test_path_pattern_high_confidence(self):
        """Path pattern matches should have high confidence (0.80+)."""
        result = classify_vendor_minified("node_modules/pkg/index.js")
        assert result.confidence >= 0.80

    def test_extension_high_confidence(self):
        """Extension matches should have high confidence (0.80+)."""
        result = classify_vendor_minified("app.min.js")
        assert result.confidence >= 0.80

    def test_content_heuristic_lower_confidence(self):
        """Content heuristics should have lower confidence (<0.80)."""
        minified = "var a={x:1};var b={y:2};" * 30
        result = classify_vendor_minified("normal.js", minified)
        if result.is_vendor:
            assert result.confidence < 0.80


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
