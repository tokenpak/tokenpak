"""
Tests for tokenpak/engines/ module
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Covers:
  - engines package imports and get_engine() factory (__init__.py)
  - CompactionHints: default and custom values (base.py)
  - CompactionEngine: estimate_tokens via concrete subclass (base.py)
  - HeuristicEngine: init, compact() logic, truncation, edge cases (heuristic.py)
  - LLMLinguaEngine: unavailable (ImportError path) (llmlingua.py)
  - LLMLinguaEngine: available (mocked PromptCompressor) (llmlingua.py)

All ML/external dependencies (llmlingua package) are mocked.
No live API calls are made.
"""

import sys
from unittest.mock import MagicMock, patch

import pytest

# ===========================================================================
# engines/__init__.py — package imports and get_engine() factory
# ===========================================================================


def test_engines_package_importable():
    import tokenpak.compression.engines  # noqa: F401


def test_compaction_engine_importable():
    from tokenpak.compression.engines import CompactionEngine  # noqa: F401


def test_heuristic_engine_importable():
    from tokenpak.compression.engines import HeuristicEngine  # noqa: F401


def test_llmlingua_available_is_bool():
    from tokenpak.compression.engines import LLMLINGUA_AVAILABLE
    assert isinstance(LLMLINGUA_AVAILABLE, bool)


def test_engines_dict_has_heuristic():
    from tokenpak.compression.engines import ENGINES, HeuristicEngine
    assert ENGINES.get("heuristic") is HeuristicEngine


def test_engines_dict_has_fast():
    from tokenpak.compression.engines import ENGINES, HeuristicEngine
    assert ENGINES.get("fast") is HeuristicEngine


def test_get_engine_no_args_returns_heuristic():
    from tokenpak.compression.engines import HeuristicEngine, get_engine
    assert isinstance(get_engine(), HeuristicEngine)


def test_get_engine_heuristic_by_name():
    from tokenpak.compression.engines import HeuristicEngine, get_engine
    assert isinstance(get_engine("heuristic"), HeuristicEngine)


def test_get_engine_fast_returns_heuristic():
    from tokenpak.compression.engines import HeuristicEngine, get_engine
    assert isinstance(get_engine("fast"), HeuristicEngine)


def test_get_engine_unknown_name_falls_back_to_heuristic():
    from tokenpak.compression.engines import HeuristicEngine, get_engine
    assert isinstance(get_engine("nonexistent_engine_xyz"), HeuristicEngine)


# ===========================================================================
# engines/base.py — CompactionHints dataclass
# ===========================================================================


def test_hints_default_target_tokens():
    from tokenpak.compression.engines.base import CompactionHints
    assert CompactionHints().target_tokens == 1000


def test_hints_default_preserve_patterns_is_none():
    from tokenpak.compression.engines.base import CompactionHints
    assert CompactionHints().preserve_patterns is None


def test_hints_default_preserve_first_n_sentences():
    from tokenpak.compression.engines.base import CompactionHints
    assert CompactionHints().preserve_first_n_sentences == 1


def test_hints_default_preserve_last_n_sentences():
    from tokenpak.compression.engines.base import CompactionHints
    assert CompactionHints().preserve_last_n_sentences == 0


def test_hints_default_keep_headers():
    from tokenpak.compression.engines.base import CompactionHints
    assert CompactionHints().keep_headers is True


def test_hints_default_keep_code_blocks():
    from tokenpak.compression.engines.base import CompactionHints
    assert CompactionHints().keep_code_blocks is True


def test_hints_default_aggressive_is_false():
    from tokenpak.compression.engines.base import CompactionHints
    assert CompactionHints().aggressive is False


def test_hints_custom_target_tokens():
    from tokenpak.compression.engines.base import CompactionHints
    assert CompactionHints(target_tokens=500).target_tokens == 500


def test_hints_custom_preserve_patterns():
    from tokenpak.compression.engines.base import CompactionHints
    h = CompactionHints(preserve_patterns=[r"\bfoo\b"])
    assert h.preserve_patterns == [r"\bfoo\b"]


def test_hints_custom_aggressive():
    from tokenpak.compression.engines.base import CompactionHints
    assert CompactionHints(aggressive=True).aggressive is True


# ===========================================================================
# engines/base.py — CompactionEngine.estimate_tokens (via concrete subclass)
# ===========================================================================


@pytest.fixture(scope="module")
def minimal_engine():
    from tokenpak.compression.engines.base import CompactionEngine

    class _Minimal(CompactionEngine):
        name = "minimal"

        def compact(self, text, hints=None):
            return text

    return _Minimal()


def test_estimate_tokens_empty_string_is_one(minimal_engine):
    assert minimal_engine.estimate_tokens("") == 1


def test_estimate_tokens_four_chars_one_token(minimal_engine):
    assert minimal_engine.estimate_tokens("abcd") == 1


def test_estimate_tokens_eight_chars_two_tokens(minimal_engine):
    assert minimal_engine.estimate_tokens("abcdefgh") == 2


def test_estimate_tokens_400_chars_100_tokens(minimal_engine):
    assert minimal_engine.estimate_tokens("a" * 400) == 100


def test_estimate_tokens_single_char_floors_to_one(minimal_engine):
    assert minimal_engine.estimate_tokens("x") == 1


def test_estimate_tokens_returns_int(minimal_engine):
    result = minimal_engine.estimate_tokens("hello world")
    assert isinstance(result, int)


# ===========================================================================
# engines/heuristic.py — HeuristicEngine
# ===========================================================================


def test_heuristic_engine_name():
    from tokenpak.compression.engines.heuristic import HeuristicEngine
    assert HeuristicEngine().name == "heuristic"


def test_heuristic_compact_empty_string_short_circuits():
    from tokenpak.compression.engines.heuristic import HeuristicEngine
    eng = HeuristicEngine()
    result = eng.compact("")
    assert result == ""


def test_heuristic_compact_returns_string():
    from tokenpak.compression.engines.heuristic import HeuristicEngine
    eng = HeuristicEngine()
    assert isinstance(eng.compact("hello world"), str)


def test_heuristic_compact_none_hints_works():
    from tokenpak.compression.engines.heuristic import HeuristicEngine
    eng = HeuristicEngine()
    result = eng.compact("some text", hints=None)
    assert isinstance(result, str)


def test_heuristic_compact_target_zero_skips_truncation():
    from tokenpak.compression.engines.base import CompactionHints
    from tokenpak.compression.engines.heuristic import HeuristicEngine

    mock_proc = MagicMock()
    long_output = "X" * 2000
    mock_proc.process.return_value = long_output
    with patch("tokenpak.compression.processors.text.TextProcessor", return_value=mock_proc):
        eng = HeuristicEngine()

    # target_tokens=0 → condition `hints.target_tokens > 0` is False → no truncation
    result = eng.compact("input", hints=CompactionHints(target_tokens=0))
    assert result == long_output


def test_heuristic_compact_truncates_when_over_target():
    from tokenpak.compression.engines.base import CompactionHints
    from tokenpak.compression.engines.heuristic import HeuristicEngine

    mock_proc = MagicMock()
    mock_proc.process.return_value = "A" * 200
    with patch("tokenpak.compression.processors.text.TextProcessor", return_value=mock_proc):
        eng = HeuristicEngine()

    # target_tokens=10 → target_chars=40; 200-char output exceeds it
    result = eng.compact("any input", hints=CompactionHints(target_tokens=10))
    assert result.endswith("…")
    assert len(result) < 200


def test_heuristic_compact_no_truncation_when_under_target():
    from tokenpak.compression.engines.base import CompactionHints
    from tokenpak.compression.engines.heuristic import HeuristicEngine

    expected = "short output text"
    mock_proc = MagicMock()
    mock_proc.process.return_value = expected
    with patch("tokenpak.compression.processors.text.TextProcessor", return_value=mock_proc):
        eng = HeuristicEngine()

    result = eng.compact("input", hints=CompactionHints(target_tokens=1000))
    assert result == expected


def test_heuristic_compact_calls_processor_with_text_and_empty_path():
    from tokenpak.compression.engines.heuristic import HeuristicEngine

    mock_proc = MagicMock()
    mock_proc.process.return_value = "processed"
    with patch("tokenpak.compression.processors.text.TextProcessor", return_value=mock_proc):
        eng = HeuristicEngine()

    eng.compact("hello world")
    mock_proc.process.assert_called_once_with("hello world", "")


def test_heuristic_compact_processor_not_called_for_empty():
    from tokenpak.compression.engines.heuristic import HeuristicEngine

    mock_proc = MagicMock()
    mock_proc.process.return_value = ""
    with patch("tokenpak.compression.processors.text.TextProcessor", return_value=mock_proc):
        eng = HeuristicEngine()

    eng.compact("")
    mock_proc.process.assert_not_called()


def test_heuristic_compact_truncates_at_newline_boundary():
    from tokenpak.compression.engines.base import CompactionHints
    from tokenpak.compression.engines.heuristic import HeuristicEngine

    mock_proc = MagicMock()
    # 20 A's + newline + 20 B's; target_tokens=5 → target_chars=20
    # result[:20] = "A"*20; rsplit('\n',1)[0] = "A"*20; then appends "\n…"
    mock_proc.process.return_value = "A" * 20 + "\n" + "B" * 20
    with patch("tokenpak.compression.processors.text.TextProcessor", return_value=mock_proc):
        eng = HeuristicEngine()

    result = eng.compact("input", hints=CompactionHints(target_tokens=5))
    assert "B" not in result
    assert result.endswith("…")


# ===========================================================================
# engines/llmlingua.py — LLMLinguaEngine when package is unavailable
# ===========================================================================


@pytest.fixture
def llmlingua_unavailable_engine():
    """LLMLinguaEngine instance created with llmlingua blocked from importing."""
    with patch.dict(sys.modules, {"llmlingua": None}):
        from tokenpak.compression.engines.llmlingua import LLMLinguaEngine
        return LLMLinguaEngine()


def test_llmlingua_unavailable_marks_flag(llmlingua_unavailable_engine):
    assert llmlingua_unavailable_engine._available is False


def test_llmlingua_unavailable_stores_error_string(llmlingua_unavailable_engine):
    assert hasattr(llmlingua_unavailable_engine, "_error")
    assert isinstance(llmlingua_unavailable_engine._error, str)


def test_llmlingua_unavailable_compact_raises_runtime_error(llmlingua_unavailable_engine):
    with pytest.raises(RuntimeError, match="LLMLingua not available"):
        llmlingua_unavailable_engine.compact("some text")


def test_llmlingua_unavailable_compact_raises_before_empty_check(llmlingua_unavailable_engine):
    # Empty string still hits the _available guard first → RuntimeError
    with pytest.raises(RuntimeError):
        llmlingua_unavailable_engine.compact("")


def test_llmlingua_unavailable_name(llmlingua_unavailable_engine):
    assert llmlingua_unavailable_engine.name == "llmlingua"


def test_llmlingua_unavailable_estimate_tokens_floor_one(llmlingua_unavailable_engine):
    assert llmlingua_unavailable_engine.estimate_tokens("") == 1


def test_llmlingua_unavailable_estimate_tokens_eight_chars(llmlingua_unavailable_engine):
    assert llmlingua_unavailable_engine.estimate_tokens("abcdefgh") == 2


# ===========================================================================
# engines/llmlingua.py — LLMLinguaEngine when package is available (mocked)
# ===========================================================================


@pytest.fixture
def mock_compressor():
    m = MagicMock()
    m.compress_prompt.return_value = {"compressed_prompt": "mocked output"}
    return m


@pytest.fixture
def llmlingua_available_engine(mock_compressor):
    """LLMLinguaEngine with mocked PromptCompressor injected via sys.modules."""
    mock_llm = MagicMock()
    mock_llm.PromptCompressor.return_value = mock_compressor
    with patch.dict(sys.modules, {"llmlingua": mock_llm}):
        from tokenpak.compression.engines.llmlingua import LLMLinguaEngine
        return LLMLinguaEngine()


def test_llmlingua_available_marks_flag(llmlingua_available_engine):
    assert llmlingua_available_engine._available is True


def test_llmlingua_available_compact_empty_returns_empty(llmlingua_available_engine, mock_compressor):
    result = llmlingua_available_engine.compact("")
    assert result == ""
    mock_compressor.compress_prompt.assert_not_called()


def test_llmlingua_available_compact_returns_compressed_prompt(llmlingua_available_engine):
    result = llmlingua_available_engine.compact("hello world")
    assert result == "mocked output"


def test_llmlingua_available_compact_rate_over_target(llmlingua_available_engine, mock_compressor):
    from tokenpak.compression.engines.base import CompactionHints
    # 400 chars → 100 estimated tokens; target=50 → ratio = 50/100 = 0.5
    llmlingua_available_engine.compact("a" * 400, hints=CompactionHints(target_tokens=50))
    kwargs = mock_compressor.compress_prompt.call_args[1]
    assert kwargs["rate"] == pytest.approx(0.5)


def test_llmlingua_available_compact_rate_defaults_half_when_under_target(
    llmlingua_available_engine, mock_compressor
):
    from tokenpak.compression.engines.base import CompactionHints
    # 4 chars → 1 token; target=1000 → current <= target → default rate 0.5
    llmlingua_available_engine.compact("test", hints=CompactionHints(target_tokens=1000))
    kwargs = mock_compressor.compress_prompt.call_args[1]
    assert kwargs["rate"] == pytest.approx(0.5)


def test_llmlingua_available_compact_rate_defaults_half_when_target_zero(
    llmlingua_available_engine, mock_compressor
):
    from tokenpak.compression.engines.base import CompactionHints
    llmlingua_available_engine.compact("some text", hints=CompactionHints(target_tokens=0))
    kwargs = mock_compressor.compress_prompt.call_args[1]
    assert kwargs["rate"] == pytest.approx(0.5)


def test_llmlingua_available_compact_force_tokens_from_preserve_patterns(
    llmlingua_available_engine, mock_compressor
):
    from tokenpak.compression.engines.base import CompactionHints
    hints = CompactionHints(preserve_patterns=[r"foo"])
    llmlingua_available_engine.compact("foo bar foo baz", hints=hints)
    kwargs = mock_compressor.compress_prompt.call_args[1]
    assert kwargs["force_tokens"] == ["foo", "foo"]


def test_llmlingua_available_compact_no_patterns_passes_none_for_force_tokens(
    llmlingua_available_engine, mock_compressor
):
    from tokenpak.compression.engines.base import CompactionHints
    llmlingua_available_engine.compact("text here", hints=CompactionHints(preserve_patterns=None))
    kwargs = mock_compressor.compress_prompt.call_args[1]
    assert kwargs["force_tokens"] is None


def test_llmlingua_available_compact_fallback_when_key_missing(
    llmlingua_available_engine, mock_compressor
):
    mock_compressor.compress_prompt.return_value = {}  # no 'compressed_prompt'
    result = llmlingua_available_engine.compact("original text here")
    assert result == "original text here"


def test_llmlingua_available_estimate_tokens_uses_tokenizer(
    llmlingua_available_engine, mock_compressor
):
    mock_compressor.tokenizer = MagicMock()
    mock_compressor.tokenizer.encode.return_value = list(range(7))
    assert llmlingua_available_engine.estimate_tokens("some text") == 7


def test_llmlingua_available_estimate_tokens_fallback_without_tokenizer():
    # Use spec so MagicMock does NOT auto-create a tokenizer attribute
    mock_comp = MagicMock(spec=["compress_prompt"])
    mock_comp.compress_prompt.return_value = {"compressed_prompt": "out"}
    mock_llm = MagicMock()
    mock_llm.PromptCompressor.return_value = mock_comp
    with patch.dict(sys.modules, {"llmlingua": mock_llm}):
        from tokenpak.compression.engines.llmlingua import LLMLinguaEngine
        eng = LLMLinguaEngine()
    # hasattr(mock_comp, "tokenizer") is False (restricted by spec)
    assert eng.estimate_tokens("abcdefgh") == 2  # 8 chars → 2 tokens
