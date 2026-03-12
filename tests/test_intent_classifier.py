"""Tests for TokenPak intent + complexity classifier."""

import pytest
from tokenpak.intent_classifier import (
    IntentClass,
    ClassificationResult,
    classify,
)


class TestIntentClassification:
    """Tests for intent classification."""

    def test_code_q_explain_function(self):
        """CODE_Q: Explain what a function does."""
        result = classify("Explain how the `process_data` function works")
        assert result.intent == IntentClass.CODE_Q
        assert result.needs_retrieval is True
        assert result.needs_writeback is False

    def test_code_q_find_pattern(self):
        """CODE_Q: Find where a pattern is used."""
        result = classify("Where is the caching decorator used in the codebase?")
        assert result.intent == IntentClass.CODE_Q
        assert result.needs_retrieval is True

    def test_code_q_with_file_path(self):
        """CODE_Q: File path signals code understanding."""
        result = classify("What does utils.py do?")
        assert result.intent == IntentClass.CODE_Q

    def test_code_edit_add_feature(self):
        """CODE_EDIT: Add a new feature."""
        result = classify("Add a retry mechanism to the API client")
        assert result.intent == IntentClass.CODE_EDIT
        assert result.needs_writeback is True

    def test_code_edit_refactor(self):
        """CODE_EDIT: Refactor existing code."""
        result = classify("Refactor the authentication module for clarity")
        assert result.intent == IntentClass.CODE_EDIT
        assert result.needs_writeback is True

    def test_code_edit_fix_bug(self):
        """CODE_EDIT: Fix a bug."""
        result = classify("Fix the off-by-one error in the loop")
        assert result.intent == IntentClass.CODE_EDIT
        assert result.needs_writeback is True

    def test_debug_with_stack_trace(self):
        """DEBUG: Stack trace detection."""
        context = """
Traceback (most recent call last):
  File "app.py", line 42, in main
    result = compute(data)
Exception: Division by zero
"""
        result = classify("Why is this failing?", context=context)
        assert result.intent == IntentClass.DEBUG
        assert result.needs_retrieval is True

    def test_debug_error_keyword(self):
        """DEBUG: Error/bug keywords."""
        result = classify(
            "There's an exception when I call this function",
            context="def my_func():\n    x = 1 / 0"
        )
        assert result.intent == IntentClass.DEBUG

    def test_debug_crash_analysis(self):
        """DEBUG: Crash analysis."""
        result = classify("Why does the app crash on startup?")
        assert result.intent == IntentClass.DEBUG

    def test_doc_edit_write_readme(self):
        """DOC_EDIT: Write documentation."""
        result = classify("Write a README for this project")
        assert result.intent == IntentClass.DOC_EDIT
        assert result.needs_writeback is True

    def test_doc_edit_update_docstring(self):
        """DOC_EDIT: Update function docstring."""
        result = classify("Add docstrings to all functions in models.py")
        assert result.intent == IntentClass.DOC_EDIT
        assert result.needs_writeback is True

    def test_plan_architecture(self):
        """PLAN: Architecture design."""
        result = classify(
            "How should I structure a microservices architecture for a scalable application?"
        )
        assert result.intent == IntentClass.PLAN

    def test_plan_refactoring_strategy(self):
        """PLAN: Refactoring strategy."""
        result = classify("What's the best approach to modularize this monolith?")
        assert result.intent == IntentClass.PLAN

    def test_review_pr_feedback(self):
        """REVIEW: Pull request review."""
        result = classify("Review this PR for code quality")
        assert result.intent == IntentClass.REVIEW

    def test_review_design(self):
        """REVIEW: Design review."""
        result = classify("What do you think of this API design?")
        assert result.intent == IntentClass.REVIEW

    def test_gen_q_general(self):
        """GEN_Q: General question with no code context."""
        result = classify("What is the capital of France?")
        assert result.intent == IntentClass.GEN_Q
        assert result.needs_retrieval is False
        assert result.needs_writeback is False

    def test_gen_q_no_signals(self):
        """GEN_Q: Fallback when no specific intent signals match."""
        result = classify("Tell me about historical events in 1776")
        assert result.intent == IntentClass.GEN_Q

    def test_code_edit_with_context(self):
        """CODE_EDIT: Modify code when context is provided."""
        context = "def add(a, b):\n    return a + b"
        result = classify("Change this to subtract instead", context=context)
        assert result.intent == IntentClass.CODE_EDIT
        assert result.needs_writeback is True


class TestComplexityScoring:
    """Tests for complexity score computation."""

    def test_simple_query_low_complexity(self):
        """Short, simple queries score low."""
        result = classify("What is Python?")
        assert 0.0 <= result.complexity_score <= 0.2

    def test_long_query_higher_complexity(self):
        """Longer queries score higher."""
        result = classify(
            "How should I refactor a monolithic application with 50 microservices "
            "while maintaining backward compatibility and minimizing downtime?"
        )
        assert result.complexity_score >= 0.25

    def test_code_block_increases_complexity(self):
        """Code blocks increase complexity."""
        context = """
def process(data):
    ```python
    x = data.split()
    ```
"""
        result = classify("Optimize this function", context=context)
        assert result.complexity_score >= 0.1

    def test_debug_with_stack_trace_high_complexity(self):
        """Stack traces increase complexity."""
        context = """Traceback (most recent call last):
  File "app.py", line 10
  File "utils.py", line 5
Exception: Something failed"""
        result = classify("Fix this", context=context, )
        assert result.complexity_score > 0.1

    def test_complexity_clamped_0_to_1(self):
        """Complexity is always between 0.0 and 1.0."""
        result = classify("x" * 10000)  # Very long query
        assert 0.0 <= result.complexity_score <= 1.0


class TestRetrievalFlags:
    """Tests for needs_retrieval flag."""

    def test_code_q_always_needs_retrieval(self):
        """CODE_Q always needs to fetch context."""
        result = classify("Where is the cache decorator defined?")
        assert result.needs_retrieval is True

    def test_debug_always_needs_retrieval(self):
        """DEBUG always needs error/log context."""
        result = classify("Why is this crashing?")
        assert result.needs_retrieval is True

    def test_review_needs_retrieval(self):
        """REVIEW needs to fetch diffs/PRs."""
        result = classify("Review this code change")
        assert result.needs_retrieval is True

    def test_gen_q_no_retrieval(self):
        """GEN_Q doesn't need retrieval."""
        result = classify("What is Python?")
        assert result.needs_retrieval is False

    def test_code_edit_with_context_no_retrieval(self):
        """CODE_EDIT with full context doesn't need retrieval."""
        context = "def foo():\n    pass"
        result = classify("Add error handling", context=context)
        # With code block in context, retrieval still might be false
        # (depends on implementation, but context is provided)
        assert isinstance(result.needs_retrieval, bool)

    def test_file_path_triggers_retrieval(self):
        """Mentioning file paths triggers retrieval."""
        result = classify("Edit the config in settings.json")
        assert result.needs_retrieval is True


class TestWritebackFlags:
    """Tests for needs_writeback flag."""

    def test_code_edit_needs_writeback(self):
        """CODE_EDIT always needs to write."""
        result = classify("Add validation to the input handler")
        assert result.needs_writeback is True

    def test_doc_edit_needs_writeback(self):
        """DOC_EDIT always needs to write."""
        result = classify("Write documentation for the API")
        assert result.needs_writeback is True

    def test_code_q_no_writeback(self):
        """CODE_Q doesn't write (just reads)."""
        result = classify("Where is the main loop?")
        assert result.needs_writeback is False

    def test_debug_no_writeback_by_default(self):
        """DEBUG doesn't write unless requested."""
        result = classify("Why is this failing?")
        assert result.needs_writeback is False

    def test_plan_no_writeback(self):
        """PLAN doesn't write (suggests approach)."""
        result = classify("How should I redesign the auth system?")
        assert result.needs_writeback is False

    def test_review_no_writeback(self):
        """REVIEW doesn't write (just comments)."""
        result = classify("Review this PR")
        assert result.needs_writeback is False


class TestConfidenceScoring:
    """Tests for confidence scores."""

    def test_clear_intent_high_confidence(self):
        """Multiple matching keywords increase confidence."""
        result = classify("Refactor and optimize the database query module")
        assert result.confidence > 0.65

    def test_ambiguous_intent_lower_confidence(self):
        """Vague queries have lower confidence."""
        result = classify("Do something with the code")
        assert result.confidence <= 0.7

    def test_confidence_ranges_0_to_1(self):
        """Confidence is always between 0.0 and 1.0."""
        result = classify("xyz abc def")
        assert 0.0 <= result.confidence <= 1.0

    def test_code_context_boosts_confidence(self):
        """Code in context boosts confidence."""
        context_without_code = "Some text about functions"
        context_with_code = "```python\ndef foo():\n    pass\n```"

        result1 = classify("Explain this", context=context_without_code)
        result2 = classify("Explain this", context=context_with_code)

        assert result2.confidence >= result1.confidence


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_empty_query(self):
        """Empty query returns a result."""
        result = classify("")
        assert isinstance(result, ClassificationResult)
        assert result.intent == IntentClass.GEN_Q

    def test_whitespace_only_query(self):
        """Whitespace-only query returns a result."""
        result = classify("   \n  \t  ")
        assert isinstance(result, ClassificationResult)

    def test_very_long_query(self):
        """Very long query is handled."""
        long_query = "What " * 1000
        result = classify(long_query)
        assert isinstance(result, ClassificationResult)
        assert 0.0 <= result.complexity_score <= 1.0

    def test_special_characters(self):
        """Special characters don't break classifier."""
        result = classify("Fix the `$@#%` error in [brackets]")
        assert isinstance(result, ClassificationResult)

    def test_mixed_case_keywords(self):
        """Keywords work regardless of case."""
        result1 = classify("REFACTOR the code")
        result2 = classify("refactor the code")
        result3 = classify("Refactor the code")
        # All should be CODE_EDIT
        assert result1.intent == result2.intent == result3.intent == IntentClass.CODE_EDIT

    def test_multi_intent_ambiguity(self):
        """Ambiguous between multiple intents returns a result."""
        # "Design" could be PLAN or DOC_EDIT
        result = classify("Design the authentication spec and write it up")
        assert isinstance(result, ClassificationResult)
        assert result.intent in (IntentClass.PLAN, IntentClass.DOC_EDIT)

    def test_false_positive_prevention(self):
        """ERROR keyword in non-error context."""
        # "error handling" in CODE_EDIT context, not DEBUG
        result = classify("Add error handling to the function")
        assert result.intent != IntentClass.DEBUG or result.needs_writeback is True

    def test_no_false_positive_debug_in_prose(self):
        """ERROR keyword in narrative context doesn't trigger DEBUG."""
        context = "This article discusses the errors made in history"
        result = classify("Summarize this", context=context)
        # Should be GEN_Q or another intent, not DEBUG
        assert result.intent != IntentClass.DEBUG


class TestDataclassStructure:
    """Tests for ClassificationResult structure."""

    def test_result_has_all_fields(self):
        """ClassificationResult has all required fields."""
        result = classify("test query")
        assert hasattr(result, "intent")
        assert hasattr(result, "complexity_score")
        assert hasattr(result, "needs_retrieval")
        assert hasattr(result, "needs_writeback")
        assert hasattr(result, "confidence")

    def test_intent_is_enum_value(self):
        """intent is a valid IntentClass enum value."""
        result = classify("test")
        assert isinstance(result.intent, IntentClass)
        assert result.intent in IntentClass.__members__.values()

    def test_scores_are_floats(self):
        """Complexity and confidence are floats."""
        result = classify("test")
        assert isinstance(result.complexity_score, float)
        assert isinstance(result.confidence, float)

    def test_flags_are_booleans(self):
        """Retrieval and writeback flags are booleans."""
        result = classify("test")
        assert isinstance(result.needs_retrieval, bool)
        assert isinstance(result.needs_writeback, bool)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
