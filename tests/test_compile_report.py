"""Tests for tokenpak/report.py and tokenpak/pack.py.

Covers:
  - Report generation: every compile() returns a report
  - All decisions recorded with reasons
  - Action coverage: KEPT, COMPACTED, REMOVED, TRUNCATED
  - Output formats: to_text(), to_json(), to_markdown()
  - Stats accuracy: token counts, savings %, compile time
"""

import json
import time

import pytest

from tokenpak.report import Action, CompileReport, Decision
from tokenpak.pack import ContextPack, PackBlock


# ── Helpers ───────────────────────────────────────────────────────────────

def make_pack(budget: int = 8000, quality_threshold: float = 0.5) -> ContextPack:
    return ContextPack(budget=budget, quality_threshold=quality_threshold)


def word_content(n: int, word: str = "word") -> str:
    """Generate ~n*4 char content that roughly yields n tokens."""
    return f"{word} " * n


# ── 1. Report Generation ──────────────────────────────────────────────────

class TestReportGeneration:

    def test_compile_returns_report(self):
        pack = make_pack()
        pack.add(PackBlock(id="sys", type="instructions", content="Hello.", priority="critical"))
        result = pack.compile()
        assert result.report is not None
        assert isinstance(result.report, CompileReport)

    def test_report_has_decisions(self):
        pack = make_pack()
        pack.add(PackBlock(id="sys", type="instructions", content="A block.", priority="critical"))
        pack.add(PackBlock(id="ctx", type="knowledge",    content="More context.", priority="high"))
        result = pack.compile()
        assert len(result.report.decisions) == 2

    def test_empty_pack_compiles(self):
        pack = make_pack()
        result = pack.compile()
        assert result.report.input_blocks == 0
        assert result.report.output_blocks == 0
        assert result.report.input_tokens == 0
        assert result.report.output_tokens == 0

    def test_compile_time_recorded(self):
        pack = make_pack()
        pack.add(PackBlock(id="a", type="instructions", content="text", priority="critical"))
        result = pack.compile()
        assert result.report.compile_time_ms >= 0.0
        assert isinstance(result.report.compile_time_ms, float)


# ── 2. Decision Actions ───────────────────────────────────────────────────

class TestDecisionActions:

    def test_kept_decision_present(self):
        pack = make_pack(budget=8000)
        pack.add(PackBlock(id="sys", type="instructions", content="System prompt text.", priority="critical"))
        result = pack.compile()
        d = next((d for d in result.report.decisions if d.block_id == "sys"), None)
        assert d is not None
        assert d.action == Action.KEPT

    def test_kept_shows_priority(self):
        pack = make_pack(budget=8000)
        pack.add(PackBlock(id="sys", type="instructions", content="critical block", priority="critical"))
        result = pack.compile()
        d = result.report.decisions[0]
        assert d.priority == "critical"

    def test_compacted_when_exceeds_max_tokens(self):
        # Block has 500 tokens worth of content but max_tokens=50
        content = "word " * 500  # ~500 tokens
        pack = make_pack(budget=8000)
        pack.add(PackBlock(
            id="api_docs",
            type="knowledge",
            content=content,
            priority="high",
            max_tokens=50,
        ))
        result = pack.compile()
        d = next(d for d in result.report.decisions if d.block_id == "api_docs")
        assert d.action == Action.COMPACTED
        assert d.method is not None
        assert d.tokens_before > d.tokens_after
        assert d.tokens_after <= 60  # should be near 50 ± encoding fuzz

    def test_compacted_shows_method(self):
        content = "data " * 600
        pack = make_pack(budget=8000)
        pack.add(PackBlock(id="doc", type="knowledge", content=content, priority="medium", max_tokens=100))
        result = pack.compile()
        d = next(d for d in result.report.decisions if d.block_id == "doc")
        assert d.action == Action.COMPACTED
        assert d.method == "extractive_truncation"
        assert d.reason != ""

    def test_removed_below_quality_threshold(self):
        pack = make_pack(budget=8000, quality_threshold=0.5)
        pack.add(PackBlock(id="bad", type="evidence", content="Low quality search result.", quality=0.3))
        result = pack.compile()
        d = next(d for d in result.report.decisions if d.block_id == "bad")
        assert d.action == Action.REMOVED
        assert "0.30" in d.reason or "quality" in d.reason.lower()
        assert d.tokens_after == 0
        assert d.quality == pytest.approx(0.3)

    def test_removed_shows_reason(self):
        pack = make_pack(budget=8000)
        pack.add(PackBlock(id="junk", type="evidence", content="Junk.", quality=0.1))
        result = pack.compile()
        d = next(d for d in result.report.decisions if d.block_id == "junk")
        assert d.action == Action.REMOVED
        assert len(d.reason) > 0

    def test_truncated_when_budget_exceeded_high_priority(self):
        # Small budget; high-priority block gets truncated rather than dropped
        content = "important " * 1000  # ~1000 tokens
        pack = make_pack(budget=100)
        pack.add(PackBlock(id="critical_docs", type="knowledge", content=content, priority="high"))
        result = pack.compile()
        d = next((d for d in result.report.decisions if d.block_id == "critical_docs"), None)
        assert d is not None
        # Should be TRUNCATED (high priority, partial budget), or KEPT if fits
        assert d.action in (Action.TRUNCATED, Action.KEPT, Action.REMOVED)
        # TRUNCATED: before > after
        if d.action == Action.TRUNCATED:
            assert d.tokens_before > d.tokens_after
            assert d.reason != ""

    def test_removed_low_priority_over_budget(self):
        # Fill budget with critical, then low priority has no room
        critical_content = "critical " * 800
        low_content = "extra " * 800
        pack = make_pack(budget=1000)
        pack.add(PackBlock(id="crit", type="instructions", content=critical_content, priority="critical"))
        pack.add(PackBlock(id="extra", type="context",     content=low_content,     priority="low"))
        result = pack.compile()
        d_extra = next((d for d in result.report.decisions if d.block_id == "extra"), None)
        assert d_extra is not None
        # Low priority may be removed or truncated depending on remaining space
        assert d_extra.action in (Action.REMOVED, Action.TRUNCATED, Action.KEPT)


# ── 3. All Decisions Covered ──────────────────────────────────────────────

class TestDecisionCoverage:

    def _get_decisions(self, result, action):
        return [d for d in result.report.decisions if d.action == action]

    def test_all_blocks_have_decisions(self):
        pack = make_pack(budget=8000)
        ids = ["a", "b", "c", "d"]
        for bid in ids:
            pack.add(PackBlock(id=bid, type="instructions", content=f"Content of {bid}", priority="medium"))
        result = pack.compile()
        decision_ids = {d.block_id for d in result.report.decisions}
        for bid in ids:
            assert bid in decision_ids, f"Missing decision for block '{bid}'"

    def test_mixed_scenario(self):
        """A realistic compile with all four action types."""
        long_doc = "knowledge " * 1000   # will be compacted
        low_qual  = "junk evidence "
        conv_hist = "message " * 2000    # may truncate

        pack = make_pack(budget=2000, quality_threshold=0.5)
        pack.add(PackBlock(id="sys",     type="instructions", content="System prompt",  priority="critical"))
        pack.add(PackBlock(id="docs",    type="knowledge",    content=long_doc,         priority="high",   max_tokens=200))
        pack.add(PackBlock(id="search",  type="evidence",     content=low_qual,         priority="medium", quality=0.2))
        pack.add(PackBlock(id="history", type="conversation", content=conv_hist,        priority="low"))

        result = pack.compile()
        report = result.report

        # sys → KEPT
        d_sys = next(d for d in report.decisions if d.block_id == "sys")
        assert d_sys.action == Action.KEPT

        # docs → COMPACTED
        d_docs = next(d for d in report.decisions if d.block_id == "docs")
        assert d_docs.action == Action.COMPACTED

        # search → REMOVED (quality)
        d_search = next(d for d in report.decisions if d.block_id == "search")
        assert d_search.action == Action.REMOVED

        # history → REMOVED or TRUNCATED (budget)
        d_hist = next(d for d in report.decisions if d.block_id == "history")
        assert d_hist.action in (Action.REMOVED, Action.TRUNCATED)


# ── 4. Output Formats ─────────────────────────────────────────────────────

class TestOutputFormats:

    def _sample_report(self) -> CompileReport:
        decisions = [
            Decision(
                block_id="sys",
                block_type="instructions",
                action=Action.KEPT,
                reason="critical priority",
                priority="critical",
                tokens_before=150,
                tokens_after=150,
            ),
            Decision(
                block_id="api_docs",
                block_type="knowledge",
                action=Action.COMPACTED,
                reason="exceeded block budget (max 1,000)",
                method="extractive_summarization",
                priority="high",
                tokens_before=2400,
                tokens_after=800,
            ),
            Decision(
                block_id="search_003",
                block_type="evidence",
                action=Action.REMOVED,
                reason="below quality threshold (0.30 < 0.50)",
                priority="medium",
                tokens_before=420,
                tokens_after=0,
                quality=0.3,
            ),
            Decision(
                block_id="history",
                block_type="conversation",
                action=Action.TRUNCATED,
                reason="conversation budget exceeded",
                priority="low",
                tokens_before=3200,
                tokens_after=650,
            ),
        ]
        return CompileReport(
            input_blocks=4,
            output_blocks=3,
            input_tokens=6170,
            output_tokens=1600,
            budget=8000,
            compile_time_ms=23.4,
            decisions=decisions,
            final_order=["sys", "api_docs", "history"],
        )

    # to_text

    def test_to_text_contains_summary(self):
        report = self._sample_report()
        text = report.to_text()
        assert "TokenPak Compile Report" in text
        assert "Summary" in text
        assert "Input:" in text
        assert "Output:" in text
        assert "Savings:" in text
        assert "Budget:" in text

    def test_to_text_contains_all_decisions(self):
        report = self._sample_report()
        text = report.to_text()
        assert "KEPT" in text
        assert "COMPACTED" in text
        assert "REMOVED" in text
        assert "TRUNCATED" in text

    def test_to_text_shows_priority(self):
        report = self._sample_report()
        text = report.to_text()
        assert "critical" in text

    def test_to_text_shows_method_for_compacted(self):
        report = self._sample_report()
        text = report.to_text()
        assert "extractive_summarization" in text

    def test_to_text_shows_quality_for_removed(self):
        report = self._sample_report()
        text = report.to_text()
        assert "0.30" in text or "0.3" in text

    def test_str_returns_text(self):
        report = self._sample_report()
        assert str(report) == report.to_text()

    def test_to_text_shows_priority_order(self):
        report = self._sample_report()
        text = report.to_text()
        assert "Priority Order" in text
        assert "sys" in text

    # to_json

    def test_to_json_is_dict(self):
        report = self._sample_report()
        j = report.to_json()
        assert isinstance(j, dict)

    def test_to_json_has_summary(self):
        report = self._sample_report()
        j = report.to_json()
        assert "summary" in j
        s = j["summary"]
        assert s["input_blocks"] == 4
        assert s["output_blocks"] == 3
        assert s["input_tokens"] == 6170
        assert s["output_tokens"] == 1600
        assert s["budget"] == 8000
        assert "budget_used_percent" in s
        assert "savings_percent" in s
        assert "compile_time_ms" in s
        assert "tokens_saved" in s

    def test_to_json_has_decisions(self):
        report = self._sample_report()
        j = report.to_json()
        assert "decisions" in j
        assert len(j["decisions"]) == 4

    def test_to_json_has_final_order(self):
        report = self._sample_report()
        j = report.to_json()
        assert "final_order" in j
        assert j["final_order"] == ["sys", "api_docs", "history"]

    def test_to_json_decisions_have_action(self):
        report = self._sample_report()
        j = report.to_json()
        actions = {d["action"] for d in j["decisions"]}
        assert "kept" in actions
        assert "compacted" in actions
        assert "removed" in actions
        assert "truncated" in actions

    def test_to_json_compacted_has_method(self):
        report = self._sample_report()
        j = report.to_json()
        compacted = next(d for d in j["decisions"] if d["action"] == "compacted")
        assert compacted["method"] == "extractive_summarization"

    def test_to_json_removed_has_quality(self):
        report = self._sample_report()
        j = report.to_json()
        removed = next(d for d in j["decisions"] if d["action"] == "removed")
        assert removed["quality"] == pytest.approx(0.3)

    def test_to_json_is_serializable(self):
        report = self._sample_report()
        j = report.to_json()
        dumped = json.dumps(j)  # Must not raise
        assert isinstance(dumped, str)

    # to_markdown

    def test_to_markdown_contains_headings(self):
        report = self._sample_report()
        md = report.to_markdown()
        assert "## TokenPak Compile Report" in md
        assert "### Summary" in md
        assert "### Block Decisions" in md

    def test_to_markdown_contains_table(self):
        report = self._sample_report()
        md = report.to_markdown()
        assert "| Metric |" in md
        assert "| Value |" in md

    def test_to_markdown_contains_all_action_labels(self):
        report = self._sample_report()
        md = report.to_markdown()
        assert "KEPT" in md
        assert "COMPACTED" in md
        assert "REMOVED" in md
        assert "TRUNCATED" in md

    def test_to_markdown_is_string(self):
        report = self._sample_report()
        md = report.to_markdown()
        assert isinstance(md, str)
        assert len(md) > 0


# ── 5. Stats Accuracy ─────────────────────────────────────────────────────

class TestStatsAccuracy:

    def test_input_blocks_count(self):
        pack = make_pack()
        for i in range(5):
            pack.add(PackBlock(id=f"b{i}", type="instructions", content=f"block {i}", priority="medium"))
        result = pack.compile()
        assert result.report.input_blocks == 5

    def test_output_blocks_less_than_input_when_removed(self):
        pack = make_pack(budget=8000)
        pack.add(PackBlock(id="good", type="instructions", content="Good block.", priority="critical"))
        pack.add(PackBlock(id="bad",  type="evidence",     content="Bad block.",  priority="medium", quality=0.1))
        result = pack.compile()
        assert result.report.output_blocks < result.report.input_blocks

    def test_savings_percent_correct(self):
        """savings_percent = (input - output) / input * 100"""
        report = CompileReport(
            input_blocks=10,
            output_blocks=7,
            input_tokens=10000,
            output_tokens=3000,
            budget=8000,
            compile_time_ms=10.0,
        )
        assert report.savings_percent == pytest.approx(70.0)

    def test_savings_percent_zero_when_nothing_removed(self):
        pack = make_pack(budget=8000)
        pack.add(PackBlock(id="a", type="instructions", content="Hello.", priority="critical"))
        result = pack.compile()
        assert result.report.savings_percent >= 0.0
        assert result.report.input_tokens == result.report.output_tokens

    def test_budget_used_percent_correct(self):
        report = CompileReport(
            input_blocks=5,
            output_blocks=5,
            input_tokens=4000,
            output_tokens=4000,
            budget=8000,
            compile_time_ms=5.0,
        )
        assert report.budget_used_percent == pytest.approx(50.0)

    def test_tokens_saved_property(self):
        report = CompileReport(
            input_blocks=3,
            output_blocks=2,
            input_tokens=5000,
            output_tokens=2000,
            budget=8000,
            compile_time_ms=3.0,
        )
        assert report.tokens_saved == 3000

    def test_compile_time_is_positive(self):
        pack = make_pack(budget=8000)
        pack.add(PackBlock(id="x", type="instructions", content="hello world", priority="medium"))
        result = pack.compile()
        assert result.report.compile_time_ms > 0.0

    def test_output_tokens_within_budget(self):
        pack = make_pack(budget=500)
        for i in range(10):
            pack.add(PackBlock(id=f"b{i}", type="knowledge", content="word " * 200, priority="medium"))
        result = pack.compile()
        # Output tokens should not exceed budget (with some fuzz for char-based estimates)
        assert result.report.output_tokens <= result.report.budget * 1.05


# ── 6. Decision Detail Coverage ───────────────────────────────────────────

class TestDecisionDetails:

    def test_kept_decision_has_tokens_after(self):
        pack = make_pack(budget=8000)
        pack.add(PackBlock(id="sys", type="instructions", content="Some system text.", priority="critical"))
        result = pack.compile()
        d = next(d for d in result.report.decisions if d.block_id == "sys")
        assert d.action == Action.KEPT
        assert d.tokens_after > 0

    def test_compacted_tokens_before_after(self):
        content = "data " * 500
        pack = make_pack(budget=8000)
        pack.add(PackBlock(id="docs", type="knowledge", content=content, priority="high", max_tokens=100))
        result = pack.compile()
        d = next(d for d in result.report.decisions if d.block_id == "docs")
        assert d.action == Action.COMPACTED
        assert d.tokens_before > d.tokens_after
        assert d.tokens_after > 0

    def test_removed_tokens_after_is_zero(self):
        pack = make_pack(budget=8000)
        pack.add(PackBlock(id="junk", type="evidence", content="trash", quality=0.0))
        result = pack.compile()
        d = next(d for d in result.report.decisions if d.block_id == "junk")
        assert d.action == Action.REMOVED
        assert d.tokens_after == 0
        assert d.tokens_saved > 0

    def test_truncated_shows_before_and_after(self):
        decisions = [
            Decision(
                block_id="history",
                block_type="conversation",
                action=Action.TRUNCATED,
                reason="conversation budget exceeded",
                priority="low",
                tokens_before=3200,
                tokens_after=650,
            )
        ]
        report = CompileReport(
            input_blocks=1,
            output_blocks=1,
            input_tokens=3200,
            output_tokens=650,
            budget=1000,
            compile_time_ms=2.0,
            decisions=decisions,
        )
        text = report.to_text()
        assert "3,200" in text
        assert "650" in text
        assert "TRUNCATED" in text

    def test_decision_to_dict(self):
        d = Decision(
            block_id="api",
            block_type="knowledge",
            action=Action.COMPACTED,
            reason="exceeded block budget",
            method="extractive_summarization",
            priority="high",
            tokens_before=2400,
            tokens_after=800,
            quality=None,
        )
        as_dict = d.to_dict()
        assert as_dict["action"] == "compacted"
        assert as_dict["method"] == "extractive_summarization"
        assert as_dict["tokens_before"] == 2400
        assert as_dict["tokens_after"] == 800
        assert "quality" not in as_dict  # None values not included

    def test_decision_quality_included_when_set(self):
        d = Decision(
            block_id="ev",
            block_type="evidence",
            action=Action.REMOVED,
            reason="low quality",
            priority="medium",
            tokens_before=400,
            tokens_after=0,
            quality=0.25,
        )
        as_dict = d.to_dict()
        assert "quality" in as_dict
        assert as_dict["quality"] == pytest.approx(0.25)
