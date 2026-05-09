"""Unit tests for citation_tracker.py (Part A — Citation-Mapped Utility Scoring)."""


import pytest

pytest.importorskip("tokenpak.citation_tracker", reason="module not available in current build")
import json
import os
import tempfile

import pytest
from tokenpak.citation_tracker import (
    CITE_DELTA,
    DECAY_DELTA,
    SCORE_MAX,
    SCORE_MIN,
    get_utility_score,
    get_utility_weight,
    track_citations,
    update_utility,
)
from tokenpak.wire import make_slice_id, pack

# ---------------------------------------------------------------------------
# make_slice_id
# ---------------------------------------------------------------------------

class TestMakeSliceId:
    def test_format(self):
        sid = make_slice_id("content", "path/to/file.py")
        assert sid.startswith("s_")
        assert len(sid) == 10  # "s_" + 8 hex chars

    def test_deterministic(self):
        sid1 = make_slice_id("some content", "ref")
        sid2 = make_slice_id("some content", "ref")
        assert sid1 == sid2

    def test_unique_per_content(self):
        sid1 = make_slice_id("content A", "ref")
        sid2 = make_slice_id("content B", "ref")
        assert sid1 != sid2

    def test_unique_per_ref(self):
        sid1 = make_slice_id("same content", "ref1")
        sid2 = make_slice_id("same content", "ref2")
        assert sid1 != sid2


# ---------------------------------------------------------------------------
# wire.pack — slice_id in output
# ---------------------------------------------------------------------------

class TestWirePack:
    def test_slice_id_in_output(self):
        blocks = [{"ref": "src/auth.py", "type": "CODE", "quality": 0.9,
                   "tokens": 100, "content": "def login(): pass"}]
        output = pack(blocks, budget=1000)
        assert "[SLICE: s_" in output

    def test_respects_existing_slice_id(self):
        blocks = [{"ref": "src/auth.py", "type": "CODE", "quality": 0.9,
                   "tokens": 100, "content": "def login(): pass",
                   "slice_id": "s_custom01"}]
        output = pack(blocks, budget=1000)
        assert "[SLICE: s_custom01]" in output

    def test_multiple_blocks_unique_ids(self):
        blocks = [
            {"ref": "a.py", "type": "CODE", "quality": 1.0, "tokens": 10, "content": "aaa"},
            {"ref": "b.py", "type": "CODE", "quality": 1.0, "tokens": 10, "content": "bbb"},
        ]
        output = pack(blocks, budget=1000)
        slice_ids = [line.split("[SLICE: ")[1].rstrip("]")
                     for line in output.splitlines() if "[SLICE:" in line]
        assert len(slice_ids) == 2
        assert slice_ids[0] != slice_ids[1]


# ---------------------------------------------------------------------------
# track_citations
# ---------------------------------------------------------------------------

class TestTrackCitations:
    def _make_slice(self, sid, content, ref=""):
        return {"slice_id": sid, "content": content, "ref": ref}

    def test_exact_content_match(self):
        content = "def authenticate(user, password):\n    return check(user, password)"
        slices = [self._make_slice("s_abc12345", content, "src/auth.py")]
        response = f"I used the auth logic:\n{content[:60]}"
        cited = track_citations(response, slices)
        assert "s_abc12345" in cited

    def test_path_mention(self):
        slices = [self._make_slice("s_path0001", "short", "src/auth/login.py")]
        response = "Looking at src/auth/login.py, the function does..."
        cited = track_citations(response, slices)
        assert "s_path0001" in cited

    def test_function_name_mention(self):
        content = "def authenticate(user, password):\n    pass"
        slices = [self._make_slice("s_fn000001", content)]
        response = "The authenticate function handles all user logins."
        cited = track_citations(response, slices)
        assert "s_fn000001" in cited

    def test_class_name_mention(self):
        content = "class UserRepository:\n    def find(self, id): pass"
        slices = [self._make_slice("s_cls00001", content)]
        response = "We inject a UserRepository into the service."
        cited = track_citations(response, slices)
        assert "s_cls00001" in cited

    def test_uncited_block_not_returned(self):
        content = "def completely_unrelated_fn(): pass"
        slices = [self._make_slice("s_miss0001", content, "nowhere.py")]
        response = "This response talks about something completely different."
        cited = track_citations(response, slices)
        assert "s_miss0001" not in cited

    def test_empty_response(self):
        slices = [self._make_slice("s_abc00000", "def foo(): pass")]
        cited = track_citations("", slices)
        assert cited == []

    def test_empty_slices(self):
        cited = track_citations("some response", [])
        assert cited == []

    def test_missing_slice_id_skipped(self):
        slices = [{"slice_id": "", "content": "def foo(): pass", "ref": ""}]
        cited = track_citations("foo is called here", slices)
        assert cited == []

    def test_multiple_slices_partial_citation(self):
        s1 = self._make_slice("s_cited001", "def login(): pass", "auth.py")
        s2 = self._make_slice("s_miss0002", "def register(): pass", "reg.py")
        response = "The login function at auth.py handles sign-in."
        cited = track_citations(response, [s1, s2])
        assert "s_cited001" in cited
        assert "s_miss0002" not in cited


# ---------------------------------------------------------------------------
# update_utility + get_utility_score + get_utility_weight
# ---------------------------------------------------------------------------

class TestUpdateUtility:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.utility_path = os.path.join(self.tmpdir, "utility.json")

    def test_cited_block_gains_score(self):
        update_utility(["s_abc"], ["s_abc"], self.utility_path)
        score = get_utility_score("s_abc", self.utility_path)
        assert score == pytest.approx(5.0 + CITE_DELTA)

    def test_uncited_block_decays(self):
        update_utility([], ["s_xyz"], self.utility_path)
        score = get_utility_score("s_xyz", self.utility_path)
        assert score == pytest.approx(5.0 - DECAY_DELTA)

    def test_score_clamped_at_max(self):
        for _ in range(100):
            update_utility(["s_top"], ["s_top"], self.utility_path)
        score = get_utility_score("s_top", self.utility_path)
        assert score <= SCORE_MAX

    def test_score_clamped_at_min(self):
        for _ in range(100):
            update_utility([], ["s_bot"], self.utility_path)
        score = get_utility_score("s_bot", self.utility_path)
        assert score >= SCORE_MIN

    def test_hits_and_misses_tracked(self):
        update_utility(["s_a"], ["s_a", "s_b"], self.utility_path)
        data = json.loads(open(self.utility_path).read())
        assert data["s_a"]["hits"] == 1
        assert data["s_a"]["misses"] == 0
        assert data["s_b"]["hits"] == 0
        assert data["s_b"]["misses"] == 1

    def test_last_cited_set_on_hit(self):
        update_utility(["s_a"], ["s_a"], self.utility_path)
        data = json.loads(open(self.utility_path).read())
        assert data["s_a"]["last_cited"] is not None

    def test_unknown_slice_returns_neutral(self):
        score = get_utility_score("s_unknown", self.utility_path)
        assert score == 5.0

    def test_utility_weight_neutral_at_5(self):
        # Default score=5.0 → weight=1.0
        w = get_utility_weight("s_new", self.utility_path)
        assert w == pytest.approx(1.0)

    def test_utility_weight_boost_at_10(self):
        for _ in range(6):  # 5+6=11 → clamped to 10
            update_utility(["s_hot"], ["s_hot"], self.utility_path)
        w = get_utility_weight("s_hot", self.utility_path)
        assert w == pytest.approx(SCORE_MAX / 5.0)

    def test_utility_weight_suppress_at_0(self):
        for _ in range(60):
            update_utility([], ["s_cold"], self.utility_path)
        w = get_utility_weight("s_cold", self.utility_path)
        assert w == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# budget.py integration with utility_weight
# ---------------------------------------------------------------------------

class TestBudgetUtilityIntegration:
    def test_utility_weight_modulates_importance(self):
        from tokenpak.budget import BudgetBlock, quadratic_allocate

        # Two identical blocks except utility_weight
        hot = BudgetBlock(ref="hot", relevance_score=0.5, recency_score=0.5,
                          quality_score=1.0, type_weight=0.5, utility_weight=2.0)
        cold = BudgetBlock(ref="cold", relevance_score=0.5, recency_score=0.5,
                           quality_score=1.0, type_weight=0.5, utility_weight=0.1)

        allocs = quadratic_allocate([hot, cold], total_budget=1000)
        # Hot block should get more tokens
        assert allocs["hot"] > allocs["cold"]

    def test_utility_weight_neutral_keeps_parity(self):
        from tokenpak.budget import BudgetBlock, quadratic_allocate

        # Two identical blocks with neutral utility_weight
        a = BudgetBlock(ref="a", relevance_score=0.5, recency_score=0.5,
                        quality_score=1.0, type_weight=0.5, utility_weight=1.0)
        b = BudgetBlock(ref="b", relevance_score=0.5, recency_score=0.5,
                        quality_score=1.0, type_weight=0.5, utility_weight=1.0)

        allocs = quadratic_allocate([a, b], total_budget=1000)
        assert allocs["a"] == allocs["b"]
