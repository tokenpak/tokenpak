from __future__ import annotations

from pathlib import Path

from tokenpak.agent.compression.instruction_table import InstructionTable
from tokenpak.agent.compression.pipeline import CompressionPipeline


def _long_instruction() -> str:
    return "Policy: " + ("Always follow safety and formatting requirements. " * 20)


def test_instruction_table_promotes_repeated_block_to_id(tmp_path: Path):
    table = InstructionTable(path=tmp_path / "instruction_table.json", min_tokens=20, min_occurrences=2)
    text = _long_instruction()

    msgs = [{"role": "system", "content": text}]
    out1, stats1 = table.compress_messages(msgs, context_budget_tight=True)
    assert out1[0]["content"] == text
    assert stats1.total_tokens_saved == 0

    out2, stats2 = table.compress_messages(msgs, context_budget_tight=True)
    assert out2[0]["content"].startswith("[INSTRUCTION:POLICY_")
    assert stats2.total_tokens_saved > 0


def test_instruction_table_expand_restores_original_text(tmp_path: Path):
    table = InstructionTable(path=tmp_path / "instruction_table.json", min_tokens=20, min_occurrences=2)
    text = _long_instruction()
    msgs = [{"role": "system", "content": text}]

    compressed, _ = table.compress_messages(msgs, context_budget_tight=True)
    compressed, _ = table.compress_messages(compressed, context_budget_tight=True)
    expanded = table.expand_messages(compressed)

    assert expanded[0]["content"] == text


def test_instruction_table_respects_context_budget_not_tight(tmp_path: Path):
    table = InstructionTable(path=tmp_path / "instruction_table.json", min_tokens=20, min_occurrences=2)
    text = _long_instruction()
    msgs = [{"role": "system", "content": text}]

    table.compress_messages(msgs, context_budget_tight=True)
    out, stats = table.compress_messages(msgs, context_budget_tight=False)

    assert out[0]["content"] == text
    assert stats.total_tokens_saved == 0


def test_pipeline_integration_tracks_instruction_savings(tmp_path: Path):
    text = _long_instruction()
    pipeline = CompressionPipeline(
        enable_dedup=False,
        enable_segmentation=False,
        enable_directives=False,
        enable_instruction_table=True,
        instruction_table_path=str(tmp_path / "instruction_table.json"),
        context_budget_tight=True,
    )

    msgs = [{"role": "system", "content": text}]
    pipeline.run(msgs)
    result = pipeline.run(msgs)

    assert "instruction_table" in result.stages_run
    assert result.instruction_replacements
    assert sum(result.instruction_savings.values()) > 0
