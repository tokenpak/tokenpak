"""Unit tests for protected-span detection (TIP-05)."""

from __future__ import annotations

from tokenpak.services.optimization.protected_spans import (
    ALL_SPAN_TYPES,
    ProtectedSpan,
    SpanType,
    detect_protected_spans,
    merge_overlapping,
    protected_byte_count,
    rewrite_outside_spans,
    text_is_protected,
)


# ---- file_path ------------------------------------------------------------


def test_detect_absolute_file_path():
    text = "Open /home/sue/tokenpak/proxy/server.py for the patch."
    spans = detect_protected_spans(text, types=[SpanType.FILE_PATH])
    assert spans, "expected a file_path span"
    s = spans[0]
    assert text[s.start:s.end] == "/home/sue/tokenpak/proxy/server.py"
    assert s.span_type == SpanType.FILE_PATH


def test_detect_relative_file_path():
    text = "see ./recipes/oss/cp-git-diff-compression.yaml for context"
    spans = detect_protected_spans(text, types=[SpanType.FILE_PATH])
    matches = [text[s.start:s.end] for s in spans]
    assert any("cp-git-diff-compression.yaml" in m for m in matches)


# ---- function / class signatures -----------------------------------------


def test_detect_python_function_signature():
    text = "def run_observe_only(ctx: OptimizationContext) -> OptimizationTrace:\n    pass"
    spans = detect_protected_spans(text, types=[SpanType.FUNCTION_SIGNATURE])
    assert spans
    assert text[spans[0].start:spans[0].end].startswith("def run_observe_only(")


def test_detect_class_signature():
    text = "class OptimizationPipeline(BasePipeline):\n    pass"
    spans = detect_protected_spans(text, types=[SpanType.CLASS_SIGNATURE])
    assert spans
    assert text[spans[0].start:spans[0].end] == "class OptimizationPipeline(BasePipeline):"


# ---- diff -----------------------------------------------------------------


def test_detect_diff_hunk_header():
    text = "@@ -10,7 +10,9 @@ def foo():\n+ new line\n- old line\n"
    spans = detect_protected_spans(text, types=[SpanType.DIFF_HUNK_HEADER])
    assert spans
    matched = text[spans[0].start:spans[0].end]
    assert matched.startswith("@@ -10,7 +10,9 @@")


def test_detect_diff_added_removed_lines():
    text = "@@ -1 +1 @@\n+added\n-removed\nunchanged\n"
    spans = detect_protected_spans(
        text, types=[SpanType.DIFF_ADDED_REMOVED_LINES]
    )
    contents = [text[s.start:s.end] for s in spans]
    # We expect two diff content lines (+added, -removed).
    assert any(c.startswith("+added") for c in contents)
    assert any(c.startswith("-removed") for c in contents)


def test_does_not_treat_diff_metadata_as_content_line():
    text = "+++ b/foo.py\n--- a/foo.py\n"
    spans = detect_protected_spans(
        text, types=[SpanType.DIFF_ADDED_REMOVED_LINES]
    )
    contents = [text[s.start:s.end] for s in spans]
    assert not any(c.startswith("++") for c in contents)
    assert not any(c.startswith("--") for c in contents)


# ---- stack traces ---------------------------------------------------------


def test_detect_python_stack_frame():
    text = (
        "Traceback (most recent call last):\n"
        '  File "/home/x/proxy.py", line 314, in run\n'
        "    raise ValueError(\"bad\")\n"
    )
    spans = detect_protected_spans(text, types=[SpanType.STACK_TRACE_FRAME])
    matches = [text[s.start:s.end] for s in spans]
    assert any('File "/home/x/proxy.py", line 314, in run' in m for m in matches)


def test_detect_js_stack_frame():
    text = "Error: kaboom\n    at handle (/srv/api.js:12:5)\n"
    spans = detect_protected_spans(text, types=[SpanType.STACK_TRACE_FRAME])
    matches = [text[s.start:s.end] for s in spans]
    assert any("at handle (/srv/api.js:12:5)" in m for m in matches)


# ---- exception messages ---------------------------------------------------


def test_detect_python_exception_message():
    text = (
        "  File \"x.py\", line 1, in foo\n"
        "ValueError: invalid literal for int(): 'abc'\n"
    )
    spans = detect_protected_spans(text, types=[SpanType.EXCEPTION_MESSAGE])
    matches = [text[s.start:s.end] for s in spans]
    assert any("ValueError: invalid literal for int(): 'abc'" in m for m in matches)


# ---- exit codes -----------------------------------------------------------


def test_detect_exit_code_variants():
    text = "Process exited with exit code: 137\nreturncode=2\nstatus=0\n"
    spans = detect_protected_spans(text, types=[SpanType.EXIT_CODE])
    matches = [text[s.start:s.end].lower() for s in spans]
    assert any("exit code: 137" in m for m in matches)
    assert any("returncode=2" in m for m in matches)
    assert any("status=0" in m for m in matches)


# ---- yaml / config --------------------------------------------------------


def test_detect_yaml_keys():
    text = "name: tokenpak\nversion: 1.0\nfeatures:\n  proxy: true\n"
    spans = detect_protected_spans(text, types=[SpanType.YAML_KEY])
    matches = [text[s.start:s.end].strip() for s in spans]
    assert any(m.startswith("name:") for m in matches)
    assert any(m.startswith("proxy:") for m in matches)


def test_detect_config_value_envvar():
    text = "DEBUG=true\nLOG_LEVEL=warn\nX=1\n"
    spans = detect_protected_spans(text, types=[SpanType.CONFIG_VALUE])
    matches = [text[s.start:s.end] for s in spans]
    assert any(m.startswith("DEBUG=") for m in matches)
    assert any(m.startswith("LOG_LEVEL=") for m in matches)


# ---- urls + credentials ---------------------------------------------------


def test_detect_url():
    text = "fetch https://api.example.com/v1/items?id=42 next"
    spans = detect_protected_spans(text, types=[SpanType.URL])
    matches = [text[s.start:s.end] for s in spans]
    assert "https://api.example.com/v1/items?id=42" in matches


def test_detect_credential_placeholder():
    text = "use ${OPENAI_API_KEY} or <REDACTED> or xxx_REDACTED_xxx"
    spans = detect_protected_spans(
        text, types=[SpanType.CREDENTIAL_PLACEHOLDER]
    )
    matches = [text[s.start:s.end] for s in spans]
    assert "${OPENAI_API_KEY}" in matches
    assert "<REDACTED>" in matches
    assert "xxx_REDACTED_xxx" in matches


# ---- merge / rewrite ------------------------------------------------------


def test_merge_overlapping_collapses_adjacent_spans():
    spans = [
        ProtectedSpan(0, 10, SpanType.FILE_PATH),
        ProtectedSpan(5, 15, SpanType.URL),
        ProtectedSpan(20, 30, SpanType.LINE_NUMBER),
    ]
    merged = merge_overlapping(spans)
    assert len(merged) == 2
    assert merged[0].start == 0 and merged[0].end == 15
    assert merged[1].start == 20 and merged[1].end == 30


def test_rewrite_outside_spans_preserves_protected_text():
    text = "before /etc/foo.cfg middle /var/bar.log after"
    spans = detect_protected_spans(text, types=[SpanType.FILE_PATH])
    out = rewrite_outside_spans(text, spans, lambda s: s.upper())
    # protected slices come back verbatim
    assert "/etc/foo.cfg" in out
    assert "/var/bar.log" in out
    # everything else is uppercased
    assert "BEFORE " in out
    assert " MIDDLE " in out
    assert " AFTER" in out


def test_rewrite_with_empty_spans_just_calls_rewriter():
    out = rewrite_outside_spans("hello", [], lambda s: s + "!")
    assert out == "hello!"


def test_protected_byte_count_matches_lengths():
    spans = [
        ProtectedSpan(0, 5, SpanType.FILE_PATH),
        ProtectedSpan(10, 30, SpanType.URL),
    ]
    assert protected_byte_count(spans) == 25


def test_text_is_protected_short_circuits():
    assert text_is_protected("hello /foo.py", types=[SpanType.FILE_PATH])
    assert not text_is_protected("hello world", types=[SpanType.FILE_PATH])


def test_unknown_span_type_is_ignored():
    spans = detect_protected_spans("anything", types=["not_a_real_type"])
    assert spans == []


def test_all_span_types_constant_matches_detector_set():
    # The proposal lists 15 span types — guard against silent drift.
    assert len(ALL_SPAN_TYPES) == 15


def test_detect_with_no_types_runs_every_detector():
    text = (
        "open /tmp/foo.py\n"
        "def bar(x): pass\n"
        "ValueError: nope\n"
    )
    spans = detect_protected_spans(text)
    seen_types = {s.span_type for s in spans}
    # File path and function signature must each be detected; the
    # ValueError line is also covered (yaml_key may dominate the type
    # after merging, since `ValueError:` matches that pattern too — what
    # we care about is that the bytes themselves are protected).
    assert SpanType.FILE_PATH in seen_types
    assert SpanType.FUNCTION_SIGNATURE in seen_types
    error_start = text.index("ValueError")
    assert any(s.start <= error_start < s.end for s in spans), (
        "ValueError line should be covered by some protected span, "
        f"got spans={[(s.start, s.end, s.span_type) for s in spans]}"
    )
