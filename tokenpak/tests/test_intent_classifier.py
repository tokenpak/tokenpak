"""Unit tests for intent_classifier.py.

Tests cover: all IntentClass values are reachable, complexity scores in 0.0-1.0,
needs_retrieval/needs_writeback flags, edge cases.
"""

import pytest

from tokenpak.compression.intent_classifier import (
    ClassificationResult,
    IntentClass,
    classify,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _assert_valid_result(result: ClassificationResult) -> None:
    """Assert all fields are within valid ranges."""
    assert isinstance(result, ClassificationResult)
    assert isinstance(result.intent, IntentClass)
    assert 0.0 <= result.complexity_score <= 1.0
    assert 0.0 <= result.confidence <= 1.0
    assert isinstance(result.needs_retrieval, bool)
    assert isinstance(result.needs_writeback, bool)


# ---------------------------------------------------------------------------
# Test 1: GEN_Q — general question with no code signals
# ---------------------------------------------------------------------------

def test_gen_q_basic():
    result = classify("What is machine learning?")
    _assert_valid_result(result)
    assert result.intent == IntentClass.GEN_Q
    assert result.needs_retrieval is False
    assert result.needs_writeback is False


# ---------------------------------------------------------------------------
# Test 2: CODE_Q — code explanation request
# ---------------------------------------------------------------------------

def test_code_q_explain():
    result = classify(
        "Explain how the retry mechanism works",
        context="def retry(func, times=3):\n    for _ in range(times):\n        func()",
    )
    _assert_valid_result(result)
    assert result.intent == IntentClass.CODE_Q
    assert result.needs_retrieval is True


def test_code_q_file_path():
    result = classify("Where is the auth_guard.py module located?")
    _assert_valid_result(result)
    assert result.intent == IntentClass.CODE_Q


# ---------------------------------------------------------------------------
# Test 3: CODE_EDIT — code modification request
# ---------------------------------------------------------------------------

def test_code_edit_refactor():
    result = classify("Refactor the BudgetController to use async/await")
    _assert_valid_result(result)
    assert result.intent == IntentClass.CODE_EDIT
    assert result.needs_writeback is True


def test_code_edit_implement():
    result = classify("Implement a rate limiter for the proxy module")
    _assert_valid_result(result)
    assert result.intent == IntentClass.CODE_EDIT
    assert result.needs_writeback is True


# ---------------------------------------------------------------------------
# Test 4: DEBUG — error/traceback analysis
# ---------------------------------------------------------------------------

def test_debug_stack_trace():
    result = classify(
        "Why is my app crashing?",
        context="Traceback (most recent call last):\n  File 'proxy.py', line 42\nKeyError: 'model'",
    )
    _assert_valid_result(result)
    assert result.intent == IntentClass.DEBUG
    assert result.needs_retrieval is True


def test_debug_error_keyword():
    result = classify("There's an exception thrown when I call the classify function")
    _assert_valid_result(result)
    assert result.intent == IntentClass.DEBUG


# ---------------------------------------------------------------------------
# Test 5: DOC_EDIT — documentation request
# ---------------------------------------------------------------------------

def test_doc_edit_readme():
    result = classify("Write a README for the tokenpak project")
    _assert_valid_result(result)
    assert result.intent == IntentClass.DOC_EDIT
    assert result.needs_writeback is True


def test_doc_edit_docstring():
    result = classify("Add a docstring to the classify function")
    _assert_valid_result(result)
    assert result.intent == IntentClass.DOC_EDIT
    assert result.needs_writeback is True


# ---------------------------------------------------------------------------
# Test 6: PLAN — architecture/design request
# ---------------------------------------------------------------------------

def test_plan_architecture():
    result = classify("Design the architecture for a distributed rate limiter")
    _assert_valid_result(result)
    assert result.intent == IntentClass.PLAN


def test_plan_strategy():
    result = classify("What is the best strategy to modularize the proxy pipeline?")
    _assert_valid_result(result)
    assert result.intent == IntentClass.PLAN


# ---------------------------------------------------------------------------
# Test 7: REVIEW — diff/PR/feedback request
# ---------------------------------------------------------------------------

def test_review_diff():
    result = classify("Review this diff and give me feedback", context="diff --git a/proxy.py b/proxy.py")
    _assert_valid_result(result)
    assert result.intent == IntentClass.REVIEW
    assert result.needs_retrieval is True


def test_review_pull_request():
    result = classify("Can you check this pull request before I merge?")
    _assert_valid_result(result)
    assert result.intent == IntentClass.REVIEW


# ---------------------------------------------------------------------------
# Test 8: Complexity scores in valid range for all intents
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("query,context", [
    ("What?", ""),
    ("Explain this function in detail including all edge cases and performance implications", "def foo(): pass"),
    (
        "Refactor the entire auth system to use JWT tokens and add rate limiting and logging",
        "class Auth:\n    pass\n" * 50,
    ),
    ("Fix the bug", "Traceback:\n  File 'a.py', line 1\nKeyError"),
])
def test_complexity_score_always_valid(query, context):
    result = classify(query, context=context)
    _assert_valid_result(result)
    assert 0.0 <= result.complexity_score <= 1.0


# ---------------------------------------------------------------------------
# Test 9: needs_retrieval / needs_writeback flags
# ---------------------------------------------------------------------------

def test_gen_q_no_retrieval_no_writeback():
    result = classify("Tell me about Python's GIL")
    _assert_valid_result(result)
    assert result.needs_retrieval is False
    assert result.needs_writeback is False


def test_code_edit_writeback():
    result = classify("Update the config loader to support TOML files")
    _assert_valid_result(result)
    assert result.needs_writeback is True


def test_debug_needs_retrieval():
    result = classify("My app raises a traceback on startup")
    _assert_valid_result(result)
    assert result.needs_retrieval is True


# ---------------------------------------------------------------------------
# Test 10: Edge cases
# ---------------------------------------------------------------------------

def test_empty_string():
    result = classify("")
    _assert_valid_result(result)
    # Empty string should fall through to GEN_Q (no signals)
    assert result.intent == IntentClass.GEN_Q
    assert result.complexity_score == 0.0


def test_very_long_query():
    long_query = "explain " + " ".join(["this"] * 200)
    result = classify(long_query)
    _assert_valid_result(result)
    # complexity should be at the high end due to length
    assert result.complexity_score >= 0.25


def test_code_like_text():
    result = classify(
        "What does `router.add_route('/v1/chat', handler)` do?",
        context="def handler(req): ...",
    )
    _assert_valid_result(result)
    # "does" + code context: should be a code-related intent (CODE_Q, CODE_EDIT, or DEBUG)
    assert result.intent in (IntentClass.CODE_Q, IntentClass.CODE_EDIT, IntentClass.DEBUG)


def test_all_intent_classes_exist():
    """All IntentClass enum values are defined."""
    expected = {"GEN_Q", "CODE_Q", "CODE_EDIT", "DEBUG", "DOC_EDIT", "PLAN", "REVIEW"}
    actual = {ic.value for ic in IntentClass}
    assert actual == expected


def test_file_path_optional_none():
    result = classify("Fix the bug in auth.py", file_paths=None)
    _assert_valid_result(result)


def test_file_path_list_triggers_retrieval():
    # GEN_Q ignores file_paths for retrieval; use a CODE_Q query to ensure retrieval fires
    result = classify("Explain the classify function", file_paths=["tokenpak/intent_classifier.py"])
    _assert_valid_result(result)
    # CODE_Q always needs retrieval; file_paths also trigger it for other non-GEN_Q intents
    assert result.needs_retrieval is True


def test_confidence_range():
    """Confidence is always in [0.0, 1.0]."""
    queries = [
        "hello",
        "fix this error: KeyError on line 5",
        "refactor and optimize and redesign the entire module",
        "review this diff",
        "write docs for the module",
    ]
    for q in queries:
        result = classify(q)
        assert 0.0 <= result.confidence <= 1.0, f"confidence out of range for: {q!r}"
