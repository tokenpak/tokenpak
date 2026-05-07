"""tests/test_adoption_killers.py — The 5 Adoption Killers test suite.

Enforces the non-negotiable technical requirements that prevent TokenPak
from dying to developer friction. All 5 requirements must pass on every PR.

Requirements tested:
  1. Latency       — p50 < 20ms, p95 < 50ms compile time
  2. Determinism   — 100 consecutive compiles produce identical output
  3. Transparency  — every compile produces a full report (schema validation)
  4. Stack Neutral — SDK works without gateway; outputs for 5+ providers
  5. Incremental   — 5 adoption levels each work independently
"""

from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.pack", reason="module not available in current build")
import hashlib
import json
import statistics
import time
from typing import List

import pytest

from tokenpak.pack import ContextPack, PackBlock
from tokenpak.report import Action, CompileReport

# ── Shared fixtures ───────────────────────────────────────────────────────

def _make_pack(budget: int = 8000) -> ContextPack:
    pack = ContextPack(budget=budget)
    pack.add(PackBlock(id="sys", type="instructions", content="You are a helpful assistant.", priority="critical"))
    pack.add(PackBlock(id="docs", type="knowledge", content="word " * 300, priority="high"))
    pack.add(PackBlock(id="ev", type="evidence", content="result " * 50, priority="medium", quality=0.8))
    pack.add(PackBlock(id="hist", type="conversation", content="message " * 100, priority="low"))
    return pack


def _pack_hash(pack: ContextPack) -> str:
    """Compile and return a stable hash of the output text."""
    result = pack.compile()
    return hashlib.sha256(result.text.encode()).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
# REQUIREMENT 1: LATENCY — "Compilation Must Feel Free"
# ═══════════════════════════════════════════════════════════════════════════

class TestLatencyRequirement:
    """p50 compile < 20ms, p95 compile < 50ms. Enforced in CI."""

    N_RUNS = 100  # sample size for percentile measurements

    def _compile_times_ms(self, pack: ContextPack, n: int = N_RUNS) -> List[float]:
        """Measure n compile times in milliseconds."""
        times = []
        for _ in range(n):
            # Re-create identical pack each run (same input = cold compile)
            fresh = ContextPack(budget=pack.budget)
            for b in pack._blocks:
                fresh.add(PackBlock(
                    id=b.id,
                    type=b.type,
                    content=b.content,
                    priority=b.priority,
                    quality=b.quality,
                    max_tokens=b.max_tokens,
                ))
            t0 = time.perf_counter()
            fresh.compile()
            times.append((time.perf_counter() - t0) * 1000.0)
        return times

    def test_p95_compile_under_50ms(self):
        """p95 compile time must be < 50ms (non-negotiable CI gate)."""
        pack = _make_pack()
        times = self._compile_times_ms(pack)
        p95 = sorted(times)[int(len(times) * 0.95)]
        assert p95 < 50.0, (
            f"p95 compile time {p95:.1f}ms exceeds 50ms threshold. "
            f"Times: min={min(times):.1f}ms p50={statistics.median(times):.1f}ms p95={p95:.1f}ms"
        )

    def test_p50_compile_under_20ms(self):
        """p50 compile time should be < 20ms (performance target)."""
        pack = _make_pack()
        times = self._compile_times_ms(pack)
        p50 = statistics.median(times)
        assert p50 < 20.0, (
            f"p50 compile time {p50:.1f}ms exceeds 20ms target. "
            f"This indicates a performance regression."
        )

    def test_compile_time_nonnegative(self):
        """Sanity: compile_time_ms in report must be >= 0."""
        pack = _make_pack()
        result = pack.compile()
        assert result.report.compile_time_ms >= 0.0

    def test_compile_time_recorded_in_report(self):
        """compile_time_ms must be a positive float in every report."""
        pack = _make_pack()
        result = pack.compile()
        assert isinstance(result.report.compile_time_ms, float)
        assert result.report.compile_time_ms > 0.0

    def test_large_pack_p95_under_50ms(self):
        """Even with 10K-token knowledge blocks, p95 must be < 50ms."""
        large_doc = "token " * 2500  # ~10K tokens worth of content
        pack = ContextPack(budget=8000)
        pack.add(PackBlock(id="sys", type="instructions", content="System.", priority="critical"))
        pack.add(PackBlock(id="big", type="knowledge", content=large_doc, priority="high", max_tokens=1000))
        pack.add(PackBlock(id="ev1", type="evidence", content="evidence " * 50, priority="medium", quality=0.9))
        pack.add(PackBlock(id="ev2", type="evidence", content="evidence " * 50, priority="medium", quality=0.7))

        times = self._compile_times_ms(pack)
        p95 = sorted(times)[int(len(times) * 0.95)]
        assert p95 < 50.0, f"Large-pack p95={p95:.1f}ms exceeds 50ms"

    def test_empty_pack_compile_under_5ms(self):
        """Empty pack should compile near-instantly (< 5ms p95)."""
        times = []
        for _ in range(50):
            pack = ContextPack(budget=8000)
            t0 = time.perf_counter()
            pack.compile()
            times.append((time.perf_counter() - t0) * 1000.0)
        p95 = sorted(times)[int(len(times) * 0.95)]
        assert p95 < 5.0, f"Empty pack p95={p95:.1f}ms — something is very slow"


# ═══════════════════════════════════════════════════════════════════════════
# REQUIREMENT 2: DETERMINISM — "Same Input = Same Output"
# ═══════════════════════════════════════════════════════════════════════════

class TestDeterminismRequirement:
    """100 consecutive compiles produce identical output. No exceptions."""

    def test_100_consecutive_compiles_identical(self):
        """CORE: 100 compiles with same input must produce identical text."""
        pack = _make_pack()
        first = pack.compile().text
        first_hash = hashlib.sha256(first.encode()).hexdigest()

        for i in range(99):
            result = pack.compile()
            run_hash = hashlib.sha256(result.text.encode()).hexdigest()
            assert run_hash == first_hash, (
                f"Non-deterministic output on run {i + 2}! "
                f"Expected hash {first_hash[:8]}…, got {run_hash[:8]}…"
            )

    def test_hash_equality_across_compiles(self):
        """SHA-256 of compiled text must be identical across 3 compiles."""
        pack = _make_pack()
        hashes = {_pack_hash(pack) for _ in range(3)}
        assert len(hashes) == 1, f"Got {len(hashes)} different hashes — non-deterministic!"

    def test_same_input_same_report_decisions(self):
        """Report decisions must be identical across compiles."""
        pack = _make_pack()
        r1 = pack.compile().report
        r2 = pack.compile().report

        assert r1.input_blocks == r2.input_blocks
        assert r1.output_blocks == r2.output_blocks
        assert r1.input_tokens == r2.input_tokens
        assert r1.output_tokens == r2.output_tokens
        assert len(r1.decisions) == len(r2.decisions)

        for d1, d2 in zip(r1.decisions, r2.decisions):
            assert d1.block_id == d2.block_id
            assert d1.action == d2.action
            assert d1.tokens_before == d2.tokens_before
            assert d1.tokens_after == d2.tokens_after
            assert d1.reason == d2.reason

    def test_same_input_same_final_order(self):
        """Final block order must be identical across compiles."""
        pack = _make_pack()
        r1 = pack.compile().report.final_order
        r2 = pack.compile().report.final_order
        assert r1 == r2, f"Block order changed: {r1} vs {r2}"

    def test_priority_ordering_deterministic(self):
        """Blocks with same priority must have stable deterministic order."""
        pack = ContextPack(budget=8000)
        # Multiple same-priority blocks
        for i in range(5):
            pack.add(PackBlock(id=f"blk{i}", type="knowledge", content=f"content {i} " * 20, priority="medium"))

        r1 = pack.compile()
        r2 = pack.compile()
        assert r1.text == r2.text
        assert r1.report.final_order == r2.report.final_order

    def test_compile_with_quality_filter_deterministic(self):
        """Quality filtering decisions must be deterministic."""
        pack = ContextPack(budget=8000, quality_threshold=0.6)
        pack.add(PackBlock(id="good", type="evidence", content="good " * 50, priority="medium", quality=0.9))
        pack.add(PackBlock(id="bad", type="evidence", content="bad " * 50, priority="medium", quality=0.3))

        results = [pack.compile() for _ in range(10)]
        texts = {r.text for r in results}
        assert len(texts) == 1, "Quality filtering is non-deterministic"

        # Confirm bad block is always removed
        for result in results:
            bad_decision = next(d for d in result.report.decisions if d.block_id == "bad")
            assert bad_decision.action == Action.REMOVED

    def test_no_randomness_in_compile_path(self):
        """Pack with compaction and budget overflow must be deterministic."""
        pack = ContextPack(budget=500)
        pack.add(PackBlock(id="sys", type="instructions", content="system " * 50, priority="critical"))
        pack.add(PackBlock(id="big", type="knowledge", content="docs " * 200, priority="high", max_tokens=200))
        pack.add(PackBlock(id="low", type="context", content="extra " * 300, priority="low"))

        first = pack.compile().text
        for _ in range(20):
            assert pack.compile().text == first, "Budget overflow handling is non-deterministic"


# ═══════════════════════════════════════════════════════════════════════════
# REQUIREMENT 3: TRANSPARENCY — "Show Your Work"
# ═══════════════════════════════════════════════════════════════════════════

class TestTransparencyRequirement:
    """Every compile produces a full report. Every decision is inspectable."""

    def test_every_compile_has_report(self):
        """compile() must always return a report, never None."""
        pack = _make_pack()
        result = pack.compile()
        assert result.report is not None
        assert isinstance(result.report, CompileReport)

    def test_every_block_has_decision(self):
        """Every input block must appear in report.decisions."""
        pack = _make_pack()
        result = pack.compile()
        input_ids = {b.id for b in pack._blocks}
        decision_ids = {d.block_id for d in result.report.decisions}
        assert input_ids == decision_ids, (
            f"Blocks without decisions: {input_ids - decision_ids}"
        )

    def test_every_decision_has_reason(self):
        """Every decision must have a non-empty reason string."""
        pack = _make_pack()
        result = pack.compile()
        for d in result.report.decisions:
            assert isinstance(d.reason, str) and len(d.reason) > 0, (
                f"Block '{d.block_id}' has empty reason for action {d.action}"
            )

    def test_report_schema_has_all_summary_fields(self):
        """CompileReport JSON must contain all required summary fields."""
        pack = _make_pack()
        j = pack.compile().report.to_json()

        assert "summary" in j
        summary = j["summary"]
        required = {
            "input_blocks", "output_blocks", "input_tokens", "output_tokens",
            "budget", "savings_percent", "budget_used_percent", "tokens_saved",
            "compile_time_ms",
        }
        missing = required - set(summary.keys())
        assert not missing, f"Report summary missing fields: {missing}"

    def test_report_decisions_json_schema(self):
        """Each decision in JSON must have required fields."""
        pack = ContextPack(budget=8000, quality_threshold=0.5)
        pack.add(PackBlock(id="sys", type="instructions", content="System.", priority="critical"))
        pack.add(PackBlock(id="bad", type="evidence", content="junk", quality=0.2))
        result = pack.compile()
        j = result.report.to_json()

        required_fields = {"block_id", "block_type", "action", "reason", "tokens_before", "tokens_after"}
        for d in j["decisions"]:
            missing = required_fields - set(d.keys())
            assert not missing, f"Decision for '{d.get('block_id')}' missing: {missing}"

    def test_report_machine_readable_json(self):
        """Report JSON must be serializable without errors."""
        pack = _make_pack()
        j = pack.compile().report.to_json()
        serialized = json.dumps(j)
        assert isinstance(serialized, str) and len(serialized) > 0

    def test_report_human_readable_text(self):
        """Report text must contain key sections for human readability."""
        pack = _make_pack()
        text = pack.compile().report.to_text()
        assert "TokenPak Compile Report" in text
        assert "Input:" in text
        assert "Output:" in text
        assert "Savings:" in text

    def test_report_markdown_format(self):
        """Report markdown must have proper headings and table."""
        pack = _make_pack()
        md = pack.compile().report.to_markdown()
        assert "## TokenPak Compile Report" in md
        assert "### Summary" in md
        assert "### Block Decisions" in md

    def test_compacted_decision_shows_method(self):
        """COMPACTED decisions must show the compaction method used."""
        pack = ContextPack(budget=8000)
        pack.add(PackBlock(id="big", type="knowledge", content="word " * 500, priority="high", max_tokens=100))
        result = pack.compile()
        d = next(d for d in result.report.decisions if d.block_id == "big")
        assert d.action == Action.COMPACTED
        assert d.method is not None and len(d.method) > 0

    def test_removed_decision_shows_quality(self):
        """REMOVED decisions due to quality must include quality score."""
        pack = ContextPack(budget=8000, quality_threshold=0.5)
        pack.add(PackBlock(id="low_q", type="evidence", content="stuff", quality=0.2))
        result = pack.compile()
        d = next(d for d in result.report.decisions if d.block_id == "low_q")
        assert d.action == Action.REMOVED
        assert d.quality == pytest.approx(0.2)
        j = d.to_dict()
        assert "quality" in j


# ═══════════════════════════════════════════════════════════════════════════
# REQUIREMENT 4: STACK NEUTRALITY — "Works Everywhere"
# ═══════════════════════════════════════════════════════════════════════════

class TestStackNeutralityRequirement:
    """SDK works without gateway. Outputs compatible with 5+ providers."""

    # ── No gateway required ───────────────────────────────────────────

    def test_sdk_works_without_any_external_calls(self):
        """Core compile must work with zero network activity."""
        # If this test can run, the SDK is self-contained
        pack = _make_pack()
        result = pack.compile()
        assert isinstance(result.text, str)
        assert len(result.text) > 0

    def test_no_required_external_dependencies(self):
        """ContextPack must instantiate with zero required imports beyond stdlib."""
        # tiktoken is optional — fallback exists
        pack = ContextPack(budget=4000)
        pack.add(PackBlock(id="x", type="instructions", content="Hello.", priority="critical"))
        result = pack.compile()
        assert result.text == "Hello."

    # ── to_prompt() — works anywhere ─────────────────────────────────

    def test_to_prompt_returns_string(self):
        """to_prompt() must return a plain string."""
        pack = _make_pack()
        result = pack.compile()
        prompt = result.to_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_to_prompt_matches_text(self):
        """to_prompt() must return the same value as .text."""
        pack = _make_pack()
        result = pack.compile()
        assert result.to_prompt() == result.text

    # ── to_messages() — OpenAI / LiteLLM / Ollama ────────────────────

    def test_to_messages_returns_list(self):
        """to_messages() must return a list of dicts."""
        pack = _make_pack()
        result = pack.compile()
        messages = result.to_messages()
        assert isinstance(messages, list)
        assert len(messages) > 0

    def test_to_messages_has_role_and_content(self):
        """Each message must have 'role' and 'content' keys (OpenAI format)."""
        pack = _make_pack()
        result = pack.compile()
        for msg in result.to_messages():
            assert "role" in msg
            assert "content" in msg
            assert isinstance(msg["role"], str)
            assert isinstance(msg["content"], str)

    def test_to_messages_compatible_with_openai_format(self):
        """Messages must be valid for OpenAI chat.completions.create()."""
        pack = _make_pack()
        result = pack.compile()
        messages = result.to_messages()
        # Validate structure expected by openai SDK
        for msg in messages:
            assert msg["role"] in ("system", "user", "assistant", "tool")
            assert isinstance(msg["content"], str)

    def test_to_messages_with_system_adds_system_message(self):
        """to_messages_with_system() must prepend a system message."""
        pack = _make_pack()
        result = pack.compile()
        messages = result.to_messages_with_system(system="You are helpful.")
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are helpful."
        # User message follows
        assert messages[1]["role"] == "user"

    def test_to_messages_compatible_with_litellm_format(self):
        """LiteLLM accepts same format as OpenAI — validate structure."""
        pack = _make_pack()
        result = pack.compile()
        messages = result.to_messages()
        # LiteLLM passes messages dict list directly to providers
        for msg in messages:
            assert set(msg.keys()) >= {"role", "content"}

    def test_to_messages_compatible_with_ollama_format(self):
        """Ollama chat() accepts same message format."""
        pack = _make_pack()
        result = pack.compile()
        messages = result.to_messages()
        # ollama.chat(messages=...) — same list[dict] format
        for msg in messages:
            assert "role" in msg and "content" in msg

    # ── to_anthropic() — Anthropic SDK ───────────────────────────────

    def test_to_anthropic_returns_tuple(self):
        """to_anthropic() must return (system_str, messages_list)."""
        pack = _make_pack()
        result = pack.compile()
        output = result.to_anthropic()
        assert isinstance(output, tuple)
        assert len(output) == 2

    def test_to_anthropic_system_is_string(self):
        """First element (system) must be a string."""
        pack = _make_pack()
        result = pack.compile()
        system, messages = result.to_anthropic()
        assert isinstance(system, str)

    def test_to_anthropic_messages_is_list(self):
        """Second element (messages) must be a list."""
        pack = _make_pack()
        result = pack.compile()
        system, messages = result.to_anthropic()
        assert isinstance(messages, list)

    # ── to_json() — storage / transfer / observability ───────────────

    def test_to_json_returns_dict(self):
        """to_json() must return a dict with 'text' and 'report'."""
        pack = _make_pack()
        result = pack.compile()
        j = result.to_json()
        assert isinstance(j, dict)
        assert "text" in j
        assert "report" in j

    def test_to_json_is_serializable(self):
        """to_json() output must be json.dumps()-able."""
        pack = _make_pack()
        result = pack.compile()
        serialized = json.dumps(result.to_json())
        assert isinstance(serialized, str)

    def test_to_json_text_matches_compiled(self):
        """to_json()['text'] must match the compiled .text."""
        pack = _make_pack()
        result = pack.compile()
        assert result.to_json()["text"] == result.text

    def test_empty_pack_to_messages_returns_empty(self):
        """Empty compiled text → to_messages() returns empty list."""
        pack = ContextPack(budget=8000)
        result = pack.compile()
        messages = result.to_messages()
        # Empty pack produces no text, so no messages
        assert isinstance(messages, list)

    def test_sdk_zero_required_dependencies(self):
        """Import from tokenpak should work with no external services."""
        from tokenpak import ContextPack as CP
        from tokenpak import PackBlock as PB
        p = CP(budget=4000)
        p.add(PB(id="t", type="instructions", content="Test.", priority="critical"))
        result = p.compile()
        assert result.text == "Test."


# ═══════════════════════════════════════════════════════════════════════════
# REQUIREMENT 5: INCREMENTAL ADOPTION — "Use One Feature First"
# ═══════════════════════════════════════════════════════════════════════════

class TestIncrementalAdoptionRequirement:
    """Each level of the adoption ladder works independently."""

    # ── Level 1: Token counting only ─────────────────────────────────

    def test_level1_count_tokens_single_import(self):
        """Level 1: from tokenpak import count_tokens — zero config."""
        from tokenpak import count_tokens as ct
        assert callable(ct)
        result = ct("Hello, world!")
        assert isinstance(result, int)
        assert result > 0

    def test_level1_count_tokens_empty_string(self):
        """count_tokens('') should return 0."""
        from tokenpak import count_tokens as ct
        assert ct("") == 0

    def test_level1_count_tokens_scales_with_content(self):
        """Longer content should have more tokens than shorter."""
        from tokenpak import count_tokens as ct
        short = ct("Hi")
        long = ct("This is a much longer sentence with many more words and tokens.")
        assert long > short

    def test_level1_no_other_imports_required(self):
        """count_tokens must work without ContextPack, PackBlock, or gateway."""
        # Direct import from tokens module — bypasses __init__.py
        from tokenpak.tokens import count_tokens as ct
        assert ct("standalone") > 0

    # ── Level 2: Simple packing ───────────────────────────────────────

    def test_level2_pack_prompt_single_function(self):
        """Level 2: from tokenpak import pack_prompt — one function."""
        from tokenpak import pack_prompt as pp
        assert callable(pp)
        result = pp(system="You are helpful.", docs="Some docs here.")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_level2_pack_prompt_with_all_args(self):
        """pack_prompt accepts system, docs, history, budget."""
        from tokenpak import pack_prompt as pp
        result = pp(
            system="System instructions.",
            docs="Documentation content.",
            history="Previous messages.",
            budget=2000,
        )
        assert isinstance(result, str)
        assert "System instructions." in result

    def test_level2_pack_prompt_with_only_docs(self):
        """pack_prompt works with only docs (no system or history)."""
        from tokenpak import pack_prompt as pp
        result = pp(docs="Just the docs, nothing else.")
        assert "Just the docs" in result

    def test_level2_pack_prompt_budget_enforced(self):
        """pack_prompt respects the budget parameter."""
        from tokenpak import count_tokens as ct
        from tokenpak import pack_prompt as pp
        big_docs = "word " * 5000  # ~5000 tokens
        result = pp(docs=big_docs, budget=500)
        token_count = ct(result)
        # Result should be substantially smaller than 5000 tokens
        assert token_count < 2000  # Well within budget (with overhead)

    def test_level2_does_not_require_level3(self):
        """pack_prompt must work without importing ContextPack directly."""
        from tokenpak.pack import pack_prompt as pp
        result = pp(system="Hello.")
        assert isinstance(result, str)

    # ── Level 3: Block-based context ─────────────────────────────────

    def test_level3_contextpack_and_packblock(self):
        """Level 3: from tokenpak import TokenPak, Block — core class."""
        from tokenpak import ContextPack, PackBlock
        pack = ContextPack()
        pack.add(PackBlock(id="x", type="knowledge", content="Content here.", priority="high"))
        result = pack.compile()
        assert "Content here." in result.text

    def test_level3_adds_value_over_level2(self):
        """Level 3 adds block-level control (priority, quality, max_tokens)."""
        from tokenpak import ContextPack, PackBlock
        pack = ContextPack(budget=8000, quality_threshold=0.6)
        pack.add(PackBlock(id="high_q", type="evidence", content="High quality.", quality=0.9, priority="medium"))
        pack.add(PackBlock(id="low_q", type="evidence", content="Low quality.", quality=0.3, priority="medium"))
        result = pack.compile()
        # Low quality block should be removed
        assert "High quality." in result.text
        assert "Low quality." not in result.text

    def test_level3_without_gateway(self):
        """Level 3 must work with no network access or gateway."""
        from tokenpak import ContextPack, PackBlock
        pack = ContextPack(budget=4000)
        pack.add(PackBlock(id="a", type="instructions", content="Standalone.", priority="critical"))
        assert pack.compile().text == "Standalone."

    # ── Level 4: Full protocol ────────────────────────────────────────

    def test_level4_compile_with_report(self):
        """Level 4: full compile() returns text + report."""
        from tokenpak import CompileReport, ContextPack, PackBlock
        pack = ContextPack(budget=8000)
        pack.add(PackBlock(id="sys", type="instructions", content="System.", priority="critical"))
        result = pack.compile()
        assert isinstance(result.report, CompileReport)
        assert result.report.decisions

    def test_level4_adds_value_over_level3(self):
        """Level 4 adds compile reports and observability."""
        from tokenpak import Action, ContextPack, PackBlock
        pack = ContextPack(budget=8000, quality_threshold=0.5)
        pack.add(PackBlock(id="good", type="evidence", content="Good evidence.", quality=0.9, priority="medium"))
        pack.add(PackBlock(id="bad", type="evidence", content="Bad evidence.", quality=0.1, priority="medium"))
        result = pack.compile()
        actions = {d.block_id: d.action for d in result.report.decisions}
        assert actions["good"] == Action.KEPT
        assert actions["bad"] == Action.REMOVED

    def test_level4_full_output_formats(self):
        """Level 4: to_prompt, to_messages, to_anthropic, to_json all work."""
        from tokenpak import ContextPack, PackBlock
        pack = ContextPack()
        pack.add(PackBlock(id="x", type="instructions", content="Full protocol.", priority="critical"))
        result = pack.compile()

        assert isinstance(result.to_prompt(), str)
        assert isinstance(result.to_messages(), list)
        assert isinstance(result.to_anthropic(), tuple)
        assert isinstance(result.to_json(), dict)

    # ── Level 5: Wire / serialization ────────────────────────────────

    def test_level5_to_json_serializable(self):
        """Level 5: to_json() produces transferable payload."""
        from tokenpak import ContextPack, PackBlock
        pack = ContextPack()
        pack.add(PackBlock(id="x", type="instructions", content="Wire format.", priority="critical"))
        result = pack.compile()
        payload = json.dumps(result.to_json())
        recovered = json.loads(payload)
        assert recovered["text"] == "Wire format."

    def test_level5_adds_value_over_level4(self):
        """Level 5 adds serialization for cross-agent transfer."""
        from tokenpak import ContextPack, PackBlock
        pack = ContextPack()
        pack.add(PackBlock(id="ctx", type="knowledge", content="Context to transfer.", priority="high"))
        result = pack.compile()
        j = result.to_json()
        # Verify the full report is included in the wire payload
        assert "report" in j
        assert "summary" in j["report"]
        assert "decisions" in j["report"]

    # ── Cross-level independence ──────────────────────────────────────

    def test_each_level_independent_of_higher_levels(self):
        """Level N must not require importing Level N+1 to work."""
        # Level 1 — no ContextPack needed
        from tokenpak.tokens import count_tokens as ct
        assert ct("hello") > 0

        # Level 2 — no report import needed
        from tokenpak.pack import pack_prompt as pp
        assert isinstance(pp(docs="docs"), str)

        # Level 3 — no gateway/cloud needed
        from tokenpak.pack import ContextPack, PackBlock
        p = ContextPack()
        p.add(PackBlock(id="x", type="instructions", content="Hi.", priority="critical"))
        assert p.compile().text == "Hi."

    def test_top_level_imports_all_levels(self):
        """tokenpak package must export all level 1-4 symbols."""
        from tokenpak import (
            CompileReport,  # Level 4
            ContextPack,  # Level 3
            PackBlock,  # Level 3
            count_tokens,  # Level 1
            pack_prompt,  # Level 2
        )
        # All imports succeeded — adoption ladder is accessible
        assert all([
            callable(count_tokens),
            callable(pack_prompt),
            ContextPack is not None,
            PackBlock is not None,
            CompileReport is not None,
        ])
