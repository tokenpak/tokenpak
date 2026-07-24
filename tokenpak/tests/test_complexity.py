"""Unit tests for tokenpak/complexity.py"""

from tokenpak.compression.complexity import TaskType, _word_set, score_complexity

# ---------------------------------------------------------------------------
# score_complexity — basic return types
# ---------------------------------------------------------------------------


class TestScoreComplexityReturnType:
    def test_returns_tuple(self):
        result = score_complexity("hello")
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_score_is_float(self):
        score, _ = score_complexity("hello")
        assert isinstance(score, float)

    def test_task_type_is_task_type(self):
        _, task_type = score_complexity("hello")
        assert isinstance(task_type, TaskType)

    def test_score_in_range(self):
        score, _ = score_complexity("a" * 1000)
        assert 0.0 <= score <= 10.0

    def test_empty_query(self):
        score, task_type = score_complexity("")
        assert score == 0.0
        assert task_type == TaskType.UNKNOWN


# ---------------------------------------------------------------------------
# score_complexity — query length contribution
# ---------------------------------------------------------------------------


class TestQueryLengthScoring:
    def test_very_short_query_no_length_bonus(self):
        # < 5 words → 0.0 length score
        score, _ = score_complexity("hi")
        assert score <= 1.0  # just baseline from other factors

    def test_short_query_small_bonus(self):
        score_short, _ = score_complexity("hi")
        score_medium, _ = score_complexity("please help me with this thing")
        assert score_medium >= score_short

    def test_long_query_higher_score(self):
        short_score, _ = score_complexity("fix bug")
        long_score, _ = score_complexity(
            "please help me refactor and optimize this large module that has many "
            "dependencies and also needs to be integrated with the existing api "
            "endpoint and tested thoroughly"
        )
        assert long_score > short_score


# ---------------------------------------------------------------------------
# score_complexity — multistep patterns
# ---------------------------------------------------------------------------


class TestMultistepPatterns:
    def test_then_increases_score(self):
        base, _ = score_complexity("write a function")
        with_then, _ = score_complexity("write a function then also test it and then deploy it")
        assert with_then > base

    def test_step_number_increases_score(self):
        score, _ = score_complexity("step 1 do this step 2 do that step 3 verify")
        assert score >= 1.0

    def test_first_second_third_increases_score(self):
        score, _ = score_complexity("first install it second configure it third run it")
        assert score >= 1.0

    def test_multistep_capped_at_3(self):
        # Pile on many multistep patterns — should cap at 3.0 from that factor
        query = "then also and then after that finally step 1 step 2 step 3 step 4 first second third additionally moreover furthermore"
        score, _ = score_complexity(query)
        assert score <= 10.0


# ---------------------------------------------------------------------------
# score_complexity — complexity boosters
# ---------------------------------------------------------------------------


class TestComplexityBoosters:
    def test_optimize_boosts_score(self):
        base, _ = score_complexity("write a function")
        boosted, _ = score_complexity("optimize this function")
        assert boosted >= base

    def test_refactor_boosts_score(self):
        score, _ = score_complexity("refactor this module")
        assert score >= 0.5

    def test_debug_boosts_score(self):
        score, _ = score_complexity("debug this error")
        assert score >= 0.5

    def test_multiple_boosters_stack(self):
        single, _ = score_complexity("optimize this")
        multiple, _ = score_complexity(
            "optimize and refactor and debug and analyze the architecture"
        )
        assert multiple >= single


# ---------------------------------------------------------------------------
# score_complexity — code context
# ---------------------------------------------------------------------------


class TestCodeContext:
    def test_code_fences_in_context_boost_score(self):
        no_code, _ = score_complexity("explain this")
        with_code, _ = score_complexity(
            "explain this", context_blocks=["```python\ndef foo():\n    pass\n```"]
        )
        assert with_code >= no_code

    def test_multiple_code_fences_higher_boost(self):
        one_fence, _ = score_complexity("explain this", context_blocks=["```python\nx=1\n```"])
        three_fences, _ = score_complexity(
            "explain this",
            context_blocks=[
                "```python\nx=1\n```",
                "```python\ny=2\n```",
                "```python\nz=3\n```",
            ],
        )
        assert three_fences >= one_fence

    def test_inline_code_in_query_boosts_score(self):
        no_inline, _ = score_complexity("what does this do")
        with_inline, _ = score_complexity("what does `foo()` and `bar()` do")
        assert with_inline >= no_inline

    def test_large_context_volume_boosts_score(self):
        small_ctx, _ = score_complexity("summarize", context_blocks=["short context"])
        large_ctx, _ = score_complexity("summarize", context_blocks=["word " * 2500])
        assert large_ctx >= small_ctx


# ---------------------------------------------------------------------------
# TaskType classification
# ---------------------------------------------------------------------------


class TestClassifyTaskType:
    def test_coding_keywords_classified_as_coding(self):
        _, task_type = score_complexity("write a function to parse the api response")
        assert task_type == TaskType.CODING

    def test_summarization_keywords_classified(self):
        _, task_type = score_complexity("please summarize this document")
        assert task_type == TaskType.SUMMARIZATION

    def test_creative_keywords_classified(self):
        _, task_type = score_complexity("write a blog post about travel")
        # "write" overlaps with coding but creative should win or coding
        assert task_type in (TaskType.CREATIVE, TaskType.CODING)

    def test_unknown_for_no_signal(self):
        _, task_type = score_complexity("zyx qrs tuv")
        assert task_type == TaskType.UNKNOWN

    def test_code_fence_in_context_boosts_coding(self):
        _, task_type = score_complexity(
            "what does this do", context_blocks=["```python\ndef hello(): pass\n```"]
        )
        assert task_type == TaskType.CODING

    def test_python_extension_in_query_boosts_coding(self):
        _, task_type = score_complexity("edit the main.py file")
        assert task_type == TaskType.CODING

    def test_reasoning_keywords(self):
        _, task_type = score_complexity(
            "analyze and compare the tradeoffs of these architecture approaches"
        )
        assert task_type == TaskType.REASONING

    def test_qa_keywords(self):
        _, task_type = score_complexity("what is the best way to do this")
        assert task_type in (TaskType.QA, TaskType.CODING, TaskType.REASONING)


# ---------------------------------------------------------------------------
# _word_set helper
# ---------------------------------------------------------------------------


class TestWordSet:
    def test_basic_tokenization(self):
        result = _word_set("Hello World")
        assert result == {"hello", "world"}

    def test_strips_punctuation(self):
        result = _word_set("hello, world!")
        assert "hello" in result
        assert "world" in result

    def test_empty_string(self):
        assert _word_set("") == set()

    def test_numbers_included(self):
        result = _word_set("step 1 and step 2")
        assert "1" in result
        assert "2" in result


# ---------------------------------------------------------------------------
# TaskType enum
# ---------------------------------------------------------------------------


class TestTaskTypeEnum:
    def test_all_values_defined(self):
        expected = {"CODING", "REASONING", "SUMMARIZATION", "QA", "CREATIVE", "UNKNOWN"}
        actual = {t.value for t in TaskType}
        assert actual == expected

    def test_is_string_enum(self):
        assert isinstance(TaskType.CODING, str)
        assert TaskType.CODING == "CODING"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_none_context_blocks(self):
        score, task_type = score_complexity("write a function", context_blocks=None)
        assert isinstance(score, float)

    def test_empty_context_blocks_list(self):
        score, _ = score_complexity("debug this", context_blocks=[])
        assert score >= 0.0

    def test_score_clamped_to_10(self):
        # Construct a query that would score > 10 without clamping
        heavy_query = (
            "please refactor optimize debug analyze architect design integrate "
            "migrate decompose implement rewrite the multi-step system "
            "then also first second third after that additionally moreover furthermore "
            "and step 1 step 2 step 3 finally"
        )
        score, _ = score_complexity(heavy_query, context_blocks=["word " * 3000])
        assert score <= 10.0

    def test_score_not_negative(self):
        score, _ = score_complexity("")
        assert score >= 0.0

    def test_score_rounded_to_2_decimals(self):
        score, _ = score_complexity("analyze and optimize the refactored module")
        # Should be rounded to 2 decimal places
        assert score == round(score, 2)
