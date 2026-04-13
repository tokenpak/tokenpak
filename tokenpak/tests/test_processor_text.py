"""Unit tests for processors/text.py — TextProcessor."""
import pytest

from tokenpak.processors.text import TextProcessor


# ---------------------------------------------------------------------------
# Instantiation
# ---------------------------------------------------------------------------


class TestTextProcessorInit:
    def test_default_aggressive(self):
        proc = TextProcessor()
        assert proc.aggressive is True

    def test_aggressive_true(self):
        proc = TextProcessor(aggressive=True)
        assert proc.aggressive is True

    def test_aggressive_false(self):
        proc = TextProcessor(aggressive=False)
        assert proc.aggressive is False

    def test_has_process_method(self):
        assert callable(TextProcessor().process)


# ---------------------------------------------------------------------------
# process() — basic structure
# ---------------------------------------------------------------------------


class TestTextProcessorProcess:
    def setup_method(self):
        self.proc = TextProcessor(aggressive=True)
        self.mild = TextProcessor(aggressive=False)

    def test_empty_string(self):
        assert self.proc.process("") == ""

    def test_headers_always_kept(self):
        content = "# H1\n\nSome text.\n\n## H2\n\nMore.\n"
        result = self.proc.process(content)
        assert "# H1" in result
        assert "## H2" in result

    def test_h3_kept(self):
        content = "### Deep Header\n\nBody text.\n"
        result = self.proc.process(content)
        assert "### Deep Header" in result

    def test_code_block_preserved(self):
        content = "# Section\n\n```python\ndef hello():\n    return 42\n```\n"
        result = self.proc.process(content)
        assert "```python" in result
        assert "def hello():" in result
        assert "return 42" in result

    def test_code_block_fence_closing_kept(self):
        content = "```\nsome code\n```\n"
        result = self.proc.process(content)
        # Fences should appear in output
        assert result.count("```") >= 2

    def test_blank_lines_collapsed(self):
        content = "Line one\n\n\n\nLine two"
        result = self.proc.process(content)
        assert "\n\n\n" not in result

    def test_single_blank_line_preserved(self):
        content = "Line one\n\nLine two"
        result = self.proc.process(content)
        assert "\n\n" in result


# ---------------------------------------------------------------------------
# process() — bullet / numbered lists
# ---------------------------------------------------------------------------


class TestTextProcessorLists:
    def setup_method(self):
        self.proc = TextProcessor(aggressive=True)

    def test_bullet_dash_kept(self):
        content = "# Section\n\n- Item A\n- Item B\n"
        result = self.proc.process(content)
        assert "- Item A" in result

    def test_bullet_star_kept(self):
        content = "# Section\n\n* Star item\n"
        result = self.proc.process(content)
        assert "* Star item" in result

    def test_numbered_list_kept(self):
        content = "# Section\n\n1. First\n2. Second\n"
        result = self.proc.process(content)
        assert "1. First" in result

    def test_long_bullet_truncated_aggressive(self):
        long_item = "- " + "word " * 30  # > 80 chars
        content = "# Header\n\n" + long_item + "\n"
        result = self.proc.process(content)
        lines = [l for l in result.split("\n") if l.startswith("- ")]
        if lines:
            assert len(lines[0]) <= 83  # 80 + "…" + margin

    def test_long_bullet_less_truncated_mild(self):
        long_item = "- " + "word " * 30
        content = "# Header\n\n" + long_item + "\n"
        mild_result = TextProcessor(aggressive=False).process(content)
        agg_result = TextProcessor(aggressive=True).process(content)
        # Mild mode allows longer bullets (120 chars limit)
        mild_lines = [l for l in mild_result.split("\n") if l.startswith("- ")]
        agg_lines = [l for l in agg_result.split("\n") if l.startswith("- ")]
        if mild_lines and agg_lines:
            assert len(mild_lines[0]) >= len(agg_lines[0])


# ---------------------------------------------------------------------------
# process() — HTML stripping
# ---------------------------------------------------------------------------


class TestTextProcessorHTML:
    def setup_method(self):
        self.proc = TextProcessor(aggressive=True)

    def test_html_tags_stripped(self):
        content = "<h1>Title</h1><p>Hello <b>world</b></p>"
        result = self.proc.process(content, path="page.html")
        assert "<h1>" not in result
        assert "<b>" not in result
        assert "Title" in result
        assert "Hello" in result

    def test_script_tag_removed(self):
        content = "<p>Content</p><script>alert('xss')</script>"
        result = self.proc.process(content, path="index.html")
        assert "alert" not in result
        assert "Content" in result

    def test_style_tag_removed(self):
        content = "<style>body { color: red; }</style><p>Text</p>"
        result = self.proc.process(content, path="page.html")
        assert "color: red" not in result
        assert "Text" in result

    def test_htm_extension_also_stripped(self):
        content = "<p>Hello <em>world</em></p>"
        result = self.proc.process(content, path="old.htm")
        assert "<em>" not in result
        assert "Hello" in result

    def test_non_html_file_tags_not_stripped(self):
        content = "<p>This is not HTML</p>"
        result = self.proc.process(content, path="notes.txt")
        # Without .html path, tags should not be stripped
        assert "<p>" in result


# ---------------------------------------------------------------------------
# process() — boilerplate dropping
# ---------------------------------------------------------------------------


class TestTextProcessorBoilerplate:
    def setup_method(self):
        self.proc = TextProcessor(aggressive=True)

    def test_all_rights_reserved_dropped(self):
        content = "# Report\n\nAll rights reserved\n\n- Data point\n"
        result = self.proc.process(content)
        assert "All rights reserved" not in result

    def test_privacy_policy_dropped(self):
        content = "# Page\n\nPrivacy Policy\n\n- Item\n"
        result = self.proc.process(content)
        assert "Privacy Policy" not in result

    def test_copyright_dropped(self):
        content = "# Doc\n\nCopyright 2025 Acme Corp\n\n- Fact\n"
        result = self.proc.process(content)
        assert "Copyright 2025 Acme Corp" not in result

    def test_boilerplate_not_dropped_mild_mode(self):
        mild = TextProcessor(aggressive=False)
        content = "# Doc\n\nAll rights reserved\n\nSome real content\n"
        result = mild.process(content)
        assert "All rights reserved" in result

    def test_frontmatter_stripped(self):
        content = "---\ntitle: Test\nauthor: Alice\n---\n# Body\n\nContent here.\n"
        result = self.proc.process(content)
        assert "title: Test" not in result
        assert "# Body" in result


# ---------------------------------------------------------------------------
# process() — blockquotes
# ---------------------------------------------------------------------------


class TestTextProcessorBlockquotes:
    def test_blockquote_kept(self):
        content = "# Section\n\n> Important note here.\n"
        result = TextProcessor().process(content)
        assert "> Important note here." in result


# ---------------------------------------------------------------------------
# _has_signal()
# ---------------------------------------------------------------------------


class TestHasSignal:
    def setup_method(self):
        self.proc = TextProcessor()

    def test_critical_is_signal(self):
        assert self.proc._has_signal("This is CRITICAL") is True

    def test_bug_is_signal(self):
        assert self.proc._has_signal("There is a bug here") is True

    def test_error_is_signal(self):
        assert self.proc._has_signal("An error occurred") is True

    def test_cost_is_signal(self):
        assert self.proc._has_signal("The cost is high") is True

    def test_no_signal_in_generic_text(self):
        assert self.proc._has_signal("The weather is nice today") is False

    def test_case_insensitive(self):
        assert self.proc._has_signal("BLOCKER detected") is True
        assert self.proc._has_signal("Blocker found") is True


# ---------------------------------------------------------------------------
# _first_sentence()
# ---------------------------------------------------------------------------


class TestFirstSentence:
    def setup_method(self):
        self.proc = TextProcessor()

    def test_extracts_first_sentence(self):
        text = "First sentence. Second sentence. Third."
        result = self.proc._first_sentence(text)
        assert result == "First sentence."

    def test_question_mark_ends_sentence(self):
        text = "Is this working? Yes it is."
        result = self.proc._first_sentence(text)
        assert result == "Is this working?"

    def test_exclamation_ends_sentence(self):
        text = "Amazing! What a result."
        result = self.proc._first_sentence(text)
        assert result == "Amazing!"

    def test_truncates_long_sentence(self):
        text = "This is a very long sentence " + "that keeps going " * 10 + "forever."
        result = self.proc._first_sentence(text, max_chars=50)
        assert len(result) <= 53  # 50 chars + possible "…"

    def test_returns_full_text_if_no_sentence_end(self):
        text = "No period here at all"
        result = self.proc._first_sentence(text)
        assert result == text

    def test_truncates_no_sentence_end_long(self):
        text = "x " * 100  # long, no sentence-ending punctuation
        result = self.proc._first_sentence(text, max_chars=30)
        assert len(result) <= 33


# ---------------------------------------------------------------------------
# _is_boilerplate()
# ---------------------------------------------------------------------------


class TestIsBoilerplate:
    def setup_method(self):
        self.proc = TextProcessor()

    def test_all_rights_reserved(self):
        assert self.proc._is_boilerplate("All rights reserved.") is True

    def test_privacy_policy(self):
        assert self.proc._is_boilerplate("Read our Privacy Policy here.") is True

    def test_terms_of_service(self):
        assert self.proc._is_boilerplate("Terms of Service apply.") is True

    def test_click_here(self):
        assert self.proc._is_boilerplate("Click here to subscribe.") is True

    def test_copyright(self):
        assert self.proc._is_boilerplate("Copyright 2024 Acme") is True

    def test_powered_by(self):
        assert self.proc._is_boilerplate("Powered by WordPress") is True

    def test_normal_text_not_boilerplate(self):
        assert self.proc._is_boilerplate("The system processes requests") is False

    def test_case_insensitive_match(self):
        assert self.proc._is_boilerplate("ALL RIGHTS RESERVED") is True
