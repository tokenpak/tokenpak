"""
tests.test_query_rewriter
==========================

Tests for tokenpak.agent.compression.query_rewriter:
  - QueryRewriter class
  - rewrite_query convenience function
  - rewrite_messages for multi-turn workflows
"""

from __future__ import annotations

import pytest
from tokenpak.agent.compression.query_rewriter import (
    QueryRewriter,
    RewriteResult,
    rewrite_query,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rewriter() -> QueryRewriter:
    return QueryRewriter()


# ---------------------------------------------------------------------------
# 1. Greeting / opener stripping
# ---------------------------------------------------------------------------


class TestOpenerStripping:
    def test_hey_can_you(self, rewriter):
        result = rewriter.rewrite("Hey, can you explain what a neural network is?")
        assert "Hey" not in result.rewritten
        assert "neural network" in result.rewritten.lower()

    def test_i_was_wondering(self, rewriter):
        result = rewriter.rewrite(
            "I was wondering if you could help me understand gradient descent."
        )
        assert "wondering" not in result.rewritten.lower()
        assert "gradient descent" in result.rewritten.lower()

    def test_could_you_please(self, rewriter):
        result = rewriter.rewrite(
            "Could you please summarise the main points of this document?"
        )
        assert result.rewritten.strip() != ""
        assert "summarise" in result.rewritten.lower() or "summariz" in result.rewritten.lower()

    def test_hello_opener(self, rewriter):
        result = rewriter.rewrite("Hello! I need you to write a Python function.")
        assert "Hello" not in result.rewritten
        assert "Python function" in result.rewritten

    def test_no_opener_unchanged(self, rewriter):
        text = "Explain the difference between L1 and L2 regularisation."
        result = rewriter.rewrite(text)
        # No opener — core intent fully preserved
        assert "L1" in result.rewritten
        assert "L2" in result.rewritten


# ---------------------------------------------------------------------------
# 2. Closing pleasantry stripping
# ---------------------------------------------------------------------------


class TestCloserStripping:
    def test_thanks_in_advance(self, rewriter):
        result = rewriter.rewrite("What is the capital of France? Thanks in advance!")
        assert "thanks" not in result.rewritten.lower()
        assert "France" in result.rewritten

    def test_thank_you_so_much(self, rewriter):
        result = rewriter.rewrite("Translate this to Spanish. Thank you so much.")
        assert "thank you" not in result.rewritten.lower()
        assert "Spanish" in result.rewritten

    def test_appreciate_it(self, rewriter):
        result = rewriter.rewrite("List the SOLID principles. I really appreciate it.")
        assert "appreciate" not in result.rewritten.lower()
        assert "SOLID" in result.rewritten


# ---------------------------------------------------------------------------
# 3. Inline filler stripping
# ---------------------------------------------------------------------------


class TestInlineFillerStripping:
    def test_basically(self, rewriter):
        result = rewriter.rewrite("I basically need a Dockerfile for a FastAPI app.")
        assert "basically" not in result.rewritten.lower()
        assert "Dockerfile" in result.rewritten

    def test_kind_of(self, rewriter):
        result = rewriter.rewrite("I need kind of a summary of this article.")
        assert "kind of" not in result.rewritten.lower()

    def test_as_i_mentioned(self, rewriter):
        result = rewriter.rewrite(
            "As I mentioned before, the service runs on port 8080."
        )
        assert "mentioned" not in result.rewritten.lower()
        assert "8080" in result.rewritten


# ---------------------------------------------------------------------------
# 4. Repeated-sentence collapsing
# ---------------------------------------------------------------------------


class TestRepeatedSentenceCollapsing:
    def test_exact_repeat(self, rewriter):
        text = "Summarise the document. Summarise the document."
        result = rewriter.rewrite(text)
        # Should contain the request once, not twice
        lower = result.rewritten.lower()
        assert lower.count("summarise") <= 1 or lower.count("summariz") <= 1

    def test_near_duplicate(self, rewriter):
        text = (
            "Please write unit tests for the login function. "
            "Can you write unit tests for the login function?"
        )
        result = rewriter.rewrite(text)
        # Near-dupes collapsed — "login" should appear ≤ once
        assert result.rewritten.lower().count("login") <= 1

    def test_distinct_sentences_kept(self, rewriter):
        text = "What is a transformer? What is an RNN?"
        result = rewriter.rewrite(text)
        assert "transformer" in result.rewritten.lower()
        assert "rnn" in result.rewritten.lower()


# ---------------------------------------------------------------------------
# 5. RewriteResult metadata
# ---------------------------------------------------------------------------


class TestRewriteResult:
    def test_savings_computed(self, rewriter):
        text = "Hey, could you please just basically explain what Docker is? Thanks a lot!"
        result = rewriter.rewrite(text)
        assert result.chars_saved >= 0
        assert 0.0 <= result.savings_pct <= 100.0
        assert result.modified is True

    def test_unchanged_input(self, rewriter):
        text = "What is Docker?"
        result = rewriter.rewrite(text)
        assert result.modified is False
        assert result.chars_saved == 0

    def test_empty_string(self, rewriter):
        result = rewriter.rewrite("")
        assert result.rewritten == ""
        assert result.chars_saved == 0


# ---------------------------------------------------------------------------
# 6. Technical-span preservation
# ---------------------------------------------------------------------------


class TestTechnicalPreservation:
    def test_backtick_code_preserved(self, rewriter):
        text = "Can you explain what `sort_of_value` does in this context?"
        result = rewriter.rewrite(text)
        assert "`sort_of_value`" in result.rewritten

    def test_url_preserved(self, rewriter):
        text = "Can you basically summarise https://example.com/kind-of-long-path for me?"
        result = rewriter.rewrite(text)
        assert "https://example.com/kind-of-long-path" in result.rewritten

    def test_code_fence_preserved(self, rewriter):
        code = "```python\ndef kind_of_weird(): pass\n```"
        text = f"Hey, can you review this code?\n{code}"
        result = rewriter.rewrite(text)
        assert "kind_of_weird" in result.rewritten


# ---------------------------------------------------------------------------
# 7. rewrite_query convenience function
# ---------------------------------------------------------------------------


class TestRewriteQueryFunction:
    def test_basic(self):
        result = rewrite_query(
            "Hey, can you please help me understand what a tensor is? Thanks!"
        )
        assert isinstance(result, RewriteResult)
        assert "tensor" in result.rewritten.lower()
        assert "Hey" not in result.rewritten

    def test_technical_preserved(self):
        result = rewrite_query(
            "Could you basically check `sort_of_weird` at https://example.com?"
        )
        assert "`sort_of_weird`" in result.rewritten
        assert "https://example.com" in result.rewritten


# ---------------------------------------------------------------------------
# 8. rewrite_messages for multi-turn workflows
# ---------------------------------------------------------------------------


class TestRewriteMessages:
    def test_rewrites_user_only_by_default(self):
        messages = [
            {"role": "system", "content": "Hey, you are a helpful assistant. Thanks."},
            {"role": "user", "content": "Hey, can you list the planets? Thanks!"},
            {"role": "assistant", "content": "Sure! Here are the planets..."},
        ]
        rewriter = QueryRewriter()
        result = rewriter.rewrite_messages(messages)

        # System and assistant untouched
        assert result[0]["content"] == messages[0]["content"]
        assert result[2]["content"] == messages[2]["content"]

        # User message rewritten
        assert "Hey" not in result[1]["content"]
        assert "planets" in result[1]["content"].lower()

    def test_rewrites_system_when_requested(self):
        messages = [
            {"role": "system", "content": "Hey! You are basically a helpful assistant."},
            {"role": "user", "content": "What is Python?"},
        ]
        rewriter = QueryRewriter()
        result = rewriter.rewrite_messages(messages, roles=["system", "user"])

        assert "Hey" not in result[0]["content"]
        assert "Python" in result[1]["content"]

    def test_preserves_assistant_messages(self):
        messages = [
            {"role": "user", "content": "Hey, what is 2+2?"},
            {"role": "assistant", "content": "Hey, that's 4!"},
        ]
        rewriter = QueryRewriter()
        result = rewriter.rewrite_messages(messages)
        # Assistant not touched
        assert result[1]["content"] == "Hey, that's 4!"

    def test_block_format_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hey, can you basically describe this image?"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
                ],
            }
        ]
        rewriter = QueryRewriter()
        result = rewriter.rewrite_messages(messages)
        text_block = result[0]["content"][0]
        assert "Hey" not in text_block["text"]
        assert "describe" in text_block["text"].lower()
        # Image block untouched
        assert result[0]["content"][1]["type"] == "image_url"

    def test_empty_messages(self):
        rewriter = QueryRewriter()
        assert rewriter.rewrite_messages([]) == []


# ---------------------------------------------------------------------------
# 9. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_only_pleasantry(self, rewriter):
        # After stripping, we shouldn't crash on empty result
        result = rewriter.rewrite("Hey!")
        assert isinstance(result.rewritten, str)

    def test_multiline_query(self, rewriter):
        text = (
            "Hey, I was wondering if you could help me.\n"
            "I need to understand backpropagation.\n"
            "Could you explain backpropagation in simple terms?"
        )
        result = rewriter.rewrite(text)
        assert "backpropagation" in result.rewritten.lower()

    def test_capitals_preserved_in_technical_names(self, rewriter):
        text = "Can you explain how BERT handles tokenisation?"
        result = rewriter.rewrite(text)
        assert "BERT" in result.rewritten

    def test_collapse_threshold_param(self):
        # With very low threshold, even distinct sentences may collapse
        rewriter_strict = QueryRewriter(collapse_threshold=0.1)
        text = "What is X? What is Y?"
        result = rewriter_strict.rewrite(text)
        assert isinstance(result.rewritten, str)
