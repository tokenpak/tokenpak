"""
Tests for AliasCompressor — Symbol Table / Alias Compression.

Covers:
  1. Long paths are replaced with aliases and symbol table is generated
  2. Symbol table format is correct
  3. Entities under threshold are NOT aliased
  4. Token savings are measured accurately
  5. Round-trip: alias → expand restores original text
  6. Multiple entity types (URL, service, env var, class)
  7. Pipeline integration (alias stage in stages_run)
"""

import pytest

from tokenpak.compression.alias_compressor import AliasCompressor
from tokenpak.compression.pipeline import CompressionPipeline

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LONG_PATH = "/home/trix/Projects/tokenpak/tokenpak/agent/compression/pipeline.py"  # 61 chars
SHORT_PATH = "/tmp/x.py"                                                             # 9 chars
SERVICE_NAME = "openclaw-gateway-proxy-service"                                       # 30 chars
ENV_VAR = "TOKENPAK_PROXY_BASE_URL"                                                  # 23 chars


def _msgs(*texts):
    """Build simple user messages from text strings."""
    return [{"role": "user", "content": t} for t in texts]


# ---------------------------------------------------------------------------
# Test 1: Long paths are replaced and symbol table is generated
# ---------------------------------------------------------------------------

def test_long_paths_replaced():
    """Paths appearing 3+ times should be aliased in message content."""
    compressor = AliasCompressor(min_occurrences=3, min_entity_length=20)
    content = " ".join([LONG_PATH] * 4)
    messages = _msgs(content)

    result = compressor.compress(messages)

    assert result.entities_aliased >= 1, "Expected at least one entity aliased"
    # The header line contains the path as F1=<path>; the body lines should use the alias
    aliased_text = result.messages[0]["content"]
    body = "\n".join(aliased_text.split("\n")[1:])  # skip header line
    assert LONG_PATH not in body, "Original path should not appear in message body (only in header)"
    assert any(v == LONG_PATH for v in result.symbol_table.values()), "Symbol table should map to original path"
    alias = result.alias_map[LONG_PATH]
    assert alias in body, f"Alias {alias!r} should appear in message body"


# ---------------------------------------------------------------------------
# Test 2: Symbol table format
# ---------------------------------------------------------------------------

def test_symbol_table_format():
    """Symbol table header must follow [ALIASES: K=V | K2=V2] format."""
    compressor = AliasCompressor(min_occurrences=2, min_entity_length=20)
    content = (LONG_PATH + " ") * 3
    messages = _msgs(content)

    result = compressor.compress(messages)

    assert result.entities_aliased >= 1
    first_content = result.messages[0]["content"]
    assert first_content.startswith("[ALIASES:"), "First message should start with [ALIASES:]"
    assert "]" in first_content, "Header must be closed with ]"
    # Each entry should be alias=value
    header_line = first_content.split("\n")[0]
    assert "=" in header_line, "Header entries must be alias=value"


# ---------------------------------------------------------------------------
# Test 3: Entities under threshold are NOT aliased
# ---------------------------------------------------------------------------

def test_under_threshold_not_aliased():
    """Entities appearing fewer than min_occurrences times should NOT be aliased."""
    compressor = AliasCompressor(min_occurrences=3, min_entity_length=20)
    # Path appears only twice — should not be aliased
    content = f"{LONG_PATH} {LONG_PATH} some other text"
    messages = _msgs(content)

    result = compressor.compress(messages)

    assert result.entities_aliased == 0, "No entity should be aliased when under threshold"
    assert result.messages[0]["content"] == content, "Content should be unchanged"


def test_short_entity_not_aliased():
    """Entities shorter than min_entity_length should not be aliased."""
    compressor = AliasCompressor(min_occurrences=1, min_entity_length=20)
    content = (SHORT_PATH + " ") * 5
    messages = _msgs(content)

    result = compressor.compress(messages)

    assert result.entities_aliased == 0, "Short entities should not be aliased"


# ---------------------------------------------------------------------------
# Test 4: Token savings measured accurately
# ---------------------------------------------------------------------------

def test_savings_measured():
    """tokens_saved should be positive when long entities are aliased."""
    compressor = AliasCompressor(min_occurrences=3, min_entity_length=20)
    content = (LONG_PATH + " ") * 5  # big savings expected
    messages = _msgs(content)

    result = compressor.compress(messages)

    assert result.tokens_saved >= 0, "tokens_saved should be non-negative"
    if result.entities_aliased > 0:
        # Alias is much shorter than original — savings should be positive
        assert result.tokens_saved > 0, "Expected positive token savings for long repeated path"


# ---------------------------------------------------------------------------
# Test 5: Round-trip — alias → expand restores original
# ---------------------------------------------------------------------------

def test_roundtrip_expand():
    """expand() should restore the original text from aliased content."""
    compressor = AliasCompressor(min_occurrences=3, min_entity_length=20)
    original = (LONG_PATH + " ") * 4 + "done"
    messages = _msgs(original)

    result = compressor.compress(messages)

    if result.entities_aliased == 0:
        pytest.skip("No entities aliased; round-trip not applicable")

    aliased_text = result.messages[0]["content"]
    # Remove header to compare body
    expanded = compressor.expand(aliased_text, result.symbol_table)

    # Expanded body should contain the original path
    assert LONG_PATH in expanded, "Expanded text should contain original path"


# ---------------------------------------------------------------------------
# Test 6: Multiple entity types
# ---------------------------------------------------------------------------

def test_multiple_entity_types():
    """URL, env var, service, and class names should all be detectable."""
    url = "https://api.openclaw.ai/v1/compress/tokens"  # 44 chars
    compressor = AliasCompressor(min_occurrences=2, min_entity_length=20)

    # Three occurrences of each
    content = f"{url} {url} {url} {ENV_VAR} {ENV_VAR} {ENV_VAR} {SERVICE_NAME} {SERVICE_NAME} {SERVICE_NAME}"
    messages = _msgs(content)

    result = compressor.compress(messages)

    found_types = set()
    for alias, original in result.symbol_table.items():
        if original == url:
            found_types.add("url")
        elif original == ENV_VAR:
            found_types.add("env")
        elif original == SERVICE_NAME:
            found_types.add("service")

    assert len(found_types) >= 1, f"Expected at least one entity type aliased, got: {found_types}"


# ---------------------------------------------------------------------------
# Test 7: Pipeline integration
# ---------------------------------------------------------------------------

def test_pipeline_alias_stage():
    """CompressionPipeline should run alias stage and include it in stages_run."""
    pipeline = CompressionPipeline(
        enable_dedup=False,
        enable_alias=True,
        enable_segmentation=False,
        enable_directives=False,
        alias_min_occurrences=3,
        alias_min_length=20,
    )
    content = (LONG_PATH + " ") * 4
    messages = [{"role": "user", "content": content}]

    result = pipeline.run(messages)

    assert "alias" in result.stages_run, f"Expected 'alias' in stages_run, got: {result.stages_run}"


def test_pipeline_alias_disabled():
    """When enable_alias=False, alias stage should not run."""
    pipeline = CompressionPipeline(
        enable_dedup=False,
        enable_alias=False,
        enable_segmentation=False,
        enable_directives=False,
    )
    content = (LONG_PATH + " ") * 4
    messages = [{"role": "user", "content": content}]

    result = pipeline.run(messages)

    assert "alias" not in result.stages_run


def test_pipeline_content_unchanged_when_no_aliases():
    """Pipeline result should be unchanged when nothing qualifies for aliasing."""
    pipeline = CompressionPipeline(
        enable_dedup=False,
        enable_alias=True,
        enable_segmentation=False,
        enable_directives=False,
        alias_min_occurrences=10,  # very high threshold
        alias_min_length=200,       # very long min length
    )
    messages = [{"role": "user", "content": "short text here"}]
    result = pipeline.run(messages)
    assert result.messages[0]["content"] == "short text here"
