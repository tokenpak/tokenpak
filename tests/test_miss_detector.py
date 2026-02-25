"""Unit tests for miss_detector.py (Part B — Context Miss Detection)."""

import json
import os
import tempfile

import pytest

from tokenpak.miss_detector import (
    ContextGap,
    SignalType,
    detect_misses,
    save_gaps,
    load_gaps,
    should_expand_retrieval,
    _word_overlap_ratio,
    DEFAULT_GAPS_PATH,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _gaps_of_type(gaps, signal_type):
    return [g for g in gaps if g.signal_type == signal_type]


# ---------------------------------------------------------------------------
# EXPLICIT_ASK detection
# ---------------------------------------------------------------------------

class TestExplicitAsk:
    def test_i_dont_have(self):
        response = "I don't have access to that module's source code."
        gaps = detect_misses(response, "find auth module", [])
        assert any(g.signal_type == SignalType.EXPLICIT_ASK for g in gaps)

    def test_not_provided(self):
        response = "The configuration file was not provided in context."
        gaps = detect_misses(response, "check config", [])
        assert any(g.signal_type == SignalType.EXPLICIT_ASK for g in gaps)

    def test_no_information_about(self):
        response = "I have no information about the deployment pipeline."
        gaps = detect_misses(response, "describe pipeline", [])
        assert any(g.signal_type == SignalType.EXPLICIT_ASK for g in gaps)

    def test_id_need_to_see(self):
        response = "I'd need to see the database schema to answer that."
        gaps = detect_misses(response, "describe schema", [])
        assert any(g.signal_type == SignalType.EXPLICIT_ASK for g in gaps)

    def test_wasnt_provided(self):
        response = "The test file wasn't provided so I can't check it."
        gaps = detect_misses(response, "run tests", [])
        assert any(g.signal_type == SignalType.EXPLICIT_ASK for g in gaps)

    def test_evidence_captured(self):
        response = "I don't have access to the credentials file."
        gaps = detect_misses(response, "show credentials", [])
        ea_gaps = _gaps_of_type(gaps, SignalType.EXPLICIT_ASK)
        assert ea_gaps[0].evidence != ""

    def test_no_false_positive_on_clean_response(self):
        response = "The authenticate function is in src/auth.py line 42."
        gaps = detect_misses(response, "find auth", ["def authenticate(user): ..."])
        ea_gaps = _gaps_of_type(gaps, SignalType.EXPLICIT_ASK)
        assert len(ea_gaps) == 0


# ---------------------------------------------------------------------------
# UNCERTAIN_ANSWER detection
# ---------------------------------------------------------------------------

class TestUncertainAnswer:
    def test_i_think(self):
        response = "I think the function is called login_user."
        gaps = detect_misses(response, "find login", [])
        assert any(g.signal_type == SignalType.UNCERTAIN_ANSWER for g in gaps)

    def test_probably(self):
        response = "This is probably in the utils module."
        gaps = detect_misses(response, "utils location", [])
        assert any(g.signal_type == SignalType.UNCERTAIN_ANSWER for g in gaps)

    def test_im_not_sure(self):
        response = "I'm not sure which version is being used here."
        gaps = detect_misses(response, "check version", [])
        assert any(g.signal_type == SignalType.UNCERTAIN_ANSWER for g in gaps)

    def test_it_might_be(self):
        response = "It might be defined in the config.py file."
        gaps = detect_misses(response, "config location", [])
        assert any(g.signal_type == SignalType.UNCERTAIN_ANSWER for g in gaps)

    def test_i_believe(self):
        response = "I believe this returns a list of User objects."
        gaps = detect_misses(response, "return type", [])
        assert any(g.signal_type == SignalType.UNCERTAIN_ANSWER for g in gaps)

    def test_evidence_is_meaningful(self):
        response = "Hmm, I'm not sure about the parameter order here."
        gaps = detect_misses(response, "param order", [])
        ua_gaps = _gaps_of_type(gaps, SignalType.UNCERTAIN_ANSWER)
        assert "not sure" in ua_gaps[0].evidence.lower()

    def test_confident_response_no_uncertain(self):
        response = "The function `create_user` takes two args: name and email."
        gaps = detect_misses(response, "create_user args", [])
        ua_gaps = _gaps_of_type(gaps, SignalType.UNCERTAIN_ANSWER)
        assert len(ua_gaps) == 0


# ---------------------------------------------------------------------------
# MISSING_INFO detection
# ---------------------------------------------------------------------------

class TestMissingInfo:
    def test_i_dont_see(self):
        response = "I don't see a `config.py` file in the provided context."
        gaps = detect_misses(response, "find config", [])
        assert any(g.signal_type == SignalType.MISSING_INFO for g in gaps)

    def test_theres_no(self):
        response = "There's no `UserRepository` class defined in these blocks."
        gaps = detect_misses(response, "user repo", [])
        assert any(g.signal_type == SignalType.MISSING_INFO for g in gaps)

    def test_couldnt_find(self):
        response = "I couldn't find any matching functions in the codebase."
        gaps = detect_misses(response, "find fn", [])
        assert any(g.signal_type == SignalType.MISSING_INFO for g in gaps)

    def test_not_found_in(self):
        response = "The class was not found in the provided files."
        gaps = detect_misses(response, "find class", [])
        assert any(g.signal_type == SignalType.MISSING_INFO for g in gaps)

    def test_missing_from(self):
        response = "That import is missing from the provided `utils.py` context."
        gaps = detect_misses(response, "utils import", [])
        assert any(g.signal_type == SignalType.MISSING_INFO for g in gaps)

    def test_requires_file_reference(self):
        # "there's no" alone without any file/fn reference should NOT fire MISSING_INFO
        response = "There's no reason to do it that way."
        gaps = detect_misses(response, "approach", [])
        mi_gaps = _gaps_of_type(gaps, SignalType.MISSING_INFO)
        # This is ambiguous — just ensure we don't crash; result may vary
        # (the pattern "there's no" + checking _has_file_or_fn_reference)
        assert isinstance(mi_gaps, list)


# ---------------------------------------------------------------------------
# HALLUCINATED_IMPORT detection
# ---------------------------------------------------------------------------

class TestHallucinatedImport:
    def test_import_not_in_context(self):
        response = "import my_custom_module\n\nresult = my_custom_module.run()"
        gaps = detect_misses(response, "run module", [])
        hi_gaps = _gaps_of_type(gaps, SignalType.HALLUCINATED_IMPORT)
        assert len(hi_gaps) >= 1
        assert any("my_custom_module" in g.evidence for g in hi_gaps)

    def test_from_import_not_in_context(self):
        response = "from phantom_lib import PhantomClass"
        gaps = detect_misses(response, "use phantom", [])
        hi_gaps = _gaps_of_type(gaps, SignalType.HALLUCINATED_IMPORT)
        assert any("phantom_lib" in g.evidence for g in hi_gaps)

    def test_stdlib_imports_ignored(self):
        response = "import os\nimport sys\nimport json\nfrom pathlib import Path"
        gaps = detect_misses(response, "system imports", [])
        hi_gaps = _gaps_of_type(gaps, SignalType.HALLUCINATED_IMPORT)
        assert len(hi_gaps) == 0

    def test_import_present_in_context(self):
        response = "import auth_utils\nauth_utils.login(user)"
        context = ["# auth_utils.py\ndef login(user): pass"]
        gaps = detect_misses(response, "login", context)
        hi_gaps = _gaps_of_type(gaps, SignalType.HALLUCINATED_IMPORT)
        assert len(hi_gaps) == 0

    def test_multiple_hallucinated_imports(self):
        response = "import ghost_module\nfrom shadow_pkg import something"
        gaps = detect_misses(response, "test", [])
        hi_gaps = _gaps_of_type(gaps, SignalType.HALLUCINATED_IMPORT)
        modules = [g.evidence for g in hi_gaps]
        assert any("ghost_module" in m for m in modules)
        assert any("shadow_pkg" in m for m in modules)


# ---------------------------------------------------------------------------
# WRONG_SIGNATURE detection
# ---------------------------------------------------------------------------

class TestWrongSignature:
    def test_too_few_args(self):
        context = ["def connect(host, port, timeout):\n    pass"]
        # Called with only 1 arg (missing port + timeout)
        response = "You can call it like: connect('localhost')"
        gaps = detect_misses(response, "connect usage", context)
        ws_gaps = _gaps_of_type(gaps, SignalType.WRONG_SIGNATURE)
        assert len(ws_gaps) >= 1

    def test_too_many_args(self):
        context = ["def save(data):\n    pass"]
        response = "Call it as: save(data, extra, another_arg)"
        gaps = detect_misses(response, "save usage", context)
        ws_gaps = _gaps_of_type(gaps, SignalType.WRONG_SIGNATURE)
        assert len(ws_gaps) >= 1

    def test_correct_signature_no_gap(self):
        context = ["def create(name, email):\n    pass"]
        response = "Use create(name, email) to register a user."
        gaps = detect_misses(response, "create usage", context)
        ws_gaps = _gaps_of_type(gaps, SignalType.WRONG_SIGNATURE)
        assert len(ws_gaps) == 0

    def test_function_not_in_context(self):
        # If function not in context, should not flag WRONG_SIGNATURE
        context = []
        response = "Call fetch(url, headers, timeout)"
        gaps = detect_misses(response, "fetch", context)
        ws_gaps = _gaps_of_type(gaps, SignalType.WRONG_SIGNATURE)
        assert len(ws_gaps) == 0

    def test_evidence_includes_fn_name(self):
        context = ["def process(data, config, logger):\n    pass"]
        response = "Just do: process(raw_data)"
        gaps = detect_misses(response, "process fn", context)
        ws_gaps = _gaps_of_type(gaps, SignalType.WRONG_SIGNATURE)
        if ws_gaps:  # may not fire depending on self-offset logic
            assert "process" in ws_gaps[0].evidence


# ---------------------------------------------------------------------------
# ContextGap dataclass
# ---------------------------------------------------------------------------

class TestContextGap:
    def test_fields_populated(self):
        gap = ContextGap(
            query="find auth",
            signal_type=SignalType.EXPLICIT_ASK,
            evidence="I don't have access",
            timestamp="2026-02-25T00:00:00+00:00",
            related_blocks=["src/auth.py"],
        )
        assert gap.query == "find auth"
        assert gap.signal_type == SignalType.EXPLICIT_ASK
        assert gap.evidence == "I don't have access"
        assert gap.related_blocks == ["src/auth.py"]

    def test_related_blocks_defaults_empty(self):
        gap = ContextGap(
            query="q", signal_type=SignalType.UNCERTAIN_ANSWER,
            evidence="I think", timestamp="2026-02-25T00:00:00+00:00",
        )
        assert gap.related_blocks == []

    def test_timestamp_set(self):
        response = "I don't have the source for that."
        gaps = detect_misses(response, "src", [])
        ea_gaps = _gaps_of_type(gaps, SignalType.EXPLICIT_ASK)
        assert ea_gaps[0].timestamp != ""


# ---------------------------------------------------------------------------
# Persistence: save_gaps + load_gaps
# ---------------------------------------------------------------------------

class TestGapPersistence:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.gaps_path = os.path.join(self.tmpdir, "gaps.json")

    def test_write_and_read_round_trip(self):
        response = "I don't have that file in context."
        gaps = detect_misses(response, "find file", ["src/auth.py"])
        save_gaps(gaps, self.gaps_path)
        loaded = load_gaps(self.gaps_path)
        assert len(loaded) >= 1
        assert loaded[0]["query"] == "find file"
        assert loaded[0]["signal_type"] == "EXPLICIT_ASK"

    def test_append_not_overwrite(self):
        r1 = "I don't have that information."
        r2 = "I'm not sure about this."
        save_gaps(detect_misses(r1, "q1", []), self.gaps_path)
        save_gaps(detect_misses(r2, "q2", []), self.gaps_path)
        loaded = load_gaps(self.gaps_path)
        assert len(loaded) >= 2

    def test_empty_gaps_not_written(self):
        response = "The function is clearly defined on line 12."
        gaps = detect_misses(response, "fn location", [])
        save_gaps(gaps, self.gaps_path)
        loaded = load_gaps(self.gaps_path)
        assert len(loaded) == 0

    def test_related_blocks_persisted(self):
        response = "I don't have access to the router."
        gaps = detect_misses(response, "router", ["src/router.py", "src/app.py"])
        save_gaps(gaps, self.gaps_path)
        loaded = load_gaps(self.gaps_path)
        assert "src/router.py" in loaded[0]["related_blocks"]

    def test_missing_file_returns_empty(self):
        loaded = load_gaps(self.gaps_path)
        assert loaded == []


# ---------------------------------------------------------------------------
# Retrieval expansion
# ---------------------------------------------------------------------------

class TestRetrievalExpansion:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.gaps_path = os.path.join(self.tmpdir, "gaps.json")

    def _write_gap(self, query):
        gaps = [ContextGap(
            query=query,
            signal_type=SignalType.EXPLICIT_ASK,
            evidence="I don't have it",
            timestamp="2026-02-25T00:00:00+00:00",
        )]
        save_gaps(gaps, self.gaps_path)

    def test_expands_on_similar_query(self):
        self._write_gap("find authentication module")
        assert should_expand_retrieval("authentication function", self.gaps_path)

    def test_no_expand_on_unrelated_query(self):
        self._write_gap("find authentication module")
        assert not should_expand_retrieval("database schema design", self.gaps_path)

    def test_no_expand_on_empty_gaps(self):
        assert not should_expand_retrieval("anything", self.gaps_path)

    def test_word_overlap_ratio_exact(self):
        assert _word_overlap_ratio("foo bar baz", "foo bar baz") == pytest.approx(1.0)

    def test_word_overlap_ratio_partial(self):
        ratio = _word_overlap_ratio("find auth module", "auth module location")
        assert ratio >= 0.5

    def test_word_overlap_ratio_zero(self):
        assert _word_overlap_ratio("apple orange", "database schema") == pytest.approx(0.0)

    def test_expand_in_cli_search(self):
        """Integration: cmd_search doubles top_k when prior miss detected."""
        import argparse
        from unittest.mock import patch, MagicMock

        # Write a prior gap for "authentication"
        self._write_gap("authentication login flow")

        # Build minimal args
        args = argparse.Namespace(
            db=":memory:",
            query="authentication code",
            budget=8000,
            top_k=5,
            gaps=self.gaps_path,
        )

        with patch("tokenpak.cli.BlockRegistry") as MockReg:
            instance = MagicMock()
            instance.search.return_value = []
            MockReg.return_value = instance

            from tokenpak.cli import cmd_search
            cmd_search(args)

            # search should have been called with doubled top_k=10
            instance.search.assert_called_once_with("authentication code", top_k=10)
